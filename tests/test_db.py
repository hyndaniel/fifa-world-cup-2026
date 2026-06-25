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


def test_enrich_save_latest_replace(tmp_path):
    """save_enrich 存阵容+新闻; latest_enrich 解析; latest_enrich_all 含此队;
    重存覆盖; lineup=None 存 NULL 并返回 None。"""
    db = Db(tmp_path / "t.db")
    db.init()
    lineup = {"formation": "4-3-3", "players": ["A", "B"], "source": "s", "note": ""}
    news = [{"title": "西班牙备战", "url": "http://x/1", "ts": "2026-06-15"}]
    db.save_enrich("西班牙", lineup, news)

    got = db.latest_enrich("西班牙")
    assert got is not None
    assert got["team_cn"] == "西班牙"
    assert got["lineup"] == lineup
    assert got["news"] == news
    assert got["ts"]  # ts 非空

    all_ = db.latest_enrich_all()
    assert "西班牙" in all_
    assert all_["西班牙"]["lineup"]["formation"] == "4-3-3"

    # 缺席的队 → None
    assert db.latest_enrich("不存在") is None

    # 替换语义: 重存覆盖, 仍单行
    db.save_enrich("西班牙", {"formation": "4-4-2", "players": ["C"],
                              "source": "s2", "note": "更新"}, [])
    got2 = db.latest_enrich("西班牙")
    assert got2["lineup"]["formation"] == "4-4-2"
    assert got2["news"] == []
    assert len(db.latest_enrich_all()) == 1

    # lineup=None → 存 NULL, 返回 None; news 仍可存
    db.save_enrich("西班牙", None, news)
    got3 = db.latest_enrich("西班牙")
    assert got3["lineup"] is None
    assert got3["news"] == news
