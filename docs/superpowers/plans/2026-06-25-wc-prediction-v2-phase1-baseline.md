# 预测 v2 · Phase 1:胜平负基线引擎 + Brier 打分 + 回测立基准 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个确定性的「胜平负」市场基线引擎(多源去水+加权融合+置信分级)、一套 Brier/校准打分函数、一个历史回测脚本,跑出「市场 Brier 基准线」——这是 v2 以后必须打败的靶子。

**Architecture:** 纯函数 + 读 `.cache/odds_cache.db`(odds_watch 缓存,source 维度存 payload)。代码做两端(基线、打分),不碰 LLM、不碰现有 dashboard `db.py`。复用 `backend/devig.py`。新增 `backend/baseline.py`、`backend/scoring.py`、`tools/backtest_baseline.py`,结果落 `.cache/odds_cache.db` 新表 `match_results`。

**Tech Stack:** Python 3(stdlib `sqlite3`/`json`/`argparse`)、pytest。无新增第三方依赖。

## Global Constraints

- Python 3,仅 stdlib + 现有依赖(不新增三方库)。
- 概率单位**统一用百分数 %**(如 62.4),与 `backend/devig.py` 一致(`devig` 归一化到 sum=100)。
- 读缓存路径默认 `.cache/odds_cache.db`,可被参数覆盖(照 odds_watch 的 `WC_ODDS_CACHE` 习惯)。
- 缺数据**绝不编**:源缺就降置信并标注;函数对缺失返回 `None` 或跳过,不臆造概率。
- 测试用 pytest,放 `tests/`,与现有 `tests/test_db.py` 等同目录同风格。
- 胜平负三选项键统一用 `"h"/"d"/"a"`(主胜/平/客胜),与 `models.ZucaiMatch.had` 一致。

---

### Task 1:Brier + 校准打分函数(`backend/scoring.py`)

纯函数,无数据依赖,先做。

**Files:**
- Create: `backend/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- Produces:
  - `brier_multi(probs: dict, actual: str) -> float` — 多分类 Brier,`probs` 形如 `{"h":62.4,"d":23.4,"a":14.2}`(%,自动归一),`actual ∈ {"h","d","a"}`,返回 `Σ(p_k − y_k)²`(p_k 为分数,y_k 命中=1),范围 [0,2],越低越准。
  - `calibration_buckets(preds: list, n: int = 5) -> list` — `preds` 为 `[(prob_pct, occurred_0_or_1), ...]`,按概率分 n 桶,返回 `[{"lo","hi","mean_pred","freq","count"}, ...]`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scoring.py
from backend.scoring import brier_multi, calibration_buckets


def test_brier_multi_perfect_confident():
    # 说主胜 100%,真主胜 → 0 罚分
    assert brier_multi({"h": 100, "d": 0, "a": 0}, "h") == 0.0


def test_brier_multi_known_value():
    # {h:60,d:30,a:10}, 真主胜 → (0.6-1)^2+(0.3)^2+(0.1)^2 = 0.16+0.09+0.01
    assert brier_multi({"h": 60, "d": 30, "a": 10}, "h") == 0.26


def test_brier_multi_normalizes_percent():
    # 输入和不为 100 也应先归一
    assert brier_multi({"h": 120, "d": 60, "a": 20}, "h") == 0.26  # 比例同上


def test_calibration_buckets_basic():
    # 两条预测都在高桶:预测 80%,一中一不中 → freq=0.5
    preds = [(80.0, 1), (80.0, 0)]
    out = calibration_buckets(preds, n=5)
    hi_bucket = [b for b in out if b["count"] > 0][0]
    assert hi_bucket["count"] == 2
    assert hi_bucket["mean_pred"] == 80.0
    assert hi_bucket["freq"] == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.scoring'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scoring.py
"""预测准度打分:多分类 Brier + 校准分桶。概率单位 %。"""


def brier_multi(probs: dict, actual: str) -> float:
    """多分类 Brier = Σ(p_k − y_k)²(p_k 分数,actual 命中 y=1)。越低越准,[0,2]。"""
    s = sum(probs.values())
    if s <= 0:
        return 0.0
    frac = {k: v / s for k, v in probs.items()}
    return round(sum((frac.get(k, 0.0) - (1.0 if k == actual else 0.0)) ** 2
                     for k in frac), 4)


def calibration_buckets(preds: list, n: int = 5) -> list:
    """preds: [(prob_pct, occurred_0/1)]。按概率分 n 桶,比较平均预测 vs 实际频率。"""
    width = 100.0 / n
    buckets = []
    for i in range(n):
        lo, hi = i * width, (i + 1) * width
        sel = [(p, y) for (p, y) in preds if (lo <= p < hi or (i == n - 1 and p == 100.0))]
        if sel:
            mean_pred = round(sum(p for p, _ in sel) / len(sel), 1)
            freq = round(sum(y for _, y in sel) / len(sel), 3)
        else:
            mean_pred, freq = None, None
        buckets.append({"lo": lo, "hi": hi, "mean_pred": mean_pred,
                        "freq": freq, "count": len(sel)})
    return buckets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scoring.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scoring.py tests/test_scoring.py
git commit -m "feat(v2): Brier + 校准打分函数"
```

