# 预测 v2 · Phase 2:v2 预测 agent + 剧本库 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Phase 1 基线之上,建「会自我证伪的剧本库」+「v2 预测装配/落库」代码,并产出 v2 预测 agent 定义——agent 读基线、有据偏离、打靠谱度(稳/中/乱)、贴剧本标签。

**Architecture:** 纯函数 + 读写 `.cache/odds_cache.db` 新表 `v2_predictions` + JSON 文件 `reports/scenario_library.json`。消费 Phase 1 的 `backend.baseline.baseline_had`。代码做装配/落库/剧本记账;LLM 判断由 agent 定义承载。不新增三方库。

**Tech Stack:** Python 3(stdlib `sqlite3`/`json`)、pytest。

## Global Constraints

- 同 Phase 1:Python 3 仅 stdlib;概率单位 %;键 `"h"/"d"/"a"`;缓存默认 `.cache/odds_cache.db`;缺数据不编;测试放 `tests/`。
- 依赖 Phase 1 已合入:`backend/baseline.py` 的 `baseline_had(cache_path, match_key) -> {"match_key","baseline":{h,d,a},"sources","confidence"}`。
- 靠谱度取值固定三档:`"稳"/"中"/"乱"`。

---

### Task 1:剧本库 store(`backend/scenarios.py` + `reports/scenario_library.json`)

**Files:**
- Create: `backend/scenarios.py`
- Create: `reports/scenario_library.json`(初始剧本种子)
- Test: `tests/test_scenarios.py`

**Interfaces:**
- Produces:
  - `load_library(path) -> list` — 读 JSON 剧本列表;文件不存在返回内置 `DEFAULT_LIBRARY` 的拷贝。
  - `save_library(path, lib) -> None`。
  - `update_hit(path, name, hit: bool) -> dict` — 给指定剧本 `triggered += 1`,`hit` 为真则 `hits += 1`;返回更新后的该剧本 dict;名字不存在抛 `KeyError`。
  - `hit_rate(scenario: dict) -> float | None` — `hits/triggered`,triggered=0 返回 None。
  - 每个剧本字段:`{"name","trigger","effect","triggered","hits"}`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scenarios.py
import os, tempfile, json
from backend.scenarios import (load_library, save_library, update_hit, hit_rate, DEFAULT_LIBRARY)


def test_load_default_when_missing():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    lib = load_library(path)
    assert isinstance(lib, list) and len(lib) == len(DEFAULT_LIBRARY)
    assert {"name", "trigger", "effect", "triggered", "hits"} <= set(lib[0])


def test_save_then_load_roundtrip():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    save_library(path, [{"name": "X", "trigger": "t", "effect": "e", "triggered": 0, "hits": 0}])
    assert load_library(path)[0]["name"] == "X"


def test_update_hit_increments():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    save_library(path, [{"name": "默契平", "trigger": "t", "effect": "平↑", "triggered": 0, "hits": 0}])
    s = update_hit(path, "默契平", True)
    assert s["triggered"] == 1 and s["hits"] == 1
    s = update_hit(path, "默契平", False)
    assert s["triggered"] == 2 and s["hits"] == 1
    assert hit_rate(s) == 0.5


def test_update_hit_unknown_raises():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    save_library(path, [])
    try:
        update_hit(path, "不存在", True)
        assert False, "should raise"
    except KeyError:
        pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scenarios.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.scenarios'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scenarios.py
"""剧本库:命名套路 + 触发条件 + 历史命中(会自我证伪)。"""
import json
import os

DEFAULT_LIBRARY = [
    {"name": "大热门被摆大巴逼平", "trigger": "强队 vs 龟缩弱旅,弱旅无出线压力/只想守",
     "effect": "平/0-0↑、进球↓", "triggered": 0, "hits": 0},
    {"name": "死亡橡皮擦轮换", "trigger": "已出线队大轮换",
     "effect": "冷门/平↑", "triggered": 0, "hits": 0},
    {"name": "默契平", "trigger": "双方平即出线",
     "effect": "平↑、进球↓", "triggered": 0, "hits": 0},
    {"name": "生死战必有胜负", "trigger": "平=双亡,无平局动机",
     "effect": "平↓", "triggered": 0, "hits": 0},
    {"name": "强队刷净胜球", "trigger": "热门为出线/种子位需狂攻",
     "effect": "大球、让球↑", "triggered": 0, "hits": 0},
]


