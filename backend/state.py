"""组装 /state JSON (契约: 输出形状, 见计划)。

build_state(db, cfg, now_bj) -> dict:
  - value_radar: 取每场最新一批 value_points 中 flag∈{green,yellow} (排除 skip),
                 按 ev_pct (去水) 降序; 每项带 match 名 + ko_bj + cutoff_bj。
  - next_cutoff: 未停售 (status!='Stopped') 的场里, cutoff 时刻 >= now 的最近一个 + 倒计时秒。
  - watchlist:   各 pin 项 (队/场/人); v1 matches/lineup/news 字段占位 (Task9 填)。
  - ledger:      来自 db.ledger()。
  - matches_today: 今日 (now_bj 同日) 的场次精简列表。

形状对照计划 §契约:
{
  "ts": "...+08:00",
  "next_cutoff": {"match": "...", "cutoff_bj": "23:00", "countdown_sec": 8950},
  "value_radar": [ {match,ko_bj,market,outcome,zucai_odds,poly_prob_devig,
                    poly_prob_raw,ev_pct_devig,ev_pct_raw,flag,cutoff_bj}, ... ],
  "watchlist": [ {kind,key,note,matches,lineup,news}, ... ],
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

    # ---- watchlist: pin 项 + v1 占位字段 ----
    watch = []
    for w in db.watchlist():
        watch.append({
            "kind": w.get("kind"),
            "key": w.get("key"),
            "note": w.get("note") or "",
            "matches": [],
            "lineup": None,
            "news": [],
        })

    # ---- ledger ----
    b_budget = cfg.get("wallet", {}).get("B_weekly_budget", 100) if cfg else 100
    ledger = db.ledger(b_budget=b_budget)

    # ---- matches_today: now 同日 (按 ko_bj 的 M.D 前缀) ----
    today_head = f"{now_bj.month}.{now_bj.day:02d}"
    today_head_alt = f"{now_bj.month}.{now_bj.day}"
    matches_today = []
    for m in matches:
        head = str(m.get("ko_bj") or "").strip().split(" ")[0]
        if head in (today_head, today_head_alt):
            matches_today.append({
                "match": f"{m.get('home_cn', '')} vs {m.get('away_cn', '')}",
                "ko_bj": m.get("ko_bj") or "",
                "cutoff_bj": m.get("cutoff_bj") or "",
                "status": m.get("status") or "",
            })

    return {
        "ts": now_bj.isoformat(timespec="seconds"),
        "next_cutoff": next_cutoff,
        "value_radar": radar,
        "watchlist": watch,
        "ledger": ledger,
        "matches_today": matches_today,
    }