---

### Task 2:基线纯函数 — 去水/融合/置信(`backend/baseline.py`)

竞彩欧赔去水、多源加权融合、置信分级 + 源分歧。纯函数,无 IO。

**Files:**
- Create: `backend/baseline.py`
- Test: `tests/test_baseline.py`

**Interfaces:**
- Consumes: `backend.devig.devig`(已存在:`devig(dict)->dict`,乘法归一到 100)。
- Produces:
  - `DEFAULT_WEIGHTS = {"zucai": 0.20, "poly": 0.45, "consensus": 0.35}`
  - `zucai_had_devig(had: dict) -> dict` — 竞彩 had 欧赔 `{"h","d","a"}` → 去水概率 % `{"h","d","a"}`。
  - `blend_had(sources: dict, weights: dict = DEFAULT_WEIGHTS) -> dict` — `sources` 形如 `{"zucai":{h,d,a%}, "poly":{...}, "consensus":{...}}`,只用在场的源、权重重新归一,输出融合 % 并重新归一到 100。
  - `confidence(sources: dict) -> dict` — 返回 `{"n_sources","label","max_spread"}`,label:3源=`hard`/2=`medium`/1=`soft`/0=`none`,max_spread=各选项跨源最大极差(pct)。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_baseline.py
from backend.baseline import zucai_had_devig, blend_had, confidence, DEFAULT_WEIGHTS


def test_zucai_had_devig_sums_100():
    out = zucai_had_devig({"h": 1.44, "d": 3.87, "a": 6.00})
    assert abs(sum(out.values()) - 100.0) < 0.01
    # 主胜赔率最低 → 概率最高
    assert out["h"] > out["d"] > out["a"]


def test_blend_had_weighted_and_renormalized():
    sources = {
        "zucai": {"h": 60.0, "d": 25.0, "a": 15.0},
        "poly": {"h": 62.0, "d": 24.0, "a": 14.0},
        "consensus": {"h": 58.0, "d": 26.0, "a": 16.0},
    }
    out = blend_had(sources)
    assert abs(sum(out.values()) - 100.0) < 0.01
    # poly 权重最高,结果应偏向 poly 的 62
    assert 59.0 < out["h"] < 62.0


def test_blend_had_missing_source_renormalizes_weights():
    # 只有 zucai + poly,consensus 缺 → 权重在两者间重新归一
    sources = {"zucai": {"h": 60.0, "d": 25.0, "a": 15.0},
               "poly": {"h": 62.0, "d": 24.0, "a": 14.0}}
    out = blend_had(sources)
    assert abs(sum(out.values()) - 100.0) < 0.01


