# WC Value Dashboard v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 部署在 AWS-HK、手机浏览器实时访问的双模式世界杯下注价值看板 v1（拉足彩+Polymarket → 算去水价值 → 价值雷达/特别关注/账本/报告/停售倒计时）。

**Architecture:** 单台 HK VPS 直连两平台（无代理）。Python 后端：poller 定时拉盘 → 存 SQLite → value-engine 算 `足彩赔率×去水Poly概率`。FastAPI 暴露 `/state` JSON + serve 单页前端 + `/reports` markdown。前端每 N 秒轮询 `/state` 自刷新。docker-compose 部署。

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, SQLite(stdlib sqlite3), pytest, 原生 HTML/JS, markdown-it(前端渲染), docker-compose.

**并行说明：** Task 1-3 为地基（顺序，锁定契约：DB schema / value 引擎 / /state 形状）。Task 4-9 可并行（文件互不重叠，各自对地基契约编程）。Task 10 整合验证。

---

## 文件结构（决定分解）

```
backend/
  config.py        # 读 config.toml, 默认值
  db.py            # SQLite schema + CRUD (契约: 表结构)
  models.py        # dataclass: ZucaiMatch/PolyProbs/ValuePoint (契约: 类型)
  devig.py         # 去抽水
  value.py         # 价值计算(吸收 wc_value.py) + de-vig
  sporttery.py     # 足彩 client
  polymarket.py    # Polymarket client + 配对
  poller.py        # 轮询编排: 拉两边→配对→算value→写库
  state.py         # 组装 /state JSON (契约: 输出形状)
  web.py           # FastAPI: /state /reports /bets /watchlist
  reports.py       # 读 reports/*.md
frontend/
  index.html  app.js  style.css
tests/
  test_devig.py test_value.py test_sporttery.py test_polymarket.py
  test_poller.py test_state.py test_web.py
  fixtures/  zucai_sample.json  poly_esp_cvi.json  poly_more.json
config.example.toml
docker-compose.yml  Dockerfile  requirements.txt
```

---

## 契约（所有任务共享，Task 1-3 锁定）

**DB 表（db.py）：**
- `matches(id, zucai_num, home_cn, away_cn, home_en, away_en, poly_slug, ko_bj, cutoff_bj, status)`
- `odds_snapshots(id, match_id, ts, source, payload_json)` source∈{zucai,poly}
- `value_points(id, match_id, ts, market, outcome, zucai_odds, poly_prob_raw, poly_prob_devig, value_raw, value_devig, ev_pct, flag)` flag∈{green,yellow,skip}
- `watchlist(id, kind, key, note)` kind∈{team,match,player}
- `bets(id, ts, wallet, legs_json, stake, odds, status, payout, note)` wallet∈{A,B}
- `app_config(key, value)`

**/state JSON（state.py 输出）：**
```json
{
  "ts": "2026-06-15T20:30:00+08:00",
  "next_cutoff": {"match": "西班牙 vs 佛得角", "cutoff_bj": "23:00", "countdown_sec": 8950},
  "value_radar": [
    {"match":"西班牙 vs 佛得角","ko_bj":"6.16 00:00","market":"让-2","outcome":"平",
     "zucai_odds":4.55,"poly_prob_devig":22.3,"poly_prob_raw":23.0,
     "ev_pct_devig":1.5,"ev_pct_raw":4.6,"flag":"yellow","cutoff_bj":"23:00"}
  ],
  "watchlist": [{"kind":"team","key":"西班牙","note":"","matches":[],"lineup":null,"news":[]}],
  "ledger": {"A":{"stake":0,"pnl":0,"roi":0,"n":0},"B":{"budget":100,"spent":0,"pnl":0,"n":0}},
  "matches_today": []
}
```

