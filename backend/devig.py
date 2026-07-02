"""去抽水 (de-vig)。"""


def devig(probs: dict) -> dict:
    """乘法归一化到 100%（只用于 Poly 概率，单位 %）。"""
    s = sum(probs.values())
    if s <= 0:
        return dict(probs)
    return {k: v / s * 100 for k, v in probs.items()}


def devig_from_odds(odds, keys=("h", "d", "a"), ndigits=1):
    """欧赔 → 去水隐含概率% {k: pct}; 任一键缺/0/负 则 None。

    此前 refresh_all/odds_watch/odds_consensus 各自拷贝一份 1/赔率 归一,
    舍入与边界处理已漂移 —— 收敛到这一处。poly_fetch_hk 的 devig 是另一形状
    (概率归一, 非赔率), 且该脚本刻意纯 stdlib 不 import backend, 不并入。
    """
    if not odds:
        return None
    vals = [odds.get(k) for k in keys]
    if any(v is None or v <= 0 for v in vals):
        return None
    imp = [1.0 / v for v in vals]
    s = sum(imp)
    return {k: round(x / s * 100, ndigits) for k, x in zip(keys, imp)}
