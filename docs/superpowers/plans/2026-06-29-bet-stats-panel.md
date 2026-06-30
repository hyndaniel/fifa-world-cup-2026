# 看板「下注统计」面板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 6 天下注台账聚合成看板「下注统计」面板,展示推荐腿战绩(命中率/分档)与实购票盈亏(真钱 ROI),替换掉一直空着的钱包 A/B 组件。

**Architecture:** 三层——手整结构化数据 `data/bet_ledger.json`(markdown 台账仍是真源)→ 纯函数聚合 `backend/bet_stats.py` → 只读端点 `GET /api/bets/summary` → 前端 `renderBetStats()` 替换 `renderLedger`。后端算账、前端纯展示。

**Tech Stack:** Python 3 stdlib(`json`)+ FastAPI(已用)+ 原生 JS/CSS(无构建)。无新依赖。

## Global Constraints

- 仅用 stdlib + 现有依赖,**不新增任何包**(`/usr/bin/python3` 可跑,只用 `json`)。
- 数据真源是 markdown 台账 `reports/agents/wc-bet__下注复盘.md`;JSON 是手整镜像,不写解析器。
- **诚实文案硬约束(逐字焊进面板,不可粉饰):**
  - 实购票合计须显示 **总投注 292 / 总盈亏 −292(红) / ROI −100% / 6 张 0 中**。
  - 推荐腿 **🟢green 迄今 = 0**;命中率旁固定挂 **「命中 ≠ 有价值」**。
  - 假设单位盈亏(`hypo_unit_pnl` = +6.51 / +46.5%)旁必须挂 **「假设每条 best_leg 各投 1 注、非真钱;正收益是高赔腿方差兑现,全部腿赛前皆 -EV,不可复制」**。
- 前端复用现有 CSS 变量与类:`--edge`(正/绿)、`--bleed`(负/红)、`--fair`(黄)、`.pos`/`.neg`、`.panel`/`.panel-head`/`.eyebrow`/`.panel-h`/`.panel-sub`/`.led-row`/`.led-k`/`.led-v`。
- **不碰**决策卡(`#panel-decisions`)、价值雷达(`#panel-radar`)、特别关注(`#panel-watch`)、报告 tab。
- DB `bets` 表与 `POST /api/bets` 端点**保留不动**(本次只移除其前端表单 UI)。

---

## File Structure

- **Create** `data/bet_ledger.json` — 结构化台账镜像(`recommendations[]` + `tickets[]`)。纯数据。
- **Create** `backend/bet_stats.py` — `load_ledger(data_dir)` + `build_summary(ledger)`。纯聚合,不碰 DB/网络。
- **Create** `tests/test_bet_stats.py` — 聚合逻辑单测。
- **Modify** `backend/web.py` — `create_app` 加 `data_dir="data"` 参数;新增 `GET /api/bets/summary`。
- **Modify** `tests/test_web.py` — 加 `/api/bets/summary` 用例。
- **Modify** `frontend/index.html` — `#panel-ledger` 整段换成 `#panel-betstats`。
- **Modify** `frontend/app.js` — 删 `renderLedger`/`ledgerRow`/`setupBetForm`;加 `renderBetStats`/`loadBetStats`。
- **Modify** `frontend/style.css` — 加 `.betstats-*` / `.tier-bar` / `.ticket-table` 样式。

---

## Task 1: 结构化台账数据 `data/bet_ledger.json`

**Files:**
- Create: `data/bet_ledger.json`
- Test: `tests/test_bet_stats.py`(本任务先放一个数据完整性测试,Task 2 复用同文件)

**Interfaces:**
- Produces: JSON 文件,顶层 `{ "updated": str, "recommendations": [...], "tickets": [...] }`。
  - `recommendations[i]` = `{date, match, leg, odds(float), tier("green"|"yellow"|"red"), value_poly(float), result("win"|"loss"|"pending"), settled(bool)}`
  - `tickets[i]` = `{date, who, type, stake(float), legs_hit(str), pnl(float), settled(bool)}`

数据来源:`reports/agents/wc-bet__下注复盘.md`。**去重口径**:每个比赛日只取该日「推荐单关 / best_leg」头条表(6.24 取 19:52 的 3 条单关;6.24 23:53 refresh、6.26 20:56 实购票复盘 不计入 recommendations——后者进 tickets)。

- [ ] **Step 1: 写数据文件**

创建 `data/bet_ledger.json`,内容逐字如下(数字均来自台账,已对平):