**核心类型（models.py）：**
```python
from dataclasses import dataclass, field

@dataclass
class ZucaiMatch:
    zucai_num: str; home_cn: str; away_cn: str
    ko_bj: str; cutoff_bj: str
    had: dict | None      # {"h":1.41,"d":3.92,"a":6.05} or None
    hhad: dict | None     # {"line":-1,"h":2.35,"d":3.40,"a":2.44}
    ttg: dict             # {0:38.0,1:9.8,...,7:7.0}

@dataclass
class PolyProbs:
    slug: str; home_en: str; away_en: str
    ml: dict              # {"home":91.5,"draw":6.5,"away":2.5} (raw %)
    home_cover: dict      # {1.5:78.5, 2.5:55.5, ...}
    away_cover: dict      # {1.5:..,2.5:..}
    ou_over: dict         # {0.5:98.4,1.5:90.5,...}

@dataclass
class ValuePoint:
    market: str; outcome: str; zucai_odds: float
    poly_prob_raw: float; poly_prob_devig: float
    value_raw: float; value_devig: float; ev_pct: float; flag: str
```

---

## Task 1: 项目骨架 + config + 测试基建

**Files:**
- Create: `requirements.txt`, `backend/__init__.py`, `backend/config.py`, `config.example.toml`, `tests/__init__.py`, `pytest.ini`

- [ ] **Step 1: requirements.txt**
```
fastapi==0.115.*
uvicorn[standard]==0.32.*
httpx==0.27.*
tomli==2.*; python_version < "3.11"
pytest==8.*
```

- [ ] **Step 2: config.example.toml**
```toml
[server]
host = "0.0.0.0"
port = 8000
password = "change-me"

[poll]
interval_sec = 180
zucai_pools = "had,hhad,ttg"

[poly]
tag_id = "102232"
gamma_base = "https://gamma-api.polymarket.com"

[zucai]
api = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148"

[value]
devig_yellow_below = 1.03   # 去水 value < 此值标 yellow

[wallet]
A_unit_pct = 1.5
B_weekly_budget = 100
```

- [ ] **Step 3: 写 test_config 失败测试** `tests/test_config.py`
```python
from backend.config import load_config
def test_load_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.toml")  # 缺文件→默认
    assert cfg["poll"]["interval_sec"] == 180
    assert cfg["poly"]["tag_id"] == "102232"
```

- [ ] **Step 4: 运行验证失败** `pytest tests/test_config.py -v` → FAIL (no module)

- [ ] **Step 5: backend/config.py**
```python
import tomllib, pathlib
DEFAULTS = {
  "server": {"host":"0.0.0.0","port":8000,"password":"change-me"},
  "poll": {"interval_sec":180,"zucai_pools":"had,hhad,ttg"},
  "poly": {"tag_id":"102232","gamma_base":"https://gamma-api.polymarket.com"},
  "zucai": {"api":"https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry",
            "ua":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148"},
  "value": {"devig_yellow_below":1.03},
  "wallet": {"A_unit_pct":1.5,"B_weekly_budget":100},
}
def load_config(path="config.toml"):
    p = pathlib.Path(path)
    cfg = {k: dict(v) for k,v in DEFAULTS.items()}
    if p.exists():
        user = tomllib.loads(p.read_text())
        for k,v in user.items(): cfg.setdefault(k,{}).update(v)
    return cfg
```

- [ ] **Step 6: 运行通过** `pytest tests/test_config.py -v` → PASS

- [ ] **Step 7: Commit** `git add -A && git commit -m "feat: 项目骨架 + config 加载"`

---

## Task 2: DB schema (db.py)

**Files:** Create `backend/db.py`, `tests/test_db.py`

- [ ] **Step 1: 失败测试** `tests/test_db.py`
```python
from backend.db import Db
def test_init_and_upsert_match(tmp_path):
    db = Db(tmp_path/"t.db"); db.init()
    mid = db.upsert_match(zucai_num="周一013", home_cn="西班牙", away_cn="佛得角",
        home_en="Spain", away_en="Cabo Verde", poly_slug="fifwc-esp-cvi-2026-06-15",
        ko_bj="6.16 00:00", cutoff_bj="23:00")
    rows = db.matches()
    assert rows[0]["home_cn"] == "西班牙" and rows[0]["poly_slug"].startswith("fifwc-")
def test_add_bet_and_ledger(tmp_path):
    db = Db(tmp_path/"t.db"); db.init()
    db.add_bet(wallet="A", legs=[{"m":"x","o":"平"}], stake=5, odds=4.55)
    led = db.ledger()
    assert led["A"]["n"] == 1 and led["A"]["stake"] == 5
```

