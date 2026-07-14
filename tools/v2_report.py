#!/usr/bin/env python3
"""渲染三方跑分卡 → reports/scoring/三方跑分卡.md。

(报告正名: 它量 v1/v2/市场 三方 Brier, 非 v2 独有 —— §3 命名迁移正名自 reports/预测v2.md。)
用法: python3 tools/v2_report.py [--cache .cache/odds_cache.db] [--out reports/scoring/三方跑分卡.md]
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from backend.scorecard import (aggregate, bucket_of, derive_matchdays,  # noqa: E402
                               deviation_audit, parse_score, score_arm, three_way)
from backend.baseline import baseline_market, MARKETS, get_result_goals, _actual_for  # noqa: E402
from backend.scoring import calibration_buckets  # noqa: E402
from backend.v2_predict import get_v2_prediction  # noqa: E402
from backend.v1_log import get_v1  # noqa: E402

MARKET_NAMES = {"had": "胜平负", "hhad": "让球", "ttg": "总进球/大小球"}

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))
DEFAULT_OUT = os.path.join(REPO, "reports", "scoring", "三方跑分卡.md")
DEFAULT_WC_DB = os.path.join(REPO, "data", "wc.db")


def _load_matchdays(wc_db_path):
    """读 wc.db matches → derive_matchdays(zucai_num→轮次)。库/表缺失 → {}(无中立分桶)。"""
    try:
        conn = sqlite3.connect(f"file:{wc_db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        rows = conn.execute("SELECT zucai_num, home_cn, away_cn, ko_bj FROM matches").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return derive_matchdays(rows)


def _fmt(x):
    # 统一展示粒度: 数值一律 2 位小数, 缺失(None)显示 —。三均值与每场表共用,
    # 避免每场 Brier 露出 4 位原始精度而均值只有 2 位的不齐(Minor #1/#2)。
    if isinstance(x, (int, float)):
        return f"{x:.2f}"
    return "—" if x is None else x


def _pct(x):
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "—"


def _pctnum(x):
    # x 已是 0–100 百分数(如均预测) → "80%";None → —
    return f"{x:.0f}%" if isinstance(x, (int, float)) else "—"


def _bucket_for_match(v2rec):
    """从 v2 预测记录派生场型 bucket(常规/动机畸形);借 reliability/scenarios,post-hoc。"""
    scen = (v2rec or {}).get("scenarios") or []
    names = [s.get("name") if isinstance(s, dict) else s for s in scen]
    return bucket_of((v2rec or {}).get("reliability", ""), names)


def _bucket_lines(per_match, matchdays=None):
    """每盘口"按场型分桶"子表:全局 + 常规/动机畸形(借 v2 判断)+ 末轮/非末轮(中立)。

    各切片调 aggregate(越低越准)。单桶 n<5 标 ⚠(末轮畸形场稀少,小样本只能定性)。
    matchdays={zucai_num:轮次};缺则不出中立切。空桶不出行。
    """
    if not per_match:
        return []

    def agg_where(pred):
        return aggregate([{"v1": p["brier"]["v1"], "v2": p["brier"]["v2"],
                           "market": p["brier"]["market"]} for p in per_match if pred(p)])

    md = matchdays or {}
    specs = [("全局", lambda p: True),
             ("常规", lambda p: p.get("bucket", "常规") == "常规"),
             ("动机畸形", lambda p: p.get("bucket", "常规") == "动机畸形")]
    if matchdays:
        specs += [("非末轮", lambda p: md.get(p["match_key"]) in (1, 2)),
                  ("末轮", lambda p: md.get(p["match_key"]) == 3)]
    out = ["**按场型分桶**(同盘口切片):", "",
           "| 分桶 | n | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
    for label, pred in specs:
        a = agg_where(pred)
        if a["n"] == 0:
            continue
        n_disp = f"{a['n']} ⚠" if (label != "全局" and a["n"] < 5) else str(a["n"])
        out.append(f"| {label} | {n_disp} | {_fmt(a.get('v1_mean'))} | "
                   f"{_fmt(a.get('v2_mean'))} | {_fmt(a.get('market_mean'))} |")
    out.append("> _常规/动机畸形=借 v2 reliability/scenarios(post-hoc);末轮/非末轮=按出场"
               "轮次中立切。n=该桶场数,v1/v2 列只均有预测的场。_")
    return out + [""]


def _score_arm_lines(score_rows, matchdays=None):
    """v1 比分臂分桶子表:全局 + 常规/动机畸形 + 末轮/非末轮,各算 score_arm。

    score_rows=collect_score_arm 的行(带 pred/actual/bucket)。无任何 v1 比分场 → 不出段。
    单桶 n<5 标 ⚠(末轮畸形场稀少,小样本仅定性)。
    """
    rows = score_rows or []
    if not any(r.get("pred") is not None for r in rows):
        return []
    md = matchdays or {}
    specs = [("全局", lambda r: True),
             ("常规", lambda r: r.get("bucket", "常规") == "常规"),
             ("动机畸形", lambda r: r.get("bucket", "常规") == "动机畸形")]
    if matchdays:
        specs += [("非末轮", lambda r: md.get(r["match_key"]) in (1, 2)),
                  ("末轮", lambda r: md.get(r["match_key"]) == 3)]
    out = ["## v1 比分臂(精确比分,had 概率臂之外)", "",
           "| 分桶 | n | 精确命中率 | 平均比分距离 |", "|---|---|---|---|"]
    for label, pred in specs:
        a = score_arm([r for r in rows if pred(r)])
        if a["n"] == 0:
            continue
        n_disp = f"{a['n']} ⚠" if (label != "全局" and a["n"] < 5) else str(a["n"])
        out.append(f"| {label} | {n_disp} | {_pct(a.get('exact_rate'))} "
                   f"({a['exact']}/{a['n']}) | {_fmt(a.get('avg_distance'))} |")
    out += ["", "> _比分距离=|Δ主|+|Δ客|,量 v1 出比分能力(had Brier 量不到)。"
            "常规/动机畸形借 v2 判断,末轮/非末轮按轮次中立切。_", ""]
    return out


def _calibration_lines(calib, n=5):
    """校准段(胜平负):每概率档比 v2/市场 的「平均预测%」vs「实际频率」。

    完美校准 = 均预测 ≈ 实际频率;均预测 > 频率 = 系统性高估(吹高自信)。
    calib={"v2":[(prob%,occ)..], "market":[..]};两源皆空 → 不出段。空桶不出行。
    """
    v2_pairs = (calib or {}).get("v2") or []
    mkt_pairs = (calib or {}).get("market") or []
    if not v2_pairs and not mkt_pairs:
        return []
    empty = {"count": 0, "mean_pred": None, "freq": None}
    vb = calibration_buckets(v2_pairs, n) if v2_pairs else [dict(empty) for _ in range(n)]
    mb = calibration_buckets(mkt_pairs, n) if mkt_pairs else [dict(empty) for _ in range(n)]
    width = 100.0 / n
    out = ["## 校准(胜平负:说 X% 时,实际真发生约百分之几)", "",
           "| 概率档 | v2 n | v2 均预测 | v2 实际频率 | 市场 n | 市场均预测 | 市场实际频率 |",
           "|---|---|---|---|---|---|---|"]
    for i in range(n):
        v, m = vb[i], mb[i]
        if v["count"] == 0 and m["count"] == 0:
            continue
        lo, hi = i * width, (i + 1) * width
        out.append(f"| {lo:.0f}–{hi:.0f}% | {v['count']} | {_pctnum(v['mean_pred'])} | "
                   f"{_pct(v['freq'])} | {m['count']} | {_pctnum(m['mean_pred'])} | "
                   f"{_pct(m['freq'])} |")
    out += ["", "> _每场 had 贡献 h/d/a 三条点。均预测>频率=高估、<频率=低估;"
            "样本小只能定性。_", ""]
    return out


def render(collected, audits, score_rows=None, matchdays=None, calib=None):
    lines = ["# 三方跑分卡(全盘口)", ""]
    for market, _ in MARKETS:
        data = collected[market]
        agg = aggregate([{"v1": p["brier"]["v1"], "v2": p["brier"]["v2"],
                          "market": p["brier"]["market"]} for p in data["per_match"]])
        lines += [f"## {MARKET_NAMES[market]}", "",
                  f"配对场数: {agg['n']}", "",
                  "| 方 | 平均 Brier(越低越准) |", "|---|---|",
                  f"| v1(老方法) | {_fmt(agg.get('v1_mean'))} |",
                  f"| **v2** | **{_fmt(agg.get('v2_mean'))}** |",
                  f"| 市场基线 | {_fmt(agg.get('market_mean'))} |", ""]
        lines += _bucket_lines(data["per_match"], matchdays)
        audit = audits.get(market, {})
        if audit.get("delta") is not None:
            verdict = ("偏离平均**拉低** Brier(有用)" if audit["delta"] < 0
                       else "偏离平均**拉高** Brier(帮倒忙,该收敛回市场)")
            lines += [f"偏离审计:{audit['n_deviated']} 场有偏离,v2 {audit['v2_mean']} vs "
                      f"市场 {audit['market_mean']},delta {audit['delta']} → {verdict}", ""]
        lines += ["| 场次 | 靠谱度 | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
        for m in data["per_match"]:
            b = m["brier"]
            lines.append(f"| {m['match_key']} | {m.get('reliability','')} | "
                         f"{_fmt(b.get('v1'))} | {_fmt(b.get('v2'))} | {_fmt(b.get('market'))} |")
        attrib = [(m["match_key"], d) for m in data["per_match"]
                  for d in m.get("deviations", [])]
        if attrib:
            lines += ["", "**偏离归因**:"]
            for mk_, d in attrib:
                fs = d.get("factor_source") or "⚠无因子来源"
                lines.append(f"- {mk_}: {d.get('outcome')}→{d.get('to')}% · {fs}")
        lines.append("")
    lines += _score_arm_lines(score_rows, matchdays)
    lines += _calibration_lines(calib)
    lines += ["_红线:概率预测非投注建议;v2 跑不赢市场就回归市场基线+避雷器。_"]
    return "\n".join(lines)


def _result_keys(cache_path):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS match_results (
            match_key TEXT PRIMARY KEY, home_goals INTEGER, away_goals INTEGER,
            outcome TEXT, ts TEXT)""")
        rows = conn.execute("SELECT match_key FROM match_results").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def collect(cache_path):
    out = {m: {"rows": [], "per_match": []} for m, _ in MARKETS}
    for mk in _result_keys(cache_path):
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        hg, ag = goals
        v2rec = get_v2_prediction(cache_path, mk)
        v1rec = get_v1(cache_path, mk)

        # 🔴 同基铁则: 只统计**有 v2 预测**的场次。
        #
        # match_results 里混着非世界杯场次(2xx 其他联赛), 它们没有 v1/v2 预测, 只有市场基线。
        # 此前照收不误 → 市场基线在 86 场上平均, 而 v1/v2 只在 44 场上平均, **三方分母不同、
        # 对比无效**; 叠加 2xx 的赛果还会被跨周主键覆盖而错配(match_key='周五201' 这类周内
        # 循环编号不含日期, 上周五被本周五覆盖 → 拿 A 场预测对 B 场比分算分, 周一201 的 Brier
        # 因此飙到 1.41), 把市场基线的分数往上抬。
        #
        # 两个错误叠加, 制造了「v2(0.430) 跑赢市场(0.444)」的假象。同基复算后真相相反:
        # 市场基线 0.374 显著优于 v2 0.430(2026-07-13 实测, n=44)。
        # 评测的意义在于三方在**同一批样本**上比 —— 没有 v2 预测的场次不属于这个比较。
        if not v2rec:
            continue

        mk_bucket = _bucket_for_match(v2rec)
        for market, cfg in MARKETS:
            bl = baseline_market(cache_path, mk, cfg)
            if not bl:
                continue
            actual = _actual_for(market, hg, ag, bl.get("line"))
            if actual is None:
                continue
            v2p = ((v2rec or {}).get("markets", {}).get(market) or {}).get("v2")
            v1p = v1rec["probs"] if (market == "had" and v1rec) else None  # v1 仅胜平负
            b = three_way(v1p, v2p, bl["baseline"], actual)
            deviated = bool(((v2rec or {}).get("markets", {}).get(market) or {}).get("deviations"))
            out[market]["rows"].append({"deviated": deviated, "v2": b["v2"], "market": b["market"]})
            devs = ((v2rec or {}).get("markets", {}).get(market) or {}).get("deviations") or []
            out[market]["per_match"].append({"match_key": mk,
                                             "reliability": (v2rec or {}).get("reliability", ""),
                                             "bucket": mk_bucket,
                                             "brier": b, "deviations": devs})
    return out


