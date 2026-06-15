"""SQLite schema + CRUD (stdlib sqlite3).

契约表 (见计划):
- matches(id, zucai_num, home_cn, away_cn, home_en, away_en, poly_slug, ko_bj, cutoff_bj, status)
- odds_snapshots(id, match_id, ts, source, payload_json)            source∈{zucai,poly}
- value_points(id, match_id, ts, market, outcome, zucai_odds, poly_prob_raw,
               poly_prob_devig, value_raw, value_devig, ev_pct, flag)  flag∈{green,yellow,skip}
- watchlist(id, kind, key, note)                                     kind∈{team,match,player}
- bets(id, ts, wallet, legs_json, stake, odds, status, payout, note) wallet∈{A,B}
- app_config(key, value)

方法返回 dict (row_factory=sqlite3.Row)。
"""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    zucai_num TEXT,
    home_cn TEXT, away_cn TEXT,
    home_en TEXT, away_en TEXT,
    poly_slug TEXT,
    ko_bj TEXT, cutoff_bj TEXT,
    status TEXT DEFAULT 'Selling',
    UNIQUE(zucai_num)
);
CREATE TABLE IF NOT EXISTS odds_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER,
    ts TEXT,
    source TEXT,
    payload_json TEXT
);
CREATE TABLE IF NOT EXISTS value_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER,
    ts TEXT,
    market TEXT, outcome TEXT,
    zucai_odds REAL,
    poly_prob_raw REAL, poly_prob_devig REAL,
    value_raw REAL, value_devig REAL,
    ev_pct REAL,
    flag TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, key TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    wallet TEXT,
    legs_json TEXT,
    stake REAL,
    odds REAL,
    status TEXT DEFAULT 'open',
    payout REAL DEFAULT 0,
    note TEXT
);
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now_bj():
    return datetime.now(BJ).isoformat(timespec="seconds")


class Db:
    def __init__(self, path):
        self.path = str(path)

    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------- matches ----------
    def upsert_match(self, zucai_num, home_cn, away_cn, home_en, away_en,
                     poly_slug, ko_bj, cutoff_bj, status="Selling"):
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO matches
                   (zucai_num, home_cn, away_cn, home_en, away_en, poly_slug, ko_bj, cutoff_bj, status)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(zucai_num) DO UPDATE SET
                     home_cn=excluded.home_cn, away_cn=excluded.away_cn,
                     home_en=excluded.home_en, away_en=excluded.away_en,
                     poly_slug=excluded.poly_slug, ko_bj=excluded.ko_bj,
                     cutoff_bj=excluded.cutoff_bj, status=excluded.status""",
                (zucai_num, home_cn, away_cn, home_en, away_en, poly_slug,
                 ko_bj, cutoff_bj, status),
            )
            if cur.lastrowid:
                row = conn.execute(
                    "SELECT id FROM matches WHERE zucai_num=?", (zucai_num,)
                ).fetchone()
                return row["id"]

    def matches(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM matches ORDER BY ko_bj").fetchall()
            return [dict(r) for r in rows]

    # ---------- snapshots ----------
    def save_snapshot(self, match_id, source, payload):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO odds_snapshots (match_id, ts, source, payload_json) VALUES (?,?,?,?)",
                (match_id, _now_bj(), source, json.dumps(payload, ensure_ascii=False)),
            )

    # ---------- value points ----------
    def save_value_points(self, match_id, points):
        """points: iterable of ValuePoint (dataclass) 或 dict。

        替换语义: 先删该场旧 value_points 再插新批, 保证只保留最新一轮
        (历史盘口在 odds_snapshots; value_points 只反映当前态, 避免累积/陈旧 flag)。
        """
        ts = _now_bj()
        with self._conn() as conn:
            conn.execute("DELETE FROM value_points WHERE match_id=?", (match_id,))
            for p in points:
                d = p if isinstance(p, dict) else p.__dict__
                conn.execute(
                    """INSERT INTO value_points
                       (match_id, ts, market, outcome, zucai_odds,
                        poly_prob_raw, poly_prob_devig, value_raw, value_devig, ev_pct, flag)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (match_id, ts, d["market"], d["outcome"], d["zucai_odds"],
                     d["poly_prob_raw"], d["poly_prob_devig"], d["value_raw"],
                     d["value_devig"], d["ev_pct"], d["flag"]),
                )

    def value_points(self):
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM value_points ORDER BY ev_pct DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def latest_value_points(self):
        """每个 match_id 取最新一批 (最大 ts) 的 value_points。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT vp.* FROM value_points vp
                   JOIN (SELECT match_id, MAX(ts) AS mts FROM value_points GROUP BY match_id) m
                   ON vp.match_id=m.match_id AND vp.ts=m.mts
                   ORDER BY vp.ev_pct DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- bets ----------
    def add_bet(self, wallet, legs, stake, odds, note=""):
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO bets (ts, wallet, legs_json, stake, odds, status, payout, note)
                   VALUES (?,?,?,?,?,'open',0,?)""",
                (_now_bj(), wallet, json.dumps(legs, ensure_ascii=False), stake, odds, note),
            )
            return cur.lastrowid

    def settle_bet(self, id, status, payout):
        with self._conn() as conn:
            conn.execute(
                "UPDATE bets SET status=?, payout=? WHERE id=?",
                (status, payout, id),
            )

    def bets(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM bets ORDER BY ts DESC").fetchall()
            return [dict(r) for r in rows]

    def ledger(self, b_budget=100):
        """A: {stake,pnl,roi,n}; B: {budget,spent,pnl,n}。
        pnl = Σ(payout-stake for settled); roi = pnl/stake。"""
        out = {
            "A": {"stake": 0.0, "pnl": 0.0, "roi": 0.0, "n": 0},
            "B": {"budget": b_budget, "spent": 0.0, "pnl": 0.0, "n": 0},
        }
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM bets").fetchall()
        for r in rows:
            w = r["wallet"]
            if w not in out:
                continue
            stake = r["stake"] or 0.0
            out[w]["n"] += 1
            if w == "A":
                out["A"]["stake"] += stake
            else:
                out["B"]["spent"] += stake
            if r["status"] in ("won", "lost", "settled", "push"):
                out[w]["pnl"] += (r["payout"] or 0.0) - stake
        if out["A"]["stake"] > 0:
            out["A"]["roi"] = round(out["A"]["pnl"] / out["A"]["stake"], 4)
        # 数值收口
        for w in out:
            for k in ("stake", "pnl", "spent"):
                if k in out[w]:
                    out[w][k] = round(out[w][k], 2)
        return out

    # ---------- watchlist ----------
    def watchlist(self):
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM watchlist ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def add_watch(self, kind, key, note=""):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO watchlist (kind, key, note) VALUES (?,?,?)",
                (kind, key, note),
            )
            return cur.lastrowid

    def del_watch(self, id):
        with self._conn() as conn:
            conn.execute("DELETE FROM watchlist WHERE id=?", (id,))

    # ---------- app_config ----------
    def get_config(self, key, default=None):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_config(self, key, value):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO app_config (key, value) VALUES (?,?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (key, str(value)),
            )