- [ ] **Step 2: 验证失败** `pytest tests/test_db.py -v` → FAIL

- [ ] **Step 3: backend/db.py**（用 stdlib sqlite3，建上面 6 张表；方法：`init()`, `upsert_match(...)`, `matches()`, `save_snapshot(match_id,source,payload)`, `save_value_points(match_id,points)`, `value_points()`, `add_bet(...)`, `settle_bet(id,status,payout)`, `ledger()`→{A,B 各 stake/pnl/roi/n}, `watchlist()`, `add_watch(kind,key,note)`, `del_watch(id)`, `get_config/set_config`）。`row_factory=sqlite3.Row`，方法返回 dict。`ledger()`: pnl = Σ(payout-stake for settled) ，roi = pnl/stake。
- [ ] **Step 4: 验证通过** `pytest tests/test_db.py -v` → PASS
- [ ] **Step 5: Commit** `git commit -am "feat: SQLite schema + CRUD"`

---

## Task 3: 去水 + 价值引擎 (devig.py, value.py, models.py)

**Files:** Create `backend/models.py`, `backend/devig.py`, `backend/value.py`, `tests/test_devig.py`, `tests/test_value.py`

- [ ] **Step 1: models.py** — 粘贴上面"核心类型"三个 dataclass。
- [ ] **Step 2: 失败测试 test_devig.py**
```python
from backend.devig import devig
def test_devig_normalizes():
    out = devig({"home":61.5,"draw":23.5,"away":15.5})  # sum 100.5
    assert abs(sum(out.values())-100) < 1e-6
    assert abs(out["home"]-61.5/1.005*1) < 0.05  # ~61.2
```
- [ ] **Step 3: 验证失败** `pytest tests/test_devig.py -v` → FAIL
- [ ] **Step 4: backend/devig.py**
```python
def devig(probs: dict) -> dict:
    """乘法归一化到 100%（只用于 Poly 概率，单位 %）。"""
    s = sum(probs.values())
    if s <= 0: return dict(probs)
    return {k: v/s*100 for k,v in probs.items()}
```
- [ ] **Step 5: 验证通过** → PASS
- [ ] **Step 6: 失败测试 test_value.py**（用西佛真实数据，校验已知结果）
```python
from backend.models import ZucaiMatch, PolyProbs
from backend.value import compute_value
def test_esp_cvi_known():
    z = ZucaiMatch("周一013","西班牙","佛得角","6.16 00:00","23:00",
        had=None, hhad={"line":-2,"h":1.54,"d":4.55,"a":3.85},
        ttg={0:38,1:9.8,2:5.55,3:4.1,4:4.1,5:5.5,6:7.5,7:7.0})
    p = PolyProbs("fifwc-esp-cvi-2026-06-15","Spain","Cabo Verde",
        ml={"home":91.5,"draw":6.5,"away":2.5},
        home_cover={1.5:78.5,2.5:55.5,3.5:33.5,4.5:23.5}, away_cover={},
        ou_over={0.5:98.4,1.5:90.5,2.5:73.5,3.5:50.5,4.5:31.5,5.5:18.5})
    pts = compute_value(z,p)
    rang2 = next(x for x in pts if x.market=="让-2" and x.outcome=="平")
    assert abs(rang2.poly_prob_raw-23.0) < 0.1        # 78.5-55.5
    assert abs(rang2.value_raw-1.046) < 0.01
    assert rang2.value_devig < rang2.value_raw        # 去水后更低
    # 高分桶缺线→跳过
    assert not any(x.market=="总进球" and x.outcome=="7+球" and x.flag!="skip" for x in pts)
```
- [ ] **Step 7: 验证失败** → FAIL
- [ ] **Step 8: backend/value.py** — 吸收 `prototype/wc_value.py` 的映射（had/hhad 让球机制/ttg 派生桶，见 spec §5）。新增：① 对 had 三路、每条 spread 两路、每条 O/U 两路先 `devig` 再用于派生；② 每个 ValuePoint 同时算 `value_raw`(用生概率) 与 `value_devig`(用去水概率)；③ ev_pct 用 value_devig；④ flag: value_devig≥1.03→green, ≥cfg阈值→实为 ≥1.0→… 规则：`value_devig>=1.03→green; 0.97<=value_devig<1.03→yellow; 高分桶缺线→skip`；⑤ 缺高 O/U 线的 6/7+ 桶 flag=skip 不计 EV。函数签名 `compute_value(z:ZucaiMatch, p:PolyProbs, yellow_below=1.03)->list[ValuePoint]`。
- [ ] **Step 9: 验证通过** `pytest tests/test_value.py -v` → PASS
- [ ] **Step 10: Commit** `git commit -am "feat: de-vig + 价值引擎(吸收原型)"`