```json
{
  "updated": "2026-06-29",
  "recommendations": [
    {"date": "2026-06-24", "match": "南非vs韩国", "leg": "主胜(南非)", "odds": 5.65, "tier": "yellow", "value_poly": 0.972, "result": "win", "settled": true},
    {"date": "2026-06-24", "match": "波黑vs卡塔尔", "leg": "客胜(卡塔尔)", "odds": 3.32, "tier": "red", "value_poly": 0.81, "result": "loss", "settled": true},
    {"date": "2026-06-24", "match": "捷克vs墨西哥", "leg": "主胜(捷克)", "odds": 3.55, "tier": "red", "value_poly": 0.90, "result": "loss", "settled": true},
    {"date": "2026-06-26", "match": "日本vs瑞典", "leg": "平", "odds": 3.55, "tier": "yellow", "value_poly": 0.994, "result": "win", "settled": true},
    {"date": "2026-06-26", "match": "厄瓜多尔vs德国", "leg": "平", "odds": 4.90, "tier": "yellow", "value_poly": 0.985, "result": "loss", "settled": true},
    {"date": "2026-06-26", "match": "土耳其vs美国", "leg": "主胜(土耳其)", "odds": 3.75, "tier": "red", "value_poly": 0.953, "result": "win", "settled": true},
    {"date": "2026-06-26", "match": "巴拉圭vs澳大利亚", "leg": "平", "odds": 2.20, "tier": "red", "value_poly": 0.931, "result": "win", "settled": true},
    {"date": "2026-06-27", "match": "埃及vs伊朗", "leg": "平", "odds": 2.50, "tier": "red", "value_poly": 0.922, "result": "win", "settled": true},
    {"date": "2026-06-27", "match": "佛得角vs沙特", "leg": "客胜(沙特)", "odds": 2.60, "tier": "red", "value_poly": 0.918, "result": "loss", "settled": true},
    {"date": "2026-06-27", "match": "挪威vs法国", "leg": "客胜(法国)", "odds": 1.48, "tier": "red", "value_poly": 0.906, "result": "win", "settled": true},
    {"date": "2026-06-27", "match": "乌拉圭vs西班牙", "leg": "客胜(西班牙)", "odds": 1.38, "tier": "red", "value_poly": 0.894, "result": "win", "settled": true},
    {"date": "2026-06-28", "match": "刚果金vs乌兹别克", "leg": "平", "odds": 4.05, "tier": "yellow", "value_poly": 0.988, "result": "loss", "settled": true},
    {"date": "2026-06-28", "match": "哥伦比亚vs葡萄牙", "leg": "哥伦比亚胜", "odds": 3.60, "tier": "red", "value_poly": 0.950, "result": "loss", "settled": true},
    {"date": "2026-06-28", "match": "阿尔及利亚vs奥地利", "leg": "阿尔及利亚胜", "odds": 3.90, "tier": "red", "value_poly": 0.959, "result": "loss", "settled": true},
    {"date": "2026-06-29", "match": "巴西vs日本", "leg": "主-1负(平或日本)", "odds": 2.26, "tier": "red", "value_poly": 0.967, "result": "pending", "settled": false},
    {"date": "2026-06-29", "match": "德国vs巴拉圭", "leg": "主-1负(平或巴拉圭)", "odds": 3.44, "tier": "red", "value_poly": 0.932, "result": "pending", "settled": false},
    {"date": "2026-06-29", "match": "荷兰vs摩洛哥", "leg": "主-1负(平或摩洛哥)", "odds": 1.66, "tier": "red", "value_poly": 0.958, "result": "pending", "settled": false}
  ],
  "tickets": [
    {"date": "2026-06-24", "who": "楼主", "type": "比分6串1", "stake": 30, "legs_hit": "0/6", "pnl": -30, "settled": true},
    {"date": "2026-06-24", "who": "楼主", "type": "胜负6串1", "stake": 30, "legs_hit": "2/6", "pnl": -30, "settled": true},
    {"date": "2026-06-24", "who": "朋友A", "type": "半全场050(5组合)", "stake": 50, "legs_hit": "0组合", "pnl": -50, "settled": true},
    {"date": "2026-06-24", "who": "朋友A", "type": "半全场049(4组合)", "stake": 40, "legs_hit": "0组合", "pnl": -40, "settled": true},
    {"date": "2026-06-24", "who": "朋友B", "type": "4串1复式(6腿)", "stake": 82, "legs_hit": "2/6", "pnl": -82, "settled": true},
    {"date": "2026-06-26", "who": "用户", "type": "6场3串1", "stake": 60, "legs_hit": "1/6", "pnl": -60, "settled": true}
  ]
}
```

- [ ] **Step 2: 写数据完整性测试**

创建 `tests/test_bet_stats.py`:

