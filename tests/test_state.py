from datetime import datetime, timezone, timedelta

from backend.db import Db
from backend.config import load_config
from backend.state import build_state

BJ = timezone(timedelta(hours=8))


def _seed(db):
    """两场比赛 + 各自 value_points(含 green/yellow/skip) + watchlist + 一笔已结算 bet。"""
    mid1 = db.upsert_match(
        zucai_num="周一013", home_cn="西班牙", away_cn="佛得角",
        home_en="Spain", away_en="Cabo Verde", poly_slug="fifwc-esp-cvi-2026-06-15",
        ko_bj="6.16 00:00", cutoff_bj="23:00",
    )
    mid2 = db.upsert_match(
        zucai_num="周一014", home_cn="法国", away_cn="韩国",
        home_en="France", away_en="South Korea", poly_slug="fifwc-fra-kor-2026-06-15",
        ko_bj="6.16 03:00", cutoff_bj="02:00",
    )
    db.save_value_points(mid1, [
        {"market": "让-2", "outcome": "平", "zucai_odds": 4.55,
         "poly_prob_raw": 23.0, "poly_prob_devig": 22.3,
         "value_raw": 1.046, "value_devig": 1.015, "ev_pct": 1.5, "flag": "yellow"},
        {"market": "胜平负", "outcome": "主胜", "zucai_odds": 1.41,
         "poly_prob_raw": 91.5, "poly_prob_devig": 91.0,
         "value_raw": 1.290, "value_devig": 1.283, "ev_pct": 28.3, "flag": "green"},
        {"market": "总进球", "outcome": "7+球", "zucai_odds": 7.0,
         "poly_prob_raw": 0.0, "poly_prob_devig": 0.0,
         "value_raw": 0.0, "value_devig": 0.0, "ev_pct": 0.0, "flag": "skip"},
    ])
    db.save_value_points(mid2, [
        {"market": "胜平负", "outcome": "主胜", "zucai_odds": 1.60,
         "poly_prob_raw": 65.0, "poly_prob_devig": 64.0,
         "value_raw": 1.040, "value_devig": 1.024, "ev_pct": 2.4, "flag": "yellow"},
    ])
    db.add_watch(kind="team", key="西班牙", note="看好")
    db.add_bet(wallet="A", legs=[{"m": "esp", "o": "平"}], stake=5, odds=4.55)
    bid = db.add_bet(wallet="A", legs=[{"m": "fra", "o": "主胜"}], stake=10, odds=1.60)
    db.settle_bet(bid, status="won", payout=16.0)
    return mid1, mid2


def _state():
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Db(path)
    db.init()
    _seed(db)
    cfg = load_config("nope.toml")
    # now 选在两个 cutoff (02:00, 23:00) 之前, 同一天 6.16
    now = datetime(2026, 6, 16, 20, 30, tzinfo=BJ)
    return build_state(db, cfg, now), db, path


def test_state_shape_top_level():
    st, db, path = _state()
    for k in ("ts", "next_cutoff", "value_radar", "watchlist", "ledger", "matches_today"):
        assert k in st


def test_value_radar_sorted_and_filtered():
    st, db, path = _state()
    radar = st["value_radar"]
    # skip 被排除: 只剩 3 条 (green/yellow)
    assert len(radar) == 3
    assert all(r["flag"] in ("green", "yellow") for r in radar)
    # 按 ev (去水) 降序
    evs = [r["ev_pct_devig"] for r in radar]
    assert evs == sorted(evs, reverse=True)
    # 头条是 28.3 那个 (法国主胜 2.4 / 西佛让-2 1.5)
    assert radar[0]["ev_pct_devig"] == 28.3
    assert radar[0]["market"] == "胜平负" and radar[0]["outcome"] == "主胜"


