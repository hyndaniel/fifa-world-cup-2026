"""下注台账聚合: 读 data/bet_ledger.json, 算推荐腿战绩 + 实购票盈亏。

纯函数, 不碰 DB/网络。数据真源是 markdown 台账 reports/agents/wc-bet__下注复盘.md,
本模块只消费其手整镜像 bet_ledger.json。
"""
import json
import os

_TIERS = ("green", "yellow", "red")


def load_ledger(data_dir: str = "data") -> dict:
    path = os.path.join(data_dir, "bet_ledger.json")
    if not os.path.exists(path):
        return {"updated": None, "recommendations": [], "tickets": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("updated", None)
    data.setdefault("recommendations", [])
    data.setdefault("tickets", [])
    return data


def _round(x: float, n: int = 4) -> float:
    return round(float(x), n)


def build_summary(ledger: dict) -> dict:
    recs = ledger.get("recommendations", []) or []
    tix = ledger.get("tickets", []) or []

    settled = [r for r in recs if r.get("settled")]
    wins = [r for r in settled if r.get("result") == "win"]
    pending = [r for r in recs if r.get("result") == "pending"]

    by_tier = {t: {"total": 0, "win": 0} for t in _TIERS}
    for r in settled:
        t = r.get("tier")
        if t in by_tier:
            by_tier[t]["total"] += 1
            if r.get("result") == "win":
                by_tier[t]["win"] += 1

    by_date_map = {}
    for r in settled:
        d = by_date_map.setdefault(r["date"], {"date": r["date"], "settled": 0, "win": 0})
        d["settled"] += 1
        if r.get("result") == "win":
            d["win"] += 1
    by_date = sorted(by_date_map.values(), key=lambda d: d["date"])

    hypo = 0.0
    for r in settled:
        if r.get("result") == "win":
            hypo += float(r["odds"]) - 1.0
        else:
            hypo -= 1.0
    n_settled = len(settled)
    hit_rate = _round(len(wins) / n_settled) if n_settled else 0.0
    hypo_roi = _round(hypo / n_settled) if n_settled else 0.0

    total_stake = sum(float(t.get("stake", 0)) for t in tix)
    total_pnl = sum(float(t.get("pnl", 0)) for t in tix)
    roi = _round(total_pnl / total_stake) if total_stake else 0.0
    won = sum(1 for t in tix if float(t.get("pnl", 0)) > 0)

    return {
        "updated": ledger.get("updated"),
        "recommendations": {
            "total": len(recs),
            "settled": n_settled,
            "win": len(wins),
            "pending": len(pending),
            "hit_rate": hit_rate,
            "by_tier": by_tier,
            "by_date": by_date,
            "hypo_unit_pnl": _round(hypo, 2),
            "hypo_roi": hypo_roi,
        },
        "tickets": {
            "count": len(tix),
            "won": won,
            "total_stake": _round(total_stake, 2),
            "total_pnl": _round(total_pnl, 2),
            "roi": roi,
            "rows": tix,
        },
    }