---

## Task 4: 足彩 client (sporttery.py)  〔可并行〕

**Files:** Create `backend/sporttery.py`, `tests/test_sporttery.py`, `tests/fixtures/zucai_sample.json`

- [ ] **Step 1:** 把一次真实响应存 `tests/fixtures/zucai_sample.json`（含西佛/比埃等，had/hhad/ttg 字段）。
- [ ] **Step 2: 失败测试**
```python
import json
from backend.sporttery import parse_matches
def test_parse():
    data = json.load(open("tests/fixtures/zucai_sample.json"))
    ms = parse_matches(data)
    esp = next(m for m in ms if m.home_cn=="西班牙")
    assert esp.had is None and esp.hhad["line"]==-2 and esp.hhad["h"]==1.54
    assert esp.ttg[2]==5.55
```
- [ ] **Step 3: 验证失败** → FAIL
- [ ] **Step 4: backend/sporttery.py** — `fetch(cfg)->dict`（httpx GET，带 UA+Referer，poolCode=had,hhad,ttg）；`parse_matches(data)->list[ZucaiMatch]`：遍历 `value.matchInfoList[].subMatchList[]`，取 had/hhad(含 goalLine→int line)/ttg(s0..s7→{0..7})，homeTeamAbbName/awayTeamAbbName→cn，matchTime/matchDate→ko_bj，停售时间→cutoff_bj（v1 可先用开球时间-1h 占位或留空）。空 dict 字段→None。
- [ ] **Step 5: 验证通过** → PASS
- [ ] **Step 6: Commit** `git commit -am "feat: 足彩 client + 解析"`

---

## Task 5: Polymarket client + 配对 (polymarket.py)  〔可并行〕

**Files:** Create `backend/polymarket.py`, `tests/test_polymarket.py`, `tests/fixtures/poly_esp_cvi.json`, `tests/fixtures/poly_more.json`, `backend/teammap.py`

- [ ] **Step 1:** 存两个 fixture（base event 的 markets / more-markets 的 spread+O/U）。
- [ ] **Step 2: teammap.py** — `CN2EN` 字典（48 队，复用 `prototype/wc_value.py` 里的 CN2EN）。
- [ ] **Step 3: 失败测试**
```python
import json
from backend.polymarket import parse_probs
def test_parse_probs():
    base=json.load(open("tests/fixtures/poly_esp_cvi.json"))
    more=json.load(open("tests/fixtures/poly_more.json"))
    p = parse_probs(base, more, "Spain", "Cabo Verde")
    assert abs(p.ml["home"]-91.5)<1 and abs(p.home_cover[2.5]-55.5)<1
    assert abs(p.ou_over[2.5]-73.5)<1
```
- [ ] **Step 4: 验证失败** → FAIL
- [ ] **Step 5: backend/polymarket.py** — `list_events(cfg)->dict{slug:title}`（tag_id 翻页 offset 0/100/200，filter slug 前缀 fifwc- 且非 more-markets）；`find_slug(idx,home_en,away_en)`（title 含两队子串）；`fetch_event(cfg,slug)`；`parse_probs(base,more,home_en,away_en)->PolyProbs`：base 取 "Will X win"/"draw"→ml（按 home_en/away_en 子串归位），more 取 `Spread: TEAM (-X.5)`→home_cover/away_cover、`: O/U X.5`→ou_over（解析逻辑见 prototype/wc_value.py poly_probs）。
- [ ] **Step 6: 验证通过** → PASS
- [ ] **Step 7: Commit** `git commit -am "feat: Polymarket client + 对阵配对"`

