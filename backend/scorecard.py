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


def derive_matchdays(matches):
    """按出场次数推每场小组赛轮次(中立分桶用,不靠 v2 判断)。

    同组两队同步推进,故场次轮次 = max(两队此前出场数)+1;末轮=第3次出场(round==3)。
    matches=iterable of (zucai_num, home, away, ko);按 (ko, zucai_num) 升序数。
    返回 {zucai_num: round_int}。淘汰赛会算出 >3,调用方按需处理。
    """
    out = {}
    seen = {}
    for zn, h, a, ko in sorted(matches, key=lambda m: (m[3] or "", m[0])):
        out[zn] = max(seen.get(h, 0), seen.get(a, 0)) + 1
        seen[h] = seen.get(h, 0) + 1
        seen[a] = seen.get(a, 0) + 1
    return out


def parse_score(s):
    """"H-A" → (int, int);None/'无'/格式错/非数字 → None。"""
    if not s or not isinstance(s, str):
        return None
    parts = s.strip().split("-")
    if len(parts) != 2:
        return None
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def score_arm(rows):
    """v1 比分臂指标(had 概率臂之外另立)。rows=[{"pred":(ph,pa)|None,"actual":(ah,aa)}]。

    只统计 pred 与 actual 都非 None 的场:精确命中率(预测比分==实际)+ 平均比分距离
    (|Δ主|+|Δ客|,越低越准)。无可统计场 → rate/distance 为 None。
    """
    scored = [r for r in rows if r.get("pred") is not None and r.get("actual") is not None]
    n = len(scored)
    if n == 0:
        return {"n": 0, "exact": 0, "exact_rate": None, "avg_distance": None}
    exact = sum(1 for r in scored if tuple(r["pred"]) == tuple(r["actual"]))
    dist = sum(abs(r["pred"][0] - r["actual"][0]) + abs(r["pred"][1] - r["actual"][1])
               for r in scored)
    return {"n": n, "exact": exact,
            "exact_rate": round(exact / n, 4),
            "avg_distance": round(dist / n, 4)}


def deviation_audit(rows):
    dev = [r for r in rows if r.get("deviated")]
    if not dev:
        return {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
    v2m = round(sum(r["v2"] for r in dev) / len(dev), 4)
    mkm = round(sum(r["market"] for r in dev) / len(dev), 4)
    return {"n_deviated": len(dev), "v2_mean": v2m, "market_mean": mkm,
            "delta": round(v2m - mkm, 4)}