```python
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_ledger_json_loads_and_totals():
    data = json.loads((REPO / "data" / "bet_ledger.json").read_text(encoding="utf-8"))
    recs = data["recommendations"]
    tix = data["tickets"]
    # 17 条推荐腿: 14 已结 + 3 pending
    assert len(recs) == 17
    assert sum(1 for r in recs if r["settled"]) == 14
    assert sum(1 for r in recs if r["result"] == "pending") == 3
    # green 迄今 = 0(诚实锚点)
    assert sum(1 for r in recs if r["tier"] == "green") == 0
    # 实购票 6 张, 合计 stake 292 / pnl -292 / 0 中
    assert len(tix) == 6
    assert sum(t["stake"] for t in tix) == 292
    assert sum(t["pnl"] for t in tix) == -292
    assert all(t["pnl"] < 0 for t in tix)
```

- [ ] **Step 3: 跑测试确认通过**

Run: `cd <repo> && /usr/bin/python3 -m pytest tests/test_bet_stats.py::test_ledger_json_loads_and_totals -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add data/bet_ledger.json tests/test_bet_stats.py
git commit -m "feat(下注统计): 手整 6 天台账为结构化 bet_ledger.json"
```

---

## Task 2: 聚合逻辑 `backend/bet_stats.py`

**Files:**
- Create: `backend/bet_stats.py`
- Test: `tests/test_bet_stats.py`(追加)

**Interfaces:**
- Produces:
  - `load_ledger(data_dir: str = "data") -> dict` — 读 `<data_dir>/bet_ledger.json`;文件缺失返回 `{"updated": None, "recommendations": [], "tickets": []}`。
  - `build_summary(ledger: dict) -> dict` — 返回:
    ```
    {
      "updated": str|None,
      "recommendations": {
        "total": int, "settled": int, "win": int, "pending": int,
        "hit_rate": float,                          # win/settled, settled=0 时 0.0
        "by_tier": {"green":{"total","win"}, "yellow":{...}, "red":{...}},  # 仅计已结
        "by_date": [{"date","settled","win"}...],   # 按 date 升序, 仅计已结
        "hypo_unit_pnl": float, "hypo_roi": float    # 每条已结 best_leg 投 1 注
      },
      "tickets": {"count","won","total_stake","total_pnl","roi","rows":[...]}
    }
    ```
- Consumes(Task 3 依赖):`backend.bet_stats.load_ledger`、`backend.bet_stats.build_summary`。

- [ ] **Step 1: 写聚合测试(追加到 tests/test_bet_stats.py)**

```python
from backend.bet_stats import build_summary

SAMPLE = {
    "updated": "2026-06-29",
    "recommendations": [
        {"date": "2026-06-24", "match": "m1", "leg": "x", "odds": 5.65, "tier": "yellow", "value_poly": 0.972, "result": "win", "settled": True},
        {"date": "2026-06-24", "match": "m2", "leg": "x", "odds": 3.32, "tier": "red", "value_poly": 0.81, "result": "loss", "settled": True},
        {"date": "2026-06-26", "match": "m3", "leg": "x", "odds": 3.75, "tier": "red", "value_poly": 0.953, "result": "win", "settled": True},
        {"date": "2026-06-29", "match": "m4", "leg": "x", "odds": 1.66, "tier": "red", "value_poly": 0.958, "result": "pending", "settled": False},
    ],
    "tickets": [
        {"date": "2026-06-24", "who": "楼主", "type": "t1", "stake": 30, "legs_hit": "0/6", "pnl": -30, "settled": True},
        {"date": "2026-06-26", "who": "用户", "type": "t2", "stake": 60, "legs_hit": "1/6", "pnl": -60, "settled": True},
    ],
}


def test_build_summary_recommendations():
    s = build_summary(SAMPLE)["recommendations"]
    assert s["total"] == 4
    assert s["settled"] == 3
    assert s["pending"] == 1
    assert s["win"] == 2
    assert s["hit_rate"] == round(2 / 3, 4)
    assert s["by_tier"]["green"] == {"total": 0, "win": 0}
    assert s["by_tier"]["yellow"] == {"total": 1, "win": 1}
    assert s["by_tier"]["red"] == {"total": 2, "win": 1}
    # hypo: 投 3 注(已结), 赢 5.65→+4.65, 输 3.32→-1, 赢 3.75→+2.75 = 6.40
    assert s["hypo_unit_pnl"] == 6.40
    assert s["hypo_roi"] == round(6.40 / 3, 4)
    # by_date 升序
    assert [d["date"] for d in s["by_date"]] == ["2026-06-24", "2026-06-26"]
    assert s["by_date"][0] == {"date": "2026-06-24", "settled": 2, "win": 1}


def test_build_summary_tickets():
    s = build_summary(SAMPLE)["tickets"]
    assert s["count"] == 2
    assert s["won"] == 0
    assert s["total_stake"] == 90
    assert s["total_pnl"] == -90
    assert s["roi"] == -1.0
    assert len(s["rows"]) == 2


def test_build_summary_empty():
    s = build_summary({"recommendations": [], "tickets": []})
    assert s["recommendations"]["hit_rate"] == 0.0
    assert s["recommendations"]["hypo_unit_pnl"] == 0.0
    assert s["tickets"]["roi"] == 0.0
    assert s["tickets"]["count"] == 0


def test_full_ledger_summary_matches_hand_count():
    """跑真数据(Task 1 的 17 条/6 张), 锚定手算结果。"""
    from backend.bet_stats import load_ledger
    s = build_summary(load_ledger(str(REPO / "data")))
    assert s["recommendations"]["settled"] == 14
    assert s["recommendations"]["win"] == 7
    assert s["recommendations"]["hit_rate"] == 0.5
    assert s["recommendations"]["by_tier"]["green"] == {"total": 0, "win": 0}
    assert s["recommendations"]["by_tier"]["yellow"] == {"total": 4, "win": 2}
    assert s["recommendations"]["by_tier"]["red"] == {"total": 10, "win": 5}
    assert s["recommendations"]["hypo_unit_pnl"] == 6.51
    assert s["tickets"]["total_pnl"] == -292
    assert s["tickets"]["roi"] == -1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd <repo> && /usr/bin/python3 -m pytest tests/test_bet_stats.py -v`
