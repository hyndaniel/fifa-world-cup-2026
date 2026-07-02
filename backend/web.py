"""FastAPI 应用: /api/* + 静态前端挂载。

create_app(db_path=..., cfg=None, reports_dir="reports", frontend_dir="frontend",
           data_dir="data", require_auth=None) -> FastAPI

路由:
- GET    /api/state            → backend.state.build_state(db, cfg, now=北京时间)
- GET    /api/reports          → backend.reports.list_reports()  (列表)
- GET    /api/reports/{name}   → backend.reports.read_report()   ({name,title,content})
- POST   /api/bets             → db.add_bet(wallet, legs, stake, odds, note)
- GET    /api/bets/summary     → backend.bet_stats.build_summary(load_ledger(data_dir))
- GET    /api/watchlist        → db.watchlist()
- POST   /api/watchlist        → db.add_watch(kind, key, note)
- DELETE /api/watchlist/{id}   → db.del_watch(id)
- POST   /api/ingest/tickets   → backend.bet_stats.save_ledger()   (台账整份/分段落盘, 绕开git)
- POST   /api/ingest/reports   → backend.reports.write_report()    (报告md落盘, 绕开git)
- /                            → StaticFiles(frontend, html=True)

Basic-Auth 依赖: 密码取 cfg["server"]["password"]。关掉鉴权 (测试用):
  - 传 require_auth=False, 或
  - 环境变量 WC_DASHBOARD_NO_AUTH=1, 或
  - cfg["server"]["password"] 为空。
鉴权开启时, 仅 /api/* 需要 (静态资源放行); 用户名任意, 比对密码。
"""
import logging
import os
import secrets
import threading

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import load_config
from backend.db import Db
from backend import bet_stats
from backend import decision_contract
from backend import poller
from backend import reports as reports_mod
from backend.state import BJ, build_state, datetime, decisions_view

log = logging.getLogger(__name__)


class BetIn(BaseModel):
    wallet: str
    legs: list = []
    stake: float
    odds: float
    note: str = ""


class WatchIn(BaseModel):
    kind: str
    key: str
    note: str = ""


class PredictionsIn(BaseModel):
    # 宽松: decisions 为 Decision dict 列表 (未知字段透传); ts 选填。
    decisions: list = []
    ts: str = ""


def _auth_enabled(cfg, require_auth):
    if require_auth is not None:
        return bool(require_auth)
    if os.environ.get("WC_DASHBOARD_NO_AUTH") == "1":
        return False
    pw = (cfg.get("server", {}) or {}).get("password") if cfg else None
    return bool(pw)


