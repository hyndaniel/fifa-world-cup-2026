# 预测 v2 Phase 4 全盘口扩展 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已上线的胜平负(had)market-anchored 全链路上,增加让球(hhad)与总进球/大小球(ttg)两个盘口,完全复用现有去水/融合/Brier 机制,had 路径零回归。

**Architecture:** 抽出盘口无关的纯函数核(`zucai_odds_devig`/`blend`/`confidence`/`baseline_market`/`apply_deviations` 都吃 `keys`),had/hhad/ttg 各是一份 `market_cfg` 适配器;`baseline_had` 退化为 `baseline_market(..., HAD_CFG)` 的瘦封装以保兼容。结果表不动,各盘口实际结果由进球纯函数派生;v2 预测落库与跑分卡改为按盘口分组/分节。

**Tech Stack:** Python 3.12 标准库 + sqlite3;pytest。无新依赖。

## Global Constraints

- 概率单位 = 百分数(%),每盘口归一到 100(残差并入最大项)。
- **had 零回归**:`tests/test_baseline.py`、`tests/test_scoring.py`、`tests/test_scorecard.py` 现有用例必须保持全绿;所有泛化函数保留 `keys=_KEYS` 默认值与 had 瘦封装。
- 让球/总进球在我们的源里只有竞彩 → 恒 🔴软锚(`confidence.label="soft"`);绝不假装多源硬锚。
- 比分分布 / BTTS / 半全场不做(无市场定价)。大小球只 2.5 派生「可下/别碰」,不单独计 Brier。
- `verdict`(可下/别碰)由 agent 判,代码只承载字段、不内置阈值。
- 缓存 payload 形状(既有):`zucai` = `{"had":{h,d,a 赔},"hhad":{line,h,d,a 赔},"ttg":{"0":赔,…,"7":赔}}`("7"=7+);`poly` = `{"poly_devig":{h,d,a %}}`;`consensus` = `{"had":{欧赔},…}`。
- 每任务结束 `git add` 仅相关文件并提交,提交信息前缀 `feat(v2):` / `test(v2):`。

> **实现期需确认一处**:竞彩 hhad 的 `line` 符号约定。本计划按「主队让球记负」(主让一球 `line=-1`)编写 `_hhad_outcome`。Task 2 实现时,用一条真实 `hhad` payload 核对符号;若竞彩主让记正,则把 `_hhad_outcome` 里的 `+ line` 改为 `- line` 并相应改测试注释。

---

### Task 1: 泛化纯函数核(去水/融合/置信吃 keys)

**Files:**
- Modify: `backend/baseline.py`(`zucai_had_devig`、`blend_had`、`confidence`)
- Test: `tests/test_baseline.py`

**Interfaces:**
- Produces:
  - `zucai_odds_devig(odds: dict, keys=_KEYS) -> dict`(竞彩欧赔→去水%,限定 keys)
  - `blend(sources: dict, keys, weights=DEFAULT_WEIGHTS) -> dict`
  - `confidence(sources: dict, keys=_KEYS) -> dict`
  - 兼容封装:`zucai_had_devig(had)`、`blend_had(sources, weights=DEFAULT_WEIGHTS)` 行为不变。

- [ ] **Step 1: Write failing tests**

追加到 `tests/test_baseline.py`(顶部已 import `zucai_had_devig, blend_had, confidence`;补 import 泛化名):

```python
from backend.baseline import zucai_odds_devig, blend  # 追加到现有 import 行下方


def test_zucai_odds_devig_generic_keys_sum_100():
    out = zucai_odds_devig({"0": 5.0, "1": 2.5, "2": 3.0}, keys=("0", "1", "2"))
    assert abs(sum(out.values()) - 100.0) < 0.5      # 1 位四舍五入残差容差
    assert out["1"] > out["2"] > out["0"]            # 赔率越低概率越高


def test_blend_generic_keys_single_source_sum_100():
    out = blend({"zucai": {"0": 20.0, "1": 30.0, "2": 25.0, "3": 25.0}},
                keys=("0", "1", "2", "3"), weights={"zucai": 1.0})
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert set(out) == {"0", "1", "2", "3"}


def test_confidence_generic_keys_single_source_soft():
    c = confidence({"zucai": {"0": 50, "1": 50}}, keys=("0", "1"))
    assert c["n_sources"] == 1 and c["label"] == "soft"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_baseline.py -q`
Expected: FAIL —`ImportError: cannot import name 'zucai_odds_devig'`.

- [ ] **Step 3: Implement generalization**

在 `backend/baseline.py` 中替换 `zucai_had_devig`、`blend_had`、`confidence` 三处为:

