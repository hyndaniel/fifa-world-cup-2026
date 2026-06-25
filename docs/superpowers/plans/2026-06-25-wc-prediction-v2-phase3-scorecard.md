# 预测 v2 · Phase 3:v1 并行记录 + 三方跑分卡 + 报告 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 v1 预测并行记录下来,赛后算 v1/v2/市场三方 Brier + 偏离审计(v2 的偏离到底拉低还是拉高了 Brier),渲染成 `reports/预测v2.md` 跑分卡。这是整套设计的「审判台」。

**Architecture:** 纯函数 + 读写 `.cache/odds_cache.db`(新表 `v1_predictions`,复用 Phase 1/2 的 `match_results`、`v2_predictions`、`baseline_had`)。复用 `backend.scoring.brier_multi`。新增 `backend/v1_log.py`、`backend/scorecard.py`、`tools/v2_report.py`。不新增三方库。

**Tech Stack:** Python 3(stdlib)、pytest。

## Global Constraints

- 同前:Python 3 仅 stdlib;概率 %;键 `"h"/"d"/"a"`;缓存默认 `.cache/odds_cache.db`;缺数据不编;测试放 `tests/`。
- 依赖已合入:`backend/scoring.py:brier_multi(probs,actual)`、`backend/baseline.py:baseline_had/get_result`、`backend/v2_predict.py:get_v2_prediction`。
- v1 预测以 `{h,d,a}` 概率 % 存(由 v1 自评 split 转换),才与 v2/市场可比 Brier。

---

### Task 1:v1 预测并行记录(`backend/v1_log.py`)

**Files:**
- Create: `backend/v1_log.py`
- Test: `tests/test_v1_log.py`

**Interfaces:**
- Produces:
  - `record_v1(cache_path, match_key, probs: dict, score_pred: str = "") -> None` — 落表 `v1_predictions(match_key, ts, probs_json, score_pred)`,替换语义;`probs={h,d,a%}`。
  - `get_v1(cache_path, match_key) -> dict | None` — 返回 `{"probs":{h,d,a},"score_pred":...}`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v1_log.py
import os, tempfile
from backend.v1_log import record_v1, get_v1


def test_record_and_get_v1():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    record_v1(path, "M1", {"h": 25, "d": 26, "a": 49}, "0-1")
    got = get_v1(path, "M1")
    assert got["probs"]["a"] == 49 and got["score_pred"] == "0-1"
    record_v1(path, "M1", {"h": 30, "d": 30, "a": 40}, "1-1")  # 替换
    assert get_v1(path, "M1")["score_pred"] == "1-1"
    assert get_v1(path, "NONE") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_v1_log.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.v1_log'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/v1_log.py
"""v1 预测并行记录(冻结对照)。概率 %。"""
import json
import sqlite3

_SCHEMA = """CREATE TABLE IF NOT EXISTS v1_predictions (
    match_key TEXT PRIMARY KEY, ts TEXT, probs_json TEXT, score_pred TEXT)"""


def record_v1(cache_path, match_key, probs, score_pred=""):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_SCHEMA)
        conn.execute(
            """INSERT INTO v1_predictions(match_key, ts, probs_json, score_pred)
               VALUES (?, datetime('now'), ?, ?)
               ON CONFLICT(match_key) DO UPDATE SET
                 ts=excluded.ts, probs_json=excluded.probs_json, score_pred=excluded.score_pred""",
            (match_key, json.dumps(probs, ensure_ascii=False), score_pred))
        conn.commit()
    finally:
        conn.close()


