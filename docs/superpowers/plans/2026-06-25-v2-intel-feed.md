# v2 intel-feed 实施计划(给 wc-forecaster-v2 喂确证事实)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把库里各队 enrich 新闻装成本场「事实卡」喂给 `wc-forecaster-v2`,让它能有据偏离;每条偏离记 `factor_source` 供归因。

**Architecture:** 新增 `backend/intel.py` 的确定性事实卡装配器(单库 app `wc.db`:`matches` 给两队、`enrich` 给新闻,标龄/截最近N/标 stale);v2 读卡自判偏离;`factor_source` 顺现有 `apply_deviations`/`build_v2_prediction` 零改动持久化;`tools/v2_report.py` 加「偏离归因」列表。

**Tech Stack:** Python 3.12 标准库(`email.utils`、`sqlite3`、`datetime`)+ pytest。无新依赖。

## Global Constraints

- **绝不喂 v1**(football-match-predictor)的预测/概率/比分;喂的只能是中立事实(护三方 Brier「v1↔v2 互不读」红线)。
- v2 **不加 WebSearch / 新工具 / 自研**;事实只来自 `enrich` 表。
- 事实卡是**纯 plumbing**:代码只标 `age_h`/`stale`,**不做方向/置信推断**(判断归 v2)。
- 首发(lineup)**恒 null**(无源);新闻 **>48h 或 pubDate 不可解析 → `stale=True`**;每队截最近 `cap=5` 条。
- 概率 %;**零回归**:全套现有测试保绿。每任务只 `git add` 计划指定文件,提交前缀 `feat(intel):` / `test(intel):` / `docs(intel):`。
- 事实卡单库 = app `wc.db`(`backend.db.Db`),不碰 `.cache/odds_cache.db`。

---

### Task 1: `db.match(zucai_num)` 单场 getter

**Files:**
- Modify: `backend/db.py`(`matches()` 之后加 `match`)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Db._conn`(已有,`row_factory=sqlite3.Row`)。
- Produces: `Db.match(zucai_num) -> dict | None`(`matches` 表一行 → dict,含 `home_cn/away_cn`;无 → None)。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_db.py`(顶部已 `from backend.db import Db`):

```python
def test_match_getter_hit_and_miss(tmp_path):
    db = Db(str(tmp_path / "wc.db")); db.init()
    db.upsert_match("周四055", "南非", "韩国", "South Africa", "Korea Republic",
                    None, "23:00", "22:45", "Selling")
    m = db.match("周四055")
    assert m is not None and m["home_cn"] == "南非" and m["away_cn"] == "韩国"
    assert db.match("无此场") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_db.py::test_match_getter_hit_and_miss -q`
Expected: FAIL — `AttributeError: 'Db' object has no attribute 'match'`.

- [ ] **Step 3: Implement**

在 `backend/db.py` 的 `matches()` 方法之后插入:

