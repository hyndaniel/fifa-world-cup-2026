"""胜平负市场基线:多源去水 + 加权融合 + 置信分级。概率单位 %。"""
import json
import sqlite3

from .devig import devig

DEFAULT_WEIGHTS = {"zucai": 0.20, "poly": 0.45, "consensus": 0.35}
_KEYS = ("h", "d", "a")


def zucai_odds_devig(odds: dict, keys=_KEYS) -> dict:
    """竞彩欧赔 → 去水概率 %。隐含=1/赔率, 乘法归一到 100。keys 限定参与的 outcome。"""
    implied = {k: 1.0 / float(odds[k]) for k in keys if odds.get(k)}
    return {k: round(v, 1) for k, v in devig(implied).items()}


def zucai_had_devig(had: dict) -> dict:
    """胜平负三选一去水(zucai_odds_devig 瘦封装, 向后兼容)。"""
    return zucai_odds_devig(had, _KEYS)


def blend(sources: dict, keys, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """多源去水概率加权融合;只用在场源、权重重新归一;输出按 keys 归一到 100。"""
    present = {s: weights[s] for s in sources if s in weights and sources[s]}
    wsum = sum(present.values())
    if wsum <= 0:
        return {}
    raw = {k: sum(sources[s].get(k, 0.0) * present[s] for s in present) / wsum for k in keys}
    tot = sum(raw.values())
    if tot <= 0:
        return {}
    out = {k: round(v / tot * 100, 1) for k, v in raw.items()}
    # 独立四舍五入会让和偏离 100,把残差并入最大项,严格归一。
    residual = round(100.0 - sum(out.values()), 1)
    if residual:
        kmax = max(out, key=out.get)
        out[kmax] = round(out[kmax] + residual, 1)
    return out


def blend_had(sources: dict, weights: dict = DEFAULT_WEIGHTS) -> dict:
    """胜平负融合(blend 瘦封装, 向后兼容)。"""
    return blend(sources, _KEYS, weights)


def confidence(sources: dict, keys=_KEYS) -> dict:
    """覆盖几个源 + 跨源最大极差。3源=hard/2=medium/1=soft/0=none。"""
    n = len(sources)
    label = {3: "hard", 2: "medium", 1: "soft"}.get(n, "none")
    spread = 0.0
    for k in keys:
        vals = [s[k] for s in sources.values() if k in s]
        if len(vals) >= 2:
            spread = max(spread, max(vals) - min(vals))
    return {"n_sources": n, "label": label, "max_spread": round(spread, 1)}


def _latest_payload(conn, source, match_key):
    r = conn.execute(
        "SELECT payload_json FROM odds_cache WHERE source=? AND match_key=? "
        "ORDER BY ts DESC LIMIT 1", (source, match_key)).fetchone()
    return json.loads(r[0]) if r else None


HAD_CFG = {"market": "had", "pool": "had", "keys": _KEYS, "weights": DEFAULT_WEIGHTS}
HHAD_CFG = {"market": "hhad", "pool": "hhad", "keys": _KEYS, "weights": {"zucai": 1.0}}


def _market_keys(cfg, payloads):
    """固定 keys 直接返回;keys=None(ttg)从竞彩 payload 动态取并按数值排序。"""
    if cfg["keys"] is not None:
        return cfg["keys"]
    pool = (payloads.get("zucai") or {}).get(cfg["pool"]) or {}
    return tuple(sorted((k for k in pool if k != "line"), key=lambda x: int(x)))


def baseline_market(cache_path, match_key, cfg, weights=None):
    """从 odds_cache 装配一场某盘口基线表。无任何源 → None。
    返回 {match_key, market, baseline, sources, confidence[, line]}。"""
    weights = weights or cfg["weights"]
    conn = sqlite3.connect(cache_path)
    try:
        raw = {s: _latest_payload(conn, s, match_key) for s in ("zucai", "poly", "consensus")}
    finally:
        conn.close()
    keys = _market_keys(cfg, raw)
    pool, sources, line = cfg["pool"], {}, None
    z = raw.get("zucai")
    if z and z.get(pool):
        zp = z[pool]
        line = zp.get("line") if isinstance(zp, dict) else None
        sources["zucai"] = zucai_odds_devig(zp, keys)
    # poly / consensus 仅对胜平负有对应定价(我们的源里 hhad/ttg 无)
    if pool == "had":
        p = raw.get("poly")
        if p and p.get("poly_devig"):
            sources["poly"] = {k: round(float(p["poly_devig"][k]), 1) for k in keys
                               if p["poly_devig"].get(k) is not None}
        c = raw.get("consensus")
        if c and c.get("had"):
            sources["consensus"] = zucai_odds_devig(c["had"], keys)
    if not sources:
        return None
    out = {"match_key": match_key, "market": cfg["market"],
           "baseline": blend(sources, keys, weights),
           "sources": sources, "confidence": confidence(sources, keys)}
    if line is not None:
        out["line"] = line
    return out


def baseline_had(cache_path: str, match_key: str, weights: dict = DEFAULT_WEIGHTS):
    """胜平负基线(baseline_market 瘦封装, 向后兼容)。无任何源 → None。"""
    return baseline_market(cache_path, match_key, HAD_CFG, weights)


def _hhad_outcome(home_goals: int, away_goals: int, line) -> str:
    """让球结算: (主 + line - 客) 的符号 → h/d/a。line=主队让球数(主让一球记 -1)。
    整数盘三分法, 无 push。"""
    adj = home_goals + float(line) - away_goals
    if adj > 0:
        return "h"
    if adj == 0:
        return "d"
    return "a"


_RESULTS_SCHEMA = """CREATE TABLE IF NOT EXISTS match_results (
    match_key TEXT PRIMARY KEY, home_goals INTEGER, away_goals INTEGER,
    outcome TEXT, ts TEXT)"""


def _outcome_key(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "h"
    if home_goals == away_goals:
        return "d"
    return "a"


def record_result(cache_path: str, match_key: str, home_goals: int, away_goals: int) -> str:
    outcome = _outcome_key(home_goals, away_goals)
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)
        conn.execute(
            """INSERT INTO match_results(match_key, home_goals, away_goals, outcome, ts)
               VALUES (?,?,?,?,datetime('now'))
               ON CONFLICT(match_key) DO UPDATE SET
                 home_goals=excluded.home_goals, away_goals=excluded.away_goals,
                 outcome=excluded.outcome, ts=excluded.ts""",
            (match_key, home_goals, away_goals, outcome))
        conn.commit()
    finally:
        conn.close()
    return outcome


def get_result(cache_path: str, match_key: str):
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)
        r = conn.execute("SELECT outcome FROM match_results WHERE match_key=?",
                         (match_key,)).fetchone()
    finally:
        conn.close()
    return r[0] if r else None
