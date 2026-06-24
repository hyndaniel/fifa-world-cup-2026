from backend.baseline import zucai_had_devig, blend_had, confidence, DEFAULT_WEIGHTS


def test_zucai_had_devig_sums_100():
    out = zucai_had_devig({"h": 1.44, "d": 3.87, "a": 6.00})
    assert abs(sum(out.values()) - 100.0) < 0.01
    # 主胜赔率最低 → 概率最高
    assert out["h"] > out["d"] > out["a"]


def test_blend_had_weighted_and_renormalized():
    sources = {
        "zucai": {"h": 60.0, "d": 25.0, "a": 15.0},
        "poly": {"h": 62.0, "d": 24.0, "a": 14.0},
        "consensus": {"h": 58.0, "d": 26.0, "a": 16.0},
    }
    out = blend_had(sources)
    assert abs(sum(out.values()) - 100.0) < 0.01
    # poly 权重最高,结果应偏向 poly 的 62
    assert 59.0 < out["h"] < 62.0


def test_blend_had_missing_source_renormalizes_weights():
    # 只有 zucai + poly,consensus 缺 → 权重在两者间重新归一
    sources = {"zucai": {"h": 60.0, "d": 25.0, "a": 15.0},
               "poly": {"h": 62.0, "d": 24.0, "a": 14.0}}
    out = blend_had(sources)
    assert abs(sum(out.values()) - 100.0) < 0.01


def test_confidence_levels_and_spread():
    three = {"zucai": {"h": 60, "d": 25, "a": 15}, "poly": {"h": 62, "d": 24, "a": 14},
             "consensus": {"h": 50, "d": 30, "a": 20}}
    c = confidence(three)
    assert c["n_sources"] == 3 and c["label"] == "hard"
    assert c["max_spread"] == 12.0  # 主胜 62-50
    one = {"zucai": {"h": 60, "d": 25, "a": 15}}
    assert confidence(one)["label"] == "soft"
    assert confidence({})["label"] == "none"
