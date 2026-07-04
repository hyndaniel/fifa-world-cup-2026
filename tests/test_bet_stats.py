import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PEOPLE = {"HYN", "LYH", "ZFW", "LYZ", "YBB"}


def test_ledger_json_loads_and_totals():
    # 真台账每天在长 → 只验结构不变式, 不锚定会漂移的快照数值
    # (数值级锚定由下方 SAMPLE/TICKET_SAMPLE 固定 fixture 承担)
    data = json.loads((REPO / "data" / "bet_ledger.json").read_text(encoding="utf-8"))
    recs = data["recommendations"]
    tix = data["tickets"]
    assert recs and tix
    for r in recs:
        assert r["tier"] in {"green", "yellow", "red"}
        # pending 与 settled 必须互斥自洽
        assert (r["result"] == "pending") == (not r["settled"])
    people = set(data["people"])
    assert people >= PEOPLE  # 人只会增不会减
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


def test_full_ledger_recommendations_consistent():
    """推荐腿段跑真数据: 聚合结果与台账原始行独立对账(不锚定会漂移的快照数值)。"""
    from backend.bet_stats import load_ledger
    ledger = load_ledger(str(REPO / "data"))
    s = build_summary(ledger)["recommendations"]
    recs = ledger["recommendations"]
    settled = [r for r in recs if r["settled"]]
    win = [r for r in settled if r["result"] == "win"]
    assert s["total"] == len(recs)
    assert s["settled"] == len(settled)
    assert s["pending"] == len(recs) - len(settled)
    assert s["win"] == len(win)
    assert s["hit_rate"] == (round(len(win) / len(settled), 4) if settled else 0.0)
    # by_tier 各档已结数之和 == 全局已结数
    assert sum(v["total"] for v in s["by_tier"].values()) == len(settled)
    assert sum(v["win"] for v in s["by_tier"].values()) == len(win)
    # by_date 已结数之和 == 全局已结数
    assert sum(d["settled"] for d in s["by_date"]) == len(settled)


def test_full_ledger_tickets_global_consistent():
    """实购票全局聚合跑真数据: 与台账原始行独立对账(待结/已结拆分自洽)。"""
    from backend.bet_stats import load_ledger
    ledger = load_ledger(str(REPO / "data"))
    s = build_summary(ledger)["tickets"]
    tix = ledger["tickets"]
    settled = [t for t in tix if t["settled"]]
    assert s["count"] == len(tix)
    assert s["settled_count"] == len(settled)
    assert s["pending_count"] == len(tix) - len(settled)
    assert s["won"] <= s["settled_count"]
    assert s["settled_stake"] == sum(t["stake"] for t in settled)
    assert s["settled_pnl"] == round(sum(t["pnl"] for t in settled), 2)
    assert s["settled_roi"] == (
        round(s["settled_pnl"] / s["settled_stake"], 4) if s["settled_stake"] else 0.0
    )
    assert s["pending_stake"] == sum(t["stake"] for t in tix if not t["settled"])


def test_full_ledger_tickets_by_person_consistent():
    """by_person 跑真数据: 排序规则 + 分人汇总 == 全局(不锚定每人快照数值)。"""
    from backend.bet_stats import load_ledger
    ledger = load_ledger(str(REPO / "data"))
    s = build_summary(ledger)["tickets"]
    bp = s["by_person"]
    tix = ledger["tickets"]
    assert {p["who"] for p in bp} == {t["who"] for t in tix}
    # 排序: settled_pnl 降序, 平局按 who 升序
    assert bp == sorted(bp, key=lambda p: (-p["settled_pnl"], p["who"]))
    # 分人汇总回加 == 全局
    assert round(sum(p["settled_pnl"] for p in bp), 2) == s["settled_pnl"]
    assert sum(p["settled"] for p in bp) == s["settled_count"]
    assert sum(p["pending"] for p in bp) == s["pending_count"]
    assert sum(p["won"] for p in bp) == s["won"]
    assert sum(p["tickets"] for p in bp) == s["count"]
    # 每人 stake 拆分自洽
    for p in bp:
        assert p["stake"] == p["settled_stake"] + p["pending_stake"]
        assert p["tickets"] == p["settled"] + p["pending"]


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
    tix_optional = {"serial", "picks", "odds_max", "settles", "note", "唯一码"}
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


def test_save_ledger_roundtrip(tmp_path):
    """save_ledger 写的文件, load_ledger 读回来内容一致(供 /api/ingest/tickets 用)。"""
    from backend.bet_stats import load_ledger, save_ledger

    ledger = {"updated": "2026-07-01", "recommendations": [], "tickets": [
        {"date": "2026-07-01", "who": "HYN", "type": "t", "stake": 10,
         "legs_hit": "x", "pnl": None, "settled": False},
    ], "people": ["HYN"]}
    save_ledger(ledger, str(tmp_path))
    assert (tmp_path / "bet_ledger.json").exists()
    assert load_ledger(str(tmp_path)) == ledger


def test_save_ledger_creates_data_dir(tmp_path):
    """data_dir 不存在时 save_ledger 自动建目录(ingest 到全新环境不报错)。"""
    from backend.bet_stats import save_ledger

    target = tmp_path / "nested" / "data"
    save_ledger({"updated": None, "recommendations": [], "tickets": []}, str(target))
    assert (target / "bet_ledger.json").exists()
