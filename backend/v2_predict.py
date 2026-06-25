"""v2 预测装配:在基线上应用有据偏离,附靠谱度 + 剧本标签。概率 %。"""
import json
import sqlite3

_KEYS = ("h", "d", "a")


def apply_deviations(baseline: dict, deviations: list, keys=_KEYS) -> dict:
    """把每条偏离的 outcome 钉到 to;非偏离 outcome 按基线原比例吸收差额;归一到 100。
    先一次性收集所有被钉 outcome(同一 outcome 多次取最后),再统一分配剩余,
    同场多条偏离不互相重标前者;单条结果与原实现一致。keys 支持任意盘口(ttg 多键)。"""
    cur = {k: float(baseline.get(k, 0.0)) for k in keys}
    pinned = {dv["outcome"]: float(dv["to"]) for dv in deviations}
    for oc, to in pinned.items():
        cur[oc] = to
    others = [k for k in keys if k not in pinned]
    rest_old = sum(float(baseline.get(k, 0.0)) for k in others)
    rest_new = max(0.0, 100.0 - sum(pinned.values()))
    if others:
        if rest_old > 0:
            for k in others:
                cur[k] = float(baseline.get(k, 0.0)) / rest_old * rest_new
        else:
            for k in others:
                cur[k] = rest_new / len(others)
    tot = sum(cur.values())
    return {k: round(cur[k] / tot * 100, 1) for k in keys} if tot else cur


def build_v2_prediction(match_key, reliability, scenarios, markets_in):
    """按盘口装配 v2 预测。markets_in: {market: {baseline, deviations[, line]}}。
    每盘口产 {baseline, v2(应用偏离), deviations[, line][, ou(仅 ttg)]}。"""
    from .baseline import over_under
    markets = {}
    for m, mi in markets_in.items():
        base = mi["baseline"]
        keys = tuple(base)
        entry = {"baseline": dict(base),
                 "v2": apply_deviations(base, mi.get("deviations") or [], keys=keys),
                 "deviations": mi.get("deviations") or []}
        if "line" in mi:
            entry["line"] = mi["line"]
        if m == "ttg":
            entry["ou"] = over_under(entry["v2"], lines=(2.5,))
        markets[m] = entry
    return {"match_key": match_key, "reliability": reliability,
            "scenarios": scenarios or [], "markets": markets}


_V2_SCHEMA = """CREATE TABLE IF NOT EXISTS v2_predictions (
    match_key TEXT PRIMARY KEY, ts TEXT, prediction_json TEXT)"""


def record_v2_prediction(cache_path, match_key, prediction):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_V2_SCHEMA)
        conn.execute(
            """INSERT INTO v2_predictions(match_key, ts, prediction_json)
               VALUES (?, datetime('now'), ?)
               ON CONFLICT(match_key) DO UPDATE SET
                 ts=excluded.ts, prediction_json=excluded.prediction_json""",
            (match_key, json.dumps(prediction, ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()


def get_v2_prediction(cache_path, match_key):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_V2_SCHEMA)
        r = conn.execute("SELECT prediction_json FROM v2_predictions WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return json.loads(r[0]) if r else None