```python
def zucai_odds_devig(odds: dict, keys=_KEYS) -> dict:
    """竞彩欧赔 → 去水概率 %。隐含=1/赔率, 乘法归一到 100。keys 限定参与的 outcome。"""
    implied = {k: 1.0 / float(odds[k]) for k in keys if odds.get(k)}
    return {k: round(v, 1) for k, v in devig(implied).items()}


def zucai_had_devig(had: dict) -> dict:
    """胜平负三选一去水(zucai_odds_devig 瘦封装, 向后兼容)。"""
    return zucai_odds_devig(had, _KEYS)


def blend(sources: dict, keys, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """多源去水概率加权融合;只用在场源、权重重新归一;输出按 keys 归一到 100。"""
    present = {s: weights[s] for s in sources if s in weights and sources[s]}
    wsum = sum(present.values())
    if wsum <= 0:
        return {}
    raw = {k: sum(sources[s].get(k, 0.0) * present[s] for s in present) / wsum for k in keys}
    tot = sum(raw.values())
    if tot <= 0:
        return {}
    out = {k: round(v / tot * 100, 1) for k, v in raw.items()}
    # 独立四舍五入会让和偏离 100,把残差并入最大项,严格归一。
    residual = round(100.0 - sum(out.values()), 1)
    if residual:
        kmax = max(out, key=out.get)
        out[kmax] = round(out[kmax] + residual, 1)
    return out


def blend_had(sources: dict, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """胜平负融合(blend 瘦封装, 向后兼容)。"""
    return blend(sources, _KEYS, weights)


def confidence(sources: dict, keys=_KEYS) -> dict:
    """覆盖几个源 + 跨源最大极差。3源=hard/2=medium/1=soft/0=none。"""
    n = len(sources)
    label = {3: "hard", 2: "medium", 1: "soft"}.get(n, "none")
    spread = 0.0
    for k in keys:
        vals = [s[k] for s in sources.values() if k in s]
        if len(vals) >= 2:
            spread = max(spread, max(vals) - min(vals))
    return {"n_sources": n, "label": label, "max_spread": round(spread, 1)}
```

- [ ] **Step 4: Run to verify pass (含 had 回归)**

