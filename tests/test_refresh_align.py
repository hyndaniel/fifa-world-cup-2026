import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import refresh_all as ra  # noqa: E402


def test_arrow():
    assert ra.arrow(2.0, 2.2) == "▲"
    assert ra.arrow(2.2, 2.0) == "▼"
    assert ra.arrow(2.0, 2.0) == "-"
    assert ra.arrow(None, 2.0) == "-"
    assert ra.arrow(2.0, None) == "-"


def test_devig_from_had():
    dv = ra._devig_from_had({"h": 2.0, "d": 4.0, "a": 4.0})
    # 1/2 : 1/4 : 1/4 = 0.5:0.25:0.25 → 50/25/25
    assert dv == {"h": 50.0, "d": 25.0, "a": 25.0}
    assert ra._devig_from_had({"h": 2.0, "d": None, "a": 4.0}) is None


def test_build_panel_aligns_by_label_and_divergence():
    zucai = [{"match_key": "周四055", "label": "土耳其 vs 美国", "ko": "2026-06-26 10:00",
              "payload": {"had": {"h": 2.7, "d": 3.4, "a": 1.95}, "hhad": None, "ttg": {}}}]
    consensus = [{"match_key": "500-1359193", "label": "土耳其 vs 美国", "ko": "2026-06-26 10:00",
                  "payload": {"had": {"h": 2.7, "d": 3.4, "a": 2.45},
                              "devig_pct": {"h": 34.5, "d": 27.4, "a": 38.1}, "n_books": 47}}]
    poly = [{"match_key": "周四055", "label": "土耳其 vs 美国", "ko": "2026-06-26 10:00",
             "payload": {"poly_devig": {"h": 30.0, "d": 28.0, "a": 42.0}}}]
    panel = ra.build_panel(zucai, consensus, poly, prev_lookup=lambda s, k: None)
    assert len(panel) == 1
    p = panel[0]
    assert p["match_key"] == "周四055"            # 对齐到 zucai_num
    assert not p["sources"]["zucai"]["stale"]
    assert not p["sources"]["consensus"]["stale"]
    assert not p["sources"]["poly"]["stale"]
    assert p["sources"]["consensus"]["n_books"] == 47
    assert p["divergence"]["h"] is not None        # 竞彩去水 − 欧盘去水 有值


def test_build_panel_marks_missing_source_stale():
    zucai = [{"match_key": "周四055", "label": "A vs B", "ko": "k",
              "payload": {"had": {"h": 2.0, "d": 3.0, "a": 3.5}, "hhad": None, "ttg": {}}}]
    panel = ra.build_panel(zucai, [], [], prev_lookup=lambda s, k: None)
    p = panel[0]
    assert p["sources"]["consensus"]["stale"] is True
    assert p["sources"]["poly"]["stale"] is True
    assert p["divergence"]["h"] is None            # 缺欧盘 → 无分歧


def test_build_panel_delta_arrows_from_prev():
    zucai = [{"match_key": "周四055", "label": "A vs B", "ko": "k",
              "payload": {"had": {"h": 2.0, "d": 3.0, "a": 3.5}, "hhad": None, "ttg": {}}}]

    def prev(source, key):
        if source == "zucai":
            return {"had": {"h": 1.8, "d": 3.0, "a": 3.9}}  # 主 1.8→2.0 升, 客 3.9→3.5 降
        return None

    p = ra.build_panel(zucai, [], [], prev_lookup=prev)[0]
    assert p["sources"]["zucai"]["delta"]["h"] == "▲"
    assert p["sources"]["zucai"]["delta"]["d"] == "-"
    assert p["sources"]["zucai"]["delta"]["a"] == "▼"


def test_build_panel_aligns_despite_name_variants():
    # 竞彩"沙特" vs 欧盘"沙特阿拉伯"; 竞彩"刚果金" vs 欧盘"刚果(金)" 都应对齐
    zucai = [
        {"match_key": "周五073", "label": "佛得角 vs 沙特", "ko": "k",
         "payload": {"had": {"h": 2.6, "d": 3.3, "a": 2.6}, "hhad": None, "ttg": {}}},
        {"match_key": "周五074", "label": "刚果金 vs 乌兹别克", "ko": "k",
         "payload": {"had": {"h": 2.0, "d": 3.0, "a": 3.5}, "hhad": None, "ttg": {}}},
    ]
    consensus = [
        {"match_key": "500-a", "label": "佛得角 vs 沙特阿拉伯", "ko": "k",
         "payload": {"had": {"h": 2.6, "d": 3.3, "a": 2.6},
                     "devig_pct": {"h": 35.0, "d": 28.0, "a": 37.0}, "n_books": 40}},
        {"match_key": "500-b", "label": "刚果(金) vs 乌兹别克", "ko": "k",
         "payload": {"had": {"h": 2.0, "d": 3.0, "a": 3.5},
                     "devig_pct": {"h": 45.0, "d": 28.0, "a": 27.0}, "n_books": 40}},
    ]
    panel = ra.build_panel(zucai, consensus, [], prev_lookup=lambda s, k: None)
    assert not panel[0]["sources"]["consensus"]["stale"]   # 沙特/沙特阿拉伯 对齐
    assert not panel[1]["sources"]["consensus"]["stale"]   # 刚果金/刚果(金) 对齐


def test_team_eq_no_false_positive():
    # 刚果金 vs 刚果布 不应误配
    assert ra._team_eq("刚果金", "刚果(金)")
    assert not ra._team_eq("刚果金", "刚果布")