def load_library(path):
    if not os.path.exists(path):
        return [dict(s) for s in DEFAULT_LIBRARY]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_library(path, lib):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)


def update_hit(path, name, hit):
    lib = load_library(path)
    for s in lib:
        if s["name"] == name:
            s["triggered"] += 1
            if hit:
                s["hits"] += 1
            save_library(path, lib)
            return s
    raise KeyError(name)


def hit_rate(scenario):
    t = scenario.get("triggered", 0)
    return round(scenario["hits"] / t, 3) if t else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scenarios.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Seed the library file + commit**

```bash
python3 -c "from backend.scenarios import DEFAULT_LIBRARY, save_library; save_library('reports/scenario_library.json', DEFAULT_LIBRARY)"
git add backend/scenarios.py tests/test_scenarios.py reports/scenario_library.json
git commit -m "feat(v2): 剧本库 store(自我证伪命中记账)"
```

---

### Task 2:v2 预测装配 — 应用偏离(`backend/v2_predict.py`)

**Files:**
- Create: `backend/v2_predict.py`
- Test: `tests/test_v2_predict.py`

**Interfaces:**
- Produces:
  - `apply_deviations(baseline: dict, deviations: list) -> dict` — `baseline={h,d,a%}`;`deviations=[{"outcome":"a","to":64.0,"reason":"..."}]`;把指定 outcome 概率改成 `to`,其余按原比例吸收差额,**重新归一到 100**;无偏离原样返回(归一)。
  - `build_v2_prediction(baseline_sheet: dict, deviations: list, reliability: str, scenarios: list) -> dict` — 返回 `{"match_key","baseline":{h,d,a},"v2":{h,d,a},"deviations","reliability","scenarios"}`;`reliability ∈ {"稳","中","乱"}`。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_v2_predict.py
from backend.v2_predict import apply_deviations, build_v2_prediction


def test_apply_no_deviation_normalizes():
    out = apply_deviations({"h": 30.0, "d": 30.0, "a": 40.0}, [])
    assert abs(sum(out.values()) - 100.0) < 0.01


def test_apply_single_deviation_renormalizes():
    # 把客胜从 40 抬到 64,其余按比例缩,最后归一 100
    out = apply_deviations({"h": 30.0, "d": 30.0, "a": 40.0},
                           [{"outcome": "a", "to": 64.0, "reason": "韩国只需平+满血"}])
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert out["a"] > out["h"] and out["a"] > out["d"]
    assert abs(out["h"] - out["d"]) < 0.01  # h,d 原本相等,缩放后仍相等


def test_build_v2_prediction_shape():
    sheet = {"match_key": "M1", "baseline": {"h": 30.0, "d": 30.0, "a": 40.0}}
    out = build_v2_prediction(sheet, [{"outcome": "a", "to": 64.0, "reason": "r"}], "乱",
                              ["默契平"])
    assert out["match_key"] == "M1"
    assert out["reliability"] == "乱"
    assert out["scenarios"] == ["默契平"]
    assert abs(sum(out["v2"].values()) - 100.0) < 0.01
    assert out["baseline"] == {"h": 30.0, "d": 30.0, "a": 40.0}  # 基线原值留存
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_v2_predict.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.v2_predict'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/v2_predict.py
"""v2 预测装配:在基线上应用有据偏离,附靠谱度 + 剧本标签。概率 %。"""
_KEYS = ("h", "d", "a")


