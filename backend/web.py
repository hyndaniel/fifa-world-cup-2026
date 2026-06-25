"""FastAPI 应用: /api/* + 静态前端挂载。

create_app(db_path=..., cfg=None, reports_dir="reports", frontend_dir="frontend",
           require_auth=None) -> FastAPI

路由:
- GET    /api/state            → backend.state.build_state(db, cfg, now=北京时间)
- GET    /api/reports          → backend.reports.list_reports()  (列表)
- GET    /api/reports/{name}   → backend.reports.read_report()   ({name,title,content})
- POST   /api/bets             → db.add_bet(wallet, legs, stake, odds, note)
- GET    /api/watchlist        → db.watchlist()
- POST   /api/watchlist        → db.add_watch(kind, key, note)
- DELETE /api/watchlist/{id}   → db.del_watch(id)
- /                            → StaticFiles(frontend, html=True)

Basic-Auth 依赖: 密码取 cfg["server"]["password"]。关掉鉴权 (测试用):
  - 传 require_auth=False, 或
  - 环境变量 WC_DASHBOARD_NO_AUTH=1, 或
  - cfg["server"]["password"] 为空。
鉴权开启时, 仅 /api/* 需要 (静态资源放行); 用户名任意, 比对密码。
"""
import os
import secrets
import threading

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import load_config
from backend.db import Db
from backend import poller
from backend import reports as reports_mod
from backend.state import BJ, build_state, datetime


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
               frontend_dir="frontend", require_auth=None) -> FastAPI:
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

        def work():
            try:
                poller.poll_once(db, cfg, zucai_fetch=lambda: body)
            except Exception as e:  # noqa: BLE001
                print("ingest poll_once 失败:", e)

        threading.Thread(target=work, daemon=True).start()
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
        n = db.save_decisions(decisions)
        return {"accepted": True, "n": n, "skipped": total - n}

    # ---------------- /api/decisions ----------------
    # 前端"每场决策卡"据此渲染。按 ko_bj 升序 (缺末尾), 附服务端当前北京时间 ts。
    @app.get("/api/decisions", dependencies=[Depends(auth_dep)])
    def api_decisions():
        now_bj = datetime.now(BJ)
        return {
            "ts": now_bj.isoformat(timespec="seconds"),
            "decisions": db.get_decisions(),
        }

    # ---------------- /api/refresh ----------------
    # 价值"重抓+刷新"按钮用: 取最新一条 source='zucai' 的快照 payload, 后台经
    # poll_once(注入此 payload) 重跑 (内部重连 Poly 刷去水 → 重算 value_points, 治
    # "陈旧 Poly 假黄档")。无 zucai 快照 → {accepted: false, reason}, 不抛错。
    @app.post("/api/refresh", dependencies=[Depends(auth_dep)])
    def api_refresh():
        snapshot = db.latest_snapshot("zucai")
        if snapshot is None:
            return {"accepted": False, "reason": "no zucai snapshot"}

        def work():
            try:
                poller.poll_once(db, cfg, zucai_fetch=lambda: snapshot)
            except Exception as e:  # noqa: BLE001
                print("refresh poll_once 失败:", e)

        threading.Thread(target=work, daemon=True).start()
        return {"accepted": True}

    # ---------------- static frontend ----------------
    if os.path.isdir(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