def get_v1(cache_path, match_key):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_SCHEMA)
        r = conn.execute("SELECT probs_json, score_pred FROM v1_predictions WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return {"probs": json.loads(r[0]), "score_pred": r[1]} if r else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_v1_log.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/v1_log.py tests/test_v1_log.py
git commit -m "feat(v2): v1预测并行记录(v1_predictions 表)"
```

---

### Task 2:三方 Brier + 聚合(`backend/scorecard.py`)

**Files:**
- Create: `backend/scorecard.py`
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Consumes: `backend.scoring.brier_multi`。
- Produces:
  - `three_way(v1: dict, v2: dict, market: dict, actual: str) -> dict` — 三方概率各算 Brier,返回 `{"v1","v2","market"}`(任一为 None 则该项 None)。
  - `aggregate(rows: list) -> dict` — `rows=[{"v1","v2","market"}...]`,返回各方均值 `{"v1_mean","v2_mean","market_mean","n"}`(忽略 None)。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scorecard.py
from backend.scorecard import three_way, aggregate


def test_three_way_briers():
    out = three_way({"h": 25, "d": 26, "a": 49}, {"h": 15, "d": 24, "a": 61},
                    {"h": 17, "d": 23, "a": 60}, "a")
    # 都重押客胜且客胜发生 → 都较小;v2(61) 最自信应最低
    assert out["v2"] < out["market"] <= out["v1"] or out["v2"] <= out["market"]
    assert all(0 <= out[k] <= 2 for k in ("v1", "v2", "market"))


def test_three_way_none_passthrough():
    out = three_way(None, {"h": 15, "d": 24, "a": 61}, {"h": 17, "d": 23, "a": 60}, "a")
    assert out["v1"] is None and out["v2"] is not None


def test_aggregate_means_ignore_none():
    rows = [{"v1": 0.4, "v2": 0.2, "market": 0.3},
            {"v1": None, "v2": 0.4, "market": 0.5}]
    agg = aggregate(rows)
    assert agg["n"] == 2
    assert agg["v2_mean"] == 0.3      # (0.2+0.4)/2
    assert agg["v1_mean"] == 0.4      # 只 1 个非 None
    assert agg["market_mean"] == 0.4  # (0.3+0.5)/2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scorecard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.scorecard'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scorecard.py
"""三方 Brier 跑分卡 + 偏离审计。"""
from .scoring import brier_multi


def three_way(v1, v2, market, actual):
    def b(p):
        return brier_multi(p, actual) if p else None
    return {"v1": b(v1), "v2": b(v2), "market": b(market)}


def aggregate(rows):
    out = {"n": len(rows)}
    for key in ("v1", "v2", "market"):
        vals = [r[key] for r in rows if r.get(key) is not None]
        out[f"{key}_mean"] = round(sum(vals) / len(vals), 4) if vals else None
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scorecard.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/scorecard.py tests/test_scorecard.py
git commit -m "feat(v2): 三方 Brier + 聚合"
```

---

### Task 3:偏离审计(`backend/scorecard.py`)

**Files:**
- Modify: `backend/scorecard.py`(追加 `deviation_audit`)
- Test: `tests/test_scorecard.py`(追加)

**Interfaces:**
- Produces:
  - `deviation_audit(rows: list) -> dict` — `rows=[{"deviated":bool,"v2":brier,"market":brier}...]`;只看 `deviated=True` 的场,返回 `{"n_deviated","v2_mean","market_mean","delta"}`,`delta=v2_mean-market_mean`(<0=偏离平均拉低 Brier=有用;>0=帮倒忙)。无偏离场返回 `{"n_deviated":0,...None}`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scorecard.py (追加)
from backend.scorecard import deviation_audit


def test_deviation_audit_helpful():
    rows = [{"deviated": True, "v2": 0.20, "market": 0.30},   # 偏离更准
            {"deviated": True, "v2": 0.25, "market": 0.28},
            {"deviated": False, "v2": 0.40, "market": 0.40}]  # 不计入
    a = deviation_audit(rows)
    assert a["n_deviated"] == 2
    assert a["v2_mean"] == 0.225 and a["market_mean"] == 0.29
    assert a["delta"] < 0  # 偏离平均拉低 Brier = 有用


def test_deviation_audit_none_when_no_deviation():
    a = deviation_audit([{"deviated": False, "v2": 0.4, "market": 0.4}])
    assert a["n_deviated"] == 0 and a["delta"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_scorecard.py -v -k deviation`
Expected: FAIL — `ImportError: cannot import name 'deviation_audit'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scorecard.py (追加)
def deviation_audit(rows):
    dev = [r for r in rows if r.get("deviated")]
    if not dev:
        return {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
    v2m = round(sum(r["v2"] for r in dev) / len(dev), 4)
    mkm = round(sum(r["market"] for r in dev) / len(dev), 4)
    return {"n_deviated": len(dev), "v2_mean": v2m, "market_mean": mkm,
            "delta": round(v2m - mkm, 4)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_scorecard.py -v`
Expected: PASS(全部)

- [ ] **Step 5: Commit**

```bash
git add backend/scorecard.py tests/test_scorecard.py
git commit -m "feat(v2): 偏离审计(v2偏离是否拉低Brier)"
```

---

### Task 4:跑分卡报告渲染(`tools/v2_report.py`)

**Files:**
- Create: `tools/v2_report.py`
- Test: `tests/test_v2_report.py`

**Interfaces:**
- Consumes: `backend.scorecard.aggregate / deviation_audit`。
- Produces:
  - `render(agg: dict, audit: dict, per_match: list) -> str` — 返回 markdown 文本:置顶三方均值 Brier 表 + 偏离审计一行结论 + 每场一行(key/靠谱度/三方Brier)。
  - CLI:`python3 tools/v2_report.py [--cache PATH] [--out reports/预测v2.md]` 把渲染结果写文件。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_v2_report.py
from tools.v2_report import render


def test_render_contains_scorecard_and_verdict():
    agg = {"n": 3, "v1_mean": 0.42, "v2_mean": 0.30, "market_mean": 0.33}
    audit = {"n_deviated": 2, "v2_mean": 0.25, "market_mean": 0.31, "delta": -0.06}
    per_match = [{"match_key": "M1", "reliability": "乱",
                  "brier": {"v1": 0.5, "v2": 0.3, "market": 0.35}}]
    md = render(agg, audit, per_match)
    assert "跑分卡" in md
    assert "0.30" in md and "0.33" in md          # v2/market 均值出现
    assert "M1" in md and "乱" in md
    # 偏离有用(delta<0)应给出正向结论文案
    assert "拉低" in md or "有用" in md or "-0.06" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_v2_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.v2_report'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/v2_report.py
#!/usr/bin/env python3
"""渲染 v2 跑分卡 → reports/预测v2.md。

用法: python3 tools/v2_report.py [--cache .cache/odds_cache.db] [--out reports/预测v2.md]
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from backend.scoring import brier_multi  # noqa: E402
from backend.scorecard import aggregate, deviation_audit, three_way  # noqa: E402
from backend.baseline import baseline_had, get_result  # noqa: E402
from backend.v2_predict import get_v2_prediction  # noqa: E402
from backend.v1_log import get_v1  # noqa: E402

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))
DEFAULT_OUT = os.path.join(REPO, "reports", "预测v2.md")


def render(agg, audit, per_match):
    lines = ["# 预测 v2 跑分卡", "",
             f"配对场数: {agg['n']}", "",
             "| 方 | 平均 Brier(越低越准) |", "|---|---|",
             f"| v1(老方法) | {agg.get('v1_mean')} |",
             f"| **v2** | **{agg.get('v2_mean')}** |",
             f"| 市场基线 | {agg.get('market_mean')} |", ""]
    if audit.get("delta") is not None:
        verdict = "偏离平均**拉低** Brier(有用)" if audit["delta"] < 0 else "偏离平均**拉高** Brier(帮倒忙,该收敛回市场)"
        lines += [f"偏离审计:{audit['n_deviated']} 场有偏离,v2 {audit['v2_mean']} vs 市场 {audit['market_mean']},delta {audit['delta']} → {verdict}", ""]
    lines += ["## 每场", "", "| 场次 | 靠谱度 | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
    for m in per_match:
        b = m["brier"]
        lines.append(f"| {m['match_key']} | {m.get('reliability','')} | {b.get('v1')} | {b.get('v2')} | {b.get('market')} |")
    lines += ["", "_红线:概率预测非投注建议;v2 跑不赢市场就回归市场基线+避雷器。_"]
    return "\n".join(lines)


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


def collect(cache_path):
    rows, per_match = [], []
    for mk in _result_keys(cache_path):
        actual = get_result(cache_path, mk)
        bl = baseline_had(cache_path, mk)
        v2 = get_v2_prediction(cache_path, mk)
        v1 = get_v1(cache_path, mk)
        if not actual or not bl:
            continue
        market = bl["baseline"]
        v2p = v2["v2"] if v2 else None
        v1p = v1["probs"] if v1 else None
        b = three_way(v1p, v2p, market, actual)
        deviated = bool(v2 and v2.get("deviations"))
        rows.append({"deviated": deviated, "v2": b["v2"], "market": b["market"]})
        per_match.append({"match_key": mk,
                          "reliability": (v2 or {}).get("reliability", ""),
                          "brier": b})
    return aggregate([{"v1": p["brier"]["v1"], "v2": p["brier"]["v2"],
                       "market": p["brier"]["market"]} for p in per_match]), \
        deviation_audit(rows), per_match


def main():
    ap = argparse.ArgumentParser(description="渲染 v2 跑分卡")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    agg, audit, per_match = collect(a.cache)
    md = render(agg, audit, per_match)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"写出跑分卡 → {a.out}(配对 {agg['n']} 场)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_v2_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/v2_report.py tests/test_v2_report.py
git commit -m "feat(v2): 跑分卡报告渲染(reports/预测v2.md)"
```

---

## Phase 3 完成标准
- `pytest tests/test_v1_log.py tests/test_scorecard.py tests/test_v2_report.py -v` 全绿。
- 全套(Phase 1+2+3)回归:`pytest tests/test_scoring.py tests/test_baseline.py tests/test_backtest_baseline.py tests/test_scenarios.py tests/test_v2_predict.py tests/test_v1_log.py tests/test_scorecard.py tests/test_v2_report.py -v` 全绿。
- 能 `python3 tools/v2_report.py` 渲染出三方 Brier 跑分卡(有数据时)。

## 留给用户(非 AFK)
- 用 `wc-forecaster-v2` agent 对真实比赛跑首轮预测(需实时数据 + 你在场)。
- 录入实际比分后跑 `backtest_baseline.py` + `v2_report.py`,看 v2 vs 市场首批战绩。
- review 分支、决定是否合并。

## Self-Review(已自查)
- spec 覆盖:对应 spec §7(v1 并行)、§8(打分引擎三方 Brier+校准+偏离审计)、§9.1(报告 reports/预测v2.md)。
- 占位符:无 TBD;每步真实代码/命令。
- 类型一致:`three_way`/`aggregate`/`deviation_audit` 签名 Task2/3 定义、Task4 `collect` 消费一致;`brier_multi`、`baseline_had`、`get_v2_prediction`、`get_v1`、`get_result` 均沿用前序 Phase 定义。
