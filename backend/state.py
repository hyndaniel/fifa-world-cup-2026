"""组装 /state JSON (契约: 输出形状, 见计划)。

build_state(db, cfg, now_bj) -> dict:
  - value_radar: 取每场最新一批 value_points 中 flag∈{green,yellow} (排除 skip),
                 按 ev_pct (去水) 降序; 每项带 match 名 + ko_bj + cutoff_bj。
  - next_cutoff: 未停售 (status!='Stopped') 的场里, cutoff 时刻 >= now 的最近一个 + 倒计时秒。
  - watchlist:   各 pin 项 (队/场/人) 富化: 关联场次 matches + 阵容 lineup + 新闻 news
                 + 雷达命中 radar_hits (该项关联场次里 green/yellow 的 value_points 精简)。
  - ledger:      来自 db.ledger()。
  - matches_today: 今日 (now_bj 同日) 的场次精简列表。

形状对照计划 §契约:
{
  "ts": "...+08:00",
  "next_cutoff": {"match": "...", "cutoff_bj": "23:00", "countdown_sec": 8950},
  "value_radar": [ {match,ko_bj,market,outcome,zucai_odds,poly_prob_devig,
                    poly_prob_raw,ev_pct_devig,ev_pct_raw,flag,cutoff_bj}, ... ],
  "watchlist": [ {kind,key,note,matches,lineup,news,radar_hits}, ... ],
  "ledger": {"A":{...},"B":{...}},
  "matches_today": []
}
"""
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))


def _parse_cutoff_dt(cutoff_bj, ko_bj, now_bj):
    """把 cutoff_bj ("HH:MM") 落到具体日期上, 返回 aware datetime(北京时区) 或 None。

    日期优先取 ko_bj 里的 "M.D" 前缀 (年份用 now_bj 的年); 缺则用 now_bj 当天。
    若推得的 cutoff 已早于 now 超过 12h, 视为次日 (跨午夜场)。
    """
    if not cutoff_bj:
        return None
    cutoff_bj = str(cutoff_bj).strip()
    try:
        hh, mm = cutoff_bj.split(":")
        hh, mm = int(hh), int(mm)
    except (ValueError, AttributeError):
        return None

    year = now_bj.year
    month, day = now_bj.month, now_bj.day
    if ko_bj:
        head = str(ko_bj).strip().split(" ")[0]  # "6.16"
        if "." in head:
            try:
                m_s, d_s = head.split(".")
                month, day = int(m_s), int(d_s)
            except ValueError:
                pass
    try:
        dt = datetime(year, month, day, hh, mm, tzinfo=BJ)
    except ValueError:
        return None
    # 跨午夜场: cutoff 在 ko 当天, 但若已落在 now 之前很久, 仍按推得日期 (不强行 +1)。
    return dt


DECAY_H = 6  # 决策卡/今日场次: 开球后保留小时数, 超过即衰减消失


def parse_ko_dt(ko_bj, now_bj):
    """ "M.D HH:MM" -> 北京 aware datetime(年份取 now_bj.year); 无法解析 -> None。

    以开球时刻为锚, 跨北京午夜的夜场(ko_bj 带次日日期前缀)天然连续。
    """
    s = str(ko_bj or "").strip()
    parts = s.split(" ")
    if len(parts) != 2 or "." not in parts[0] or ":" not in parts[1]:
        return None
    try:
        m_s, d_s = parts[0].split(".")
        hh_s, mm_s = parts[1].split(":")
        return datetime(now_bj.year, int(m_s), int(d_s), int(hh_s), int(mm_s), tzinfo=BJ)
    except (ValueError, AttributeError):
        return None


def ko_status(ko_bj, now_bj, decay_h=DECAY_H):
    """返回 (状态, dt|None)。
    upcoming: 还没开球; recent: 已开球但在 decay_h 小时内; expired: 超过; unknown: 解析不出。
    """
    dt = parse_ko_dt(ko_bj, now_bj)
    if dt is None:
        return ("unknown", None)
    if dt >= now_bj:
        return ("upcoming", dt)
    if dt >= now_bj - timedelta(hours=decay_h):
        return ("recent", dt)
    return ("expired", dt)


