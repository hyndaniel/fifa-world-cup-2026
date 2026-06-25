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
from backend.news_enrich import run_enrich_loop
from backend.poller import run_loop
from backend.web import create_app


def main():
    cfg = load_config(os.environ.get("WC_CONFIG", "config.toml"))
    db_path = os.environ.get("WC_DB", "data/wc.db")
    d = os.path.dirname(db_path)
    if d:
        os.makedirs(d, exist_ok=True)
    Db(db_path).init()

    # poll 模式: "direct"(默认, 本地/住宅 自己拉足彩) | "ingest"(机房, 足彩被WAF拦,
    # 靠住宅端 POST /api/ingest/zucai 驱动, 不起自身足彩轮询)。
    mode = (cfg.get("poll", {}) or {}).get("mode", "direct")
    if mode != "ingest":
        threading.Thread(
            target=run_loop, args=(Db(db_path), cfg), daemon=True
        ).start()
    else:
        print("[run] poll mode=ingest: 不起足彩轮询, 等住宅端 POST /api/ingest/zucai")

    # 新闻富化线程 (HK app 内置: Google News RSS 大陆被墙, 必须 HK 抓;
    # 足彩走 Mac 住宅, 各按"哪边能连"分置)。cfg.enrich.enabled 可关。
    if (cfg.get("enrich", {}) or {}).get("enabled", True):
        threading.Thread(
            target=run_enrich_loop, args=(Db(db_path), cfg), daemon=True
        ).start()
        print("[run] 新闻富化线程已起 (Google News RSS, HK 端)")

    app = create_app(db_path=db_path, cfg=cfg)
    uvicorn.run(
        app,
        host=cfg["server"]["host"],
        port=int(cfg["server"]["port"]),
        log_level="info",
    )


if __name__ == "__main__":
    main()
