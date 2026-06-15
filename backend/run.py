"""容器入口: 启动 poller 后台线程 + uvicorn web 服务。

Db 每方法调用开独立连接 (线程安全)，poller 线程与 web 各用一个 Db 实例。
config.toml 缺失时 load_config 回退默认 (但 [zucai] proxy 需自行配，否则海外机房足彩被 WAF 拦)。
"""
from __future__ import annotations

import os
import threading

import uvicorn

from backend.config import load_config
from backend.db import Db
from backend.poller import run_loop
from backend.web import create_app


def main():
    cfg = load_config(os.environ.get("WC_CONFIG", "config.toml"))
    db_path = os.environ.get("WC_DB", "data/wc.db")
    d = os.path.dirname(db_path)
    if d:
        os.makedirs(d, exist_ok=True)
    Db(db_path).init()

    # poller 后台线程 (自带 try/except 续跑，单轮失败不致命)
    threading.Thread(
        target=run_loop, args=(Db(db_path), cfg), daemon=True
    ).start()

    app = create_app(db_path=db_path, cfg=cfg)
    uvicorn.run(
        app,
        host=cfg["server"]["host"],
        port=int(cfg["server"]["port"]),
        log_level="info",
    )


if __name__ == "__main__":
    main()