def test_confidence_levels_and_spread():
    three = {"zucai": {"h": 60, "d": 25, "a": 15}, "poly": {"h": 62, "d": 24, "a": 14},
             "consensus": {"h": 50, "d": 30, "a": 20}}
    c = confidence(three)
    assert c["n_sources"] == 3 and c["label"] == "hard"
    assert c["max_spread"] == 12.0  # 主胜 62-50
    one = {"zucai": {"h": 60, "d": 25, "a": 15}}
    assert confidence(one)["label"] == "soft"
    assert confidence({})["label"] == "none"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.baseline'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/baseline.py
"""胜平负市场基线:多源去水 + 加权融合 + 置信分级。概率单位 %。"""
from .devig import devig

DEFAULT_WEIGHTS = {"zucai": 0.20, "poly": 0.45, "consensus": 0.35}
_KEYS = ("h", "d", "a")


def zucai_had_devig(had: dict) -> dict:
    """竞彩 had 欧赔 {h,d,a} → 去水概率 %。隐含=1/赔率,再乘法归一到 100。"""
    implied = {k: 1.0 / float(had[k]) for k in _KEYS if had.get(k)}
    return {k: round(v, 1) for k, v in devig(implied).items()}


def blend_had(sources: dict, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """多源去水概率加权融合;只用在场源、权重重新归一;输出重新归一到 100。"""
    present = {s: weights[s] for s in sources if s in weights and sources[s]}
    wsum = sum(present.values())
    if wsum <= 0:
        return {}
    raw = {k: sum(sources[s][k] * present[s] for s in present) / wsum for k in _KEYS}
    tot = sum(raw.values())
    return {k: round(v / tot * 100, 1) for k, v in raw.items()}


def confidence(sources: dict) -> dict:
    """覆盖几个源 + 跨源最大极差。3源=hard/2=medium/1=soft/0=none。"""
    n = len(sources)
    label = {3: "hard", 2: "medium", 1: "soft"}.get(n, "none")
    spread = 0.0
    for k in _KEYS:
        vals = [s[k] for s in sources.values() if k in s]
        if len(vals) >= 2:
            spread = max(spread, max(vals) - min(vals))
    return {"n_sources": n, "label": label, "max_spread": round(spread, 1)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_baseline.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/baseline.py tests/test_baseline.py
git commit -m "feat(v2): 胜平负基线纯函数(去水/融合/置信)"
```

---

### Task 3:从缓存装配每场胜平负基线(`backend/baseline.py`)

读 `.cache/odds_cache.db`(odds_watch schema),把一场的三源 had 装配成基线表。

**Files:**
- Modify: `backend/baseline.py`(追加 `baseline_had`)
- Test: `tests/test_baseline.py`(追加用例,建临时缓存)

**Interfaces:**
- Consumes: 缓存表 `odds_cache(ts, source, match_key, label, ko, payload_json)`;payload 约定:`zucai={"had":{h,d,a欧赔},...}`、`poly={"poly_devig":{h,d,a%},...}`、`consensus={"had":{h,d,a欧赔},...}`。
- Produces: `baseline_had(cache_path: str, match_key: str, weights=DEFAULT_WEIGHTS) -> dict | None` — 返回 `{"match_key","baseline":{h,d,a%},"sources":{src:{h,d,a%}},"confidence":{...}}`;无任何源返回 `None`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_baseline.py (追加)
import sqlite3, json, os, tempfile
from backend.baseline import baseline_had


def _seed_cache(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    rows = [
        ("2026-06-25T09:00:00+08:00", "zucai", "周三053", "南非 vs 韩国", "ko",
         json.dumps({"had": {"h": 6.00, "d": 3.87, "a": 1.44}})),
        ("2026-06-25T09:00:00+08:00", "poly", "周三053", "南非 vs 韩国", "ko",
         json.dumps({"poly_devig": {"h": 15.4, "d": 23.4, "a": 61.2}})),
        ("2026-06-25T09:00:00+08:00", "consensus", "周三053", "南非 vs 韩国", "ko",
         json.dumps({"had": {"h": 6.5, "d": 4.0, "a": 1.55}})),
    ]
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def test_baseline_had_assembles_three_sources():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    out = baseline_had(path, "周三053")
    assert out is not None
    assert set(out["sources"]) == {"zucai", "poly", "consensus"}
    assert out["confidence"]["label"] == "hard"
    assert abs(sum(out["baseline"].values()) - 100.0) < 0.01
    # 三源都看好客胜(韩国) → 融合客胜应最高
    assert out["baseline"]["a"] > out["baseline"]["h"]


def test_baseline_had_missing_match_returns_none():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    assert baseline_had(path, "周三999") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_baseline.py -v -k baseline_had`
Expected: FAIL — `ImportError: cannot import name 'baseline_had'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/baseline.py (追加)
import json
import sqlite3


def _latest_payload(conn, source, match_key):
    r = conn.execute(
        "SELECT payload_json FROM odds_cache WHERE source=? AND match_key=? "
        "ORDER BY ts DESC LIMIT 1", (source, match_key)).fetchone()
    return json.loads(r[0]) if r else None


def baseline_had(cache_path: str, match_key: str, weights: dict = DEFAULT_WEIGHTS):
    """从 odds_cache.db 装配一场胜平负基线表。无任何源 → None。"""
    conn = sqlite3.connect(cache_path)
    try:
        sources = {}
        z = _latest_payload(conn, "zucai", match_key)
        if z and z.get("had"):
            sources["zucai"] = zucai_had_devig(z["had"])
        p = _latest_payload(conn, "poly", match_key)
        if p and p.get("poly_devig"):
            sources["poly"] = {k: round(float(p["poly_devig"][k]), 1) for k in _KEYS
                               if p["poly_devig"].get(k) is not None}
        c = _latest_payload(conn, "consensus", match_key)
        if c and c.get("had"):
            sources["consensus"] = zucai_had_devig(c["had"])  # 共识 had 也是欧赔,同路去水
    finally:
        conn.close()
    if not sources:
        return None
    return {"match_key": match_key, "baseline": blend_had(sources, weights),
            "sources": sources, "confidence": confidence(sources)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_baseline.py -v`
Expected: PASS(全部,含新增 2 条)

- [ ] **Step 5: Commit**

```bash
git add backend/baseline.py tests/test_baseline.py
git commit -m "feat(v2): 从缓存装配每场胜平负基线"
```

---

### Task 4:实际结果表 + 录入(`backend/baseline.py` + `.cache/odds_cache.db`)

回测要 (基线, 实际胜平负) 配对。在同一缓存库加 `match_results` 表 + 录入/读取函数。

**Files:**
- Modify: `backend/baseline.py`(追加 `record_result` / `get_result`)
- Test: `tests/test_baseline.py`(追加)

**Interfaces:**
- Produces:
  - `record_result(cache_path: str, match_key: str, home_goals: int, away_goals: int) -> str` — 落库并返回胜平负结果键(`"h"/"d"/"a"`,主进>客=h、相等=d、否则 a)。建表 `match_results(match_key PK, home_goals, away_goals, outcome, ts)`,替换语义(同场重录覆盖)。
  - `get_result(cache_path: str, match_key: str) -> str | None` — 返回胜平负结果键,无则 None。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_baseline.py (追加)
from backend.baseline import record_result, get_result


def test_record_and_get_result_outcome_key():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)  # 表 odds_cache 已建;match_results 由 record_result 自建
    assert record_result(path, "周三053", 0, 1) == "a"   # 客胜
    assert get_result(path, "周三053") == "a"
    assert record_result(path, "X", 2, 2) == "d"          # 平
    assert record_result(path, "Y", 3, 0) == "h"          # 主胜
    # 替换语义:重录覆盖
    assert record_result(path, "周三053", 2, 0) == "h"
    assert get_result(path, "周三053") == "h"


def test_get_result_missing_returns_none():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    assert get_result(path, "周三053") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_baseline.py -v -k result`
Expected: FAIL — `ImportError: cannot import name 'record_result'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/baseline.py (追加)
_RESULTS_SCHEMA = """CREATE TABLE IF NOT EXISTS match_results (
    match_key TEXT PRIMARY KEY, home_goals INTEGER, away_goals INTEGER,
    outcome TEXT, ts TEXT)"""


def _outcome_key(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "h"
    if home_goals == away_goals:
        return "d"
    return "a"


def record_result(cache_path: str, match_key: str, home_goals: int, away_goals: int) -> str:
    outcome = _outcome_key(home_goals, away_goals)
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)
        conn.execute(
            """INSERT INTO match_results(match_key, home_goals, away_goals, outcome, ts)
               VALUES (?,?,?,?,datetime('now'))
               ON CONFLICT(match_key) DO UPDATE SET
                 home_goals=excluded.home_goals, away_goals=excluded.away_goals,
                 outcome=excluded.outcome, ts=excluded.ts""",
            (match_key, home_goals, away_goals, outcome))
        conn.commit()
    finally:
        conn.close()
    return outcome


def get_result(cache_path: str, match_key: str):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)
        r = conn.execute("SELECT outcome FROM match_results WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return r[0] if r else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_baseline.py -v`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
git add backend/baseline.py tests/test_baseline.py
git commit -m "feat(v2): 实际胜平负结果录入/读取(match_results 表)"
```

---

### Task 5:回测脚本 — 跑出市场 Brier 基准(`tools/backtest_baseline.py`)

把所有「有基线 + 有结果」的场配对,算市场基线的 Brier + 校准,打印基准报告。

**Files:**
- Create: `tools/backtest_baseline.py`
- Test: `tests/test_backtest_baseline.py`

**Interfaces:**
- Consumes: `backend.baseline.baseline_had / get_result`、`backend.scoring.brier_multi / calibration_buckets`。
- Produces:
  - `run_backtest(cache_path: str) -> dict` — 遍历 `match_results` 里所有有结果的场,与其基线配对;返回 `{"n","market_brier","per_match":[{match_key,baseline,actual,brier}],"calibration":[...]}`;无配对返回 `{"n":0,...}`。
  - CLI:`python3 tools/backtest_baseline.py [--cache PATH]` 打印报告。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_baseline.py
import sqlite3, json, os, tempfile
from tools.backtest_baseline import run_backtest


def _seed(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    # 一场:三源都看好客胜,实际客胜 → Brier 应较低
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", [
        ("t", "zucai", "M1", "A vs B", "k", json.dumps({"had": {"h": 6.0, "d": 3.9, "a": 1.44}})),
        ("t", "poly", "M1", "A vs B", "k", json.dumps({"poly_devig": {"h": 15.4, "d": 23.4, "a": 61.2}})),
        ("t", "consensus", "M1", "A vs B", "k", json.dumps({"had": {"h": 6.5, "d": 4.0, "a": 1.55}})),
    ])
    conn.commit(); conn.close()


def test_run_backtest_pairs_and_scores():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)
    from backend.baseline import record_result
    record_result(path, "M1", 0, 1)  # 客胜
    out = run_backtest(path)
    assert out["n"] == 1
    assert 0.0 <= out["market_brier"] <= 2.0
    # 基线重押客胜且客胜真发生 → Brier 明显小于 0.5(乱猜量级)
    assert out["market_brier"] < 0.5


def test_run_backtest_no_pairs():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)  # 有基线但无结果
    assert run_backtest(path)["n"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_baseline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.backtest_baseline'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/backtest_baseline.py
#!/usr/bin/env python3
"""回测:对所有「有基线+有结果」的场,算市场基线的 Brier + 校准,立基准线。

用法: python3 tools/backtest_baseline.py [--cache .cache/odds_cache.db]
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from backend.baseline import baseline_had, get_result  # noqa: E402
from backend.scoring import brier_multi, calibration_buckets  # noqa: E402

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))


def _result_keys(cache_path):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS match_results (
            match_key TEXT PRIMARY KEY, home_goals INTEGER, away_goals INTEGER,
            outcome TEXT, ts TEXT)""")
        rows = conn.execute("SELECT match_key FROM match_results").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def run_backtest(cache_path: str) -> dict:
    per_match, briers, cal_preds = [], [], []
    for mk in _result_keys(cache_path):
        bl = baseline_had(cache_path, mk)
        actual = get_result(cache_path, mk)
        if not bl or not actual:
            continue
        b = brier_multi(bl["baseline"], actual)
        briers.append(b)
        per_match.append({"match_key": mk, "baseline": bl["baseline"],
                          "actual": actual, "brier": b})
        # 校准:记录"基线给实际结果的概率" vs 是否发生(发生=1)
        cal_preds.append((bl["baseline"].get(actual, 0.0), 1))
    n = len(briers)
    return {
        "n": n,
        "market_brier": round(sum(briers) / n, 4) if n else None,
        "per_match": per_match,
        "calibration": calibration_buckets(cal_preds) if cal_preds else [],
    }


def main():
    ap = argparse.ArgumentParser(description="回测市场基线 Brier 基准")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    a = ap.parse_args()
    out = run_backtest(a.cache)
    print(f"配对场数: {out['n']}")
    if out["n"]:
        print(f"市场基线 Brier 基准: {out['market_brier']}  (越低越准, 0=完美, ~0.66=乱猜三选一)")
        for m in out["per_match"]:
            print(f"  {m['match_key']}: 基线={m['baseline']} 实际={m['actual']} Brier={m['brier']}")
    else:
        print("无「基线+结果」配对(先 record_result 录入实际比分)。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_baseline.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add tools/backtest_baseline.py tests/test_backtest_baseline.py
git commit -m "feat(v2): 回测脚本跑出市场基线 Brier 基准"
```

---

## Phase 1 完成标准

- 全部 5 任务测试通过:`pytest tests/test_scoring.py tests/test_baseline.py tests/test_backtest_baseline.py -v`。
- 能对任一已缓存场跑出胜平负基线表(三源去水+融合+置信)。
- 能录入实际比分、跑 `python3 tools/backtest_baseline.py` 得到「市场 Brier 基准」。
- 这条基准线 = Phase 2/3 里 v2 必须打败的靶子。

## 不在 Phase 1(后续 plan)

- 让球 / 大小球 / BTTS / 比分分布 / 半全场 的基线(让球/大小球可复用 value.py 的 cover/ou 去水;比分/半全场需新写竞彩 parser)。
- v2 预测 agent + 偏离 + 靠谱度 + 剧本库(Phase 2)。
- v1 并行三方记录 + 报告/跑分卡 + 偏离审计(Phase 3)。
- 源分歧阈值标记的报告呈现(数据已在 `confidence.max_spread`,呈现放 Phase 3)。

## Self-Review(已自查)

- **spec 覆盖**:Phase 1 对应 spec §4(基线引擎,本期限胜平负)、§8(打分引擎的 Brier/校准数学)、§9.3(回测立基准)。其余 spec 组件明确列入"不在 Phase 1"。
- **占位符**:无 TBD/TODO;每步含真实可运行代码与命令。
- **类型一致**:`{"h","d","a"}` 键、% 单位、`baseline_had` 返回结构在 Task 3 定义、Task 5 消费一致;`brier_multi(probs,actual)` 签名 Task 1 定义、Task 5 调用一致;`record_result/get_result` Task 4 定义、Task 5 消费一致。
