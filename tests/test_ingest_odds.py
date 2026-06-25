import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient  # noqa: E402
from backend import web  # noqa: E402


def _client():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    app = web.create_app(db_path=f.name, cfg={}, require_auth=False)
    return TestClient(app), app.state.db


def test_ingest_odds_stores_items():
    client, db = _client()
    r = client.post(
        "/api/ingest/odds",
        json={"items": [{"match_key": "周四055", "label": "A vs B", "sources": {}}]},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    assert "周四055" in db.get_odds()


def test_ingest_odds_skips_missing_key():
    client, db = _client()
    r = client.post(
        "/api/ingest/odds",
        json={"items": [{"label": "no key"}, {"match_key": "周四056", "sources": {}}]},
    )
    assert r.json()["n"] == 1
    assert r.json()["skipped"] == 1
    assert list(db.get_odds().keys()) == ["周四056"]
