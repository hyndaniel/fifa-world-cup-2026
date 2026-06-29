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
