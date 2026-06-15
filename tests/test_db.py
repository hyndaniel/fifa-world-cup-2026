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


def test_add_bet_and_ledger(tmp_path):
    db = Db(tmp_path / "t.db")
    db.init()
    db.add_bet(wallet="A", legs=[{"m": "x", "o": "平"}], stake=5, odds=4.55)
    led = db.ledger()
    assert led["A"]["n"] == 1 and led["A"]["stake"] == 5
