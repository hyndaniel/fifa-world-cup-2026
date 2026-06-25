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
from backend.scoring import brier_multi  # noqa: E402
from backend.scorecard import aggregate, deviation_audit, three_way  # noqa: E402
from backend.baseline import baseline_had, get_result  # noqa: E402
from backend.v2_predict import get_v2_prediction  # noqa: E402
from backend.v1_log import get_v1  # noqa: E402

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))
DEFAULT_OUT = os.path.join(REPO, "reports", "预测v2.md")


def _fmt(x):
    # 统一展示粒度: 数值一律 2 位小数, 缺失(None)显示 —。三均值与每场表共用,
    # 避免每场 Brier 露出 4 位原始精度而均值只有 2 位的不齐(Minor #1/#2)。
    if isinstance(x, (int, float)):
        return f"{x:.2f}"
    return "—" if x is None else x


def render(agg, audit, per_match):
    lines = ["# 预测 v2 跑分卡", "",
             f"配对场数: {agg['n']}", "",
             "| 方 | 平均 Brier(越低越准) |", "|---|---|",
             f"| v1(老方法) | {_fmt(agg.get('v1_mean'))} |",
             f"| **v2** | **{_fmt(agg.get('v2_mean'))}** |",
             f"| 市场基线 | {_fmt(agg.get('market_mean'))} |", ""]
    if audit.get("delta") is not None:
        verdict = "偏离平均**拉低** Brier(有用)" if audit["delta"] < 0 else "偏离平均**拉高** Brier(帮倒忙,该收敛回市场)"
        lines += [f"偏离审计:{audit['n_deviated']} 场有偏离,v2 {audit['v2_mean']} vs 市场 {audit['market_mean']},delta {audit['delta']} → {verdict}", ""]
    lines += ["## 每场", "", "| 场次 | 靠谱度 | v1 | v2 | 市场 |", "|---|---|---|---|---|"]
    for m in per_match:
        b = m["brier"]
        lines.append(f"| {m['match_key']} | {m.get('reliability','')} | "
                     f"{_fmt(b.get('v1'))} | {_fmt(b.get('v2'))} | {_fmt(b.get('market'))} |")
    lines += ["", "_红线:概率预测非投注建议;v2 跑不赢市场就回归市场基线+避雷器。_"]
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
    rows, per_match = [], []
    for mk in _result_keys(cache_path):
        actual = get_result(cache_path, mk)
        bl = baseline_had(cache_path, mk)
        v2 = get_v2_prediction(cache_path, mk)
        v1 = get_v1(cache_path, mk)
        if not actual or not bl:
            continue
        market = bl["baseline"]
        v2p = v2["v2"] if v2 else None
        v1p = v1["probs"] if v1 else None
        b = three_way(v1p, v2p, market, actual)
        deviated = bool(v2 and v2.get("deviations"))
        rows.append({"deviated": deviated, "v2": b["v2"], "market": b["market"]})
        per_match.append({"match_key": mk,
                          "reliability": (v2 or {}).get("reliability", ""),
                          "brier": b})
    return aggregate([{"v1": p["brier"]["v1"], "v2": p["brier"]["v2"],
                       "market": p["brier"]["market"]} for p in per_match]), \
        deviation_audit(rows), per_match


def main():
    ap = argparse.ArgumentParser(description="渲染 v2 跑分卡")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    agg, audit, per_match = collect(a.cache)
    md = render(agg, audit, per_match)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"写出跑分卡 → {a.out}(配对 {agg['n']} 场)")


if __name__ == "__main__":
    main()
