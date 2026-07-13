# tests/test_v2_report.py
import json
import os
import sqlite3
import tempfile

from backend.baseline import HAD_CFG, baseline_market, record_result
from backend.v2_predict import build_v2_prediction, record_v2_prediction
from backend.scorecard import score_arm
from backend.v1_log import record_v1
from tools.v2_report import collect, collect_calibration, collect_score_arm, render


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
    assert pm["bucket"] == "动机畸形"                       # reliability='乱' → 动机畸形桶


def test_collect_excludes_matches_without_v2_prediction():
    """🔴 同基铁则回归: 没有 v2 预测的场次(如混进来的其他联赛)不得进跑分卡。

    否则市场基线在全部场次上平均、v1/v2 只在有预测的场次上平均 —— **三方分母不同、
    对比无效**。2026-07-13 实测: 市场基线掺进 34 场其他联赛后, 从 0.374 被抬到 0.444,
    制造出「v2(0.430) 跑赢市场」的假象; 同基复算后真相相反(市场 0.374 优于 v2 0.430)。
    """
    d = tempfile.mkdtemp()
    db = os.path.join(d, "c.db")
    wc_mk = "周三053"       # 世界杯场次: 有 v2 预测
    other_mk = "周三201"    # 其他联赛: 只有盘口与赛果, 无 v1/v2 预测

    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    for mk in (wc_mk, other_mk):
        conn.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)",
                     ("2026-06-25T09:00:00+08:00", "zucai", mk, "A vs B", "ko",
                      json.dumps({"had": {"h": 6.00, "d": 3.87, "a": 1.44}})))
    conn.commit()
    conn.close()

    # 两场都有赛果 + 都能算出市场基线
    record_result(db, wc_mk, 0, 1)
    record_result(db, other_mk, 0, 1)
    assert baseline_market(db, other_mk, HAD_CFG), "前提: 其他联赛也算得出市场基线"

    # 只有世界杯那场有 v2 预测
    bl = baseline_market(db, wc_mk, HAD_CFG)
    pred = build_v2_prediction(wc_mk, "中", [],
                               {"had": {"baseline": bl["baseline"], "deviations": []}})
    record_v2_prediction(db, wc_mk, pred)

    keys = [pm["match_key"] for pm in collect(db)["had"]["per_match"]]
    assert wc_mk in keys
    assert other_mk not in keys, "无 v2 预测的场次不得计入跑分卡(会破坏三方同基比较)"


def test_render_bucket_table_splits_regular_vs_anomaly():
    collected = {
        "had": {"rows": [], "per_match": [
            {"match_key": "A", "reliability": "中", "bucket": "常规",
             "brier": {"v1": 0.60, "v2": 0.50, "market": 0.52}},
            {"match_key": "B", "reliability": "乱", "bucket": "动机畸形",
             "brier": {"v1": 0.40, "v2": 0.55, "market": 0.58}},
        ]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []},
    }
    audits = {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
              for m in ("had", "hhad", "ttg")}
    md = render(collected, audits)
    assert "按场型分桶" in md
    assert "| 全局 | 2 |" in md                  # 全局 n=2,无 ⚠
    assert "| 常规 | 1" in md and "| 动机畸形 | 1" in md
    assert "⚠" in md                            # 单桶 n<5 带小样本警示


def test_render_bucket_table_adds_matchday_cut():
    collected = {
        "had": {"rows": [], "per_match": [
            {"match_key": "M1", "reliability": "中", "bucket": "常规",
             "brier": {"v1": 0.6, "v2": 0.5, "market": 0.52}},
            {"match_key": "M5", "reliability": "乱", "bucket": "动机畸形",
             "brier": {"v1": 0.4, "v2": 0.55, "market": 0.58}},
        ]},
        "hhad": {"rows": [], "per_match": []},
        "ttg": {"rows": [], "per_match": []},
    }
    audits = {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
              for m in ("had", "hhad", "ttg")}
    matchdays = {"M1": 2, "M5": 3}               # M1 非末轮,M5 末轮
    md = render(collected, audits, matchdays=matchdays)
    assert "| 非末轮 | 1" in md                  # 中立切:matchday∈{1,2}
    assert "| 末轮 | 1" in md                    # matchday==3
    # 既有 v2-judged 切仍在
    assert "| 常规 | 1" in md and "| 动机畸形 | 1" in md