---

## Task 6: poller 编排 (poller.py)  〔依赖 2-5〕

**Files:** Create `backend/poller.py`, `tests/test_poller.py`

- [ ] **Step 1: 失败测试**（注入假 client，验证一轮后库里有 match+value_points）
```python
from backend.poller import poll_once
def test_poll_once(tmp_path, monkeypatch):
    # monkeypatch sporttery.fetch/parse_matches 与 polymarket.* 返回 fixture 解析结果
    from backend.db import Db
    db=Db(tmp_path/"t.db"); db.init()
    n = poll_once(db, cfg_stub, fake_zucai=[...], fake_poly_idx={...}, fake_probs={...})
    assert n>=1 and len(db.value_points())>0
```
- [ ] **Step 2: 验证失败** → FAIL
- [ ] **Step 3: backend/poller.py** — `poll_once(db,cfg)`: 拉足彩→parse；拉 poly 列表；对每场 find_slug→fetch_event→parse_probs→compute_value→`db.upsert_match`+`db.save_snapshot`+`db.save_value_points`；返回处理场数。未配对/无 poly 的场记 warning 跳过。`run_loop(db,cfg)`: while True: poll_once; sleep(interval)。测试用依赖注入（fetch 函数作参数，默认真实实现）以便 mock。
- [ ] **Step 4: 验证通过** → PASS
- [ ] **Step 5: Commit** `git commit -am "feat: poller 编排"`

---

## Task 7: /state 组装 (state.py)  〔依赖 2〕可并行

**Files:** Create `backend/state.py`, `tests/test_state.py`

- [ ] **Step 1: 失败测试** — 库里塞 value_points+watchlist+bets，调 `build_state(db,cfg,now)` 校验输出含 value_radar(按 ev 降序)、next_cutoff、ledger 形状（对照上面 /state 契约）。
- [ ] **Step 2: 验证失败** → FAIL
- [ ] **Step 3: backend/state.py** — `build_state(db,cfg,now_bj)->dict`：value_radar 从最新 value_points 取 flag∈{green,yellow} 按 ev_pct 降序；next_cutoff 取未停售里最近 cutoff_bj + 倒计时秒；ledger 从 db.ledger()；watchlist 关联各 pin 的场次/首发/新闻（v1 首发/新闻字段可空，Task9 填）。
- [ ] **Step 4: 验证通过** → PASS
- [ ] **Step 5: Commit** `git commit -am "feat: /state 组装"`

---

## Task 8: FastAPI web (web.py, reports.py)  〔依赖 7〕

**Files:** Create `backend/web.py`, `backend/reports.py`, `tests/test_web.py`

