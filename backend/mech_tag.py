"""机械对错标:三方 had 预测 argmax vs 实际 outcome,纯事实、无 LLM。

每方取其胜平负概率字典的 argmax 当"预测 outcome",与实际 outcome 比较:
中 → ✅,错 → ❌,无预测/无基线 → —;无赛果 → actual=None 且三方全 —。
"""
from __future__ import annotations

from .baseline import HAD_CFG, baseline_market, get_result
from .v1_log import get_v1
from .v2_predict import get_v2_prediction

_KEYS = ("h", "d", "a")


def _argmax_had(probs) -> str | None:
    """胜平负概率字典 argmax;空/无有效键 → None。"""
    if not probs:
        return None
    items = [(k, probs[k]) for k in _KEYS if probs.get(k) is not None]
    if not items:
        return None
    return max(items, key=lambda kv: kv[1])[0]


def _v2_had(pred) -> dict | None:
    """v2 had 概率在 prediction_json 的 markets.had.v2(应用偏离后的分布)。"""
    if not pred:
        return None
    return ((pred.get("markets") or {}).get("had") or {}).get("v2")


def _mark(pred: str | None, actual: str) -> str:
    if pred is None:
        return "—"
    return "✅" if pred == actual else "❌"


def mech_tags(cache_path: str, match_key: str) -> dict:
    actual = get_result(cache_path, match_key)
    if actual is None:
        return {"match_key": match_key, "actual": None,
                "v1": "—", "v2": "—", "market": "—"}

    mkt = baseline_market(cache_path, match_key, HAD_CFG)
    mkt_pred = _argmax_had(mkt["baseline"]) if mkt else None
    v1_pred = _argmax_had((get_v1(cache_path, match_key) or {}).get("probs"))
    v2_pred = _argmax_had(_v2_had(get_v2_prediction(cache_path, match_key)))

    return {
        "match_key": match_key,
        "actual": actual,
        "v1": _mark(v1_pred, actual),
        "v2": _mark(v2_pred, actual),
        "market": _mark(mkt_pred, actual),
    }
