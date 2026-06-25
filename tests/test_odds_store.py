import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.db import Db  # noqa: E402


def _db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = Db(f.name)
    db.init()
    return db


def test_save_and_get_odds_upsert():
    db = _db()
    db.save_odds([{"match_key": "周四055", "label": "A vs B",
                   "sources": {"zucai": {"had": {"h": 2.0}}}}])
    db.save_odds([{"match_key": "周四055", "label": "A vs B",
                   "sources": {"zucai": {"had": {"h": 1.8}}}}])  # 覆盖
    m = db.get_odds()
    assert set(m.keys()) == {"周四055"}
    assert m["周四055"]["sources"]["zucai"]["had"]["h"] == 1.8


def test_save_odds_skips_missing_key():
    db = _db()
    n = db.save_odds([{"label": "no key"}, {"match_key": "周四056", "sources": {}}])
    assert n == 1
    assert list(db.get_odds().keys()) == ["周四056"]
