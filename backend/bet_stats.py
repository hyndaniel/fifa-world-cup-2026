"""下注台账聚合: 读 data/bet_ledger.json, 算推荐腿战绩 + 实购票盈亏。

纯函数, 不碰 DB/网络。数据真源是 markdown 台账 reports/agents/wc-bet__下注复盘.md,
本模块只消费其手整镜像 bet_ledger.json。
"""
import json
import os
import tempfile

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


def save_ledger(ledger: dict, data_dir: str = "data") -> None:
    """原子写 data_dir/bet_ledger.json(同目录唯一临时名+os.replace)。

    供 /api/ingest/tickets 用: 本地维护的台账直接 POST 落盘到这里, load_ledger 下次
    请求现读即生效, 不需要经 git/部署。临时名用 tempfile.mkstemp 而不是固定的
    "<path>.tmp"——固定名在两个写请求并发时会互相截断/找不到文件, 唯一名 + 同目录
    (与目标文件同一文件系统, 保证 os.replace 是原子 rename)才是真正安全的写法。
    """
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "bet_ledger.json")
    fd, tmp = tempfile.mkstemp(dir=data_dir, prefix=".bet_ledger.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


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

    tickets_summary = _build_tickets(tix)

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
        "tickets": tickets_summary,
    }


def _pnl(t: dict) -> float:
    """票据盈亏: 待结票 pnl=null → 0 贡献(守 None)。"""
    p = t.get("pnl")
    return float(p) if p is not None else 0.0


def _build_tickets(tix: list) -> dict:
    """实购票聚合: 已结/待结拆分 + 按人(who)聚合。

    待结票(settled==False)只进 count/pending_count/pending_stake 及该人 pending*,
    不进 settled_pnl/settled_roi/won/settled_stake。
    """
    settled_tix = [t for t in tix if t.get("settled")]
    pending_tix = [t for t in tix if not t.get("settled")]

    settled_stake = sum(float(t.get("stake", 0)) for t in settled_tix)
    settled_pnl = _round(sum(_pnl(t) for t in settled_tix), 2)
    pending_stake = sum(float(t.get("stake", 0)) for t in pending_tix)
    won = sum(1 for t in settled_tix if _pnl(t) > 0)
    settled_roi = _round(settled_pnl / settled_stake) if settled_stake else 0.0

    # 按 who 分组(每个有票的人一行, 含仅待结的人)。
    persons: dict = {}
    for t in tix:
        who = t.get("who")
        p = persons.setdefault(who, {
            "who": who, "tickets": 0, "settled": 0, "pending": 0, "won": 0,
            "stake": 0.0, "settled_stake": 0.0, "settled_pnl": 0.0, "pending_stake": 0.0,
        })
        stake = float(t.get("stake", 0))
        p["tickets"] += 1
        p["stake"] += stake
        if t.get("settled"):
            p["settled"] += 1
            p["settled_stake"] += stake
            pnl = _pnl(t)
            p["settled_pnl"] += pnl
            if pnl > 0:
                p["won"] += 1
        else:
            p["pending"] += 1
            p["pending_stake"] += stake

    by_person = []
    for p in persons.values():
        sstake = _round(p["settled_stake"], 2)
        spnl = _round(p["settled_pnl"], 2)
        by_person.append({
            "who": p["who"],
            "tickets": p["tickets"],
            "settled": p["settled"],
            "pending": p["pending"],
            "won": p["won"],
            "stake": _round(p["stake"], 2),
            "settled_stake": sstake,
            "settled_pnl": spnl,
            "settled_roi": _round(spnl / sstake) if sstake else 0.0,
            "pending_stake": _round(p["pending_stake"], 2),
        })
    by_person.sort(key=lambda x: (-x["settled_pnl"], x["who"]))

    return {
        "count": len(tix),
        "settled_count": len(settled_tix),
        "pending_count": len(pending_tix),
        "won": won,
        "settled_stake": _round(settled_stake, 2),
        "settled_pnl": settled_pnl,
        "settled_roi": settled_roi,
        "pending_stake": _round(pending_stake, 2),
        "by_person": by_person,
        "rows": tix,
    }