Expected: FAIL（`ModuleNotFoundError: backend.bet_stats` / `ImportError`）

- [ ] **Step 3: 写实现 `backend/bet_stats.py`**

```python
"""下注台账聚合: 读 data/bet_ledger.json, 算推荐腿战绩 + 实购票盈亏。

纯函数, 不碰 DB/网络。数据真源是 markdown 台账 reports/agents/wc-bet__下注复盘.md,
本模块只消费其手整镜像 bet_ledger.json。
"""
import json
import os

_TIERS = ("green", "yellow", "red")


def load_ledger(data_dir: str = "data") -> dict:
    path = os.path.join(data_dir, "bet_ledger.json")
    if not os.path.exists(path):
        return {"updated": None, "recommendations": [], "tickets": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("updated", None)
    data.setdefault("recommendations", [])
    data.setdefault("tickets", [])
    return data


def _round(x: float, n: int = 4) -> float:
    return round(float(x), n)


def build_summary(ledger: dict) -> dict:
    recs = ledger.get("recommendations", []) or []
    tix = ledger.get("tickets", []) or []

    settled = [r for r in recs if r.get("settled")]
    wins = [r for r in settled if r.get("result") == "win"]
    pending = [r for r in recs if r.get("result") == "pending"]

    by_tier = {t: {"total": 0, "win": 0} for t in _TIERS}
    for r in settled:
        t = r.get("tier")
        if t in by_tier:
            by_tier[t]["total"] += 1
            if r.get("result") == "win":
                by_tier[t]["win"] += 1

    by_date_map = {}
    for r in settled:
        d = by_date_map.setdefault(r["date"], {"date": r["date"], "settled": 0, "win": 0})
        d["settled"] += 1
        if r.get("result") == "win":
            d["win"] += 1
    by_date = sorted(by_date_map.values(), key=lambda d: d["date"])

    hypo = 0.0
    for r in settled:
        if r.get("result") == "win":
            hypo += float(r["odds"]) - 1.0
        else:
            hypo -= 1.0
    n_settled = len(settled)
    hit_rate = _round(len(wins) / n_settled) if n_settled else 0.0
    hypo_roi = _round(hypo / n_settled) if n_settled else 0.0

    total_stake = sum(float(t.get("stake", 0)) for t in tix)
    total_pnl = sum(float(t.get("pnl", 0)) for t in tix)
    roi = _round(total_pnl / total_stake) if total_stake else 0.0
    won = sum(1 for t in tix if float(t.get("pnl", 0)) > 0)

    return {
        "updated": ledger.get("updated"),
        "recommendations": {
            "total": len(recs),
            "settled": n_settled,
            "win": len(wins),
            "pending": len(pending),
            "hit_rate": hit_rate,
            "by_tier": by_tier,
            "by_date": by_date,
            "hypo_unit_pnl": _round(hypo, 2),
            "hypo_roi": hypo_roi,
        },
        "tickets": {
            "count": len(tix),
            "won": won,
            "total_stake": _round(total_stake, 2),
            "total_pnl": _round(total_pnl, 2),
            "roi": roi,
            "rows": tix,
        },
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd <repo> && /usr/bin/python3 -m pytest tests/test_bet_stats.py -v`
Expected: PASS（5 个用例全过）

