#!/usr/bin/env python3
"""渲染 v2 跑分卡 → reports/预测v2.md。

用法: python3 tools/v2_report.py [--cache .cache/odds_cache.db] [--out reports/预测v2.md]
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from backend.scorecard import aggregate, bucket_of, deviation_audit, three_way  # noqa: E402
from backend.baseline import baseline_market, MARKETS, get_result_goals, _actual_for  # noqa: E402
from backend.v2_predict import get_v2_prediction  # noqa: E402
from backend.v1_log import get_v1  # noqa: E402

MARKET_NAMES = {"had": "胜平负", "hhad": "让球", "ttg": "总进球/大小球"}

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))
DEFAULT_OUT = os.path.join(REPO, "reports", "预测v2.md")


def _fmt(x):
    # 统一展示粒度: 数值一律 2 位小数, 缺失(None)显示 —。三均值与每场表共用,
    # 避免每场 Brier 露出 4 位原始精度而均值只有 2 位的不齐(Minor #1/#2)。
    if isinstance(x, (int, float)):
        return f"{x:.2f}"
    return "—" if x is None else x


def _bucket_lines(per_match):
    """每盘口"按场型分桶"子表:全局/常规/动机畸形,各切片调 aggregate(越低越准)。

    单桶 n<5 标 ⚠(末轮畸形场稀少,小样本只能定性、别拿几场判 v1 死刑)。空桶不出行。
    """
    def agg_where(pred):
        return aggregate([{"v1": p["brier"]["v1"], "v2": p["brier"]["v2"],
                           "market": p["brier"]["market"]} for p in per_match if pred(p)])

    specs = [("全局", lambda p: True),
             ("常规", lambda p: p.get("bucket", "常规") == "常规"),
             ("动机畸形", lambda p: p.get("bucket", "常规") == "动机畸形")]
    out = ["**按场型分桶**(同盘口切片):", "",
           "| 分桶 | n | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
    for label, pred in specs:
        a = agg_where(pred)
        if a["n"] == 0:
            continue
        n_disp = f"{a['n']} ⚠" if (label != "全局" and a["n"] < 5) else str(a["n"])
        out.append(f"| {label} | {n_disp} | {_fmt(a.get('v1_mean'))} | "
                   f"{_fmt(a.get('v2_mean'))} | {_fmt(a.get('market_mean'))} |")
    return out + [""]


def render(collected, audits):
    lines = ["# 预测 v2 跑分卡(全盘口)", ""]
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
        lines += _bucket_lines(data["per_match"])
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
        _scen = (v2rec or {}).get("scenarios") or []
        scen_names = [s.get("name") if isinstance(s, dict) else s for s in _scen]
        mk_bucket = bucket_of((v2rec or {}).get("reliability", ""), scen_names)
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


def main():
    ap = argparse.ArgumentParser(description="渲染 v2 全盘口跑分卡")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    collected = collect(a.cache)
    audits = {m: deviation_audit(collected[m]["rows"]) for m, _ in MARKETS}
    md = render(collected, audits)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"写出全盘口跑分卡 → {a.out}")


if __name__ == "__main__":
    main()