def apply_deviations(baseline: dict, deviations: list) -> dict:
    """把每条偏离的 outcome 设为 to;其余 outcome 按原比例吸收差额;最后归一到 100。"""
    cur = {k: float(baseline.get(k, 0.0)) for k in _KEYS}
    for dv in deviations:
        oc, to = dv["outcome"], float(dv["to"])
        others = [k for k in _KEYS if k != oc]
        rest_old = sum(cur[k] for k in others)
        cur[oc] = to
        rest_new = max(0.0, 100.0 - to)
        if rest_old > 0:
            for k in others:
                cur[k] = cur[k] / rest_old * rest_new
        else:
            for k in others:
                cur[k] = rest_new / len(others)
    tot = sum(cur.values())
    return {k: round(cur[k] / tot * 100, 1) for k in _KEYS} if tot else cur


def build_v2_prediction(baseline_sheet, deviations, reliability, scenarios):
    base = baseline_sheet["baseline"]
    return {
        "match_key": baseline_sheet.get("match_key"),
        "baseline": dict(base),
        "v2": apply_deviations(base, deviations or []),
        "deviations": deviations or [],
        "reliability": reliability,
        "scenarios": scenarios or [],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_v2_predict.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/v2_predict.py tests/test_v2_predict.py
git commit -m "feat(v2): v2预测装配(应用偏离+归一)"
```

---

### Task 3:v2 预测落库 / 读取(`backend/v2_predict.py` + `.cache/odds_cache.db`)

**Files:**
- Modify: `backend/v2_predict.py`(追加 `record_v2_prediction` / `get_v2_prediction`)
- Test: `tests/test_v2_predict.py`(追加)

**Interfaces:**
- Produces:
  - `record_v2_prediction(cache_path, match_key, prediction: dict) -> None` — 落表 `v2_predictions(match_key, ts, prediction_json)`,替换语义(同场覆盖)。
  - `get_v2_prediction(cache_path, match_key) -> dict | None`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_predict.py (追加)
import os, tempfile
from backend.v2_predict import record_v2_prediction, get_v2_prediction


def test_record_and_get_v2_prediction():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    pred = {"match_key": "M1", "baseline": {"h": 30, "d": 30, "a": 40},
            "v2": {"h": 20, "d": 20, "a": 60}, "deviations": [], "reliability": "中",
            "scenarios": []}
    record_v2_prediction(path, "M1", pred)
    got = get_v2_prediction(path, "M1")
    assert got["v2"]["a"] == 60 and got["reliability"] == "中"
    # 替换语义
    pred["reliability"] = "乱"
    record_v2_prediction(path, "M1", pred)
    assert get_v2_prediction(path, "M1")["reliability"] == "乱"
    assert get_v2_prediction(path, "NONE") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_v2_predict.py -v -k record`
Expected: FAIL — `ImportError: cannot import name 'record_v2_prediction'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/v2_predict.py (追加)
import json
import sqlite3

_V2_SCHEMA = """CREATE TABLE IF NOT EXISTS v2_predictions (
    match_key TEXT PRIMARY KEY, ts TEXT, prediction_json TEXT)"""


def record_v2_prediction(cache_path, match_key, prediction):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_V2_SCHEMA)
        conn.execute(
            """INSERT INTO v2_predictions(match_key, ts, prediction_json)
               VALUES (?, datetime('now'), ?)
               ON CONFLICT(match_key) DO UPDATE SET
                 ts=excluded.ts, prediction_json=excluded.prediction_json""",
            (match_key, json.dumps(prediction, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def get_v2_prediction(cache_path, match_key):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_V2_SCHEMA)
        r = conn.execute("SELECT prediction_json FROM v2_predictions WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return json.loads(r[0]) if r else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_v2_predict.py -v`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
git add backend/v2_predict.py tests/test_v2_predict.py
git commit -m "feat(v2): v2预测落库/读取(v2_predictions 表)"
```

---

### Task 4:v2 预测 agent 定义(`.claude/agents/wc-forecaster-v2.md`)

**Files:**
- Create: `.claude/agents/wc-forecaster-v2.md`

**说明:** 这是 LLM agent 的定义文件(prompt),非 TDD 代码。本任务=按下方内容创建文件并提交。它消费 Phase 1/2 的代码:`baseline_had`、`build_v2_prediction`、`record_v2_prediction`、`load_library/update_hit`。

- [ ] **Step 1: 创建 agent 定义文件**

写入 `.claude/agents/wc-forecaster-v2.md`,内容如下:

```markdown
---
name: wc-forecaster-v2
description: 世界杯 v2 概率预测脑(market-anchored)。读市场基线 → 默认照抄 → 仅在有据时偏离 → 打靠谱度(稳/中/乱)+ 贴剧本标签 → 落库。不预测精确比分当结论,不算价值/EV(那是 odds-value-analyst)。
tools: Bash, Read, Write, Edit
model: opus
---

你是世界杯 **v2 概率预测脑**,本地运行。口径:**以市场去水概率为基线,默认照抄,只在有"能写下来的具体理由"时才偏离。** 你不出精确比分当结论、不算 +EV(那是 odds-value-analyst)、不替用户下注。

## 数据来源(都在 .cache/odds_cache.db,经 Phase 1/2 代码)
- 基线:`python3 -c "from backend.baseline import baseline_had; print(baseline_had('.cache/odds_cache.db','<场次key>'))"` → {baseline:{h,d,a%}, sources, confidence}。
- 缺基线(无任何源)→ 明说"无盘可锚",不臆造。

## 每场工作流
1. 取基线。confidence.label=soft(单源)→ 该场整体标低靠谱度。
2. **默认照抄基线**。仅当有具体理由(伤停/动机/首发市场没 price-in)才提偏离;每条偏离写 `{outcome, to, reason}`。无据不动。
3. **靠谱度(稳/中/乱)**:对阵清晰+三源一致+无混沌剧本→稳;默契平/大轮换/源分歧大/单源软锚→乱;之间→中。
4. **剧本标签**:`load_library('reports/scenario_library.json')`,逐个看触发条件,命中就记下名字 + 它的历史命中率;命中率太低的剧本只作提示、不驱动偏离。
5. **装配 + 落库**:用 `build_v2_prediction(基线sheet, 偏离, 靠谱度, 剧本)` 装配,`record_v2_prediction('.cache/odds_cache.db', key, 预测)` 落库。
6. 触发了哪些剧本,记在心(赛后由打分流程回填 `update_hit`)。

## 纪律
- 偏离要稀、要有据。一个剧本只有历史命中够高才有资格驱动偏离,否则只是标签。
- 绝不编赔率/概率;缺数据降靠谱度并标注。
- 越界:不出精确比分结论、不算价值/EV/出线 → 指给 odds-value-analyst / football-match-predictor。

## 输出
每场:基线 → (少量)偏离及理由 → 靠谱度(稳/中/乱) → 剧本标签 → 每盘口可下/别碰(软锚盘默认别碰)。结尾红线:概率预测非投注建议、market-anchored ≠ 能赢钱。
```

- [ ] **Step 2: 校验 frontmatter 合法**

Run: `python3 -c "import re,sys; t=open('.claude/agents/wc-forecaster-v2.md',encoding='utf-8').read(); assert t.startswith('---') and 'name: wc-forecaster-v2' in t and t.count('---')>=2; print('frontmatter OK')"`
Expected: 打印 `frontmatter OK`

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/wc-forecaster-v2.md
git commit -m "feat(v2): v2预测agent定义(market-anchored+靠谱度+剧本)"
```

---

## Phase 2 完成标准
- `pytest tests/test_scenarios.py tests/test_v2_predict.py -v` 全绿。
- 剧本库 JSON 已 seed;v2 预测可装配(偏离归一)+ 落库/读取;agent 定义文件就位、frontmatter 合法。

## 不在 Phase 2(Phase 3)
- v1 并行三方记录、三方 Brier 跑分卡、偏离审计、`reports/预测v2.md` 渲染。

## Self-Review(已自查)
- spec 覆盖:对应 spec §5(v2 agent)、§6 部分(预测装配)、§6 剧本库(§本计划 Task1)。
- 占位符:无 TBD;每步真实代码/命令。
- 类型一致:`build_v2_prediction` 返回结构(baseline/v2/deviations/reliability/scenarios)Task2 定义、Task3 落库消费、Task4 agent 调用一致;`baseline_had` 返回结构沿用 Phase 1。
