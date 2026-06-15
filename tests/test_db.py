from backend.db import Db


def test_init_and_upsert_match(tmp_path):
    db = Db(tmp_path / "t.db")
    db.init()
    mid = db.upsert_match(
        zucai_num="周一013", home_cn="西班牙", away_cn="佛得角",
        home_en="Spain", away_en="Cabo Verde", poly_slug="fifwc-esp-cvi-2026-06-15",
        ko_bj="6.16 00:00", cutoff_bj="23:00",
    )
    rows = db.matches()
    assert rows[0]["home_cn"] == "西班牙" and rows[0]["poly_slug"].startswith("fifwc-")
    assert mid is not None
    # 回归: 再次 upsert(冲突→UPDATE) 必须返回同一 id, 不能返 None
    mid2 = db.upsert_match(
        zucai_num="周一013", home_cn="西班牙", away_cn="佛得角",
        home_en="Spain", away_en="Cabo Verde", poly_slug="fifwc-esp-cvi-2026-06-15",
        ko_bj="6.16 00:00", cutoff_bj="22:50",
    )
    assert mid2 == mid
    assert len(db.matches()) == 1  # 仍是一场(更新非新增)


def test_save_value_points_replace_and_match_id(tmp_path):
    """value_points 带正确 match_id, 且 latest_value_points 能取到(回归 NULL match_id)。"""
    db = Db(tmp_path / "t.db")
    db.init()
    mid = db.upsert_match(
        zucai_num="周一013", home_cn="西", away_cn="佛", home_en="Spain",
        away_en="Cabo Verde", poly_slug="s", ko_bj="k", cutoff_bj="c",
    )
    db.save_value_points(mid, [{"market": "让-2", "outcome": "平", "zucai_odds": 4.55,
        "poly_prob_raw": 23.0, "poly_prob_devig": 22.3, "value_raw": 1.046,
        "value_devig": 1.015, "ev_pct": 1.5, "flag": "yellow"}])
    lvp = db.latest_value_points()
    assert len(lvp) == 1 and lvp[0]["match_id"] == mid
    # 替换语义: 再存一批应覆盖, 不累积
    db.save_value_points(mid, [{"market": "胜平负", "outcome": "客胜", "zucai_odds": 5.0,
        "poly_prob_raw": 20.0, "poly_prob_devig": 20.0, "value_raw": 1.0,
        "value_devig": 1.0, "ev_pct": 0.0, "flag": "yellow"}])
    lvp2 = db.latest_value_points()
    assert len(lvp2) == 1 and lvp2[0]["market"] == "胜平负"


def test_add_bet_and_ledger(tmp_path):
    db = Db(tmp_path / "t.db")
    db.init()
    db.add_bet(wallet="A", legs=[{"m": "x", "o": "平"}], stake=5, odds=4.55)
    led = db.ledger()
    assert led["A"]["n"] == 1 and led["A"]["stake"] == 5
