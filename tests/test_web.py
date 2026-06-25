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


def test_ingest_zucai_accepts_and_counts(client, monkeypatch):
    c, _ = client
    # 拦掉后台 poll_once(避免真连 Polymarket), 只验证接口受理 + 计数
    import backend.poller as poller_mod
    seen = []
    monkeypatch.setattr(poller_mod, "poll_once",
                        lambda db, cfg, **kw: (seen.append(kw), 0)[1])
    payload = {"value": {"matchInfoList": [
        {"subMatchList": [{"matchNumStr": "周一013"}, {"matchNumStr": "周一014"}]},
        {"subMatchList": [{"matchNumStr": "周二017"}]},
    ]}}
    r = c.post("/api/ingest/zucai", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["matches"] == 3  # 2 + 1


def test_ingest_enrich_accepts(tmp_path):
    """POST /api/ingest/enrich → 200, teams==条数; 同一 db_path 重建 Db 验证落库。"""
    db_path = tmp_path / "enrich.db"
    db = Db(str(db_path))
    db.init()
    cfg = load_config("nope.toml")
    app = create_app(db_path=str(db_path), cfg=cfg, require_auth=False)
    c = TestClient(app)

    lineup = {"formation": "4-3-3", "players": ["A", "B"], "source": "s", "note": ""}
    payload = {"items": [
        {"team": "西班牙", "lineup": lineup,
         "news": [{"title": "备战", "url": "http://x/1", "ts": ""}]},
        {"team": "法国", "lineup": None, "news": []},
    ]}
    r = c.post("/api/ingest/enrich", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["teams"] == 2

    # 同一 db_path 重建 Db, 断言已持久化
    db2 = Db(str(db_path))
    esp = db2.latest_enrich("西班牙")
    assert esp is not None
    assert esp["lineup"]["formation"] == "4-3-3"
    assert esp["news"][0]["title"] == "备战"
    fra = db2.latest_enrich("法国")
    assert fra is not None and fra["lineup"] is None and fra["news"] == []


def test_ingest_predictions_accepts_and_skips(client):
    """POST /api/ingest/predictions → {accepted,n,skipped}; 缺 match_key 计入 skipped。"""
    c, db = client
    payload = {"decisions": [
        {"match_key": "韩国 vs 南非", "home_cn": "韩国", "away_cn": "南非",
         "ko_bj": "6.27 02:00", "v1": {"score": "0-1"}},
        {"home_cn": "缺键", "away_cn": "无效"},          # 缺 match_key → skipped
        {"match_key": "法国 vs 巴西", "home_cn": "法国", "away_cn": "巴西",
         "ko_bj": "6.27 09:00"},
    ]}
    r = c.post("/api/ingest/predictions", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is True
    assert body["n"] == 2
    assert body["skipped"] == 1
    # 落库可读回
    got = db.get_decisions()
    assert {d["match_key"] for d in got} == {"韩国 vs 南非", "法国 vs 巴西"}


def test_get_decisions_sorted_and_shape(client):
    """GET /api/decisions → {ts, decisions[]}; 按 ko_bj 升序, 缺末尾。"""
    c, db = client
    db.save_decisions([
        {"match_key": "无开球", "home_cn": "X", "away_cn": "Y"},
        {"match_key": "晚场", "ko_bj": "6.27 09:00"},
        {"match_key": "早场", "ko_bj": "6.27 02:00"},
    ])
    r = c.get("/api/decisions")
    assert r.status_code == 200
    body = r.json()
    assert "ts" in body and isinstance(body["ts"], str)
    assert [d["match_key"] for d in body["decisions"]] == ["早场", "晚场", "无开球"]


def test_get_decisions_empty(tmp_path):
    """无 decisions → {ts, decisions: []}, 不崩。"""
    db_path = tmp_path / "empty.db"
    db = Db(str(db_path)); db.init()
    cfg = load_config("nope.toml")
    app = create_app(db_path=str(db_path), cfg=cfg, require_auth=False)
    c = TestClient(app)
    r = c.get("/api/decisions")
    assert r.status_code == 200
    body = r.json()
    assert body["decisions"] == []
    assert isinstance(body["ts"], str)


def test_refresh_no_snapshot_branch(tmp_path, monkeypatch):
    """无 zucai 快照 → {accepted: false, reason}, 不抛错, 不调 poll_once。"""
    db_path = tmp_path / "norefresh.db"
    db = Db(str(db_path)); db.init()
    cfg = load_config("nope.toml")
    app = create_app(db_path=str(db_path), cfg=cfg, require_auth=False)
    c = TestClient(app)

    import backend.poller as poller_mod
    called = []
    monkeypatch.setattr(poller_mod, "poll_once",
                        lambda db, cfg, **kw: (called.append(kw), 0)[1])

    r = c.post("/api/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is False
    assert "reason" in body
    assert called == []  # 没快照不应调 poll_once


def test_refresh_triggers_poll_once_with_snapshot(client, monkeypatch):
    """有 zucai 快照 → {accepted: true}; 后台 poll_once 被调, 且注入的 payload 即快照。"""
    c, db = client
    # 落一条 zucai 快照
    mid = db.upsert_match("周一013", "西", "佛", "Spain", "Cabo Verde",
                          "s", "6.16 00:00", "23:00")
    db.save_snapshot(mid, "zucai", {"value": {"matchInfoList": []}, "tag": "snap1"})

    import backend.poller as poller_mod
    captured = {}

    def fake_poll(db_, cfg_, **kw):
        # 抓住注入的 zucai_fetch 返回值, 防联网
        f = kw.get("zucai_fetch")
        captured["payload"] = f() if f else None
        return 0

    monkeypatch.setattr(poller_mod, "poll_once", fake_poll)

    r = c.post("/api/refresh")
    assert r.status_code == 200
    assert r.json()["accepted"] is True
    # 后台线程异步, 给它一点时间
    import time as _t
    for _ in range(50):
        if "payload" in captured:
            break
        _t.sleep(0.01)
    assert captured.get("payload", {}).get("tag") == "snap1"


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
