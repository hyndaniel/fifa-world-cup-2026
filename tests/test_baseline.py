import sqlite3, json, os, tempfile
from backend.baseline import zucai_had_devig, blend_had, confidence, DEFAULT_WEIGHTS
from backend.baseline import baseline_had
from backend.baseline import record_result, get_result


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
