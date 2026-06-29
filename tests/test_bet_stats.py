import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_ledger_json_loads_and_totals():
    data = json.loads((REPO / "data" / "bet_ledger.json").read_text(encoding="utf-8"))
    recs = data["recommendations"]
    tix = data["tickets"]
    # 17 条推荐腿: 14 已结 + 3 pending
    assert len(recs) == 17
    assert sum(1 for r in recs if r["settled"]) == 14
    assert sum(1 for r in recs if r["result"] == "pending") == 3
    # green 迄今 = 0(诚实锚点)
    assert sum(1 for r in recs if r["tier"] == "green") == 0
    # 实购票 6 张, 合计 stake 292 / pnl -292 / 0 中
    assert len(tix) == 6
    assert sum(t["stake"] for t in tix) == 292
    assert sum(t["pnl"] for t in tix) == -292
    assert all(t["pnl"] < 0 for t in tix)


from backend.bet_stats import build_summary

SAMPLE = {
    "updated": "2026-06-29",
    "recommendations": [
        {"date": "2026-06-24", "match": "m1", "leg": "x", "odds": 5.65, "tier": "yellow", "value_poly": 0.972, "result": "win", "settled": True},
        {"date": "2026-06-24", "match": "m2", "leg": "x", "odds": 3.32, "tier": "red", "value_poly": 0.81, "result": "loss", "settled": True},
        {"date": "2026-06-26", "match": "m3", "leg": "x", "odds": 3.75, "tier": "red", "value_poly": 0.953, "result": "win", "settled": True},
        {"date": "2026-06-29", "match": "m4", "leg": "x", "odds": 1.66, "tier": "red", "value_poly": 0.958, "result": "pending", "settled": False},
    ],
    "tickets": [
        {"date": "2026-06-24", "who": "楼主", "type": "t1", "stake": 30, "legs_hit": "0/6", "pnl": -30, "settled": True},
        {"date": "2026-06-26", "who": "用户", "type": "t2", "stake": 60, "legs_hit": "1/6", "pnl": -60, "settled": True},
    ],
}


def test_build_summary_recommendations():
    s = build_summary(SAMPLE)["recommendations"]
    assert s["total"] == 4
    assert s["settled"] == 3
    assert s["pending"] == 1
    assert s["win"] == 2
    assert s["hit_rate"] == round(2 / 3, 4)
    assert s["by_tier"]["green"] == {"total": 0, "win": 0}
    assert s["by_tier"]["yellow"] == {"total": 1, "win": 1}
    assert s["by_tier"]["red"] == {"total": 2, "win": 1}
    # hypo: 投 3 注(已结), 赢 5.65→+4.65, 输 3.32→-1, 赢 3.75→+2.75 = 6.40
    assert s["hypo_unit_pnl"] == 6.40
    assert s["hypo_roi"] == round(6.40 / 3, 4)
    # by_date 升序
    assert [d["date"] for d in s["by_date"]] == ["2026-06-24", "2026-06-26"]
    assert s["by_date"][0] == {"date": "2026-06-24", "settled": 2, "win": 1}


def test_build_summary_tickets():
    s = build_summary(SAMPLE)["tickets"]
    assert s["count"] == 2
    assert s["won"] == 0
    assert s["total_stake"] == 90
    assert s["total_pnl"] == -90
    assert s["roi"] == -1.0
    assert len(s["rows"]) == 2


def test_build_summary_empty():
    s = build_summary({"recommendations": [], "tickets": []})
    assert s["recommendations"]["hit_rate"] == 0.0
    assert s["recommendations"]["hypo_unit_pnl"] == 0.0
    assert s["tickets"]["roi"] == 0.0
    assert s["tickets"]["count"] == 0


def test_full_ledger_summary_matches_hand_count():
    """跑真数据(Task 1 的 17 条/6 张), 锚定手算结果。"""
    from backend.bet_stats import load_ledger
    s = build_summary(load_ledger(str(REPO / "data")))
    assert s["recommendations"]["settled"] == 14
    assert s["recommendations"]["win"] == 7
    assert s["recommendations"]["hit_rate"] == 0.5
    assert s["recommendations"]["by_tier"]["green"] == {"total": 0, "win": 0}
    assert s["recommendations"]["by_tier"]["yellow"] == {"total": 4, "win": 2}
    assert s["recommendations"]["by_tier"]["red"] == {"total": 10, "win": 5}
    assert s["recommendations"]["hypo_unit_pnl"] == 6.51
    assert s["tickets"]["total_pnl"] == -292
    assert s["tickets"]["roi"] == -1.0


def test_ledger_record_schema():
    """Task-1 reviewer 加固: 真台账每条记录的键集合 + 类型守卫。"""
    from backend.bet_stats import load_ledger
    led = load_ledger(str(REPO / "data"))
    rec_keys = {"date", "match", "leg", "odds", "tier", "value_poly", "result", "settled"}
    for r in led["recommendations"]:
        assert set(r.keys()) == rec_keys
        assert isinstance(r["odds"], (int, float))
        assert isinstance(r["value_poly"], (int, float))
        assert r["tier"] in {"green", "yellow", "red"}
        assert r["result"] in {"win", "loss", "pending"}
        assert isinstance(r["settled"], bool)
    tix_keys = {"date", "who", "type", "stake", "legs_hit", "pnl", "settled"}
    for t in led["tickets"]:
        assert set(t.keys()) == tix_keys
        assert isinstance(t["stake"], (int, float))
        assert isinstance(t["pnl"], (int, float))
        assert isinstance(t["legs_hit"], str)
        assert isinstance(t["settled"], bool)
