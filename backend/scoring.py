"""预测准度打分:多分类 Brier + 校准分桶。概率单位 %。"""


def brier_multi(probs: dict, actual: str) -> float:
    """多分类 Brier = Σ(p_k − y_k)²(p_k 分数,actual 命中 y=1)。越低越准,[0,2]。

    actual 不在 probs 支撑内(如总进球真值超出该场盘口覆盖的档位)= 满罚该真值类:
    它的 y=1 而预测 p≈0,补一个 (0−1)²=1 的罚分,绝不静默漏掉(否则 Brier 偏小、
    会伪造"市场基线很准")。had 恒三键齐全,此分支不触发,行为不变。"""
    s = sum(probs.values())
    if s <= 0:
        return 0.0
    frac = {k: v / s for k, v in probs.items()}
    total = sum((frac.get(k, 0.0) - (1.0 if k == actual else 0.0)) ** 2 for k in frac)
    if actual not in frac:
        total += 1.0
    return round(total, 4)


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
