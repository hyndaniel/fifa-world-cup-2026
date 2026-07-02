#!/usr/bin/env python3
"""Mac 端富化采集器: 为看板 watchlist 里的球队抓取 新闻 + (尽力)首发阵容,
POST 到 HK 看板 /api/ingest/enrich。新闻来自 Google News RSS(Mac 可直连),
阵容为尽力而为 —— 任何失败都返回 None, 绝不影响新闻。纯标准库, 不依赖第三方。

用法:
  export WC_INGEST_URL="http://18.166.71.60:8000"   # 看板基址(不含路径)
  export WC_INGEST_PW="<看板密码>"
  python3 tools/collect_enrich.py            # 循环(默认 600s)
  python3 tools/collect_enrich.py --once     # 只跑一次
环境变量:
  WC_INGEST_URL      看板基址, 如 http://18.166.71.60:8000
  WC_INGEST_PW       看板密码(basic auth)
  WC_INGEST_USER     看板用户(默认 admin)
  WC_ENRICH_INTERVAL 循环间隔秒(默认 600)
  WC_ENRICH_TEAMS    可选: 逗号分隔球队名, 覆盖从 /api/state 拉取的 watchlist
"""
import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest_client as ic  # noqa: E402

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Mobile/15E148"
INTERVAL = int(os.environ.get("WC_ENRICH_INTERVAL", "600"))
ENRICH_TEAMS = os.environ.get("WC_ENRICH_TEAMS", "")


def fetch_state():
    """GET {base}/api/state, 返回解析后的 dict; 失败抛异常。"""
    return ic.get("/api/state", headers={"User-Agent": UA})


def teams_from_watchlist(state):
    """从 /api/state 的 watchlist 收集球队名(去重, 保序)。
    kind==team -> key; kind==match -> 按 ' vs '/'vs' 拆两边; kind==player -> key(仅新闻)。
    """
    out = []
    for w in (state.get("watchlist") or []):
        kind = w.get("kind")
        key = (w.get("key") or "").strip()
        if not key:
            continue
        if kind == "match":
            sep = " vs " if " vs " in key else "vs"
            for side in key.split(sep):
                side = side.strip()
                if side:
                    out.append(side)
        else:  # team / player
            out.append(key)
    # 去重保序
    seen = set()
    teams = []
    for t in out:
        if t not in seen:
            seen.add(t)
            teams.append(t)
    return teams


def fetch_news(team):
    """GET Google News RSS, 解析 <item> title/link/pubDate, 上限 5; 任何错误 -> []。"""
    try:
        q = urllib.parse.quote(team + " 世界杯")
        url = (f"https://news.google.com/rss/search?q={q}"
               "&hl=zh-CN&gl=CN&ceid=CN:zh")
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        items = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            if not title and not link:
                continue
            items.append({"title": title, "url": link, "ts": pub})
            if len(items) >= 5:
                break
        return items
    except Exception:  # noqa: BLE001 - 新闻失败不抛, 返回空
        return []


def fetch_lineup(team):
    """尽力抓取首发阵容 -> dict|None。捕获所有异常, 任何失败返回 None。

    TODO: 接入公开首发源(如 Sofascore/footystats 等公共接口或公开页面解析)。
    目前为占位实现: 阵容信息多数赛前 1 小时才出炉, 故默认返回 None(未出炉),
    新闻仍正常上送。此处保证 lineup 的任何失败都不会影响 news。
    """
    try:
        # 占位: 暂无稳定公开首发源, 返回 None 表示 "未出炉"。
        # 真正接入时, 在此构造:
        #   {"formation": "4-3-3", "players": [...], "source": "<url>", "note": ""}
        return None
    except Exception:  # noqa: BLE001 - 阵容失败绝不影响新闻
        return None


def post_enrich(items):
    """POST {base}/api/ingest/enrich, body {"items":[...]}; 失败抛异常。"""
    return ic.post("/api/ingest/enrich", {"items": items},
                   timeout=60, headers={"User-Agent": UA})


def resolve_teams():
    """决定本轮要富化的球队列表。"""
    if ENRICH_TEAMS.strip():
        teams = [t.strip() for t in ENRICH_TEAMS.split(",") if t.strip()]
        # 去重保序
        seen = set()
        uniq = []
        for t in teams:
            if t not in seen:
                seen.add(t)
                uniq.append(t)
        return uniq
    state = fetch_state()
    return teams_from_watchlist(state)


def once():
    teams = resolve_teams()
    if not teams:
        print("watchlist 为空(无球队), 跳过本轮")
        return
    items = []
    for team in teams:
        news = fetch_news(team)
        lineup = fetch_lineup(team)
        items.append({"team": team, "lineup": lineup, "news": news})
    resp = post_enrich(items)
    print(f"已上送 {len(items)} 队富化:", resp)


def main():
    parser = argparse.ArgumentParser(description="WC 看板富化采集器(新闻+阵容)")
    parser.add_argument("--once", action="store_true", help="只跑一次, 不循环")
    args = parser.parse_args()

    if ic.pw_missing():
        print("缺 WC_INGEST_PW(看板密码), 退出")
        sys.exit(1)
    if args.once:
        once()
        return
    print(f"富化采集循环启动: 每 {INTERVAL}s 拉 watchlist → 抓新闻/阵容 → "
          f"POST {ic.INGEST}/api/ingest/enrich")
    while True:
        try:
            once()
        except Exception as e:  # noqa: BLE001
            print("本轮失败:", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
