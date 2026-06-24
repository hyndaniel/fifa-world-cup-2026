# backend/scorecard.py
"""三方 Brier 跑分卡 + 偏离审计。"""
from .scoring import brier_multi


def three_way(v1, v2, market, actual):
    def b(p):
        return brier_multi(p, actual) if p else None
    return {"v1": b(v1), "v2": b(v2), "market": b(market)}


def aggregate(rows):
    out = {"n": len(rows)}
    for key in ("v1", "v2", "market"):
        vals = [r[key] for r in rows if r.get(key) is not None]
        out[f"{key}_mean"] = round(sum(vals) / len(vals), 4) if vals else None
    return out
