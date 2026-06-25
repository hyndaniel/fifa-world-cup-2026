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


def test_render_per_match_brier_2dp_and_missing():
    # Minor #1/#2: 每场 Brier 与三均值同为 2 位小数; 缺失(None)显示 —
    agg = {"n": 1, "v1_mean": None, "v2_mean": 0.3, "market_mean": 0.33}
    audit = {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
    per_match = [{"match_key": "M1", "reliability": "稳",
                  "brier": {"v1": None, "v2": 0.1234, "market": 0.3567}}]
    md = render(agg, audit, per_match)
    assert "| M1 | 稳 | — | 0.12 | 0.36 |" in md   # 2dp + 缺失为 —
    assert "0.1234" not in md                       # 不再露出 4 位原始精度
    assert "| v1(老方法) | — |" in md              # 均值缺失也为 —
