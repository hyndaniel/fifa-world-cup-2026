"""decision_contract 软校验测试。"""
from backend.decision_contract import (
    DECISION_FIELDS, SETTLE_TIERS, TIERS, VIEW_STATUS, validate_decision,
)


def test_enums():
    assert TIERS == ("green", "yellow", "red", "skip")
    assert set(SETTLE_TIERS) < set(TIERS)  # skip 不进结算统计
    assert "upcoming" in VIEW_STATUS and "expired" in VIEW_STATUS
    assert DECISION_FIELDS["match_key"][0] is True  # 唯一必填


def test_validate_clean_card():
    d = {
        "match_key": "挪威 vs 法国", "ko_bj": "7.2 03:00",
        "best_leg": {"market": "胜平负", "outcome": "客胜", "flag": "yellow",
                     "ev_pct": -2.1, "ev_pct_devig": -3.0},
        "value": {"legs": [{"market": "胜平负", "outcome": "客胜", "flag": "yellow"}]},
    }
    assert validate_decision(d) == []


def test_validate_missing_match_key():
    warns = validate_decision({"best_leg": {"flag": "green", "ev_pct": 1.0}})
    assert any("match_key" in w for w in warns)


def test_validate_bad_flag_and_missing_ev():
    d = {"match_key": "m", "best_leg": {"flag": "gold"},
         "value": {"legs": [{"flag": "purple"}]}}
    warns = validate_decision(d)
    assert any("best_leg.flag" in w for w in warns)
    assert any("ev_pct" in w for w in warns)
    assert any("value.legs" in w for w in warns)


def test_validate_not_a_dict():
    assert validate_decision("nope") == ["decision 不是 dict"]


def test_validate_tolerates_unknown_fields():
    # 未知字段透传是契约的一部分, 不该告警
    assert validate_decision({"match_key": "m", "whatever_new_field": 1}) == []