def test_render_v1_score_arm_section_bucketed():
    collected = _collected_one_market()
    audits = {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
              for m in ("had", "hhad", "ttg")}
    # 4 场 v1 比分:2 常规(1 精确命中)+ 2 动机畸形(0 命中);M3/M4 末轮
    score_rows = [
        {"match_key": "M1", "pred": (1, 0), "actual": (1, 0), "bucket": "常规"},     # 命中
        {"match_key": "M2", "pred": (2, 0), "actual": (1, 1), "bucket": "常规"},     # 距离2
        {"match_key": "M3", "pred": (0, 1), "actual": (0, 3), "bucket": "动机畸形"},  # 距离2
        {"match_key": "M4", "pred": (1, 1), "actual": (3, 2), "bucket": "动机畸形"},  # 距离3
    ]
    matchdays = {"M1": 2, "M2": 2, "M3": 3, "M4": 3}    # M3/M4 末轮
    md = render(collected, audits, score_rows, matchdays)
    assert "## v1 比分臂" in md
    assert "| 全局 | 4 |" in md                  # 4 场全局
    assert "| 常规 | 2" in md and "| 动机畸形 | 2" in md
    assert "| 末轮 | 2" in md and "| 非末轮 | 2" in md   # 中立切
    assert "50.0%" in md                          # 常规 1/2 命中
    assert "⚠" in md                              # 单桶 n<5


def test_render_without_score_arm_back_compat():
    # 不传 score → 不渲染比分臂段(既有调用兼容)
    md = render(_collected_one_market(),
                {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
                 for m in ("had", "hhad", "ttg")})
    assert "v1 比分臂" not in md


def test_collect_score_arm_builds_rows(tmp_path):
    db = str(tmp_path / "c.db")
    record_result(db, "周四055", 2, 1)              # 实际 2-1
    record_v1(db, "周四055", {"h": 0.3, "d": 0.3, "a": 0.4}, "0-1")  # v1 比分 0-1
    record_result(db, "周三049", 1, 0)              # 实际,但无 v1
    rows = collect_score_arm(db)
    by_key = {r["match_key"]: r for r in rows}
    assert by_key["周四055"]["pred"] == (0, 1) and by_key["周四055"]["actual"] == (2, 1)
    assert by_key["周四055"]["bucket"] == "常规"     # 无 v2rec → 默认常规
    assert by_key["周三049"]["pred"] is None        # 无 v1 比分 → pred None
    out = score_arm(rows)
    assert out["n"] == 1                            # 只 1 场有 v1 比分


def test_render_calibration_section():
    # 校准段:每概率档比 均预测% vs 实际频率。v2 80% 档一中一不中→50%;市场全中→100%。
    calib = {"v2": [(80.0, 1), (80.0, 0)], "market": [(80.0, 1), (80.0, 1)]}
    md = render(_collected_one_market(),
                {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
                 for m in ("had", "hhad", "ttg")}, calib=calib)
    assert "## 校准" in md
    assert "| 80–100% | 2 | 80% | 50.0% | 2 | 80% | 100.0% |" in md


def test_render_without_calibration_back_compat():
    # 不传 calib → 不渲染校准段(既有调用兼容)
    md = render(_collected_one_market(),
                {m: {"n_deviated": 0, "v2_mean": None, "market_mean": None, "delta": None}
                 for m in ("had", "hhad", "ttg")})
    assert "## 校准" not in md


def test_collect_calibration_pairs_from_db():
    # 真集成:播种 odds + 赛果 + v2 → 每场 had 三结果各一条 (概率%, 是否发生)。
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
    record_result(db, mk, 0, 1)  # 客胜(韩国 1.44 大热) → had actual = "a"
    bl = baseline_market(db, mk, HAD_CFG)
    pred = build_v2_prediction(mk, "中", [],
                               {"had": {"baseline": bl["baseline"], "deviations": []}})
    record_v2_prediction(db, mk, pred)

    out = collect_calibration(db)
    assert set(out) == {"v2", "market"}
    assert len(out["market"]) == 3 and len(out["v2"]) == 3     # h/d/a 各一条
    occ = [p for (p, y) in out["market"] if y == 1]
    assert len(occ) == 1 and occ[0] > 50                       # 命中的是大热档,概率>50%


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
