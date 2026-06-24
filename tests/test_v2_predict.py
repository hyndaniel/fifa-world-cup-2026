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


def test_build_v2_prediction_shape():
    sheet = {"match_key": "M1", "baseline": {"h": 30.0, "d": 30.0, "a": 40.0}}
    out = build_v2_prediction(sheet, [{"outcome": "a", "to": 64.0, "reason": "r"}], "乱",
                              ["默契平"])
    assert out["match_key"] == "M1"
    assert out["reliability"] == "乱"
    assert out["scenarios"] == ["默契平"]
    assert abs(sum(out["v2"].values()) - 100.0) < 0.01
    assert out["baseline"] == {"h": 30.0, "d": 30.0, "a": 40.0}  # 基线原值留存


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
