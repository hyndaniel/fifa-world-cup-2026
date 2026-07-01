import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PEOPLE = {"你", "LYH", "ZFW", "LYZ", "YBB"}


def test_ledger_json_loads_and_totals():
    data = json.loads((REPO / "data" / "bet_ledger.json").read_text(encoding="utf-8"))
    recs = data["recommendations"]
    tix = data["tickets"]
    # 推荐腿 17 条全已结(R32 074-076 于 6/30 回填), green 迄今 = 0(诚实锚点)
    assert len(recs) == 17
    assert sum(1 for r in recs if r["settled"]) == 17
    assert sum(1 for r in recs if r["result"] == "pending") == 0
    assert sum(1 for r in recs if r["tier"] == "green") == 0
    # 实购票现 54 张, 跨 5 人, 含待结(pnl=null); 补录漏记的期2607011(074-082)全组合票 +1
    assert len(tix) == 54
    people = set(data["people"])
    assert people == PEOPLE
    for t in tix:
        assert t["who"] in people
        assert isinstance(t["settled"], bool)
        # pnl 可为 null(待结) 或数字(已结)
        assert t["pnl"] is None or isinstance(t["pnl"], (int, float))
        if not t["settled"]:
            assert t["pnl"] is None
        else:
            assert isinstance(t["pnl"], (int, float))


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
    # 推荐腿段不变(本任务不碰推荐逻辑)
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


# ---- tickets v2: 构造小 ledger 验待结隔离 + by_person 聚合 ----

TICKET_SAMPLE = {
    "recommendations": [],
    "tickets": [
        {"date": "2026-06-24", "who": "A", "type": "t", "stake": 100, "legs_hit": "4/4", "pnl": 300.0, "settled": True},   # 已结-赢
        {"date": "2026-06-25", "who": "A", "type": "t", "stake": 50, "legs_hit": "0/4", "pnl": -50.0, "settled": True},     # 已结-亏
        {"date": "2026-06-30", "who": "B", "type": "t", "stake": 70, "legs_hit": "待结", "pnl": None, "settled": False},     # 待结
    ],
}


def test_build_summary_tickets_pending_isolation():
    s = build_summary(TICKET_SAMPLE)["tickets"]
    assert s["count"] == 3
    assert s["settled_count"] == 2
    assert s["pending_count"] == 1
    assert s["won"] == 1
    assert s["settled_stake"] == 150.0
    assert s["settled_pnl"] == 250.0                       # 待结 pnl=null 不计入
    assert s["settled_roi"] == round(250.0 / 150.0, 4)
    assert s["pending_stake"] == 70.0                       # 待结按投注额记
    assert len(s["rows"]) == 3
    # by_person: A 在前(settled_pnl 250), B 在后(0.0)
    bp = s["by_person"]
    assert [p["who"] for p in bp] == ["A", "B"]
    a, b = bp
    assert a == {
        "who": "A", "tickets": 2, "settled": 2, "pending": 0, "won": 1,
        "stake": 150.0, "settled_stake": 150.0, "settled_pnl": 250.0,
        "settled_roi": round(250.0 / 150.0, 4), "pending_stake": 0.0,
    }
    assert b == {
        "who": "B", "tickets": 1, "settled": 0, "pending": 1, "won": 0,
        "stake": 70.0, "settled_stake": 0.0, "settled_pnl": 0.0,
        "settled_roi": 0.0, "pending_stake": 70.0,
    }


def test_build_summary_tickets_sort_tiebreak():
    # 同 settled_pnl 时按 who 升序
    sample = {"recommendations": [], "tickets": [
        {"date": "d", "who": "Z", "type": "t", "stake": 10, "legs_hit": "x", "pnl": 0.0, "settled": True},
        {"date": "d", "who": "A", "type": "t", "stake": 10, "legs_hit": "x", "pnl": 0.0, "settled": True},
    ]}
    bp = build_summary(sample)["tickets"]["by_person"]
    assert [p["who"] for p in bp] == ["A", "Z"]


def test_build_summary_empty():
    s = build_summary({"recommendations": [], "tickets": []})
    assert s["recommendations"]["hit_rate"] == 0.0
    assert s["recommendations"]["hypo_unit_pnl"] == 0.0
    assert s["tickets"]["count"] == 0
    assert s["tickets"]["settled_pnl"] == 0.0
    assert s["tickets"]["settled_roi"] == 0.0
    assert s["tickets"]["by_person"] == []


