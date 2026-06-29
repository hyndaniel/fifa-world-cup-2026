"""HK 端 app 内置新闻富化: 后台线程定时为 watchlist 球队抓 Google News RSS,
写 enrich 表 (与 Mac 端 tools/collect_enrich.py 同效, 但跑在 app 内)。

为何在 app 内 / HK 端: Google News 在大陆被墙, Mac 住宅 IP 直连超时, 故新闻必须
在 HK 机房抓 (HK 无 GFW, 直连 200)。足彩仍由 Mac 住宅端推送
(现为 tools/refresh_all.py, 取代旧 collect_zucai;机房 IP 被足彩 WAF 拦)。
两条数据链路按"哪边能连"分置。

lineup(首发) 暂无免费可靠源, 恒为 None(未出炉), 不影响新闻。
fetch 可注入便于测试 (默认 httpx 直连 Google News RSS)。
"""
from __future__ import annotations

import logging
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

log = logging.getLogger(__name__)

# Google News RSS 用桌面浏览器 UA(HK 直连即得 200 application/xml)。
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
_GNEWS = "https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh"


def _default_fetch(url: str) -> bytes:
    r = httpx.get(
        url,
        headers={"User-Agent": _UA, "Accept": "application/xml"},
        timeout=30,
        follow_redirects=True,
    )
    r.raise_for_status()
    return r.content


def fetch_team_news(team: str, fetch=None, cap: int = 5) -> list[dict]:
    """抓某队新闻 -> [{title,url,ts}] (上限 cap)。任何失败 -> []。"""
    fetch = fetch or _default_fetch
    try:
        q = urllib.parse.quote(f"{team} 世界杯")
        raw = fetch(_GNEWS.format(q=q))
        root = ET.fromstring(raw)
        out: list[dict] = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            if not title and not link:
                continue
            out.append({"title": title, "url": link, "ts": pub})
            if len(out) >= cap:
                break
        return out
    except Exception:  # noqa: BLE001 — 新闻失败不抛, 返回空
        log.exception("fetch_team_news 失败: %s", team)
        return []


def watch_teams(db) -> list[str]:
    """从 watchlist 收集要抓新闻的球队名(去重保序)。
    team/player -> key; match -> 按 ' vs '/'vs' 拆两边。"""
    out: list[str] = []
    for w in db.watchlist():
        kind = w.get("kind")
        key = (w.get("key") or "").strip()
        if not key:
            continue
        if kind == "match":
            sep = " vs " if " vs " in key else "vs"
            out += [s.strip() for s in key.split(sep) if s.strip()]
        else:  # team / player
            out.append(key)
    seen = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def enrich_once(db, fetch=None) -> int:
    """为 watchlist 各队抓新闻并写 enrich 表(替换语义)。返回处理队数。
    lineup 无免费源 → 恒 None(save_enrich 会存 NULL, state 显示未出炉)。"""
    teams = watch_teams(db)
    for t in teams:
        news = fetch_team_news(t, fetch=fetch)
        db.save_enrich(t, None, news)
    log.info("enrich_once: 富化 %d 队新闻", len(teams))
    return len(teams)


def run_enrich_loop(db, cfg):
    """无限轮询: enrich_once → sleep(cfg.enrich.interval_sec, 默认 600) → 重复。"""
    interval = ((cfg or {}).get("enrich") or {}).get("interval_sec", 600)
    while True:
        try:
            enrich_once(db)
        except Exception:  # noqa: BLE001 — 单轮失败不中断循环
            log.exception("enrich_once 失败, 等待下一轮")
        time.sleep(interval)
