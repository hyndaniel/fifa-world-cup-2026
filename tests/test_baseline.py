import sqlite3, json, os, tempfile
from backend.baseline import zucai_had_devig, blend_had, confidence, DEFAULT_WEIGHTS
from backend.baseline import baseline_had
from backend.baseline import record_result, get_result
from backend.baseline import zucai_odds_devig, blend  # 追加到现有 import 行下方
from backend.baseline import baseline_market, HHAD_CFG, _hhad_outcome  # 追加
from backend.baseline import TTG_CFG, over_under, _ttg_outcome, get_result_goals  # 追加


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


def test_zucai_odds_devig_generic_keys_sum_100():
    out = zucai_odds_devig({"0": 5.0, "1": 2.5, "2": 3.0}, keys=("0", "1", "2"))
    assert abs(sum(out.values()) - 100.0) < 0.5      # 1 位四舍五入残差容差
    assert out["1"] > out["2"] > out["0"]            # 赔率越低概率越高


def test_blend_generic_keys_single_source_sum_100():
    out = blend({"zucai": {"0": 20.0, "1": 30.0, "2": 25.0, "3": 25.0}},
                keys=("0", "1", "2", "3"), weights={"zucai": 1.0})
    assert abs(sum(out.values()) - 100.0) < 0.01
    assert set(out) == {"0", "1", "2", "3"}


def test_confidence_generic_keys_single_source_soft():
    c = confidence({"zucai": {"0": 50, "1": 50}}, keys=("0", "1"))
    assert c["n_sources"] == 1 and c["label"] == "soft"


def _seed_cache(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    rows = [
        ("2026-06-25T09:00:00+08:00", "zucai", "周三053", "南非 vs 韩国", "ko",
         json.dumps({"had": {"h": 6.00, "d": 3.87, "a": 1.44}})),
        ("2026-06-25T09:00:00+08:00", "poly", "周三053", "南非 vs 韩国", "ko",
         json.dumps({"poly_devig": {"h": 15.4, "d": 23.4, "a": 61.2}})),
        ("2026-06-25T09:00:00+08:00", "consensus", "周三053", "南非 vs 韩国", "ko",
         json.dumps({"had": {"h": 6.5, "d": 4.0, "a": 1.55}})),
    ]
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def test_baseline_had_assembles_three_sources():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    out = baseline_had(path, "周三053")
    assert out is not None
    assert set(out["sources"]) == {"zucai", "poly", "consensus"}
    assert out["confidence"]["label"] == "hard"
    assert abs(sum(out["baseline"].values()) - 100.0) < 0.01
    # 三源都看好客胜(韩国) → 融合客胜应最高
    assert out["baseline"]["a"] > out["baseline"]["h"]


def test_baseline_had_missing_match_returns_none():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    assert baseline_had(path, "周三999") is None


def test_record_and_get_result_outcome_key():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)  # 表 odds_cache 已建;match_results 由 record_result 自建
    assert record_result(path, "周三053", 0, 1) == "a"   # 客胜
    assert get_result(path, "周三053") == "a"
    assert record_result(path, "X", 2, 2) == "d"          # 平
    assert record_result(path, "Y", 3, 0) == "h"          # 主胜
    # 替换语义:重录覆盖
    assert record_result(path, "周三053", 2, 0) == "h"
    assert get_result(path, "周三053") == "h"


def test_get_result_missing_returns_none():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    assert get_result(path, "周三053") is None


def _seed_hhad(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("t", "zucai", "周三053", "南非 vs 韩国", "ko",
                  json.dumps({"hhad": {"line": -1, "h": 2.10, "d": 3.30, "a": 3.00}})))
    conn.commit(); conn.close()


def test_baseline_market_hhad_single_source_soft_with_line():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_hhad(path)
    out = baseline_market(path, "周三053", HHAD_CFG)
    assert out["market"] == "hhad" and out["line"] == -1
    assert set(out["sources"]) == {"zucai"}
    assert out["confidence"]["label"] == "soft"
    assert abs(sum(out["baseline"].values()) - 100.0) < 0.01


def test_baseline_had_still_three_source_hard():
    # 零回归: baseline_had(经 baseline_market) 仍三源硬锚
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_cache(path)
    out = baseline_had(path, "周三053")
    assert out["confidence"]["label"] == "hard"
    assert out["baseline"]["a"] > out["baseline"]["h"]


def test_hhad_outcome_boundaries():
    assert _hhad_outcome(2, 0, -1) == "h"   # 主让一球, 2-0 净+1 → 赢盘
    assert _hhad_outcome(1, 0, -1) == "d"   # 主让一球, 1-0 净 0  → 走盘(竞彩计平)
    assert _hhad_outcome(0, 0, -1) == "a"   # 主让一球, 0-0 净-1 → 输盘
    assert _hhad_outcome(0, 1, 1) == "d"    # 主受让一球, 0-1 净 0 → 平


def test_hhad_outcome_verified_convention_home_giving_negative():
    # 实测真实缓存确认: 主队让球记负(主让一球 line=-1), 受让记正(line=+1)
    assert _hhad_outcome(2, 0, -1) == "h"   # 主让一球, 净胜2 → 赢盘
    assert _hhad_outcome(1, 0, -1) == "d"   # 主让一球, 净胜1=让球数 → 走盘(平)
    assert _hhad_outcome(0, 0, 1) == "h"    # 主受让一球, 平局 → 赢盘
    assert _hhad_outcome(0, 1, 1) == "d"    # 主受让一球, 输1球 → 走盘(平)


def _seed_ttg(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("t", "zucai", "周三053", "南非 vs 韩国", "ko",
                  json.dumps({"ttg": {"0": 12.0, "1": 6.0, "2": 4.5, "3": 4.5,
                                      "4": 7.0, "5": 13.0, "6": 26.0, "7": 41.0}})))
    conn.commit(); conn.close()


def test_baseline_market_ttg_dynamic_keys_sum_100():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_ttg(path)
    out = baseline_market(path, "周三053", TTG_CFG)
    assert out["market"] == "ttg"
    assert set(out["baseline"]) == {"0", "1", "2", "3", "4", "5", "6", "7"}
    assert abs(sum(out["baseline"].values()) - 100.0) < 0.01
    assert out["confidence"]["label"] == "soft"   # 竞彩单源


def test_over_under_derives_from_dist():
    dist = {"0": 10.0, "1": 20.0, "2": 30.0, "3": 25.0, "4": 15.0}
    ou = over_under(dist, lines=(2.5,))
    assert ou["2.5"]["over"] == 40.0    # P(3)+P(4)
    assert ou["2.5"]["under"] == 60.0


def test_ttg_outcome_caps_at_7():
    assert _ttg_outcome(2, 1) == "3"
    assert _ttg_outcome(5, 4) == "7"    # 9 → cap 7


def test_get_result_goals_roundtrip():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed_ttg(path)
    record_result(path, "周三053", 2, 1)
    assert get_result_goals(path, "周三053") == (2, 1)
    assert get_result_goals(path, "缺") is None


def test_actual_for_dispatches_by_market():
    from backend.baseline import _actual_for
    assert _actual_for("had", 2, 0, None) == "h"
    assert _actual_for("hhad", 1, 0, -1) == "d"     # 主让一球 1-0 → 走盘
    assert _actual_for("hhad", 2, 0, None) is None  # 让球缺 line → None(不计分)
    assert _actual_for("ttg", 2, 1) == "3"          # line 省略默认 None