- [ ] **Step 1: 失败测试**（TestClient）
```python
from fastapi.testclient import TestClient
from backend.web import create_app
def test_state_and_reports(tmp_path):
    app=create_app(db_path=tmp_path/"t.db")
    c=TestClient(app)
    assert c.get("/api/state").status_code==200
    assert "value_radar" in c.get("/api/state").json()
    assert c.get("/api/reports").status_code==200   # 列表
```
- [ ] **Step 2: 验证失败** → FAIL
- [ ] **Step 3: reports.py** — `list_reports()`(reports/*.md 文件名+标题), `read_report(name)`(返回 md 文本)。
- [ ] **Step 4: web.py** — `create_app(...)`: FastAPI；路由 `GET /api/state`→build_state；`GET /api/reports`/`GET /api/reports/{name}`；`POST /api/bets`(记账)；`GET/POST/DELETE /api/watchlist`；basic-auth 依赖（密码来自 cfg）；StaticFiles 挂 `frontend/` 到 `/`。
- [ ] **Step 5: 验证通过** → PASS
- [ ] **Step 6: Commit** `git commit -am "feat: FastAPI /api/* + 静态前端挂载 + reports"`

---

## Task 9: 前端单页 + 特别关注/账本交互 (frontend/)  〔依赖 8 的 API 契约〕可并行

**Files:** Create `frontend/index.html`, `frontend/app.js`, `frontend/style.css`

- [ ] **Step 1: index.html** — 面板骨架：顶栏(倒计时) / 🎯价值雷达 / ⭐特别关注 / 📒账本 / 📄报告(tab)。引 markdown-it CDN。移动端 viewport + 响应式。
- [ ] **Step 2: app.js** — `fetchState()` 每 `interval` 拉 `/api/state` 渲染：倒计时本地 tick；价值雷达表(🟢/🟡, 生/去水 value 双列, 点开详情)；特别关注(pin 列表 + 首发 + 新闻链接 a[target=_blank])；账本(A/B 分列)；报告 tab 拉 `/api/reports/{name}` 用 markdown-it 渲染。记账表单 POST `/api/bets`，pin 操作 POST/DELETE `/api/watchlist`。
- [ ] **Step 3: style.css** — 深色、移动优先、🟢绿/🟡黄标、卡片式。
- [ ] **Step 4: 手测** — 本地起后端 `uvicorn backend.web:create_app --factory`，浏览器开 `localhost:8000`，确认五面板渲染 + 倒计时走动 + 报告 tab 显示 `小组赛比分预测.md`。
- [ ] **Step 5: Commit** `git commit -am "feat: 前端单页 + 五面板 + 记账/pin 交互"`

---

## Task 10: 容器化 + 端到端验证 + 部署文档  〔整合〕

**Files:** Create `Dockerfile`, `docker-compose.yml`, modify `README.md`

- [ ] **Step 1: Dockerfile** — python:3.12-slim，装 requirements，CMD uvicorn 起 web，挂 reports/ 与 data volume(SQLite)。
- [ ] **Step 2: docker-compose.yml** — service: app(端口映射 8000)，env(password)，volume(./data,./reports)；后台跑 poller(同容器 startup 起 poll loop 线程，或独立 service)。
- [ ] **Step 3: 端到端冒烟** — `docker compose up -d`；`curl localhost:8000/api/state` 返回真实数据(真连两平台一轮)；浏览器开看板确认价值雷达有今日真实可投点、报告 tab 正常。
- [ ] **Step 4: README** — 补：本地开发/测试命令、docker 部署、HK 上 `git pull && docker compose up -d`、口令设置、config.toml 来自 example。
- [ ] **Step 5: Commit** `git commit -am "feat: docker 部署 + 端到端验证 + README"`

---

## Self-Review（写完核对 spec 覆盖）

- spec §3 架构 → Task 1/10 ✓；§4 数据源 → Task 4/5 ✓；§5+§5.1 价值+去水 → Task 3 ✓；§6 v1 面板(倒计时/价值雷达/特别关注/账本/报告) → Task 7/8/9 ✓；§7 watchlist(队/场/人) → Task 2(表)+9(UI) ✓；§9 记账 → Task 2/8/9 ✓；§10 技术栈 → 全程 ✓；§11 仓库结构 → 文件结构 ✓。
- v2(分歧告警/LLM 叠加/构造器/追损检测) 明确不在本计划，留 v2。
- 类型一致：ZucaiMatch/PolyProbs/ValuePoint 在 Task3 定义，Task4/5/6/7 引用同名字段。
- 球员 pin 的首发/新闻数据源（WebSearch+LLM）v1 仅留 watchlist 表+UI 占位，实际抓取属 v2 lineup-watcher；v1 特别关注先支持 队/场/人 的 pin 与展示静态信息（价值点+倒计时），首发/新闻 v2 接入。