Run: `python3 -m pytest tests/test_baseline.py -q`
Expected: PASS(新 3 条 + 现有 had 用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add backend/baseline.py tests/test_baseline.py
git commit -m "feat(v2): 去水/融合/置信泛化吃 keys(had 瘦封装零回归)"
```

---

### Task 2: `baseline_market` + HAD/HHAD 配置 + 让球结算

**Files:**
- Modify: `backend/baseline.py`(加 `HAD_CFG`/`HHAD_CFG`/`_market_keys`/`baseline_market`/`_hhad_outcome`;`baseline_had` 改瘦封装)
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `zucai_odds_devig`、`blend`、`confidence`(Task 1);`_latest_payload`、`_outcome_key`(既有)。
- Produces:
  - `HAD_CFG`、`HHAD_CFG`(dict:`market/pool/keys/weights`)
  - `baseline_market(cache_path, match_key, cfg, weights=None) -> dict|None`(返回 `{match_key, market, baseline, sources, confidence[, line]}`)
  - `baseline_had(cache_path, match_key, weights=DEFAULT_WEIGHTS)`(瘦封装,签名不变)
  - `_hhad_outcome(home_goals, away_goals, line) -> "h"|"d"|"a"`

- [ ] **Step 1: Write failing tests**

追加到 `tests/test_baseline.py`:

```python
from backend.baseline import baseline_market, HHAD_CFG, _hhad_outcome  # 追加


def _seed_hhad(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("t", "zucai", "周三053", "南非 vs 韩国", "ko",
                  json.dumps({"hhad": {"line": -1, "h": 2.10, "d": 3.30, "a": 3.00}})))
    conn.commit(); conn.close()


def test_baseline_market_hhad_single_source_soft_with_line():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_hhad(path)
    out = baseline_market(path, "周三053", HHAD_CFG)
    assert out["market"] == "hhad" and out["line"] == -1
    assert set(out["sources"]) == {"zucai"}
    assert out["confidence"]["label"] == "soft"
    assert abs(sum(out["baseline"].values()) - 100.0) < 0.01


def test_baseline_had_still_three_source_hard():
    # 零回归: baseline_had(经 baseline_market) 仍三源硬锚
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    out = baseline_had(path, "周三053")
    assert out["confidence"]["label"] == "hard"
    assert out["baseline"]["a"] > out["baseline"]["h"]


def test_hhad_outcome_boundaries():
    assert _hhad_outcome(2, 0, -1) == "h"   # 主让一球, 2-0 净+1 → 赢盘
    assert _hhad_outcome(1, 0, -1) == "d"   # 主让一球, 1-0 净 0  → 走盘(竞彩计平)
    assert _hhad_outcome(0, 0, -1) == "a"   # 主让一球, 0-0 净-1 → 输盘
    assert _hhad_outcome(0, 1, 1) == "d"    # 主受让一球, 0-1 净 0 → 平
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_baseline.py -q`
Expected: FAIL —`ImportError: cannot import name 'baseline_market'`.

- [ ] **Step 3: Implement**

在 `backend/baseline.py` 加配置与泛化装配(放在 `_latest_payload` 之后),并把现有 `baseline_had` 整个函数体替换为瘦封装:

```python
HAD_CFG = {"market": "had", "pool": "had", "keys": _KEYS, "weights": DEFAULT_WEIGHTS}
HHAD_CFG = {"market": "hhad", "pool": "hhad", "keys": _KEYS, "weights": {"zucai": 1.0}}


def _market_keys(cfg, payloads):
    """固定 keys 直接返回;keys=None(ttg)从竞彩 payload 动态取并按数值排序。"""
    if cfg["keys"] is not None:
        return cfg["keys"]
    pool = (payloads.get("zucai") or {}).get(cfg["pool"]) or {}
    return tuple(sorted((k for k in pool if k != "line"), key=lambda x: int(x)))


def baseline_market(cache_path, match_key, cfg, weights=None):
    """从 odds_cache 装配一场某盘口基线表。无任何源 → None。
    返回 {match_key, market, baseline, sources, confidence[, line]}。"""
    weights = weights or cfg["weights"]
    conn = sqlite3.connect(cache_path)
    try:
        raw = {s: _latest_payload(conn, s, match_key) for s in ("zucai", "poly", "consensus")}
    finally:
        conn.close()
    keys = _market_keys(cfg, raw)
    pool, sources, line = cfg["pool"], {}, None
    z = raw.get("zucai")
    if z and z.get(pool):
        zp = z[pool]
        line = zp.get("line") if isinstance(zp, dict) else None
        sources["zucai"] = zucai_odds_devig(zp, keys)
    # poly / consensus 仅对胜平负有对应定价(我们的源里 hhad/ttg 无)
    if pool == "had":
        p = raw.get("poly")
        if p and p.get("poly_devig"):
            sources["poly"] = {k: round(float(p["poly_devig"][k]), 1) for k in keys
                               if p["poly_devig"].get(k) is not None}
        c = raw.get("consensus")
        if c and c.get("had"):
            sources["consensus"] = zucai_odds_devig(c["had"], keys)
    if not sources:
        return None
    out = {"match_key": match_key, "market": cfg["market"],
           "baseline": blend(sources, keys, weights),
           "sources": sources, "confidence": confidence(sources, keys)}
    if line is not None:
        out["line"] = line
    return out


def baseline_had(cache_path: str, match_key: str, weights: dict = DEFAULT_WEIGHTS):
    """胜平负基线(baseline_market 瘦封装, 向后兼容)。无任何源 → None。"""
    return baseline_market(cache_path, match_key, HAD_CFG, weights)


def _hhad_outcome(home_goals: int, away_goals: int, line) -> str:
    """让球结算: (主 + line - 客) 的符号 → h/d/a。line=主队让球数(主让一球记 -1)。
    整数盘三分法, 无 push。"""
    adj = home_goals + float(line) - away_goals
    if adj > 0:
        return "h"
    if adj == 0:
        return "d"
    return "a"
```

> 删除原 `baseline_had` 旧实现体(读三源那段)——已被 `baseline_market` 取代。`_latest_payload` 保留。

- [ ] **Step 4: 用真实 payload 核对 line 符号(一次性人工确认)**

抓一条真实 `hhad`(若本地 `.cache/odds_cache.db` 有数据):
Run: `python3 -c "from backend.baseline import _latest_payload; import sqlite3; c=sqlite3.connect('.cache/odds_cache.db'); print(_latest_payload(c,'zucai', input('key:')))" 2>/dev/null || echo "无缓存, 跳过, 上线前再核"`
确认 `hhad.line` 对主让球是负还是正;若为正,改 `_hhad_outcome` 的 `+ float(line)` 为 `- float(line)` 并改测试注释。无缓存则记为上线前待核。

- [ ] **Step 5: Run to verify pass (含 had 回归)**

Run: `python3 -m pytest tests/test_baseline.py -q`
Expected: PASS(新用例 + 现有 had 用例全绿)。

- [ ] **Step 6: Commit**

```bash
git add backend/baseline.py tests/test_baseline.py
git commit -m "feat(v2): baseline_market + 让球(竞彩单源软锚)+ 让球结算"
```

---

### Task 3: 总进球配置 + 大小球派生 + 结果取分

**Files:**
- Modify: `backend/baseline.py`(加 `TTG_CFG`/`over_under`/`_ttg_outcome`/`get_result_goals`)
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `baseline_market`、`_market_keys`(Task 2);`_RESULTS_SCHEMA`(既有)。
- Produces:
  - `TTG_CFG`(`keys=None` → 动态)
  - `over_under(dist: dict, lines=(2.5,)) -> dict`(`{"2.5":{"over":%,"under":%}}`)
  - `_ttg_outcome(home_goals, away_goals, cap=7) -> str`("0".."7")
  - `get_result_goals(cache_path, match_key) -> (home, away) | None`

- [ ] **Step 1: Write failing tests**

追加到 `tests/test_baseline.py`:

```python
from backend.baseline import TTG_CFG, over_under, _ttg_outcome, get_result_goals  # 追加


def _seed_ttg(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("t", "zucai", "周三053", "南非 vs 韩国", "ko",
                  json.dumps({"ttg": {"0": 12.0, "1": 6.0, "2": 4.5, "3": 4.5,
                                      "4": 7.0, "5": 13.0, "6": 26.0, "7": 41.0}})))
    conn.commit(); conn.close()


def test_baseline_market_ttg_dynamic_keys_sum_100():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_ttg(path)
    out = baseline_market(path, "周三053", TTG_CFG)
    assert out["market"] == "ttg"
    assert set(out["baseline"]) == {"0", "1", "2", "3", "4", "5", "6", "7"}
    assert abs(sum(out["baseline"].values()) - 100.0) < 0.01
    assert out["confidence"]["label"] == "soft"   # 竞彩单源


def test_over_under_derives_from_dist():
    dist = {"0": 10.0, "1": 20.0, "2": 30.0, "3": 25.0, "4": 15.0}
    ou = over_under(dist, lines=(2.5,))
    assert ou["2.5"]["over"] == 40.0    # P(3)+P(4)
    assert ou["2.5"]["under"] == 60.0


def test_ttg_outcome_caps_at_7():
    assert _ttg_outcome(2, 1) == "3"
    assert _ttg_outcome(5, 4) == "7"    # 9 → cap 7


def test_get_result_goals_roundtrip():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_ttg(path)
    record_result(path, "周三053", 2, 1)
    assert get_result_goals(path, "周三053") == (2, 1)
    assert get_result_goals(path, "缺") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_baseline.py -q`
Expected: FAIL —`ImportError: cannot import name 'TTG_CFG'`.

- [ ] **Step 3: Implement**

在 `backend/baseline.py` 加(`TTG_CFG` 放在 `HHAD_CFG` 旁;其余放结果区附近):

```python
TTG_CFG = {"market": "ttg", "pool": "ttg", "keys": None, "weights": {"zucai": 1.0}}


def over_under(dist: dict, lines=(2.5,)) -> dict:
    """从总进球分布派生大小球。P(大 L)=Σ_{k>L} P(k)。返回 {"2.5":{"over":%,"under":%}}。"""
    out = {}
    for L in lines:
        over = round(sum(v for k, v in dist.items() if int(k) > L), 1)
        out[str(L)] = {"over": over, "under": round(100.0 - over, 1)}
    return out


def _ttg_outcome(home_goals: int, away_goals: int, cap: int = 7) -> str:
    """总进球 actual: min(主+客, cap) → 字符串键('7'=7+)。"""
    return str(min(home_goals + away_goals, cap))


def get_result_goals(cache_path: str, match_key: str):
    """取某场实际进球 (home, away);无 → None。"""
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)
        r = conn.execute("SELECT home_goals, away_goals FROM match_results WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return (r[0], r[1]) if r else None
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_baseline.py -q`
Expected: PASS(全部,含 had 回归)。

- [ ] **Step 5: Commit**

```bash
git add backend/baseline.py tests/test_baseline.py
git commit -m "feat(v2): 总进球基线(动态键)+ 大小球派生 + 取分"
```

---

### Task 4: `apply_deviations` 泛化吃 keys

**Files:**
- Modify: `backend/v2_predict.py`(`apply_deviations`)
- Test: `tests/test_v2_predict.py`

**Interfaces:**
- Produces: `apply_deviations(baseline, deviations, keys=_KEYS) -> dict`(had 默认行为不变)。

- [ ] **Step 1: Write failing test**

追加到 `tests/test_v2_predict.py`:

```python
def test_apply_deviations_ttg_multikey_sum_100():
    base = {"0": 10.0, "1": 20.0, "2": 30.0, "3": 25.0, "4": 15.0}
    out = apply_deviations(base, [{"outcome": "3", "to": 35.0, "reason": "r"}],
                           keys=tuple(base))
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert abs(out["3"] - 35.0) < 0.6   # 钉住 35(归一后±四舍五入)
    assert set(out) == set(base)
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_v2_predict.py::test_apply_deviations_ttg_multikey_sum_100 -q`
Expected: FAIL —`TypeError: apply_deviations() got an unexpected keyword argument 'keys'`.

- [ ] **Step 3: Implement**

把 `backend/v2_predict.py` 的 `apply_deviations` 整体替换为(仅签名加 `keys`,内部 `_KEYS`→`keys`):

```python
def apply_deviations(baseline: dict, deviations: list, keys=_KEYS) -> dict:
    """把每条偏离的 outcome 钉到 to;非偏离 outcome 按基线原比例吸收差额;归一到 100。
    先一次性收集所有被钉 outcome(同一 outcome 多次取最后),再统一分配剩余,
    同场多条偏离不互相重标前者;单条结果与原实现一致。keys 支持任意盘口(ttg 多键)。"""
    cur = {k: float(baseline.get(k, 0.0)) for k in keys}
    pinned = {dv["outcome"]: float(dv["to"]) for dv in deviations}
    for oc, to in pinned.items():
        cur[oc] = to
    others = [k for k in keys if k not in pinned]
    rest_old = sum(float(baseline.get(k, 0.0)) for k in others)
    rest_new = max(0.0, 100.0 - sum(pinned.values()))
    if others:
        if rest_old > 0:
            for k in others:
                cur[k] = float(baseline.get(k, 0.0)) / rest_old * rest_new
        else:
            for k in others:
                cur[k] = rest_new / len(others)
    tot = sum(cur.values())
    return {k: round(cur[k] / tot * 100, 1) for k in keys} if tot else cur
```

- [ ] **Step 4: Run to verify pass (含 had 偏离回归)**

Run: `python3 -m pytest tests/test_v2_predict.py -q`
Expected: PASS(新 1 条 + 现有 `test_apply_*`/多偏离用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add backend/v2_predict.py tests/test_v2_predict.py
git commit -m "feat(v2): apply_deviations 泛化吃 keys(ttg 多键)"
```

---

### Task 5: `build_v2_prediction` 产按盘口分组形状

**Files:**
- Modify: `backend/v2_predict.py`(`build_v2_prediction`)
- Test: `tests/test_v2_predict.py`(迁移既有 shape 测试)

**Interfaces:**
- Consumes: `apply_deviations(keys=...)`(Task 4);`over_under`(Task 3)。
- Produces:
  `build_v2_prediction(match_key, reliability, scenarios, markets_in) -> dict`
  其中 `markets_in = {market: {"baseline":{...}, "deviations":[...] [, "line":..]}}`;
  返回 `{match_key, reliability, scenarios, markets:{market:{baseline, v2, deviations[, line][, ou]}}}`。

- [ ] **Step 1: 迁移既有 shape 测试为新签名(写失败测试)**

在 `tests/test_v2_predict.py` 中,把现有 `test_build_v2_prediction_shape` 整个函数替换为:

```python
def test_build_v2_prediction_markets_shape():
    out = build_v2_prediction("M1", "乱", ["默契平"], {
        "had": {"baseline": {"h": 30.0, "d": 30.0, "a": 40.0},
                "deviations": [{"outcome": "a", "to": 64.0, "reason": "韩国只需平"}]},
        "hhad": {"baseline": {"h": 40.0, "d": 30.0, "a": 30.0}, "deviations": [], "line": -1},
        "ttg": {"baseline": {"0": 20.0, "1": 30.0, "2": 30.0, "3": 20.0}, "deviations": []}})
    assert out["match_key"] == "M1" and out["reliability"] == "乱"
    assert out["scenarios"] == ["默契平"]
    had = out["markets"]["had"]
    assert had["baseline"] == {"h": 30.0, "d": 30.0, "a": 40.0}   # 基线原值留存
    assert abs(sum(had["v2"].values()) - 100.0) < 0.01 and had["v2"]["a"] > had["v2"]["h"]
    assert out["markets"]["hhad"]["line"] == -1
    ttg = out["markets"]["ttg"]
    assert "ou" in ttg and "2.5" in ttg["ou"]
    assert abs(ttg["ou"]["2.5"]["over"] - 20.0) < 0.6            # 仅 P(3)=20
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_v2_predict.py::test_build_v2_prediction_markets_shape -q`
Expected: FAIL —`TypeError`(旧 `build_v2_prediction` 签名是 `(baseline_sheet, deviations, reliability, scenarios)`)。

- [ ] **Step 3: Implement**

把 `backend/v2_predict.py` 的 `build_v2_prediction` 整体替换为:

```python
def build_v2_prediction(match_key, reliability, scenarios, markets_in):
    """按盘口装配 v2 预测。markets_in: {market: {baseline, deviations[, line]}}。
    每盘口产 {baseline, v2(应用偏离), deviations[, line][, ou(仅 ttg)]}。"""
    from .baseline import over_under
    markets = {}
    for m, mi in markets_in.items():
        base = mi["baseline"]
        keys = tuple(base)
        entry = {"baseline": dict(base),
                 "v2": apply_deviations(base, mi.get("deviations") or [], keys=keys),
                 "deviations": mi.get("deviations") or []}
        if "line" in mi:
            entry["line"] = mi["line"]
        if m == "ttg":
            entry["ou"] = over_under(entry["v2"], lines=(2.5,))
        markets[m] = entry
    return {"match_key": match_key, "reliability": reliability,
            "scenarios": scenarios or [], "markets": markets}
```

> `record_v2_prediction`/`get_v2_prediction` 不变(存取整 JSON);其 roundtrip 测试用任意 dict,无需改。

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_v2_predict.py -q`
Expected: PASS(迁移后 shape 测试 + 其余全绿)。

- [ ] **Step 5: Commit**

```bash
git add backend/v2_predict.py tests/test_v2_predict.py
git commit -m "feat(v2): build_v2_prediction 产按盘口分组(had/hhad/ttg+大小球)"
```

---

### Task 6: 逐盘口打分 + 跑分卡按盘口分节

**Files:**
- Modify: `tools/v2_report.py`(`collect`、`render`、`main`)
- Test: `tests/test_v2_report.py`(迁移既有 render 测试)

**Interfaces:**
- Consumes: `baseline_market`/`HAD_CFG`/`HHAD_CFG`/`TTG_CFG`/`_hhad_outcome`/`_ttg_outcome`/`get_result_goals`(Task 2/3);`_outcome_key`(既有);`three_way`/`aggregate`/`deviation_audit`(既有,泛型);`get_v2_prediction`/`get_v1`(既有);`_fmt`/`_result_keys`(既有)。
- Produces:
  - `collect(cache_path) -> {market: {"rows":[...], "per_match":[...]}}`
  - `render(collected, audits) -> str`(按盘口分节)

- [ ] **Step 1: 迁移既有 render 测试 + 写新失败测试**

把 `tests/test_v2_report.py` 的两个现有测试(`test_render_contains_scorecard_and_verdict`、`test_render_per_match_brier_2dp_and_missing`)整体替换为:

```python
# tests/test_v2_report.py
from tools.v2_report import render


def _collected_one_market():
    return {
        "had": {"rows": [{"deviated": True, "v2": 0.25, "market": 0.31}],
                "per_match": [{"match_key": "M1", "reliability": "乱",
                               "brier": {"v1": None, "v2": 0.1234, "market": 0.3567}}]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []},
    }


def test_render_sections_per_market_and_2dp():
    collected = _collected_one_market()
    audits = {"had": {"n_deviated": 1, "v2_mean": 0.25, "market_mean": 0.31, "delta": -0.06},
              "hhad": {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None},
              "ttg": {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}}
    md = render(collected, audits)
    assert "## 胜平负" in md and "## 让球" in md and "## 总进球" in md
    assert "| M1 | 乱 | — | 0.12 | 0.36 |" in md     # 2dp + 缺失为 —(v1 仅 had 也可能缺)
    assert "0.1234" not in md
    assert "拉低" in md or "-0.06" in md
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_v2_report.py -q`
Expected: FAIL —`render()` 旧签名是 `(agg, audit, per_match)`,新测试传 `(collected, audits)`。

- [ ] **Step 3: Implement collect + render + main**

把 `tools/v2_report.py` 中 import 区、`render`、`collect`、`main` 替换/扩充如下(`_fmt`、`_result_keys` 保留):

```python
from backend.scoring import brier_multi  # noqa: E402  (保留)
from backend.scorecard import aggregate, deviation_audit, three_way  # noqa: E402
from backend.baseline import (baseline_market, HAD_CFG, HHAD_CFG, TTG_CFG,  # noqa: E402
                              _outcome_key, _hhad_outcome, _ttg_outcome, get_result_goals)
from backend.v2_predict import get_v2_prediction  # noqa: E402
from backend.v1_log import get_v1  # noqa: E402

MARKETS = [("had", HAD_CFG), ("hhad", HHAD_CFG), ("ttg", TTG_CFG)]
MARKET_NAMES = {"had": "胜平负", "hhad": "让球", "ttg": "总进球/大小球"}


def _actual_for(market, hg, ag, line):
    if market == "had":
        return _outcome_key(hg, ag)
    if market == "hhad":
        return _hhad_outcome(hg, ag, line) if line is not None else None
    if market == "ttg":
        return _ttg_outcome(hg, ag)
    return None


def collect(cache_path):
    out = {m: {"rows": [], "per_match": []} for m, _ in MARKETS}
    for mk in _result_keys(cache_path):
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        hg, ag = goals
        v2rec = get_v2_prediction(cache_path, mk)
        v1rec = get_v1(cache_path, mk)
        for market, cfg in MARKETS:
            bl = baseline_market(cache_path, mk, cfg)
            if not bl:
                continue
            actual = _actual_for(market, hg, ag, bl.get("line"))
            if actual is None:
                continue
            v2p = ((v2rec or {}).get("markets", {}).get(market) or {}).get("v2")
            v1p = v1rec["probs"] if (market == "had" and v1rec) else None  # v1 仅胜平负
            b = three_way(v1p, v2p, bl["baseline"], actual)
            deviated = bool(((v2rec or {}).get("markets", {}).get(market) or {}).get("deviations"))
            out[market]["rows"].append({"deviated": deviated, "v2": b["v2"], "market": b["market"]})
            out[market]["per_match"].append({"match_key": mk,
                                             "reliability": (v2rec or {}).get("reliability", ""),
                                             "brier": b})
    return out


def render(collected, audits):
    lines = ["# 预测 v2 跑分卡(全盘口)", ""]
    for market, _ in MARKETS:
        data = collected[market]
        agg = aggregate([{"v1": p["brier"]["v1"], "v2": p["brier"]["v2"],
                          "market": p["brier"]["market"]} for p in data["per_match"]])
        lines += [f"## {MARKET_NAMES[market]}", "",
                  f"配对场数: {agg['n']}", "",
                  "| 方 | 平均 Brier(越低越准) |", "|---|---|",
                  f"| v1(老方法) | {_fmt(agg.get('v1_mean'))} |",
                  f"| **v2** | **{_fmt(agg.get('v2_mean'))}** |",
                  f"| 市场基线 | {_fmt(agg.get('market_mean'))} |", ""]
        audit = audits.get(market, {})
        if audit.get("delta") is not None:
            verdict = ("偏离平均**拉低** Brier(有用)" if audit["delta"] < 0
                       else "偏离平均**拉高** Brier(帮倒忙,该收敛回市场)")
            lines += [f"偏离审计:{audit['n_deviated']} 场有偏离,v2 {audit['v2_mean']} vs "
                      f"市场 {audit['market_mean']},delta {audit['delta']} → {verdict}", ""]
        lines += ["| 场次 | 靠谱度 | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
        for m in data["per_match"]:
            b = m["brier"]
            lines.append(f"| {m['match_key']} | {m.get('reliability','')} | "
                         f"{_fmt(b.get('v1'))} | {_fmt(b.get('v2'))} | {_fmt(b.get('market'))} |")
        lines.append("")
    lines += ["_红线:概率预测非投注建议;v2 跑不赢市场就回归市场基线+避雷器。_"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="渲染 v2 全盘口跑分卡")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    collected = collect(a.cache)
    audits = {m: deviation_audit(collected[m]["rows"]) for m, _ in MARKETS}
    md = render(collected, audits)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"写出全盘口跑分卡 → {a.out}")
```

> 删除旧 `collect`/`render`/`main` 实现(被上面取代)。`get_result`(had-only)不再被本文件使用,但保留在 `baseline.py` 供他处。

- [ ] **Step 4: Run to verify pass (全套)**

Run: `python3 -m pytest tests/test_v2_report.py tests/test_scorecard.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/v2_report.py tests/test_v2_report.py
git commit -m "feat(v2): 跑分卡逐盘口打分 + 按盘口分节渲染"
```

---

### Task 7: 回测逐盘口市场基线 Brier

**Files:**
- Modify: `tools/backtest_baseline.py`(`run_backtest`、`main`)
- Test: `tests/test_backtest_baseline.py`

**Interfaces:**
- Consumes: `baseline_market`/`HAD_CFG`/`HHAD_CFG`/`TTG_CFG`/`_hhad_outcome`/`_ttg_outcome`/`get_result_goals`/`_outcome_key`(Task 2/3);`brier_multi`(既有)。
- Produces: `run_backtest(cache_path) -> {market: {"n", "market_brier", "per_match"}}`。

- [ ] **Step 1: 迁移既有测试 + 写新失败测试**

把 `tests/test_backtest_baseline.py` 的两个测试整体替换为(seed 复用,断言改新结构):

```python
import sqlite3, json, os, tempfile
from tools.backtest_baseline import run_backtest


def _seed(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", [
        ("t", "zucai", "M1", "A vs B", "k",
         json.dumps({"had": {"h": 6.0, "d": 3.9, "a": 1.44},
                     "hhad": {"line": 1, "h": 2.2, "d": 3.3, "a": 2.9},
                     "ttg": {"0": 12.0, "1": 6.0, "2": 4.5, "3": 4.5, "4": 7.0}})),
        ("t", "poly", "M1", "A vs B", "k", json.dumps({"poly_devig": {"h": 15.4, "d": 23.4, "a": 61.2}})),
        ("t", "consensus", "M1", "A vs B", "k", json.dumps({"had": {"h": 6.5, "d": 4.0, "a": 1.55}})),
    ])
    conn.commit(); conn.close()


