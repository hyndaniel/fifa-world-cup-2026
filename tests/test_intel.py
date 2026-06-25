from datetime import datetime, timezone, timedelta
from backend.db import Db
from backend.intel import match_fact_card

BJ = timezone(timedelta(hours=8))
NOW = datetime(2026, 6, 25, 20, 0, 0, tzinfo=BJ)   # 注入"现在"=北京 20:00


def _db(tmp_path):
    db = Db(str(tmp_path / "wc.db")); db.init()
    db.upsert_match("周四055", "南非", "韩国", "SA", "KOR", None, "23:00", "22:45", "Selling")
    return db


def test_fact_card_ages_caps_and_flags_stale(tmp_path):
    db = _db(tmp_path)
    db.save_enrich("南非", None, [
        {"title": "n_new", "url": "u1", "ts": "25 Jun 2026 09:00:00 +0000"},  # 17:00 BJ → 3h
        {"title": "n_old", "url": "u2", "ts": "21 Jun 2026 08:00:00 +0000"},  # >48h
        {"title": "n_bad", "url": "u3", "ts": "不是时间"},                      # 不可解析
    ])
    card = match_fact_card(db, "周四055", NOW, cap=5, stale_hours=48)
    assert card["match"] == "南非 vs 韩国"
    sa = next(t for t in card["teams"] if t["team"] == "南非")
    assert sa["has_intel"] is True and sa["lineup"] is None
    assert sa["news"][0]["title"] == "n_new"                       # 最新排首
    new = next(n for n in sa["news"] if n["title"] == "n_new")
    assert abs(new["age_h"] - 3.0) < 0.2 and new["stale"] is False
    assert next(n for n in sa["news"] if n["title"] == "n_old")["stale"] is True
    bad = next(n for n in sa["news"] if n["title"] == "n_bad")
    assert bad["age_h"] is None and bad["stale"] is True           # 不可解析 = stale


def test_fact_card_cap_limits_per_team(tmp_path):
    db = _db(tmp_path)
    db.save_enrich("南非", None, [{"title": f"n{i}", "url": f"u{i}",
                   "ts": "25 Jun 2026 09:00:00 +0000"} for i in range(10)])
    sa = next(t for t in match_fact_card(db, "周四055", NOW, cap=3)["teams"]
              if t["team"] == "南非")
    assert len(sa["news"]) == 3


def test_fact_card_team_without_enrich_has_no_intel(tmp_path):
    db = _db(tmp_path)
    db.save_enrich("南非", None, [{"title": "x", "url": "u", "ts": "25 Jun 2026 09:00:00 +0000"}])
    kor = next(t for t in match_fact_card(db, "周四055", NOW)["teams"]
               if t["team"] == "韩国")
    assert kor["has_intel"] is False and kor["news"] == []


def test_fact_card_missing_match(tmp_path):
    db = _db(tmp_path)
    card = match_fact_card(db, "无此场", NOW)
    assert card["teams"] == [] and card["note"] == "无此场"
