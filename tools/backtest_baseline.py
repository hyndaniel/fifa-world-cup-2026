#!/usr/bin/env python3
"""回测:对所有「有基线+有结果」的场,算市场基线的 Brier + 校准,立基准线。

用法: python3 tools/backtest_baseline.py [--cache .cache/odds_cache.db]
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from backend.baseline import baseline_had, get_result  # noqa: E402
from backend.scoring import brier_multi, calibration_buckets  # noqa: E402

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))


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


def run_backtest(cache_path: str) -> dict:
    per_match, briers, cal_preds = [], [], []
    for mk in _result_keys(cache_path):
        bl = baseline_had(cache_path, mk)
        actual = get_result(cache_path, mk)
        if not bl or not actual:
            continue
        b = brier_multi(bl["baseline"], actual)
        briers.append(b)
        per_match.append({"match_key": mk, "baseline": bl["baseline"],
                          "actual": actual, "brier": b})
        # 校准:记录"基线给实际结果的概率" vs 是否发生(发生=1)
        cal_preds.append((bl["baseline"].get(actual, 0.0), 1))
    n = len(briers)
    return {
        "n": n,
        "market_brier": round(sum(briers) / n, 4) if n else None,
        "per_match": per_match,
        "calibration": calibration_buckets(cal_preds) if cal_preds else [],
    }


def main():
    ap = argparse.ArgumentParser(description="回测市场基线 Brier 基准")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    a = ap.parse_args()
    out = run_backtest(a.cache)
    print(f"配对场数: {out['n']}")
    if out["n"]:
        print(f"市场基线 Brier 基准: {out['market_brier']}  (越低越准, 0=完美, ~0.66=乱猜三选一)")
        for m in out["per_match"]:
            print(f"  {m['match_key']}: 基线={m['baseline']} 实际={m['actual']} Brier={m['brier']}")
    else:
        print("无「基线+结果」配对(先 record_result 录入实际比分)。")


if __name__ == "__main__":
    main()
