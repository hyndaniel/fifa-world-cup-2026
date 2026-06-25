#!/usr/bin/env python3
"""回测:对所有「有基线+有结果」的场,逐盘口算市场基线的 Brier,立基准线。

用法: python3 tools/backtest_baseline.py [--cache .cache/odds_cache.db]
"""
import argparse
import os
import sqlite3
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from backend.baseline import baseline_market, MARKETS, get_result_goals, _actual_for  # noqa: E402
from backend.scoring import brier_multi  # noqa: E402

DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))

MARKET_NAMES = {"had": "胜平负", "hhad": "让球", "ttg": "总进球"}


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
    out = {m: {"n": 0, "market_brier": None, "per_match": []} for m, _ in MARKETS}
    for mk in _result_keys(cache_path):
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        hg, ag = goals
        for market, cfg in MARKETS:
            bl = baseline_market(cache_path, mk, cfg)
            if not bl:
                continue
            actual = _actual_for(market, hg, ag, bl.get("line"))
            if actual is None:
                continue
            b = brier_multi(bl["baseline"], actual)
            out[market]["per_match"].append({"match_key": mk, "baseline": bl["baseline"],
                                             "actual": actual, "brier": b})
    for market, _ in MARKETS:
        briers = [p["brier"] for p in out[market]["per_match"]]
        out[market]["n"] = len(briers)
        out[market]["market_brier"] = round(sum(briers) / len(briers), 4) if briers else None
    return out


def main():
    ap = argparse.ArgumentParser(description="回测逐盘口市场基线 Brier 基准")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    a = ap.parse_args()
    out = run_backtest(a.cache)
    for market, _ in MARKETS:
        data = out[market]
        print(f"== {MARKET_NAMES[market]} == 配对 {data['n']} 场")
        if data["n"]:
            print(f"  市场基线 Brier 基准: {data['market_brier']}")
            for m in data["per_match"]:
                print(f"    {m['match_key']}: 实际={m['actual']} Brier={m['brier']}")
        else:
            print("  无「基线+结果」配对(先 record_result)。")


if __name__ == "__main__":
    main()
