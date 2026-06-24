# tests/test_scorecard.py
from backend.scorecard import three_way, aggregate


def test_three_way_briers():
    out = three_way({"h": 25, "d": 26, "a": 49}, {"h": 15, "d": 24, "a": 61},
                    {"h": 17, "d": 23, "a": 60}, "a")
    # 都重押客胜且客胜发生 → 都较小;v2(61) 最自信应最低
    assert out["v2"] < out["market"] <= out["v1"] or out["v2"] <= out["market"]
    assert all(0 <= out[k] <= 2 for k in ("v1", "v2", "market"))


def test_three_way_none_passthrough():
    out = three_way(None, {"h": 15, "d": 24, "a": 61}, {"h": 17, "d": 23, "a": 60}, "a")
    assert out["v1"] is None and out["v2"] is not None


def test_aggregate_means_ignore_none():
    rows = [{"v1": 0.4, "v2": 0.2, "market": 0.3},
            {"v1": None, "v2": 0.4, "market": 0.5}]
    agg = aggregate(rows)
    assert agg["n"] == 2
    assert agg["v2_mean"] == 0.3      # (0.2+0.4)/2
    assert agg["v1_mean"] == 0.4      # 只 1 个非 None
    assert agg["market_mean"] == 0.4  # (0.3+0.5)/2
