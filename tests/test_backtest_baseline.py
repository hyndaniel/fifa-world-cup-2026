import sqlite3, json, os, tempfile
from tools.backtest_baseline import run_backtest


def _seed(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", [
        ("t", "zucai", "M1", "A vs B", "k",
         json.dumps({"had": {"h": 6.0, "d": 3.9, "a": 1.44},
                     "hhad": {"line": 1, "h": 2.2, "d": 3.3, "a": 2.9},
                     "ttg": {"0": 12.0, "1": 6.0, "2": 4.5, "3": 4.5, "4": 7.0}})),
        ("t", "poly", "M1", "A vs B", "k", json.dumps({"poly_devig": {"h": 15.4, "d": 23.4, "a": 61.2}})),
        ("t", "consensus", "M1", "A vs B", "k", json.dumps({"had": {"h": 6.5, "d": 4.0, "a": 1.55}})),
    ])
    conn.commit(); conn.close()


def test_run_backtest_per_market_pairs_and_scores():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)
    from backend.baseline import record_result
    record_result(path, "M1", 0, 1)  # 客胜, 总进球 1
    out = run_backtest(path)
    assert out["had"]["n"] == 1 and out["had"]["market_brier"] < 0.5   # 重押客胜且应验
    assert out["ttg"]["n"] == 1 and 0.0 <= out["ttg"]["market_brier"] <= 2.0
    assert out["hhad"]["n"] == 1                                       # 让球也配上对


def test_run_backtest_no_results():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)
    assert run_backtest(path)["had"]["n"] == 0
