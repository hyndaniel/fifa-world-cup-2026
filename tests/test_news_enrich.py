from backend.db import Db
from backend.news_enrich import enrich_once, fetch_team_news, watch_teams

_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>\xe8\xa5\xbf\xe7\x8f\xad\xe7\x89\x99\xe8\xb5\x9b\xe5\x89\x8d</title>
  <link>https://news.example/a</link><pubDate>Mon, 15 Jun 2026 10:00:00 GMT</pubDate></item>
<item><title>\xe5\xa4\xba\xe5\x86\xa0\xe7\x83\xad\xe9\x97\xa8</title>
  <link>https://news.example/b</link><pubDate>Mon, 15 Jun 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_fetch_team_news_parses_injected_rss():
    items = fetch_team_news("西班牙", fetch=lambda url: _RSS)
    assert len(items) == 2
    assert items[0]["url"] == "https://news.example/a"
    assert items[0]["title"] and items[0]["ts"].startswith("Mon")


def test_fetch_team_news_error_returns_empty():
    def boom(url):
        raise RuntimeError("network down")
    assert fetch_team_news("西班牙", fetch=boom) == []


def test_watch_teams_collects_and_splits(tmp_path):
    db = Db(tmp_path / "t.db")
    db.init()
    db.add_watch(kind="team", key="西班牙")
    db.add_watch(kind="match", key="比利时 vs 埃及")
    db.add_watch(kind="team", key="西班牙")  # 重复
    teams = watch_teams(db)
    assert teams == ["西班牙", "比利时", "埃及"]


def test_enrich_once_writes_enrich(tmp_path):
    db = Db(tmp_path / "t.db")
    db.init()
    db.add_watch(kind="team", key="西班牙")
    n = enrich_once(db, fetch=lambda url: _RSS)
    assert n == 1
    en = db.latest_enrich("西班牙")
    assert en is not None
    assert en["lineup"] is None          # 无免费源 → 未出炉
    assert len(en["news"]) == 2