def test_radar_item_fields_and_match_name():
    st, db, path = _state()
    top = st["value_radar"][0]
    for k in ("match", "ko_bj", "market", "outcome", "zucai_odds",
              "poly_prob_devig", "poly_prob_raw", "ev_pct_devig",
              "ev_pct_raw", "flag", "cutoff_bj"):
        assert k in top
    assert top["match"] == "西班牙 vs 佛得角"
    assert top["cutoff_bj"] == "23:00"
    # ev_pct_raw 由 value_raw 派生: (1.290-1)*100 = 29.0
    assert abs(top["ev_pct_raw"] - 29.0) < 0.01


def test_next_cutoff_countdown():
    st, db, path = _state()
    nc = st["next_cutoff"]
    assert nc is not None
    # 最近未过 cutoff 是 23:00 (西佛); 法韩 02:00 已过 now=20:30
    assert nc["match"] == "西班牙 vs 佛得角"
    assert nc["cutoff_bj"] == "23:00"
    # 20:30 -> 23:00 = 2h30m = 9000s
    assert nc["countdown_sec"] == 9000


def test_ledger_shape():
    st, db, path = _state()
    led = st["ledger"]
    assert set(led["A"].keys()) == {"stake", "pnl", "roi", "n"}
    assert set(led["B"].keys()) == {"budget", "spent", "pnl", "n"}
    assert led["A"]["n"] == 2 and led["A"]["stake"] == 15
    # 已结算一笔: payout16 - stake10 = +6
    assert led["A"]["pnl"] == 6.0


def test_watchlist_and_matches_today():
    st, db, path = _state()
    w = st["watchlist"]
    assert len(w) == 1 and w[0]["key"] == "西班牙"
    for k in ("kind", "key", "note", "matches", "lineup", "news", "radar_hits"):
        assert k in w[0]
    # 两场都在 6.16
    assert len(st["matches_today"]) == 2


def test_watchlist_enriched():
    """team watch 富化: 关联场次 + 阵容 + 新闻 + 雷达命中。"""
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Db(path)
    db.init()
    mid = db.upsert_match(
        zucai_num="周一013", home_cn="西班牙", away_cn="佛得角",
        home_en="Spain", away_en="Cabo Verde", poly_slug="fifwc-esp-cvi-2026-06-15",
        ko_bj="6.16 00:00", cutoff_bj="23:00",
    )
    db.save_value_points(mid, [
        {"market": "胜平负", "outcome": "主胜", "zucai_odds": 1.41,
         "poly_prob_raw": 91.5, "poly_prob_devig": 91.0,
         "value_raw": 1.290, "value_devig": 1.283, "ev_pct": 28.3, "flag": "green"},
    ])
    db.save_enrich(
        "西班牙",
        {"formation": "4-3-3", "players": ["佩德里", "亚马尔"],
         "source": "test", "note": ""},
        [{"title": "西班牙公布首发", "url": "http://x/1", "ts": "6.16"}],
    )
    db.add_watch(kind="team", key="西班牙", note="看好")
    cfg = load_config("nope.toml")
    now = datetime(2026, 6, 16, 20, 30, tzinfo=BJ)
    st = build_state(db, cfg, now)

    w = st["watchlist"]
    assert len(w) == 1
    item = w[0]
    # 关联场次非空 (key 命中 home_cn)
    assert len(item["matches"]) >= 1
    assert item["matches"][0]["match"] == "西班牙 vs 佛得角"
    # 阵容回来
    assert item["lineup"] is not None
    assert item["lineup"]["formation"] == "4-3-3"
    assert "佩德里" in item["lineup"]["players"]
    # 新闻回来
    assert len(item["news"]) == 1
    assert item["news"][0]["url"] == "http://x/1"
    # 雷达命中非空 (绿灯, 属关联场次)
    assert len(item["radar_hits"]) >= 1
    hit = item["radar_hits"][0]
    for k in ("match", "market", "outcome", "zucai_odds",
              "poly_prob_devig", "ev_pct_devig", "flag"):
        assert k in hit
    assert hit["flag"] == "green"
    assert hit["ev_pct_devig"] == 28.3