- [ ] **Step 5: Commit**

```bash
git add backend/bet_stats.py tests/test_bet_stats.py
git commit -m "feat(下注统计): bet_stats 聚合(推荐腿战绩/分档/假设单位盈亏 + 实购票 ROI)"
```

---

## Task 3: 端点 `GET /api/bets/summary`

**Files:**
- Modify: `backend/web.py`（`create_app` 加 `data_dir` 参数 + 新端点）
- Test: `tests/test_web.py`（追加用例）

**Interfaces:**
- Consumes: `backend.bet_stats.load_ledger`、`build_summary`。
- Produces: `GET /api/bets/summary` → `build_summary(load_ledger(data_dir))`，受 `auth_dep` 保护。

- [ ] **Step 1: 写端点测试(追加到 tests/test_web.py)**

```python
def test_bets_summary_ok(client):
    c, _ = client
    r = c.get("/api/bets/summary")
    assert r.status_code == 200
    body = r.json()
    assert "recommendations" in body and "tickets" in body
    # 默认 data_dir="data" → 真台账: green=0, 票 -292
    assert body["recommendations"]["by_tier"]["green"]["total"] == 0
    assert body["tickets"]["total_pnl"] == -292


def test_bets_summary_missing_file_is_empty(tmp_path):
    cfg = load_config("nope.toml")
    app = create_app(db_path=str(tmp_path / "t.db"), cfg=cfg,
                     require_auth=False, data_dir=str(tmp_path / "no_data"))
    c = TestClient(app)
    r = c.get("/api/bets/summary")
    assert r.status_code == 200
    assert r.json()["tickets"]["count"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd <repo> && /usr/bin/python3 -m pytest tests/test_web.py::test_bets_summary_ok tests/test_web.py::test_bets_summary_missing_file_is_empty -v`
Expected: FAIL（404，端点未定义 / `create_app` 不认 `data_dir`）

- [ ] **Step 3: 改 `backend/web.py`**

3a. 顶部加 import(与现有 import 同区):

```python
from backend import bet_stats
```

3b. `create_app` 签名加 `data_dir`(改 `backend/web.py:70-71`):

```python
def create_app(db_path="wc.db", cfg=None, reports_dir="reports",
               frontend_dir="frontend", data_dir="data", require_auth=None) -> FastAPI:
```

3c. 在 `/api/decisions` 端点之后、`/api/refresh` 之前插入新端点:

```python
    # ---------------- /api/bets/summary ----------------
    # 下注统计面板据此渲染: 推荐腿战绩(命中率/分档) + 实购票盈亏(ROI)。
    # 只读 data/bet_ledger.json(手整台账镜像), 文件缺失返回空 summary。
    @app.get("/api/bets/summary", dependencies=[Depends(auth_dep)])
    def api_bets_summary():
        return bet_stats.build_summary(bet_stats.load_ledger(data_dir))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd <repo> && /usr/bin/python3 -m pytest tests/test_web.py -v`
Expected: PASS（含新 2 例 + 原有全过）

- [ ] **Step 5: Commit**

```bash
git add backend/web.py tests/test_web.py
git commit -m "feat(下注统计): GET /api/bets/summary 端点(create_app 加 data_dir)"
```

---

## Task 4: 前端面板 `renderBetStats`(替换钱包组件)

**Files:**
- Modify: `frontend/index.html:132-176`（`#panel-ledger` 整段替换）
- Modify: `frontend/app.js`（删 `renderLedger`/`ledgerRow`/`setupBetForm`;加 `renderBetStats`/`loadBetStats`;改调用点）
- Modify: `frontend/style.css`（`.ledger-grid`…段后追加新样式）

**Interfaces:**
- Consumes: `GET /api/bets/summary` 的返回结构(Task 3)。

- [ ] **Step 1: 替换 index.html 的 `#panel-ledger` 段**

把 `frontend/index.html` 第 132–176 行(`<!-- 账本 ... -->` 整个 `<section class="panel" id="panel-ledger">`,含 bet-form)整段替换为:

