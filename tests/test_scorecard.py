# tests/test_scorecard.py
from backend.scorecard import three_way, aggregate, deviation_audit, bucket_of


def test_bucket_of_reliability_luan_is_anomaly():
    # reliability=='乱'(动机倒挂)→ 动机畸形,即便无 scenario
    assert bucket_of("乱", []) == "动机畸形"
    assert bucket_of("乱", None) == "动机畸形"


def test_bucket_of_scenario_keyword_is_anomaly():
    # scenario 名含末轮畸形关键词 → 动机畸形(即便 reliability 非乱)
    assert bucket_of("中", ["生死战必有胜负"]) == "动机畸形"
    assert bucket_of("中", ["死亡橡皮擦轮换"]) == "动机畸形"
    assert bucket_of("稳", ["双方平即出线·默契平"]) == "动机畸形"


def test_bucket_of_regular():
    # 非乱 + 无畸形关键词 → 常规
    assert bucket_of("中", ["重盘热门·poly独源软锚"]) == "常规"
    assert bucket_of("稳", []) == "常规"
    assert bucket_of("", None) == "常规"


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


def test_deviation_audit_helpful():
    rows = [{"deviated": True, "v2": 0.20, "market": 0.30},   # 偏离更准
            {"deviated": True, "v2": 0.25, "market": 0.28},
            {"deviated": False, "v2": 0.40, "market": 0.40}]  # 不计入
    a = deviation_audit(rows)
    assert a["n_deviated"] == 2
    assert a["v2_mean"] == 0.225 and a["market_mean"] == 0.29
    assert a["delta"] < 0  # 偏离平均拉低 Brier = 有用


def test_deviation_audit_none_when_no_deviation():
    a = deviation_audit([{"deviated": False, "v2": 0.4, "market": 0.4}])
    assert a["n_deviated"] == 0 and a["delta"] is None
