import sqlite3, json, os, tempfile
from tools.backtest_baseline import run_backtest


def _seed(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE odds_cache (id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT)""")
    # 一场:三源都看好客胜,实际客胜 → Brier 应较低
    conn.executemany("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) "
                     "VALUES (?,?,?,?,?,?)", [
        ("t", "zucai", "M1", "A vs B", "k", json.dumps({"had": {"h": 6.0, "d": 3.9, "a": 1.44}})),
        ("t", "poly", "M1", "A vs B", "k", json.dumps({"poly_devig": {"h": 15.4, "d": 23.4, "a": 61.2}})),
        ("t", "consensus", "M1", "A vs B", "k", json.dumps({"had": {"h": 6.5, "d": 4.0, "a": 1.55}})),
    ])
    conn.commit(); conn.close()


def test_run_backtest_pairs_and_scores():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)
    from backend.baseline import record_result
    record_result(path, "M1", 0, 1)  # 客胜
    out = run_backtest(path)
    assert out["n"] == 1
    assert 0.0 <= out["market_brier"] <= 2.0
    # 基线重押客胜且客胜真发生 → Brier 明显小于 0.5(乱猜量级)
    assert out["market_brier"] < 0.5


def test_run_backtest_no_pairs():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    _seed(path)  # 有基线但无结果
    assert run_backtest(path)["n"] == 0