```html
      <!-- 下注统计 (推荐腿战绩 / 实购票盈亏) -->
      <section class="panel" id="panel-betstats">
        <div class="panel-head">
          <span class="eyebrow">stats · 下注统计</span>
          <h2 class="panel-h">战绩 · 盈亏 · 不自欺</h2>
        </div>
        <p class="panel-sub">推荐腿命中率与实购票真实盈亏 · 数据源自 wc-bet 台账</p>

        <!-- 推荐腿战绩 -->
        <div class="bs-block" id="bs-recs">
          <div class="bs-block-h">推荐腿战绩 <span class="bs-sub" id="bs-recs-sub">—</span></div>
          <div class="bs-hitline">
            <span class="bs-hit-big" id="bs-hit-big">—</span>
            <span class="bs-hit-cap">命中 ≠ 有价值 · 🟢green 迄今 0</span>
          </div>
          <div class="tier-bar" id="bs-tier-bar"></div>
          <div class="bs-bydate" id="bs-bydate"></div>
          <div class="bs-hypo" id="bs-hypo"></div>
        </div>

        <!-- 实购票盈亏 -->
        <div class="bs-block" id="bs-tickets">
          <div class="bs-block-h">实购票盈亏 <span class="bs-sub" id="bs-tix-sub">—</span></div>
          <div class="ticket-table" id="bs-ticket-table"></div>
          <div class="bs-tix-total" id="bs-tix-total"></div>
          <div class="bs-foot">足彩长期 -EV;此处真金白银全归零,非投资建议。</div>
        </div>
      </section>
```

- [ ] **Step 2: 改 app.js — 删旧账本代码**

删除三段:`renderLedger`(796–815)、`ledgerRow`(816–827)、`setupBetForm`(829–864 整个函数)。
并改三处调用点:
- `frontend/app.js:1044` 把 `renderLedger(s.ledger || {});` **删掉**(state 不再渲染账本)。
- `frontend/app.js:1057` 把 `setupBetForm();` **删掉**。
- `frontend/app.js:986` 行 `if (tab === "decisions") loadDecisions();` 之后加一行:
  ```javascript
      if (tab === "dashboard") loadBetStats();
  ```

- [ ] **Step 3: 改 app.js — 加 `renderBetStats` / `loadBetStats`**

在原 `// ================= 账本 =================` 位置(删空后)放入:

```javascript
// ================= 下注统计 =================
async function loadBetStats() {
  try {
    const s = await apiGet("/api/bets/summary");
    renderBetStats(s);
  } catch (_) { /* 静默: 面板保持占位 */ }
}

function renderBetStats(s) {
  const recs = (s && s.recommendations) || {};
  const tix = (s && s.tickets) || {};

  // — 推荐腿战绩 —
  const sub = $("#bs-recs-sub");
  if (sub) sub.textContent = `${recs.total || 0} 条 · ${recs.settled || 0} 已结 · ${recs.pending || 0} 待结`;
  const big = $("#bs-hit-big");
  if (big) big.textContent = `命中 ${recs.win || 0}/${recs.settled || 0}　(${fmtPct((recs.hit_rate || 0) * 100)})`;

  const bar = $("#bs-tier-bar");
  if (bar) {
    bar.innerHTML = "";
    const tiers = [
      { k: "green", label: "🟢", cls: "tb-green" },
      { k: "yellow", label: "🟡", cls: "tb-yellow" },
      { k: "red", label: "🔴", cls: "tb-red" },
    ];
    for (const t of tiers) {
      const o = (recs.by_tier && recs.by_tier[t.k]) || { total: 0, win: 0 };
      const seg = el("div", `tb-seg ${t.cls}`);
      seg.appendChild(el("span", "tb-emoji", t.label));
      seg.appendChild(el("span", "tb-num", `${o.win}/${o.total}`));
      bar.appendChild(seg);
    }
  }

  const bd = $("#bs-bydate");
  if (bd) {
    bd.innerHTML = "";
    for (const d of recs.by_date || []) {
      const row = el("div", "bs-date-row");
      row.appendChild(el("span", "bs-date-k", d.date.slice(5)));
      row.appendChild(el("span", "bs-date-v", `${d.win}/${d.settled}`));
      bd.appendChild(row);
    }
  }

  const hypo = $("#bs-hypo");
  if (hypo) {
    hypo.innerHTML = "";
    const pnl = recs.hypo_unit_pnl || 0;
    const line = el("div", "bs-hypo-line");
    const v = el("span", "bs-hypo-v " + (pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""),
      `${pnl > 0 ? "+" : ""}${fmtNum(pnl)} 单位 (${fmtSignedPct((recs.hypo_roi || 0) * 100)})`);
    line.appendChild(el("span", "bs-hypo-k", "假设每条 best_leg 各投 1 注"));
    line.appendChild(v);
    hypo.appendChild(line);
    hypo.appendChild(el("div", "bs-hypo-cap",
      "非真钱;正收益是高赔腿方差兑现,全部腿赛前皆 -EV、不可复制"));
  }

  // — 实购票盈亏 —
  const tsub = $("#bs-tix-sub");
  if (tsub) tsub.textContent = `${tix.count || 0} 张 · ${tix.won || 0} 中`;
  const tbl = $("#bs-ticket-table");
  if (tbl) {
    tbl.innerHTML = "";
    const head = el("div", "tk-row tk-head");
    ["日期", "谁", "票型", "注", "命中", "盈亏"].forEach((h, i) =>
      head.appendChild(el("span", "tk-c tk-c" + i, h)));
    tbl.appendChild(head);
    for (const r of tix.rows || []) {
      const row = el("div", "tk-row");
      row.appendChild(el("span", "tk-c tk-c0", (r.date || "").slice(5)));
      row.appendChild(el("span", "tk-c tk-c1", r.who || ""));
      row.appendChild(el("span", "tk-c tk-c2", r.type || ""));
      row.appendChild(el("span", "tk-c tk-c3", fmtNum(r.stake)));
      row.appendChild(el("span", "tk-c tk-c4", r.legs_hit || ""));
      const pnlEl = el("span", "tk-c tk-c5 " + (r.pnl < 0 ? "neg" : r.pnl > 0 ? "pos" : ""), fmtNum(r.pnl));
      row.appendChild(pnlEl);
      tbl.appendChild(row);
    }
  }
  const tot = $("#bs-tix-total");
  if (tot) {
    tot.innerHTML = "";
    tot.appendChild(el("span", "bs-tot-k", "合计"));
    tot.appendChild(el("span", "bs-tot-stake", `投 ${fmtNum(tix.total_stake)}`));
    tot.appendChild(el("span", "bs-tot-pnl neg", `盈亏 ${fmtNum(tix.total_pnl)}`));
    tot.appendChild(el("span", "bs-tot-roi neg", `ROI ${fmtSignedPct((tix.roi || 0) * 100)}`));
  }
}
```

