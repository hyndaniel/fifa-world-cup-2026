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

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import load_config
from backend.db import Db
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

    # ---------------- static frontend ----------------
    if os.path.isdir(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
