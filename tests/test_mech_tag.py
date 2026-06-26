# tests/test_mech_tag.py
"""机械对错标:三方 had argmax vs 实际 outcome 的纯事实判定。"""
import json
import sqlite3

from backend.baseline import record_result
from backend.mech_tag import mech_tags
from backend.v1_log import record_v1
from backend.v2_predict import build_v2_prediction, record_v2_prediction


def _empty_odds(db):
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE IF NOT EXISTS odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.commit()
    conn.close()


def _seed_odds_h_favored(db, mk):
    """三源都看主胜 h(zucai/consensus 主胜赔率最低, poly 主胜概率最高)。"""
    _empty_odds(db)
    conn = sqlite3.connect(db)
    rows = [
        ("t", "zucai", mk, "主 vs 客", "ko",
         json.dumps({"had": {"h": 1.44, "d": 3.87, "a": 6.00}})),
        ("t", "poly", mk, "主 vs 客", "ko",
         json.dumps({"poly_devig": {"h": 61.2, "d": 23.4, "a": 15.4}})),
        ("t", "consensus", mk, "主 vs 客", "ko",
         json.dumps({"had": {"h": 1.55, "d": 4.0, "a": 6.5}})),
    ]
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_mech_tags_marks_each_side(tmp_path):
    # 造场景:市场看 h、v2 看 h、v1 看 a、实际 h → market✅ v2✅ v1❌
    db = str(tmp_path / "t.db")
    mk = "周四055"
    _seed_odds_h_favored(db, mk)                            # 市场基线 argmax = h
    record_v1(db, mk, {"h": 20, "d": 30, "a": 50}, "0-1")  # v1 probs argmax = a
    # v2 用真实 build/record:had 基线偏主胜、无偏离 → markets.had.v2 argmax = h
    pred = build_v2_prediction(mk, "稳", [],
                               {"had": {"baseline": {"h": 60.0, "d": 25.0, "a": 15.0},
                                        "deviations": []}})
    record_v2_prediction(db, mk, pred)
    record_result(db, mk, 2, 0)                            # 实际 2-0 主胜 → actual = h

    t = mech_tags(db, mk)
    assert t["match_key"] == mk
    assert t["actual"] == "h"
    assert t["market"] == "✅"   # 市场 argmax h,中
    assert t["v2"] == "✅"       # v2 argmax h,中
    assert t["v1"] == "❌"       # v1 argmax a,错


def test_mech_tags_no_result_all_dash(tmp_path):
    # 无赛果 → actual=None,三方一律 —(即便有预测)
    db = str(tmp_path / "t.db")
    mk = "周四056"
    _seed_odds_h_favored(db, mk)
    record_v1(db, mk, {"h": 50, "d": 30, "a": 20}, "1-0")
    pred = build_v2_prediction(mk, "稳", [],
                               {"had": {"baseline": {"h": 60.0, "d": 25.0, "a": 15.0},
                                        "deviations": []}})
    record_v2_prediction(db, mk, pred)

    t = mech_tags(db, mk)
    assert t["actual"] is None
    assert t["v1"] == "—" and t["v2"] == "—" and t["market"] == "—"


def test_mech_tags_missing_sides_dash(tmp_path):
    # 有赛果但某方无预测/无基线 → 该方 —
    db = str(tmp_path / "t.db")
    mk = "周四057"
    _empty_odds(db)                 # odds_cache 存在但无该场 → 市场无基线
    record_result(db, mk, 1, 1)     # 平 → actual = d
    # 无 v1、无 v2

    t = mech_tags(db, mk)
    assert t["actual"] == "d"
    assert t["market"] == "—"
    assert t["v1"] == "—"
    assert t["v2"] == "—"
