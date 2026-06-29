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
    assert p["match_key"] == "土耳其 vs 美国"      # = 队名 label, 对齐决策卡 match_key
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


def test_team_eq_rejects_prefix_extended_different_teams():
    # 前缀扩展出的不同球队不应误配(短名是长名的后缀, 非前缀)
    assert not ra._team_eq("几内亚", "赤道几内亚")
    assert not ra._team_eq("苏丹", "南苏丹")
    assert not ra._team_eq("爱尔兰", "北爱尔兰")
    # 合法前缀缩写仍对齐
    assert ra._team_eq("沙特", "沙特阿拉伯")


def test_find_by_teams_exact_preferred_over_loose():
    items = [
        {"match_key": "L", "label": "沙特阿拉伯 vs 美国"},   # 宽松(前缀)候选
        {"match_key": "E", "label": "沙特 vs 美国"},         # 精确候选
    ]
    assert ra._find_by_teams(items, "沙特", "美国")["match_key"] == "E"


def test_find_by_teams_ambiguous_loose_returns_none():
    # 两个宽松候选 → 不猜, 返 None
    items = [
        {"match_key": "A", "label": "沙特阿拉伯 vs 美国"},
        {"match_key": "B", "label": "沙特 vs 美国"},
    ]
    # 用一个既非精确、又同时前缀命中两者的查询("沙" 前缀 both)
    assert ra._find_by_teams(items, "沙", "美国") is None


def test_build_panel_poly_aligned_by_match_key():
    zucai = [{"match_key": "周四055", "label": "土耳其 vs 美国", "ko": "k",
              "payload": {"had": {"h": 2.7, "d": 3.4, "a": 1.95}, "hhad": None, "ttg": {}}}]
    # poly label 故意写错, 但 match_key 对 → 仍应命中(按 key 不按 label)
    poly = [{"match_key": "周四055", "label": "錯的标签", "ko": "k",
             "payload": {"poly_devig": {"h": 30.0, "d": 28.0, "a": 42.0}}}]
    p = ra.build_panel(zucai, [], poly, prev_lookup=lambda s, k: None)[0]
    assert p["sources"]["poly"]["stale"] is False
    assert p["sources"]["poly"]["devig"]["h"] == 30.0


def _stub_fetches(monkeypatch):
    """把 run_once 的抓取/缓存层全 stub 成空(不打真网、不碰真 db),只验推送→退出码。"""
    class _Conn:
        def commit(self):
            pass
    monkeypatch.setattr(ra.sporttery, "fetch", lambda *a, **k: {"raw": 1})  # raw_env truthy
    monkeypatch.setattr(ra.ow, "fetch_zucai", lambda *a, **k: [])
    monkeypatch.setattr(ra.ow, "fetch_consensus", lambda *a, **k: [])
    monkeypatch.setattr(ra, "_fetch_poly_local", lambda *a, **k: [])
    monkeypatch.setattr(ra.ow, "connect", lambda *a, **k: _Conn())
    monkeypatch.setattr(ra.ow, "save", lambda *a, **k: None)
    monkeypatch.setattr(ra.ow, "latest", lambda *a, **k: None)


def test_run_once_returns_1_when_all_posts_fail(monkeypatch):
    # error-contract:两次 POST 全失败(_post→None)→ run_once 返 1,别让 launchd 记成健康。
    _stub_fetches(monkeypatch)
    monkeypatch.setattr(ra, "_post", lambda path, body: None)
    assert ra.run_once(dry_run=False) == 1


def test_run_once_returns_0_when_a_post_succeeds(monkeypatch):
    # 至少一条推成(返 dict)→ 0(部分成功不判失败)。
    _stub_fetches(monkeypatch)
    monkeypatch.setattr(ra, "_post", lambda path, body: {"ok": True})
    assert ra.run_once(dry_run=False) == 0


def test_run_once_dry_run_returns_0(monkeypatch):
    # dry-run 不推 HK → 恒 0(冒烟不应判失败)。
    _stub_fetches(monkeypatch)
    monkeypatch.setattr(ra, "_post", lambda path, body: None)  # 即便会失败,dry-run 也不触达
    assert ra.run_once(dry_run=True) == 0
