import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from backend.config import load_config
from backend.db import Db
from backend.web import create_app


def _seed(db):
    """一场比赛 + value_points(green/yellow/skip) + watchlist + 一笔 bet。"""
    mid = db.upsert_match(
        zucai_num="周一013", home_cn="西班牙", away_cn="佛得角",
        home_en="Spain", away_en="Cabo Verde", poly_slug="fifwc-esp-cvi-2026-06-15",
        ko_bj="6.16 00:00", cutoff_bj="23:00",
    )
    db.save_value_points(mid, [
        {"market": "胜平负", "outcome": "主胜", "zucai_odds": 1.41,
         "poly_prob_raw": 91.5, "poly_prob_devig": 91.0,
         "value_raw": 1.290, "value_devig": 1.283, "ev_pct": 28.3, "flag": "green"},
        {"market": "让-2", "outcome": "平", "zucai_odds": 4.55,
         "poly_prob_raw": 23.0, "poly_prob_devig": 22.3,
         "value_raw": 1.046, "value_devig": 1.015, "ev_pct": 1.5, "flag": "yellow"},
        {"market": "总进球", "outcome": "7+球", "zucai_odds": 7.0,
         "poly_prob_raw": 0.0, "poly_prob_devig": 0.0,
         "value_raw": 0.0, "value_devig": 0.0, "ev_pct": 0.0, "flag": "skip"},
    ])
    db.add_watch(kind="team", key="西班牙", note="看好")


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "t.db"
    db = Db(str(db_path))
    db.init()
    _seed(db)
    cfg = load_config("nope.toml")
    # 关闭鉴权 (require_auth=False)
    app = create_app(db_path=str(db_path), cfg=cfg, require_auth=False)
    return TestClient(app), db


def test_state_ok_and_has_value_radar(client):
    c, _ = client
    r = c.get("/api/state")
    assert r.status_code == 200
    body = r.json()
    assert "value_radar" in body
    # skip 被排除 → 2 条 (green/yellow)
    assert len(body["value_radar"]) == 2
    assert all(it["flag"] in ("green", "yellow") for it in body["value_radar"])


def test_reports_list_ok(client):
    c, _ = client
    r = c.get("/api/reports")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    if data:
        assert {"name", "title"} <= set(data[0].keys())


def test_read_single_report(client):
    c, _ = client
    listing = c.get("/api/reports").json()
    if not listing:
        pytest.skip("no reports on disk")
    name = listing[0]["name"]
    r = c.get(f"/api/reports/{name}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == name
    assert isinstance(body["content"], str) and len(body["content"]) > 0


def test_report_traversal_rejected(client):
    c, _ = client
    # 目录穿越尝试 → 不应 200 泄漏文件 (path param 编码后落到 404/400/405)
    r = c.get("/api/reports/..%2F..%2Fbackend%2Fweb")
    assert r.status_code in (400, 404, 405)


def test_post_bet_reflected_in_ledger(client):
    c, db = client
    before = db.ledger()["A"]["n"]
    r = c.post("/api/bets", json={
        "wallet": "A", "legs": [{"m": "esp", "o": "平"}],
        "stake": 5, "odds": 4.55,
    })
    assert r.status_code == 200
    assert "id" in r.json()
    led = db.ledger()["A"]
    assert led["n"] == before + 1
    assert led["stake"] == 5.0
    # /api/state 的 ledger 也反映
    state_led = c.get("/api/state").json()["ledger"]["A"]
    assert state_led["n"] == before + 1


def test_watchlist_get_post_delete(client):
    c, _ = client
    # GET: seed 里已有 1 条
    r = c.get("/api/watchlist")
    assert r.status_code == 200
    assert len(r.json()) == 1
    # POST: 新增
    r = c.post("/api/watchlist", json={"kind": "match", "key": "法国 vs 韩国", "note": ""})
    assert r.status_code == 200
    new_id = r.json()["id"]
    assert len(c.get("/api/watchlist").json()) == 2
    # DELETE
    r = c.delete(f"/api/watchlist/{new_id}")
    assert r.status_code == 200
    assert len(c.get("/api/watchlist").json()) == 1


def test_auth_enforced_when_password_set(tmp_path):
    db_path = tmp_path / "auth.db"
    db = Db(str(db_path))
    db.init()
    cfg = load_config("nope.toml")
    cfg["server"]["password"] = "secret"
    app = create_app(db_path=str(db_path), cfg=cfg, require_auth=True)
    c = TestClient(app)
    # 无凭证 → 401
    assert c.get("/api/state").status_code == 401
    # 错密码 → 401
    assert c.get("/api/state", auth=("u", "wrong")).status_code == 401
    # 正确密码 → 200
    assert c.get("/api/state", auth=("u", "secret")).status_code == 200