def test_run_backtest_per_market_pairs_and_scores():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)
    from backend.baseline import record_result
    record_result(path, "M1", 0, 1)  # 客胜, 总进球 1
    out = run_backtest(path)
    assert out["had"]["n"] == 1 and out["had"]["market_brier"] < 0.5   # 重押客胜且应验
    assert out["ttg"]["n"] == 1 and 0.0 <= out["ttg"]["market_brier"] <= 2.0
    assert out["hhad"]["n"] == 1                                       # 让球也配上对


def test_run_backtest_no_results():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)
    assert run_backtest(path)["had"]["n"] == 0
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_backtest_baseline.py -q`
Expected: FAIL —`KeyError: 'had'`(旧 `run_backtest` 返回平铺 `{"n","market_brier",...}`)。

- [ ] **Step 3: Implement**

替换 `tools/backtest_baseline.py` 的 import 区、`run_backtest`、`main`(`_result_keys`、`DEFAULT_CACHE` 保留):

```python
from backend.baseline import (baseline_market, HAD_CFG, HHAD_CFG, TTG_CFG,  # noqa: E402
                              _outcome_key, _hhad_outcome, _ttg_outcome, get_result_goals)
from backend.scoring import brier_multi  # noqa: E402

MARKETS = [("had", HAD_CFG), ("hhad", HHAD_CFG), ("ttg", TTG_CFG)]
MARKET_NAMES = {"had": "胜平负", "hhad": "让球", "ttg": "总进球"}


