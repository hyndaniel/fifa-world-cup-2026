# backend/scorecard.py
"""三方 Brier 跑分卡 + 偏离审计 + 按场型分桶。"""
from .scoring import brier_multi

# 末轮动机畸形的剧本关键词(对照 backend/scenarios.py 的套路名,子串匹配自由文本 scenario)。
# 含 1X2 混沌型四套路:死亡橡皮擦轮换 / 默契平 / 生死战必有胜负 / (被)摆大巴逼平。
# "强队刷净胜球"有意排除——它是攻击型(影响让球/大小球),非胜平负动机倒挂。
_ANOMALY_KEYWORDS = ("死亡橡皮擦", "默契平", "生死战", "大巴")


def bucket_of(reliability, scenario_names=None):
    """场型分桶:'动机畸形'(末轮动机倒挂/大轮换/默契平/生死战/摆大巴)vs '常规'。

    判定:reliability=='乱'(v2 自评动机倒挂)或任一 scenario 名含畸形关键词 → '动机畸形';
    否则 '常规'。借的是 v2 的 post-hoc 判断(不破 v1⊥v2 红线),供跑分卡切片对照用。
    """
    if reliability == "乱":
        return "动机畸形"
    for name in scenario_names or []:
        if any(kw in (name or "") for kw in _ANOMALY_KEYWORDS):
            return "动机畸形"
    return "常规"


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


def deviation_audit(rows):
    dev = [r for r in rows if r.get("deviated")]
    if not dev:
        return {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
    v2m = round(sum(r["v2"] for r in dev) / len(dev), 4)
    mkm = round(sum(r["market"] for r in dev) / len(dev), 4)
    return {"n_deviated": len(dev), "v2_mean": v2m, "market_mean": mkm,
            "delta": round(v2m - mkm, 4)}