- [ ] **Step 4: 在 init 里拉一次**

`frontend/app.js` 的 `init()`(约 1054–1070)中,`refreshState();` 之后加一行:

```javascript
  loadBetStats();
```

- [ ] **Step 5: 加 CSS(追加到 frontend/style.css 末尾)**

```css
/* ===== 下注统计面板 ===== */
.bs-block { margin-top: 14px; }
.bs-block + .bs-block { border-top: 1px solid var(--rule); padding-top: 12px; }
.bs-block-h { font-weight: 600; color: var(--bone); margin-bottom: 8px; }
.bs-sub { font-weight: 400; color: var(--bone-dim); font-size: 0.85em; margin-left: 6px; }
.bs-hitline { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.bs-hit-big { font-family: var(--font-mono); font-size: 1.25rem; font-weight: 700; color: var(--bone); font-variant-numeric: tabular-nums; }
.bs-hit-cap { color: var(--fair); font-size: 0.8rem; }
.tier-bar { display: flex; gap: 8px; margin: 10px 0; }
.tb-seg { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 0; border-radius: 6px; background: var(--panel-2, rgba(127,127,127,0.06)); }
.tb-emoji { font-size: 0.9rem; }
.tb-num { font-family: var(--font-mono); font-weight: 600; font-variant-numeric: tabular-nums; }
.tb-green { opacity: 0.55; }
.tb-yellow { box-shadow: inset 0 2px 0 var(--fair); }
.tb-red { box-shadow: inset 0 2px 0 var(--bleed); }
.bs-bydate { display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 6px 0; }
.bs-date-row { display: flex; gap: 5px; font-size: 0.82rem; }
.bs-date-k { color: var(--bone-dim); }
.bs-date-v { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.bs-hypo { margin-top: 8px; }
.bs-hypo-line { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
.bs-hypo-k { color: var(--bone-dim); font-size: 0.82rem; }
.bs-hypo-v { font-family: var(--font-mono); font-weight: 600; font-variant-numeric: tabular-nums; }
.bs-hypo-v.pos { color: var(--edge); }
.bs-hypo-v.neg { color: var(--bleed); }
.bs-hypo-cap { color: var(--bone-dim); font-size: 0.74rem; margin-top: 2px; line-height: 1.4; }
.ticket-table { display: flex; flex-direction: column; margin-top: 4px; }
.tk-row { display: grid; grid-template-columns: 0.7fr 0.6fr 1.6fr 0.5fr 0.7fr 0.7fr; gap: 6px; padding: 4px 0; border-bottom: 1px solid var(--rule); font-size: 0.8rem; align-items: center; }
.tk-head { color: var(--bone-dim); font-weight: 600; }
.tk-c { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tk-c3, .tk-c5 { font-family: var(--font-mono); font-variant-numeric: tabular-nums; text-align: right; }
.tk-c.pos { color: var(--edge); }
.tk-c.neg { color: var(--bleed); }
.bs-tix-total { display: flex; gap: 14px; margin-top: 8px; font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.bs-tot-k { color: var(--bone-dim); }
.bs-tot-pnl.neg, .bs-tot-roi.neg { color: var(--bleed); font-weight: 600; }
.bs-foot { color: var(--bone-dim); font-size: 0.74rem; margin-top: 8px; }
```