def _actual_for(market, hg, ag, line):
    if market == "had":
        return _outcome_key(hg, ag)
    if market == "hhad":
        return _hhad_outcome(hg, ag, line) if line is not None else None
    if market == "ttg":
        return _ttg_outcome(hg, ag)
    return None


def run_backtest(cache_path: str) -> dict:
    out = {m: {"n": 0, "market_brier": None, "per_match": []} for m, _ in MARKETS}
    for mk in _result_keys(cache_path):
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        hg, ag = goals
        for market, cfg in MARKETS:
            bl = baseline_market(cache_path, mk, cfg)
            if not bl:
                continue
            actual = _actual_for(market, hg, ag, bl.get("line"))
            if actual is None:
                continue
            b = brier_multi(bl["baseline"], actual)
            out[market]["per_match"].append({"match_key": mk, "baseline": bl["baseline"],
                                             "actual": actual, "brier": b})
    for market, _ in MARKETS:
        briers = [p["brier"] for p in out[market]["per_match"]]
        out[market]["n"] = len(briers)
        out[market]["market_brier"] = round(sum(briers) / len(briers), 4) if briers else None
    return out


def main():
    ap = argparse.ArgumentParser(description="回测逐盘口市场基线 Brier 基准")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    a = ap.parse_args()
    out = run_backtest(a.cache)
    for market, _ in MARKETS:
        data = out[market]
        print(f"== {MARKET_NAMES[market]} == 配对 {data['n']} 场")
        if data["n"]:
            print(f"  市场基线 Brier 基准: {data['market_brier']}")
            for m in data["per_match"]:
                print(f"    {m['match_key']}: 实际={m['actual']} Brier={m['brier']}")
        else:
            print("  无「基线+结果」配对(先 record_result)。")