def decisions_view(decisions, now_bj, decay_h=DECAY_H):
    """筛掉 expired 的场, 每条附 view_status, 按开球时刻升序(unknown 末尾)。

    输入是 db.get_decisions() 的全量 Decision dict 列表; 返回过滤+标注后的浅拷贝列表。
    """
    out = []
    for d in decisions or []:
        if not isinstance(d, dict):
            continue
        status, dt = ko_status(d.get("ko_bj"), now_bj, decay_h)
        if status == "expired":
            continue
        out.append((dt, {**d, "view_status": status}))
    out.sort(key=lambda t: (t[0] is None, t[0] or now_bj))
    return [d for _dt, d in out]


def _radar_item(vp, match):
    """单条 value_radar (契约字段)。"""
    value_raw = vp.get("value_raw")
    ev_pct_raw = round((value_raw - 1.0) * 100, 1) if value_raw is not None else None
    name = ""
    ko_bj = ""
    cutoff_bj = ""
    if match:
        name = f"{match.get('home_cn', '')} vs {match.get('away_cn', '')}"
        ko_bj = match.get("ko_bj") or ""
        cutoff_bj = match.get("cutoff_bj") or ""
    return {
        "match": name,
        "ko_bj": ko_bj,
        "market": vp.get("market"),
        "outcome": vp.get("outcome"),
        "zucai_odds": vp.get("zucai_odds"),
        "poly_prob_devig": vp.get("poly_prob_devig"),
        "poly_prob_raw": vp.get("poly_prob_raw"),
        "ev_pct_devig": vp.get("ev_pct"),
        "ev_pct_raw": ev_pct_raw,
        "flag": vp.get("flag"),
        "cutoff_bj": cutoff_bj,
    }


def _match_brief(m):
    """关联场次精简 (watchlist matches 字段)。"""
    return {
        "match": f"{m.get('home_cn', '')} vs {m.get('away_cn', '')}",
        "ko_bj": m.get("ko_bj") or "",
        "cutoff_bj": m.get("cutoff_bj") or "",
        "status": m.get("status") or "",
    }


def _split_match_key(key):
    """把 match 类 key 拆成两队名: 优先 " vs ", 容忍 "vs"。返回 (a, b) 或 (key, "")。"""
    s = str(key or "")
    if " vs " in s:
        a, b = s.split(" vs ", 1)
    elif "vs" in s:
        a, b = s.split("vs", 1)
    else:
        return s.strip(), ""
    return a.strip(), b.strip()


def _merge_news(*lists, cap=5):
    """合并多份 news, 按 url 去重 (无 url 用 title 兜底), 截断到 cap。"""
    out = []
    seen = set()
    for lst in lists:
        for n in lst or []:
            k = n.get("url") or n.get("title")
            if k in seen:
                continue
            seen.add(k)
            out.append(n)
            if len(out) >= cap:
                return out
    return out


def _radar_hit(vp, match):
    """单条 watchlist radar_hit (精简映射)。"""
    name = ""
    if match:
        name = f"{match.get('home_cn', '')} vs {match.get('away_cn', '')}"
    return {
        "match": name,
        "market": vp.get("market"),
        "outcome": vp.get("outcome"),
        "zucai_odds": vp.get("zucai_odds"),
        "poly_prob_devig": vp.get("poly_prob_devig"),
        "ev_pct_devig": vp.get("ev_pct"),
        "flag": vp.get("flag"),
    }