```python
    def match(self, zucai_num):
        """按 zucai_num 取单场(含 home_cn/away_cn);无 → None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM matches WHERE zucai_num=?", (zucai_num,)
            ).fetchone()
            return dict(row) if row else None
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_db.py -q`
Expected: PASS(新用例 + 现有 db 用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add backend/db.py tests/test_db.py
git commit -m "feat(intel): db.match 单场 getter(按 zucai_num)"
```

---

### Task 2: `backend/intel.py` 事实卡装配器

**Files:**
- Create: `backend/intel.py`
- Test: `tests/test_intel.py`

**Interfaces:**
- Consumes: `Db.match`(Task 1);`Db.latest_enrich(team_cn) -> {team_cn, ts, lineup, news:[{title,url,ts}]} | None`(已有)。
- Produces:
  - `match_fact_card(db, match_key, now_bj, cap=5, stale_hours=48) -> dict`
    返回 `{match_key, match, as_of_bj, teams:[{team, lineup, has_intel, news:[{title,url,age_h,stale}]}], note}`;无此场 → `{match_key, match:None, teams:[], note:"无此场"}`。
  - `_age_hours(ts, now_bj) -> float | None`(RSS pubDate → 距 now 的小时,1 位;不可解析 → None)。

- [ ] **Step 1: Write the failing tests**

Create `tests/test_intel.py`:

```python
from datetime import datetime, timezone, timedelta
from backend.db import Db
from backend.intel import match_fact_card

BJ = timezone(timedelta(hours=8))
NOW = datetime(2026, 6, 25, 20, 0, 0, tzinfo=BJ)   # 注入"现在"=北京 20:00


def _db(tmp_path):
    db = Db(str(tmp_path / "wc.db")); db.init()
    db.upsert_match("周四055", "南非", "韩国", "SA", "KOR", None, "23:00", "22:45", "Selling")
    return db


def test_fact_card_ages_caps_and_flags_stale(tmp_path):
    db = _db(tmp_path)
    db.save_enrich("南非", None, [
        {"title": "n_new", "url": "u1", "ts": "25 Jun 2026 09:00:00 +0000"},  # 17:00 BJ → 3h
        {"title": "n_old", "url": "u2", "ts": "21 Jun 2026 08:00:00 +0000"},  # >48h
        {"title": "n_bad", "url": "u3", "ts": "不是时间"},                      # 不可解析
    ])
    card = match_fact_card(db, "周四055", NOW, cap=5, stale_hours=48)
    assert card["match"] == "南非 vs 韩国"
    sa = next(t for t in card["teams"] if t["team"] == "南非")
    assert sa["has_intel"] is True and sa["lineup"] is None
    assert sa["news"][0]["title"] == "n_new"                       # 最新排首
    new = next(n for n in sa["news"] if n["title"] == "n_new")
    assert abs(new["age_h"] - 3.0) < 0.2 and new["stale"] is False
    assert next(n for n in sa["news"] if n["title"] == "n_old")["stale"] is True
    bad = next(n for n in sa["news"] if n["title"] == "n_bad")
    assert bad["age_h"] is None and bad["stale"] is True           # 不可解析 = stale


def test_fact_card_cap_limits_per_team(tmp_path):
    db = _db(tmp_path)
    db.save_enrich("南非", None, [{"title": f"n{i}", "url": f"u{i}",
                   "ts": "25 Jun 2026 09:00:00 +0000"} for i in range(10)])
    sa = next(t for t in match_fact_card(db, "周四055", NOW, cap=3)["teams"]
              if t["team"] == "南非")
    assert len(sa["news"]) == 3


def test_fact_card_team_without_enrich_has_no_intel(tmp_path):
    db = _db(tmp_path)
    db.save_enrich("南非", None, [{"title": "x", "url": "u", "ts": "25 Jun 2026 09:00:00 +0000"}])
    kor = next(t for t in match_fact_card(db, "周四055", NOW)["teams"]
               if t["team"] == "韩国")
    assert kor["has_intel"] is False and kor["news"] == []


def test_fact_card_missing_match(tmp_path):
    db = _db(tmp_path)
    card = match_fact_card(db, "无此场", NOW)
    assert card["teams"] == [] and card["note"] == "无此场"
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_intel.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.intel'`.

- [ ] **Step 3: Implement**

Create `backend/intel.py`:

```python
"""本场事实卡装配: 把一场比赛的两队最新 enrich 事实装成 v2 可读的卡。
纯 plumbing — 无方向/置信推断(判断留给 v2)。单库 = app wc.db。"""
from datetime import timezone
from email.utils import parsedate_to_datetime


def _age_hours(ts, now_bj):
    """RSS pubDate(RFC-2822 串)→ 距 now_bj 的小时数(1 位小数);不可解析 → None。"""
    try:
        pub = parsedate_to_datetime(ts)
    except (TypeError, ValueError):
        return None
    if pub is None:
        return None
    if pub.tzinfo is None:               # 无时区的 pubDate 当 UTC
        pub = pub.replace(tzinfo=timezone.utc)
    return round((now_bj - pub).total_seconds() / 3600.0, 1)


def _team_card(db, team_cn, now_bj, cap, stale_hours):
    e = db.latest_enrich(team_cn)
    if not e:
        return {"team": team_cn, "lineup": None, "has_intel": False, "news": []}
    news = []
    for n in (e.get("news") or []):
        age = _age_hours(n.get("ts"), now_bj)
        news.append({"title": n.get("title"), "url": n.get("url"),
                     "age_h": age, "stale": age is None or age > stale_hours})
    # 新近优先: age_h 小者在前; 不可解析(None)排末
    news.sort(key=lambda x: (x["age_h"] is None,
                             x["age_h"] if x["age_h"] is not None else 0.0))
    return {"team": team_cn, "lineup": e.get("lineup"),
            "has_intel": bool(news), "news": news[:cap]}


def match_fact_card(db, match_key, now_bj, cap=5, stale_hours=48):
    """一场 → 两队最新事实卡。无此场 → teams=[]。now_bj: 北京时区 datetime(注入)。"""
    m = db.match(match_key)
    if not m:
        return {"match_key": match_key, "match": None, "teams": [], "note": "无此场"}
    home, away = m.get("home_cn"), m.get("away_cn")
    teams = [_team_card(db, t, now_bj, cap, stale_hours) for t in (home, away) if t]
    return {
        "match_key": match_key,
        "match": f"{home} vs {away}",
        "as_of_bj": now_bj.isoformat(timespec="seconds"),
        "teams": teams,
        "note": ("首发源暂缺(恒 null);新闻>%dh 或 pubDate 不可解析 标 stale;"
                 "仅 watchlist 覆盖队有情报" % stale_hours),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_intel.py -q`
Expected: PASS(4 用例全绿)。

- [ ] **Step 5: Commit**

```bash
git add backend/intel.py tests/test_intel.py
git commit -m "feat(intel): match_fact_card 事实卡装配(标龄/截最近N/标 stale)"
```

---

### Task 3: `factor_source` 顺管线持久化(测试锁定,零装配改动)

**Files:**
- Test: `tests/test_v2_predict.py`

**Interfaces:**
- Consumes(均已有):`build_v2_prediction(match_key, reliability, scenarios, markets_in)`、`apply_deviations(baseline, deviations, keys=_KEYS)`、`record_v2_prediction`/`get_v2_prediction`。
- Produces: 无新代码 —— 证明偏离字典里的 `factor_source` 随现有管线原样持久化、且不影响 `apply_deviations` 结果。

- [ ] **Step 1: Write the tests**

追加到 `tests/test_v2_predict.py`(顶部已 import 这些符号;若缺 `record_v2_prediction`/`get_v2_prediction` 则补):

```python
def test_factor_source_rides_through_build_and_store(tmp_path):
    dev = {"outcome": "a", "to": 64.0, "reason": "韩国只需平",
           "factor_source": "韩国大轮换官宣[GNews 2h]"}
    pred = build_v2_prediction("M1", "中", [], {
        "had": {"baseline": {"h": 30.0, "d": 30.0, "a": 40.0}, "deviations": [dev]}})
    assert pred["markets"]["had"]["deviations"][0]["factor_source"] == "韩国大轮换官宣[GNews 2h]"
    path = str(tmp_path / "c.db")
    record_v2_prediction(path, "M1", pred)
    got = get_v2_prediction(path, "M1")
    assert got["markets"]["had"]["deviations"][0]["factor_source"] == "韩国大轮换官宣[GNews 2h]"


def test_apply_deviations_ignores_extra_factor_source_key():
    base = {"h": 30.0, "d": 30.0, "a": 40.0}
    with_fs = apply_deviations(base, [{"outcome": "a", "to": 64.0, "factor_source": "x"}])
    without = apply_deviations(base, [{"outcome": "a", "to": 64.0}])
    assert with_fs == without   # 多余键不影响概率结果
```

- [ ] **Step 2: Run to verify they pass as-is (证明零改动)**

Run: `python3 -m pytest tests/test_v2_predict.py -q`
Expected: PASS —— 现有 `build_v2_prediction` 原样落 `deviations`、`apply_deviations` 只读 `outcome/to`,故 `factor_source` 天然穿透。若任一 FAIL,说明管线吞了多余键,需在对应函数保留整条 deviation dict(但预期不会 FAIL)。

- [ ] **Step 3: Commit**

```bash
git add tests/test_v2_predict.py
git commit -m "test(intel): 锁定 factor_source 顺管线持久化(零装配改动)"
```

---

### Task 4: 跑分卡「偏离归因」列表

**Files:**
- Modify: `tools/v2_report.py`(`collect` 捕获 deviations;`render` 列归因)
- Test: `tests/test_v2_report.py`

**Interfaces:**
- Consumes: `get_v2_prediction`(已有,记录含 `markets[market].deviations[{outcome,to,reason[,factor_source]}]`)。
- Produces: `collect` 的每个 per_match 项新增 `"deviations"` 键;`render` 各盘口节加「偏离归因」块(缺 `factor_source` 标 `⚠无因子来源`)。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_v2_report.py`:

```python
def test_render_deviation_attribution_lists_factor_source():
    collected = {
        "had": {"rows": [{"deviated": True, "v2": 0.2, "market": 0.3}],
                "per_match": [{"match_key": "M1", "reliability": "中",
                               "brier": {"v1": None, "v2": 0.2, "market": 0.3},
                               "deviations": [
                                   {"outcome": "a", "to": 64.0,
                                    "factor_source": "韩国大轮换[GNews 2h]"},
                                   {"outcome": "h", "to": 20.0}]}]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []}}
    audits = {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
              for m in ("had", "hhad", "ttg")}
    md = render(collected, audits)
    assert "**偏离归因**" in md
    assert "M1: a→64.0% · 韩国大轮换[GNews 2h]" in md
    assert "M1: h→20.0% · ⚠无因子来源" in md     # 缺 factor_source 暴露失纪律
```

- [ ] **Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_v2_report.py::test_render_deviation_attribution_lists_factor_source -q`
Expected: FAIL — 断言 `**偏离归因**` 不在输出里(render 还没这块)。

- [ ] **Step 3: Implement — collect 捕获 deviations**

在 `tools/v2_report.py` 的 `collect()` 中,把 per_match 追加块替换为(加 `devs` + `"deviations"`):

```python
            devs = ((v2rec or {}).get("markets", {}).get(market) or {}).get("deviations") or []
            out[market]["per_match"].append({"match_key": mk,
                                             "reliability": (v2rec or {}).get("reliability", ""),
                                             "brier": b, "deviations": devs})
```

- [ ] **Step 4: Implement — render 列归因**

在 `tools/v2_report.py` 的 `render()` 中,把每盘口节里"per_match 表循环 + 收尾空行"那段替换为(表后加归因块):

```python
        lines += ["| 场次 | 靠谱度 | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
        for m in data["per_match"]:
            b = m["brier"]
            lines.append(f"| {m['match_key']} | {m.get('reliability','')} | "
                         f"{_fmt(b.get('v1'))} | {_fmt(b.get('v2'))} | {_fmt(b.get('market'))} |")
        attrib = [(m["match_key"], d) for m in data["per_match"]
                  for d in m.get("deviations", [])]
        if attrib:
            lines += ["", "**偏离归因**:"]
            for mk_, d in attrib:
                fs = d.get("factor_source") or "⚠无因子来源"
                lines.append(f"- {mk_}: {d.get('outcome')}→{d.get('to')}% · {fs}")
        lines.append("")
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/test_v2_report.py -q`
Expected: PASS(新归因用例 + 现有 render/collect 用例全绿;现有用例的 per_match 无 `deviations` 键 → `m.get("deviations", [])` 为空 → 不渲染归因块,不受影响)。

- [ ] **Step 6: Commit**

```bash
git add tools/v2_report.py tests/test_v2_report.py
git commit -m "feat(intel): 跑分卡偏离归因(列 factor_source, 缺则 ⚠标记)"
```

---

### Task 5: wc-forecaster-v2 agent 升级(读事实卡 + factor_source 纪律)

**Files:**
- Modify: `.claude/agents/wc-forecaster-v2.md`

**Interfaces:** 无代码;描述与 `match_fact_card`(Task 2)接口对齐。

- [ ] **Step 1: 数据来源段加事实卡取法**

在 `.claude/agents/wc-forecaster-v2.md` 的「## 数据来源」段末尾(锚硬度那行之后)追加:

```markdown
- **本场事实卡(确证事实,用于判断偏不偏离)**:`python3 -c "from backend.db import Db; from backend.intel import match_fact_card; from datetime import datetime, timezone, timedelta; print(match_fact_card(Db('data/wc.db'), '<key>', datetime.now(timezone(timedelta(hours=8)))))"` → `{teams:[{team,lineup(恒null),has_intel,news:[{title,url,age_h,stale}]}], note}`。只读**事实**(新闻),**绝不读 v1 预测/概率/比分**(护三方 Brier 红线)。
```

- [ ] **Step 2: 每场工作流 step 2 改为吃事实卡**

把「## 每场工作流」的 step 2 整段替换为:

```markdown
2. **取本场事实卡,默认照抄每盘口基线**。仅当事实卡里有**非 stale、相关**的确证事实(如某队已官宣大轮换/主力停赛)才对某盘口提偏离;每条偏离写 `{outcome, to, reason, factor_source}`,`factor_source` 为该事实的短引用(如 `"韩国大轮换官宣[GNews 2h]"`)。`stale:true` / 无关 / `has_intel:false` → **不据此偏离**。had 的 outcome∈{h,d,a};ttg 的 outcome∈{"0".."7"}。无确证事实 → 照抄(0 偏离 = 正确)。
```

- [ ] **Step 3: 纪律段加红线**

把「## 纪律」段的「绝不编赔率/概率;缺数据降靠谱度并标注。」一行替换为:

```markdown
- 绝不编赔率/概率;缺数据/陈旧降靠谱度并标注、不偏离。
- 偏离只能由事实卡里的**确证事实**驱动;每条偏离必带 `factor_source`。**绝不读 v1(football-match-predictor)的任何预测/概率/比分**——只认中立事实(护三方 Brier 对照)。
```

- [ ] **Step 4: 全套回归确认**

Run: `python3 -m pytest -q`
Expected: PASS(doc 改动,测试数不变,全绿)。

- [ ] **Step 5: Commit**

```bash
git add .claude/agents/wc-forecaster-v2.md
git commit -m "docs(intel): wc-forecaster-v2 读事实卡 + factor_source 纪律 + v1 红线"
```

---

## 收尾验证(全部任务完成后)

- [ ] 全套测试:`python3 -m pytest -q` → 全绿(原有 + 本期新增,零回归)。
- [ ] 事实卡干跑(若 `data/wc.db` 有数据):`python3 -c "from backend.db import Db; from backend.intel import match_fact_card; from datetime import datetime,timezone,timedelta; import json; print(json.dumps(match_fact_card(Db('data/wc.db'),'周四055',datetime.now(timezone(timedelta(hours=8)))),ensure_ascii=False,indent=2))"`。
- [ ] agent 干跑(需你在场):对一场**有确证事实**的比赛派 `wc-forecaster-v2`,确认它产出**一条带 `factor_source` 的偏离**;无情报/全 stale 的场仍 0 偏离。
- [ ] 完成后按 `superpowers:finishing-a-development-branch` 决定合并/PR(分支 `feat/v2-intel-feed`)。

## Self-Review(已核)

- **Spec coverage**:§5 事实卡装配→Task 2(+ Task 1 getter);§6 v2 读卡判断+factor_source→Task 5(行为)+ Task 3(持久化锁定);§7 偏离归因→Task 4;§8 测试→各任务 TDD + 收尾;§9 红线→Global Constraints + Task 5;§11 受影响文件→逐一对应。
- **Placeholder scan**:无 TBD/TODO;每步含完整代码/命令。Task 3 是「零改动+测试锁定」,Step 2 明确"预期 PASS,若 FAIL 才需改"——非占位。
- **Type consistency**:`match_fact_card(db, match_key, now_bj, cap, stale_hours)` 返回的 `teams[].news[].{age_h,stale}` 与 Task 2 测试一致;`db.match` 返回 dict 在 Task 2 `_team_card` 前由 `match_fact_card` 消费;`collect` 新增的 `per_match[].deviations` 与 `render` 的 `m.get("deviations",[])` 一致;`factor_source` 字段名在 Task 3/4/5 一致。