if __name__ == "__main__":
    main()
```

> 删除旧 `run_backtest`/`main` 及 `calibration_buckets` 的 import 与校准段(本期回测聚焦逐盘口 Brier 基准;校准如需可后续单开)。

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_backtest_baseline.py -q`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add tools/backtest_baseline.py tests/test_backtest_baseline.py
git commit -m "feat(v2): 回测逐盘口市场基线 Brier 基准"
```

---

### Task 8: 更新 wc-forecaster-v2 agent(多盘口流程)

**Files:**
- Modify: `.claude/agents/wc-forecaster-v2.md`

**Interfaces:** 无代码;描述与新接口对齐(`baseline_market`/新 `build_v2_prediction` 签名)。

- [ ] **Step 1: 替换数据来源与工作流段**

把 `.claude/agents/wc-forecaster-v2.md` 正文中「## 数据来源」「## 每场工作流」「## 输出」三段替换为:

```markdown
## 数据来源(都在 .cache/odds_cache.db,经 Phase 1-4 代码)
逐盘口取基线(返回 {baseline, sources, confidence[, line]};无源→None,明说"无盘可锚"):
- 胜平负:`from backend.baseline import baseline_market, HAD_CFG; baseline_market('.cache/odds_cache.db','<key>', HAD_CFG)`
- 让球:`... HHAD_CFG`(竞彩单源 → confidence=soft,且含 `line`)
- 总进球:`... TTG_CFG`(竞彩单源 → soft;键是 "0".."7","7"=7+)
- 锚硬度:had 可三源硬锚;hhad/ttg 恒单源 🔴软锚 → 该盘口默认偏保守、默认别碰。