def _build_watch_item(w, db, by_id, latest_vps):
    """把一条 watchlist 行富化为契约形状 (matches/lineup/news/radar_hits)。

    链接规则 (契约 §4):
      - team:   key 命中 home_cn 或 away_cn 即关联; lineup/news = latest_enrich(key)。
      - match:  拆 key 两队, 两队名都出现在 (home_cn, away_cn) 才关联;
                lineup = 主队 latest_enrich.lineup; news = 两队 news 合并去重 cap5。
      - player: 不关联场次; lineup=None; news = latest_enrich(key).news (无则 [])。
    radar_hits: latest_value_points 里 flag∈{green,yellow} 且 match_id ∈ 关联场次;
                按 ev_pct 降序, cap 6。
    """
    kind = w.get("kind")
    key = w.get("key")
    item = {
        "kind": kind,
        "key": key,
        "note": w.get("note") or "",
        "matches": [],
        "lineup": None,
        "news": [],
        "radar_hits": [],
    }

    linked_ids = set()

    if kind == "team":
        for m in by_id.values():
            if key and (key in (m.get("home_cn") or "") or key in (m.get("away_cn") or "")):
                item["matches"].append(_match_brief(m))
                linked_ids.add(m["id"])
        en = db.latest_enrich(key)
        if en:
            item["lineup"] = en.get("lineup")
            item["news"] = en.get("news") or []

    elif kind == "match":
        a, b = _split_match_key(key)
        for m in by_id.values():
            names = (m.get("home_cn") or "", m.get("away_cn") or "")
            blob = "".join(names)
            if a and b and a in blob and b in blob:
                item["matches"].append(_match_brief(m))
                linked_ids.add(m["id"])
        en_a = db.latest_enrich(a) if a else None
        en_b = db.latest_enrich(b) if b else None
        if en_a and en_a.get("lineup"):
            item["lineup"] = en_a.get("lineup")
        item["news"] = _merge_news(
            (en_a or {}).get("news") or [],
            (en_b or {}).get("news") or [],
            cap=5,
        )

    elif kind == "player":
        en = db.latest_enrich(key)
        item["news"] = (en.get("news") or []) if en else []

    if linked_ids:
        hits = [
            _radar_hit(vp, by_id.get(vp.get("match_id")))
            for vp in latest_vps
            if vp.get("flag") in ("green", "yellow") and vp.get("match_id") in linked_ids
        ]
        hits.sort(key=lambda x: (x["ev_pct_devig"] is None, -(x["ev_pct_devig"] or 0.0)))
        item["radar_hits"] = hits[:6]

    return item


def build_state(db, cfg, now_bj=None) -> dict:
    """组装 /state JSON。

    db:     backend.db.Db 实例 (已 init)。
    cfg:    load_config() 结果 dict (用 wallet.B_weekly_budget 喂 ledger)。
    now_bj: 当前北京时间 (aware datetime); 缺则取系统当前北京时间。
    """
    if now_bj is None:
        now_bj = datetime.now(BJ)
    elif now_bj.tzinfo is None:
        now_bj = now_bj.replace(tzinfo=BJ)

    matches = db.matches()
    by_id = {m["id"]: m for m in matches}

    # ---- value_radar: 最新一批, 排除 skip, 按 ev_pct(去水) 降序 ----
    vps = db.latest_value_points()
    radar = [
        _radar_item(vp, by_id.get(vp.get("match_id")))
        for vp in vps
        if vp.get("flag") in ("green", "yellow")
    ]
    radar.sort(key=lambda x: (x["ev_pct_devig"] is None, -(x["ev_pct_devig"] or 0.0)))

    # ---- next_cutoff: 未停售里 cutoff >= now 的最近一个 ----
    next_cutoff = None
    best_dt = None
    for m in matches:
        if (m.get("status") or "").lower() == "stopped":
            continue
        dt = _parse_cutoff_dt(m.get("cutoff_bj"), m.get("ko_bj"), now_bj)
        if dt is None or dt < now_bj:
            continue
        if best_dt is None or dt < best_dt:
            best_dt = dt
            next_cutoff = {
                "match": f"{m.get('home_cn', '')} vs {m.get('away_cn', '')}",
                "cutoff_bj": m.get("cutoff_bj") or "",
                "countdown_sec": int((dt - now_bj).total_seconds()),
            }

    # ---- watchlist: pin 项 富化 (matches/lineup/news/radar_hits) ----
    watch = [
        _build_watch_item(w, db, by_id, vps)
        for w in db.watchlist()
    ]

    # ---- ledger ----
    b_budget = cfg.get("wallet", {}).get("B_weekly_budget", 100) if cfg else 100
    ledger = db.ledger(b_budget=b_budget)

    # ---- matches_today: 开球 -DECAY_H 滑窗内的场, 按开球升序, 附 view_status ----
    mt = []
    for m in matches:
        status, dt = ko_status(m.get("ko_bj"), now_bj)
        if status == "expired":
            continue
        mt.append((dt, {
            "match": f"{m.get('home_cn', '')} vs {m.get('away_cn', '')}",
            "ko_bj": m.get("ko_bj") or "",
            "cutoff_bj": m.get("cutoff_bj") or "",
            "status": m.get("status") or "",
            "view_status": status,
        }))
    mt.sort(key=lambda t: (t[0] is None, t[0] or now_bj))
    matches_today = [x for _dt, x in mt]

    return {
        "ts": now_bj.isoformat(timespec="seconds"),
        "next_cutoff": next_cutoff,
        "value_radar": radar,
        "watchlist": watch,
        "ledger": ledger,
        "matches_today": matches_today,
    }