def collect_score_arm(cache_path):
    """每场 v1 比分预测 vs 实际(带场型 bucket),供 score_arm 分桶。无 v1 比分的场 pred=None。"""
    rows = []
    for mk in _result_keys(cache_path):
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        v1rec = get_v1(cache_path, mk)
        pred = parse_score(v1rec["score_pred"]) if v1rec else None
        v2rec = get_v2_prediction(cache_path, mk)
        rows.append({"match_key": mk, "pred": pred, "actual": goals,
                     "bucket": _bucket_for_match(v2rec)})
    return rows


def collect_calibration(cache_path, market="had"):
    """收每条 (预测概率%, 是否发生) 配对,供校准分桶。默认 had:每场贡献 h/d/a 三条。

    返回 {"v2":[(p,occ)..], "market":[(p,occ)..]}。

    🔴 同基铁则(与 collect() 一致):只收**有 v2 预测**的场次。
    旧实现是「无 v2 预测的场只入市场点」——于是市场列吃 86 场、v2 列只吃 44 场,
    **校准表两列根本不可比**(实测市场 n 总和 258 vs v2 132)。更糟的是多出来的那批
    全是非世界杯的 2xx 其他联赛:它们的盘口**每天被 refresh_all 刷新**、赛果又被跨周
    主键覆盖(match_key='周五201' 这类周内循环编号不含日期)→ **每跑一次 cron,校准表的
    市场列就变一次数字**,跑分卡永远处于「已修改」状态、git 里天天有假 diff。
    (2026-07-13 修 collect() 的同基问题时漏了本函数,7/14 补上。)
    """
    cfg = dict(MARKETS)[market]
    v2_pairs, mkt_pairs = [], []
    for mk in _result_keys(cache_path):
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        hg, ag = goals
        v2rec = get_v2_prediction(cache_path, mk)
        if not v2rec:
            continue  # 同基:无 v2 预测的场次不进校准表(两列必须同一批样本)
        bl = baseline_market(cache_path, mk, cfg)
        if not bl:
            continue
        actual = _actual_for(market, hg, ag, bl.get("line"))
        if actual is None:
            continue
        for k, p in bl["baseline"].items():
            mkt_pairs.append((p, 1 if k == actual else 0))
        v2p = ((v2rec.get("markets", {}) or {}).get(market) or {}).get("v2")
        if v2p:
            for k, p in v2p.items():
                v2_pairs.append((p, 1 if k == actual else 0))
    return {"v2": v2_pairs, "market": mkt_pairs}


def main():
    ap = argparse.ArgumentParser(description="渲染 v2 全盘口跑分卡")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--wc-db", default=DEFAULT_WC_DB, help="轮次来源(matches 的 ko_bj 推末轮)")
    a = ap.parse_args()
    collected = collect(a.cache)
    audits = {m: deviation_audit(collected[m]["rows"]) for m, _ in MARKETS}
    score_rows = collect_score_arm(a.cache)
    matchdays = _load_matchdays(a.wc_db)
    calib = collect_calibration(a.cache)
    md = render(collected, audits, score_rows, matchdays, calib)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"写出全盘口跑分卡 → {a.out}")


if __name__ == "__main__":
    main()
