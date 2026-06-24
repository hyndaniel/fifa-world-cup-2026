"""v2 预测装配:在基线上应用有据偏离,附靠谱度 + 剧本标签。概率 %。"""
_KEYS = ("h", "d", "a")


def apply_deviations(baseline: dict, deviations: list) -> dict:
    """把每条偏离的 outcome 设为 to;其余 outcome 按原比例吸收差额;最后归一到 100。"""
    cur = {k: float(baseline.get(k, 0.0)) for k in _KEYS}
    for dv in deviations:
        oc, to = dv["outcome"], float(dv["to"])
        others = [k for k in _KEYS if k != oc]
        rest_old = sum(cur[k] for k in others)
        cur[oc] = to
        rest_new = max(0.0, 100.0 - to)
        if rest_old > 0:
            for k in others:
                cur[k] = cur[k] / rest_old * rest_new
        else:
            for k in others:
                cur[k] = rest_new / len(others)
    tot = sum(cur.values())
    return {k: round(cur[k] / tot * 100, 1) for k in _KEYS} if tot else cur


def build_v2_prediction(baseline_sheet, deviations, reliability, scenarios):
    base = baseline_sheet["baseline"]
    return {
        "match_key": baseline_sheet.get("match_key"),
        "baseline": dict(base),
        "v2": apply_deviations(base, deviations or []),
        "deviations": deviations or [],
        "reliability": reliability,
        "scenarios": scenarios or [],
    }
