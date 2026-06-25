# tests/test_v2_report.py
from tools.v2_report import render


def _collected_one_market():
    return {
        "had": {"rows": [{"deviated": True, "v2": 0.25, "market": 0.31}],
                "per_match": [{"match_key": "M1", "reliability": "乱",
                               "brier": {"v1": None, "v2": 0.1234, "market": 0.3567}}]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []},
    }


def test_render_sections_per_market_and_2dp():
    collected = _collected_one_market()
    audits = {"had": {"n_deviated": 1, "v2_mean": 0.25, "market_mean": 0.31, "delta": -0.06},
              "hhad": {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None},
              "ttg": {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}}
    md = render(collected, audits)
    assert "## 胜平负" in md and "## 让球" in md and "## 总进球" in md
    assert "| M1 | 乱 | — | 0.12 | 0.36 |" in md     # 2dp + 缺失为 —(v1 仅 had 也可能缺)
    assert "0.1234" not in md
    assert "拉低" in md or "-0.06" in md
