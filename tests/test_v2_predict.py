import os, tempfile
from backend.v2_predict import (apply_deviations, build_v2_prediction,
                                record_v2_prediction, get_v2_prediction)


def test_apply_no_deviation_normalizes():
    out = apply_deviations({"h": 30.0, "d": 30.0, "a": 40.0}, [])
    assert abs(sum(out.values()) - 100.0) < 0.01


def test_apply_single_deviation_renormalizes():
    # 把客胜从 40 抬到 64,其余按比例缩,最后归一 100
    out = apply_deviations({"h": 30.0, "d": 30.0, "a": 40.0},
                           [{"outcome": "a", "to": 64.0, "reason": "韩国只需平+满血"}])
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert out["a"] > out["h"] and out["a"] > out["d"]
    assert abs(out["h"] - out["d"]) < 0.01  # h,d 原本相等,缩放后仍相等


def test_apply_multi_deviation_honors_all_pins():
    # Minor #4: 同场两条偏离都该被钉住, 后者不重标前者; 剩余给未钉的 outcome
    out = apply_deviations({"h": 30.0, "d": 30.0, "a": 40.0},
                           [{"outcome": "h", "to": 50.0, "reason": "r1"},
                            {"outcome": "a", "to": 30.0, "reason": "r2"}])
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert abs(out["h"] - 50.0) < 0.01   # 第一条仍被钉住, 未被第二条重标
    assert abs(out["a"] - 30.0) < 0.01   # 第二条也被钉住
    assert abs(out["d"] - 20.0) < 0.01   # 剩余 100-50-30 全归未钉的 d


def test_apply_deviations_ttg_multikey_sum_100():
    base = {"0": 10.0, "1": 20.0, "2": 30.0, "3": 25.0, "4": 15.0}
    out = apply_deviations(base, [{"outcome": "3", "to": 35.0, "reason": "r"}],
                           keys=tuple(base))
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert abs(out["3"] - 35.0) < 0.6   # 钉住 35(归一后±四舍五入)
    assert set(out) == set(base)


def test_build_v2_prediction_markets_shape():
    out = build_v2_prediction("M1", "乱", ["默契平"], {
        "had": {"baseline": {"h": 30.0, "d": 30.0, "a": 40.0},
                "deviations": [{"outcome": "a", "to": 64.0, "reason": "韩国只需平"}]},
        "hhad": {"baseline": {"h": 40.0, "d": 30.0, "a": 30.0}, "deviations": [], "line": -1},
        "ttg": {"baseline": {"0": 20.0, "1": 30.0, "2": 30.0, "3": 20.0}, "deviations": []}})
    assert out["match_key"] == "M1" and out["reliability"] == "乱"
    assert out["scenarios"] == ["默契平"]
    had = out["markets"]["had"]
    assert had["baseline"] == {"h": 30.0, "d": 30.0, "a": 40.0}   # 基线原值留存
    assert abs(sum(had["v2"].values()) - 100.0) < 0.01 and had["v2"]["a"] > had["v2"]["h"]
    assert out["markets"]["hhad"]["line"] == -1
    ttg = out["markets"]["ttg"]
    assert "ou" in ttg and "2.5" in ttg["ou"]
    assert abs(ttg["ou"]["2.5"]["over"] - 20.0) < 0.6            # 仅 P(3)=20


def test_record_and_get_v2_prediction():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    pred = {"match_key": "M1", "baseline": {"h": 30, "d": 30, "a": 40},
            "v2": {"h": 20, "d": 20, "a": 60}, "deviations": [], "reliability": "中",
            "scenarios": []}
    record_v2_prediction(path, "M1", pred)
    got = get_v2_prediction(path, "M1")
    assert got["v2"]["a"] == 60 and got["reliability"] == "中"
    # 替换语义
    pred["reliability"] = "乱"
    record_v2_prediction(path, "M1", pred)
    assert get_v2_prediction(path, "M1")["reliability"] == "乱"
    assert get_v2_prediction(path, "NONE") is None
