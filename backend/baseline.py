"""胜平负市场基线:多源去水 + 加权融合 + 置信分级。概率单位 %。"""
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
    return {k: round(v / tot * 100, 1) for k, v in raw.items()}


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