def test_full_ledger_recommendations_matches_hand_count():
    """推荐腿段跑真数据, 锚定手算(本任务保持不变)。"""
    from backend.bet_stats import load_ledger
    s = build_summary(load_ledger(str(REPO / "data")))["recommendations"]
    assert s["settled"] == 17
    assert s["win"] == 9
    assert s["hit_rate"] == round(9 / 17, 4)
    assert s["by_tier"]["green"] == {"total": 0, "win": 0}
    assert s["by_tier"]["yellow"] == {"total": 4, "win": 2}
    assert s["by_tier"]["red"] == {"total": 13, "win": 7}
    assert s["hypo_unit_pnl"] == 8.61


def test_full_ledger_tickets_global():
    """实购票全局聚合跑真数据, 锚定手算(待结/已结拆分)。"""
    from backend.bet_stats import load_ledger
    s = build_summary(load_ledger(str(REPO / "data")))["tickets"]
    assert s["count"] == 54
    assert s["settled_count"] == 42
    assert s["pending_count"] == 12
    assert s["won"] == 7
    assert s["settled_stake"] == 2992
    assert s["settled_pnl"] == 386.67
    assert s["settled_roi"] == round(386.67 / 2992, 4)
    assert s["pending_stake"] == 2152


def test_full_ledger_tickets_by_person():
    """by_person 跑真数据: 顺序 + 每人数值锚定。"""
    from backend.bet_stats import load_ledger
    bp = build_summary(load_ledger(str(REPO / "data")))["tickets"]["by_person"]
    assert [p["who"] for p in bp] == ["你", "LYZ", "YBB", "ZFW", "LYH"]
    by = {p["who"]: p for p in bp}
    # 你
    assert by["你"]["settled_pnl"] == 802.0
    assert by["你"]["settled"] == 18
    assert by["你"]["pending"] == 5
    assert by["你"]["won"] == 5
    assert by["你"]["settled_stake"] == 1766
    assert by["你"]["pending_stake"] == 1280
    # LYZ
    assert by["LYZ"]["settled_pnl"] == 378.43
    assert by["LYZ"]["settled"] == 3
    assert by["LYZ"]["pending"] == 1
    assert by["LYZ"]["won"] == 1
    assert by["LYZ"]["settled_stake"] == 320
    assert by["LYZ"]["pending_stake"] == 114
    # YBB(已结1 + 待结2)
    assert by["YBB"]["settled_pnl"] == 20.81
    assert by["YBB"]["settled"] == 1
    assert by["YBB"]["pending"] == 2
    assert by["YBB"]["won"] == 1
    assert by["YBB"]["pending_stake"] == 500
    # ZFW
    assert by["ZFW"]["settled_pnl"] == -352.57
    assert by["ZFW"]["settled"] == 6
    assert by["ZFW"]["pending"] == 1
    assert by["ZFW"]["settled_stake"] == 374
    # LYH
    assert by["LYH"]["settled_pnl"] == -462.0
    assert by["LYH"]["settled"] == 14
    assert by["LYH"]["settled_stake"] == 462


def test_ledger_record_schema():
    """真台账每条记录键集合 + 类型守卫(tickets pnl 可 null, who ∈ 人名集)。"""
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
    tix_required = {"date", "who", "type", "stake", "legs_hit", "pnl", "settled"}
    # 0630 起实购票可带可选元数据(serial/picks/odds_max/settles/note)便于赛后精确回填;
    # 后端只读必填键, 可选键无害。守卫: 必填齐 + 不出现未知键。
    tix_optional = {"serial", "picks", "odds_max", "settles", "note"}
    people = set(led["people"])
    for t in led["tickets"]:
        keys = set(t.keys())
        assert tix_required <= keys, f"缺必填键: {tix_required - keys}"
        assert keys <= tix_required | tix_optional, f"出现未知键: {keys - tix_required - tix_optional}"
        assert isinstance(t["stake"], (int, float))
        assert t["pnl"] is None or isinstance(t["pnl"], (int, float))
        assert isinstance(t["legs_hit"], str)
        assert isinstance(t["settled"], bool)
        assert t["who"] in people