## 每场工作流
1. 逐盘口取基线。任一盘口 soft → 该盘口低靠谱度;整场靠谱度仍是一个综合标。
2. **默认照抄每盘口基线**。仅当有"能写下来的具体理由"才对某盘口提偏离,每条写 `{outcome, to, reason}`。had 的 outcome∈{h,d,a};ttg 的 outcome∈{"0".."7"}。无据不动。
3. **靠谱度(稳/中/乱)**:对阵清晰+三源一致+无混沌剧本→稳;默契平/大轮换/源分歧大/多盘口软锚→乱;之间→中。每场一个。
4. **剧本标签**:`load_library('reports/scenario_library.json')`,命中记名字 + 历史命中率;命中率低只作提示、不驱动偏离。
5. **装配 + 落库**:
   `build_v2_prediction('<key>', 靠谱度, 剧本, {"had":{"baseline":..,"deviations":[..]}, "hhad":{"baseline":..,"deviations":[..],"line":..}, "ttg":{"baseline":..,"deviations":[..]}})`
   → `record_v2_prediction('.cache/odds_cache.db','<key>', 预测)`。ttg 会自动派生大小球 `ou`(2.5)。
6. 触发了哪些剧本记在心(赛后由打分流程回填 `update_hit`)。

## 输出
每场:逐盘口 基线 →(少量)偏离及理由 → 整场靠谱度(稳/中/乱)→ 剧本标签 → 每盘口「可下/别碰」(软锚盘默认别碰;大小球看 `ou` 2.5 派生概率给结论)。结尾红线:概率预测非投注建议、market-anchored ≠ 能赢钱;+EV/最短腿/出线 → 指给 odds-value-analyst / football-match-predictor。
```

- [ ] **Step 2: 全套测试回归确认**

Run: `python3 -m pytest -q`
Expected: PASS(全绿,含 had 全部回归)。

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/wc-forecaster-v2.md
git commit -m "feat(v2): wc-forecaster-v2 agent 升级为多盘口流程"
```