def create_app(db_path="wc.db", cfg=None, reports_dir="reports",
               frontend_dir="frontend", data_dir="data", require_auth=None) -> FastAPI:
    if cfg is None:
        cfg = load_config()

    db = Db(db_path)
    db.init()

    password = str((cfg.get("server", {}) or {}).get("password") or "")
    auth_on = _auth_enabled(cfg, require_auth)

    app = FastAPI(title="WC Value Dashboard")
    app.state.db = db
    app.state.cfg = cfg

    security = HTTPBasic(auto_error=auth_on)

    def auth_dep(creds: HTTPBasicCredentials = Depends(security)):
        if not auth_on:
            return
        ok = creds is not None and secrets.compare_digest(
            (creds.password or ""), password
        )
        if not ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )

    # ingest/refresh 都可能后台跑 poll_once; 加锁串行化, 防两轮并发对
    # value_points 互相 DELETE+INSERT + SQLite 写锁竞争
    _poll_lock = threading.Lock()

    def _run_poll(raw_body, label):
        with _poll_lock:
            try:
                poller.poll_once(db, cfg, zucai_fetch=lambda: raw_body)
            except Exception:  # noqa: BLE001
                log.exception("%s poll_once 失败", label)

    # ---------------- /api/state ----------------
    @app.get("/api/state", dependencies=[Depends(auth_dep)])
    def api_state():
        now_bj = datetime.now(BJ)
        return build_state(db, cfg, now_bj)

    # ---------------- /api/reports ----------------
    @app.get("/api/reports", dependencies=[Depends(auth_dep)])
    def api_reports():
        return reports_mod.list_reports(reports_dir)

    @app.get("/api/reports/{name}", dependencies=[Depends(auth_dep)])
    def api_report(name: str):
        try:
            content = reports_mod.read_report(name, reports_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid report name")
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="report not found")
        title = name
        for line in content.splitlines():
            s = line.strip()
            if s.startswith("# "):
                title = s[2:].strip()
                break
        return {"name": name, "title": title, "content": content}

    # ---------------- /api/bets ----------------
    @app.post("/api/bets", dependencies=[Depends(auth_dep)])
    def api_add_bet(bet: BetIn):
        bid = db.add_bet(
            wallet=bet.wallet, legs=bet.legs, stake=bet.stake,
            odds=bet.odds, note=bet.note,
        )
        return {"id": bid}

    # ---------------- /api/watchlist ----------------
    @app.get("/api/watchlist", dependencies=[Depends(auth_dep)])
    def api_watchlist():
        return db.watchlist()

    @app.post("/api/watchlist", dependencies=[Depends(auth_dep)])
    def api_add_watch(w: WatchIn):
        wid = db.add_watch(kind=w.kind, key=w.key, note=w.note)
        return {"id": wid}

    @app.delete("/api/watchlist/{wid}", dependencies=[Depends(auth_dep)])
    def api_del_watch(wid: int):
        db.del_watch(wid)
        return {"ok": True}

    # ---------------- /api/ingest/zucai ----------------
    # 住宅端采集脚本 POST 足彩 getMatchCalculatorV1 的原始 JSON 到这里 (HK 机房自身
    # 被足彩 WAF 拦, 故由住宅 IP 采集后中转)。收到即后台跑 poll_once(注入此数据) →
    # 配 Polymarket(HK 直连) 算价值入库。即时返回受理的场数。
    @app.post("/api/ingest/zucai", dependencies=[Depends(auth_dep)])
    async def api_ingest_zucai(request: Request):
        body = await request.json()
        n = 0
        try:
            for day in (body.get("value") or {}).get("matchInfoList") or []:
                n += len(day.get("subMatchList") or [])
        except Exception:  # noqa: BLE001
            n = 0

        # 另存一份原始信封 (source='zucai_raw'), 供 /api/refresh 回放: refresh 经
        # poll_once → parse_matches 需要原始 {value:{matchInfoList:...}} 形状, 而 poller
        # 每场存的 source='zucai' 是已解析的 ZucaiMatch dict (parse_matches 吃不动)。
        db.save_snapshot(0, "zucai_raw", body)

        threading.Thread(
            target=_run_poll, args=(body, "ingest"), daemon=True
        ).start()
        return {"accepted": True, "matches": n}

    # ---------------- /api/ingest/enrich ----------------
    # Mac 采集脚本 POST 各队阵容/新闻 (Google News RSS 等) 到这里, 入 enrich 表
    # (每队一行, 替换语义)。state.build_state 会把它挂到 watchlist 各项上。
    @app.post("/api/ingest/enrich", dependencies=[Depends(auth_dep)])
    async def api_ingest_enrich(request: Request):
        body = await request.json()
        items = (body or {}).get("items") or []
        n = 0
        for item in items:
            db.save_enrich(item["team"], item.get("lineup"), item.get("news") or [])
            n += 1
        return {"accepted": True, "teams": n}

    # ---------------- /api/ingest/predictions ----------------
    # 本地 /跑今天 skill 把 v1 比分 + v2 概率 + 价值结论汇成"决策对象"列表 POST 来,
    # 看板按 match_key 原样 upsert 存 (替换语义), 不删本批未出现的旧卡。缺 match_key
    # 的条目跳过并计入 skipped。
    @app.post("/api/ingest/predictions", dependencies=[Depends(auth_dep)])
    def api_ingest_predictions(body: PredictionsIn):
        decisions = body.decisions or []
        total = len(decisions)
        # 软校验(decision_contract): 只 warn 不拒收, 让字段脱节在日志可见,
        # 而不是看板静默空一块
        warnings = 0
        for d in decisions:
            for w in decision_contract.validate_decision(d):
                log.warning("ingest/predictions 契约警告: %s", w)
                warnings += 1
        n = db.save_decisions(decisions)
        return {"accepted": True, "n": n, "skipped": total - n,
                "contract_warnings": warnings}

    # ---------------- /api/ingest/odds ----------------
    # 本地 refresh_all POST 对齐好的赔率面板 payload(三源现价+变化+分歧), 按 match_key
    # upsert 存; /api/decisions join 进卡。纯展示, 不进决策/价值计算。
    @app.post("/api/ingest/odds", dependencies=[Depends(auth_dep)])
    async def api_ingest_odds(request: Request):
        body = await request.json()
        items = (body or {}).get("items") or []
        n = db.save_odds(items)
        return {"accepted": True, "n": n, "skipped": len(items) - n}

    # ---------------- /api/ingest/tickets ----------------
    # 本地维护的下注台账 bet_ledger.json POST 到这里, 直接落盘 data_dir/bet_ledger.json——
    # 绕开 git commit/PR/部署那套, bet_stats.load_ledger 下次请求现读即生效。按顶层字段
    # 覆盖(updated/recommendations/tickets/people/ticket_note 谁给了就覆盖谁), 未给的
    # 字段保留服务器上原值, 防止漏发某个字段时把它整段清空。
    @app.post("/api/ingest/tickets", dependencies=[Depends(auth_dep)])
    async def api_ingest_tickets(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="payload must be a JSON object")
        for key in ("recommendations", "tickets", "people"):
            if key in body and not isinstance(body[key], list):
                raise HTTPException(status_code=400, detail=f"{key} must be a list")
        current = bet_stats.load_ledger(data_dir)
        for key in ("updated", "recommendations", "tickets", "people", "ticket_note"):
            if key in body:
                current[key] = body[key]
        bet_stats.save_ledger(current, data_dir)
        return {
            "accepted": True,
            "tickets": len(current.get("tickets") or []),
            "recommendations": len(current.get("recommendations") or []),
        }

    # ---------------- /api/ingest/reports ----------------
    # 本地报告(预测/复盘等 markdown)POST 到这里直接落 reports_dir——同样绕开 git/部署,
    # reports/*.md 是 bind-mount + 每次请求现读, 落盘即生效。name 可含子目录(如
    # "agents/wc-bet__下注复盘")定位新报告该落哪个子目录; 已存在的报告按该路径覆盖
    # 原内容。落盘后顺带刷新 report_times.json 的时间戳(这条链路不走 git commit, 排序
    # 不能再指望"构建期清单靠 git 提交时间生成"那一环, 得在 ingest 时直接写)。
    @app.post("/api/ingest/reports", dependencies=[Depends(auth_dep)])
    async def api_ingest_reports(request: Request):
        body = await request.json()
        items = (body or {}).get("reports") or []
        n = 0
        errors = []
        for item in items:
            name = (item or {}).get("name")
            content = (item or {}).get("content")
            if not name or content is None:
                errors.append({"name": name, "error": "missing name/content"})
                continue
            try:
                stem = reports_mod.write_report(name, content, reports_dir)
                reports_mod.bump_time(stem, reports_dir)
                n += 1
            except ValueError as e:
                errors.append({"name": name, "error": str(e)})
        return {"accepted": True, "n": n, "errors": errors}

    # ---------------- /api/decisions ----------------
    # 前端"每场决策卡"据此渲染。按 ko_bj 升序 (缺末尾), 附服务端当前北京时间 ts。
    # include_expired=True: 已结束场也返回(tag view_status=expired), 供前端"全部"筛选展示;
    # 默认"未结束"筛选客户端只显示 upcoming, 故不污染默认视图。
    @app.get("/api/decisions", dependencies=[Depends(auth_dep)])
    def api_decisions():
        now_bj = datetime.now(BJ)
        return {
            "ts": now_bj.isoformat(timespec="seconds"),
            "decisions": decisions_view(
                db.get_decisions(), now_bj, odds_map=db.get_odds(), include_expired=True
            ),
        }

    # ---------------- /api/bets/summary ----------------
    # 下注统计面板据此渲染: 推荐腿战绩(命中率/分档) + 实购票盈亏(ROI)。
    # 只读 data/bet_ledger.json(手整台账镜像), 文件缺失返回空 summary。
    @app.get("/api/bets/summary", dependencies=[Depends(auth_dep)])
    def api_bets_summary():
        return bet_stats.build_summary(bet_stats.load_ledger(data_dir))

    # ---------------- /api/refresh ----------------
    # 价值"重抓+刷新"按钮用: 取最新一条 source='zucai_raw' 的原始信封 (由 /api/ingest/zucai
    # 落), 后台经 poll_once(注入此原始 body) 重跑 —— poll_once 内部 parse_matches 吃原始信封
    # + 重连 Poly 刷去水 → 重算 value_points (治"陈旧 Poly 假黄档")。
    # 注意: 绝不能用 source='zucai'(那是 poller 每场存的已解析 ZucaiMatch dict, parse_matches
    # 吃不动 → 静默空跑)。无原始信封 → {accepted: false, reason}, 不抛错。
    @app.post("/api/refresh", dependencies=[Depends(auth_dep)])
    def api_refresh():
        snapshot = db.latest_snapshot("zucai_raw")
        if snapshot is None:
            return {"accepted": False, "reason": "no zucai snapshot"}
        if _poll_lock.locked():
            # 已有一轮在跑, 重复触发只会对 value_points 做重复 DELETE+INSERT
            return {"accepted": False, "reason": "poll already running"}

        threading.Thread(
            target=_run_poll, args=(snapshot, "refresh"), daemon=True
        ).start()
        return {"accepted": True}

    # ---------------- static frontend ----------------
    if os.path.isdir(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
