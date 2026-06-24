# backend/v1_log.py
"""v1 预测并行记录(冻结对照)。概率 %。"""
import json
import sqlite3

_SCHEMA = """CREATE TABLE IF NOT EXISTS v1_predictions (
    match_key TEXT PRIMARY KEY, ts TEXT, probs_json TEXT, score_pred TEXT)"""


def record_v1(cache_path, match_key, probs, score_pred=""):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_SCHEMA)
        conn.execute(
            """INSERT INTO v1_predictions(match_key, ts, probs_json, score_pred)
               VALUES (?, datetime('now'), ?, ?)
               ON CONFLICT(match_key) DO UPDATE SET
                 ts=excluded.ts, probs_json=excluded.probs_json, score_pred=excluded.score_pred""",
            (match_key, json.dumps(probs, ensure_ascii=False), score_pred))
        conn.commit()
    finally:
        conn.close()


def get_v1(cache_path, match_key):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_SCHEMA)
        r = conn.execute("SELECT probs_json, score_pred FROM v1_predictions WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return {"probs": json.loads(r[0]), "score_pred": r[1]} if r else None
