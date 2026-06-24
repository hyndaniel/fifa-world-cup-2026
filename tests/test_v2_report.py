# tests/test_v2_report.py
from tools.v2_report import render


def test_render_contains_scorecard_and_verdict():
    agg = {"n": 3, "v1_mean": 0.42, "v2_mean": 0.30, "market_mean": 0.33}
    audit = {"n_deviated": 2, "v2_mean": 0.25, "market_mean": 0.31, "delta": -0.06}
    per_match = [{"match_key": "M1", "reliability": "乱",
                  "brier": {"v1": 0.5, "v2": 0.3, "market": 0.35}}]
    md = render(agg, audit, per_match)
    assert "跑分卡" in md
    assert "0.30" in md and "0.33" in md          # v2/market 均值出现
    assert "M1" in md and "乱" in md
    # 偏离有用(delta<0)应给出正向结论文案
    assert "拉低" in md or "有用" in md or "-0.06" in md
