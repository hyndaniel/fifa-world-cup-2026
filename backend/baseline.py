"""胜平负市场基线:多源去水 + 加权融合 + 置信分级。概率单位 %。"""
import json
import sqlite3

from .devig import devig

DEFAULT_WEIGHTS = {"zucai": 0.20, "poly": 0.45, "consensus": 0.35}
_KEYS = ("h", "d", "a")


def zucai_had_devig(had: dict) -> dict:
    """竞彩 had 欧赔 {h,d,a} → 去水概率 %。隐含=1/赔率,再乘法归一到 100。"""
    implied = {k: 1.0 / float(had[k]) for k in _KEYS if had.get(k)}
    return {k: round(v, 1) for k, v in devig(implied).items()}


def blend_had(sources: dict, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """多源去水概率加权融合;只用在场源、权重重新归一;输出重新归一到 100。"""
    present = {s: weights[s] for s in sources if s in weights and sources[s]}
    wsum = sum(present.values())
    if wsum <= 0:
        return {}
    raw = {k: sum(sources[s][k] * present[s] for s in present) / wsum for k in _KEYS}
    tot = sum(raw.values())
    out = {k: round(v / tot * 100, 1) for k, v in raw.items()}
    # 独立四舍五入会让三项之和偏离 100(可达 ±0.1+),把残差并入最大项,严格归一到 100。
    residual = round(100.0 - sum(out.values()), 1)
    if residual:
        kmax = max(out, key=out.get)
        out[kmax] = round(out[kmax] + residual, 1)
    return out


def confidence(sources: dict) -> dict:
    """覆盖几个源 + 跨源最大极差。3源=hard/2=medium/1=soft/0=none。"""
    n = len(sources)
    label = {3: "hard", 2: "medium", 1: "soft"}.get(n, "none")
    spread = 0.0
    for k in _KEYS:
        vals = [s[k] for s in sources.values() if k in s]
        if len(vals) >= 2:
            spread = max(spread, max(vals) - min(vals))
    return {"n_sources": n, "label": label, "max_spread": round(spread, 1)}


def _latest_payload(conn, source, match_key):
    r = conn.execute(
        "SELECT payload_json FROM odds_cache WHERE source=? AND match_key=? "
        "ORDER BY ts DESC LIMIT 1", (source, match_key)).fetchone()
    return json.loads(r[0]) if r else None


def baseline_had(cache_path: str, match_key: str, weights: dict = DEFAULT_WEIGHTS):
    """从 odds_cache.db 装配一场胜平负基线表。无任何源 → None。"""
    conn = sqlite3.connect(cache_path)
    try:
        sources = {}
        z = _latest_payload(conn, "zucai", match_key)
        if z and z.get("had"):
            sources["zucai"] = zucai_had_devig(z["had"])
        p = _latest_payload(conn, "poly", match_key)
        if p and p.get("poly_devig"):
            sources["poly"] = {k: round(float(p["poly_devig"][k]), 1) for k in _KEYS
                               if p["poly_devig"].get(k) is not None}
        c = _latest_payload(conn, "consensus", match_key)
        if c and c.get("had"):
            sources["consensus"] = zucai_had_devig(c["had"])  # 共识 had 也是欧赔,同路去水
    finally:
        conn.close()
    if not sources:
        return None
    return {"match_key": match_key, "baseline": blend_had(sources, weights),
            "sources": sources, "confidence": confidence(sources)}