> 注：若 `--rule` / `--panel-2` 变量在 `style.css:root` 不存在,改用既有变量(grep `:root` 段确认;`--bleed`/`--edge`/`--fair`/`--bone`/`--bone-dim`/`--font-mono` 已确认存在)。fallback 已在 `--panel-2` 处内联给出。

- [ ] **Step 6: 跑既有测试确认没碰坏后端**

Run: `cd <repo> && /usr/bin/python3 -m pytest tests/test_web.py tests/test_bet_stats.py -v`
Expected: PASS（前端改动不影响这些;确认无回归）

- [ ] **Step 7: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/style.css
git commit -m "feat(下注统计): 前端 renderBetStats 面板替换空钱包组件"
```

---

## Task 5: 本地视觉验证 + 用户过图

**Files:** 无(验证步骤,不改代码)

- [ ] **Step 1: 起本地 mock + 真后端截图**

按项目「Frontend 本地调试配方」: 用 stdlib 起后端(`uvicorn backend.web:create_app --factory` 或既有启动脚本,`require_auth` 关),Chrome headless 截 `view-dashboard` 的 `#panel-betstats`。命令示例:

```bash
cd <repo> && /usr/bin/python3 -m uvicorn "backend.web:create_app" --factory --port 8848 &
# 待起后, Chrome headless 截图(决策 tab 切到「看板」)
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --headless --disable-gpu --screenshot=/tmp/betstats.png --window-size=412,1400 \
  "http://127.0.0.1:8848/#dashboard"
```

- [ ] **Step 2: 自查截图**

肉眼核对:推荐腿命中 7/14 + 「命中≠有价值」+ 三色档条(🟢 0/0 灰、🟡 2/4、🔴 5/10)+ 按日 + 假设 +6.51/+46.5% 带 caveat;实购票 6 行 + 合计 −292 红字 + ROI −100%。右侧无裁切(配方里的 iframe@412 真机测量法)。

- [ ] **Step 3: 交付用户过图**

把截图交用户确认观感与文案。用户认可后,本面板完成;若要上 HK 看板,走 `fifa-deploy` skill(本计划不含部署)。

---

## Task 6（可选 · 需用户授权）: 写进 wc-bet 台账维护约定

> ⚠️ 改 `.claude/agents/wc-bet.md` 撞**自修改护栏**,需用户在其消息里明确授权后才做,不在本计划自动执行。

- 在 `wc-bet.md` §维护台账 加一句:每日落盘 `reports/agents/wc-bet__下注复盘.md` 后,同步往 `data/bet_ledger.json` 追加当日推荐腿(赛前 `settled:false`,赛后回填 `result`),保面板不陈旧。

---

## Self-Review

**1. Spec coverage:**
- spec §2 两块都做 → Task 1 数据 + Task 4 两 sub-block ✓
- spec §2 手整 JSON → Task 1 ✓
- spec §2 替换空钱包 → Task 4 Step 1/2(删 #panel-ledger + setupBetForm)✓
- spec §3 诚实锚点(green=0 / −292 / 命中≠价值 / hypo caveat)→ Global Constraints + Task 4 文案 ✓
- spec §4 架构三层 → Task 1/2/3/4 ✓
- spec §5 数据模型 → Task 1 JSON 结构 ✓
- spec §6 build_summary + 端点 → Task 2/3 ✓
- spec §7 renderBetStats 替换 + 保留 POST /api/bets 后端 → Task 4(只删前端表单)✓
- spec §8 维护 → Task 6(标授权)✓
- spec §9 测试(test_bet_stats + test_web)→ Task 2/3 ✓
- spec §10 YAGNI(不解析/不灌DB/不做表单)→ 计划无这些 ✓

**2. Placeholder scan:** 无 TBD/TODO;所有代码步给了完整代码;测试数字均算平(SAMPLE: hypo=6.40;真数据: settled14/win7/hit0.5/yellow2-of-4/red5-of-10/hypo6.51/票-292)。

**3. Type consistency:** `load_ledger`/`build_summary` 签名 Task 2 定义、Task 3 调用一致;前端 `loadBetStats`/`renderBetStats` Task 4 内部一致;端点返回结构 Task 2 输出 = Task 4 消费字段(recommendations.{total,settled,pending,win,hit_rate,by_tier,by_date,hypo_unit_pnl,hypo_roi} / tickets.{count,won,total_stake,total_pnl,roi,rows})一致。