---

## 收尾验证(全部任务完成后)

- [ ] **全套测试**:`python3 -m pytest -q` → 全绿(原 66 + Phase 4 新增,had 零回归)。
- [ ] **回测干跑**:`python3 tools/backtest_baseline.py`(无真实数据时各盘口打印「配对 0 场」即正常)。
- [ ] **跑分卡干跑**:`python3 tools/v2_report.py` → 写出 `reports/预测v2.md`,含 胜平负/让球/总进球 三节。
- [ ] **agent 干跑(需你在场)**:对一场真实比赛派 `wc-forecaster-v2`,确认逐盘口基线读取 + 落库形状 + 大小球派生。
- [ ] 完成后按 `superpowers:finishing-a-development-branch` 决定合并/PR(本分支 `feat/wc-v2-phase4-markets`)。

## Self-Review(已核)

- **Spec coverage**:§4 引擎泛化→Task1/2/3;§5.1 让球→Task2;§5.2 总进球/大小球→Task3;§6 结果派生+逐盘口打分→Task3(取分)+Task6;§7 agent+落库+报告→Task5/6/8;§8 红线→Global Constraints;§9 测试→各任务 TDD + 收尾;§11 受影响文件→逐一对应。
- **Placeholder scan**:无 TBD/TODO;每步含完整代码与命令。唯一人工确认项(line 符号)在 Task2 Step4 显式列出,非占位。
- **Type consistency**:`baseline_market` 返回含 `market`/可选 `line`,被 Task6/7 的 `_actual_for` 与 `bl.get("line")` 一致消费;`build_v2_prediction(match_key, reliability, scenarios, markets_in)` 新签名在 Task5 定义、Task8 agent 调用一致;`run_backtest`/`collect` 返回 `{market: {...}}` 结构在测试与 render 一致。
