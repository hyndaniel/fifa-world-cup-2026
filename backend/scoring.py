"""预测准度打分:多分类 Brier + 校准分桶。概率单位 %。"""


def brier_multi(probs: dict, actual: str) -> float:
    """多分类 Brier = Σ(p_k − y_k)²(p_k 分数,actual 命中 y=1)。越低越准,[0,2]。

    注:仅对 probs 里出现的类求和。若 actual 不在 probs 键里(本项目胜平负 h/d/a
    恒三键齐全,不会发生),它的 y=1 项被静默略去 → Brier 偏小;调用方须保证
    actual ∈ probs(Minor #3)。
    """
    s = sum(probs.values())
    if s <= 0:
        return 0.0
    frac = {k: v / s for k, v in probs.items()}
    return round(sum((frac.get(k, 0.0) - (1.0 if k == actual else 0.0)) ** 2
                     for k in frac), 4)


def calibration_buckets(preds: list, n: int = 5) -> list:
    """preds: [(prob_pct, occurred_0/1)]。按概率分 n 桶,比较平均预测 vs 实际频率。"""
    width = 100.0 / n
    buckets = []
    for i in range(n):
        lo, hi = i * width, (i + 1) * width
        sel = [(p, y) for (p, y) in preds if (lo <= p < hi or (i == n - 1 and p == 100.0))]
        if sel:
            mean_pred = round(sum(p for p, _ in sel) / len(sel), 1)
            freq = round(sum(y for _, y in sel) / len(sel), 3)
        else:
            mean_pred, freq = None, None
        buckets.append({"lo": lo, "hi": hi, "mean_pred": mean_pred,
                        "freq": freq, "count": len(sel)})
    return buckets
