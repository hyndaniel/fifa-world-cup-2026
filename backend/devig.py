"""去抽水 (de-vig)。"""


def devig(probs: dict) -> dict:
    """乘法归一化到 100%（只用于 Poly 概率，单位 %）。"""
    s = sum(probs.values())
    if s <= 0:
        return dict(probs)
    return {k: v / s * 100 for k, v in probs.items()}
