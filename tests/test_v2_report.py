# tests/test_v2_report.py
import json
import os
import sqlite3
import tempfile

from backend.baseline import HAD_CFG, baseline_market, record_result
from backend.v2_predict import build_v2_prediction, record_v2_prediction
from tools.v2_report import collect, render


def _collected_one_market():
    return {
        "had": {"rows": [{"deviated": True, "v2": 0.25, "market": 0.31}],
                "per_match": [{"match_key": "M1", "reliability": "乱",
                               "brier": {"v1": None, "v2": 0.1234, "market": 0.3567}}]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []},
    }


def test_render_sections_per_market_and_2dp():
    collected = _collected_one_market()
    audits = {"had": {"n_deviated": 1, "v2_mean": 0.25, "market_mean": 0.31, "delta": -0.06},
              "hhad": {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None},
              "ttg": {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}}
    md = render(collected, audits)
    assert "## 胜平负" in md and "## 让球" in md and "## 总进球" in md
    assert "| M1 | 乱 | — | 0.12 | 0.36 |" in md     # 2dp + 缺失为 —(v1 仅 had 也可能缺)
    assert "0.1234" not in md
    assert "拉低" in md or "-0.06" in md


def test_collect_smoke_db_assembles_per_market():
    # 真集成冒烟(无 mock):播种 odds_cache + match_results + v2 预测 → collect 逐盘口装配。
    d = tempfile.mkdtemp()
    db = os.path.join(d, "c.db")
    mk = "周三053"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                 "VALUES (?,?,?,?,?,?)",
                 ("2026-06-25T09:00:00+08:00", "zucai", mk, "南非 vs 韩国", "ko",
                  json.dumps({"had": {"h": 6.00, "d": 3.87, "a": 1.44}})))
    conn.commit()
    conn.close()
    record_result(db, mk, 0, 1)  # 客胜(韩国) → had actual = "a"
    bl = baseline_market(db, mk, HAD_CFG)        # had 基线由 DB 装配(非手搓)
    pred = build_v2_prediction(mk, "乱", [],
                               {"had": {"baseline": bl["baseline"], "deviations": []}})
    record_v2_prediction(db, mk, pred)

    out = collect(db)
    assert set(out) == {"had", "hhad", "ttg"}              # 三盘口键都在
    keys = [pm["match_key"] for pm in out["had"]["per_match"]]
    assert mk in keys                                       # had 含已播种场次
    pm = next(pm for pm in out["had"]["per_match"] if pm["match_key"] == mk)
    assert pm["brier"]["market"] is not None               # 市场基线对实际结果可打分


def test_render_deviation_attribution_lists_factor_source():
    collected = {
        "had": {"rows": [{"deviated": True, "v2": 0.2, "market": 0.3}],
                "per_match": [{"match_key": "M1", "reliability": "中",
                               "brier": {"v1": None, "v2": 0.2, "market": 0.3},
                               "deviations": [
                                   {"outcome": "a", "to": 64.0,
                                    "factor_source": "韩国大轮换[GNews 2h]"},
                                   {"outcome": "h", "to": 20.0}]}]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []}}
    audits = {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
              for m in ("had", "hhad", "ttg")}
    md = render(collected, audits)
    assert "**偏离归因**" in md
    assert "M1: a→64.0% · 韩国大轮换[GNews 2h]" in md
    assert "M1: h→20.0% · ⚠无因子来源" in md     # 缺 factor_source 暴露失纪律
