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
                     "age_h": age,
                     # 未来时间(负龄, 源时钟偏差/错标时区)也算 stale: 不可信, 绝不当活因子
                     "stale": age is None or age < 0 or age > stale_hours})
    # 非 stale 优先, 其内新近优先; stale(含不可解析/未来)沉底, 不冒充"最新"
    news.sort(key=lambda x: (x["stale"], x["age_h"] is None,
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
        "note": ("首发源暂缺(恒 null);新闻>%dh、pubDate 不可解析、或时间为未来(负龄)"
                 "标 stale;仅 watchlist 覆盖队有情报" % stale_hours),
    }
