#!/usr/bin/env python3
"""odds_watch — 本地稳定缓存各盘口赔率 + 看变化(line movement)。

默认抓竞彩(backend.sporttery, 本地直连可达)→ 存独立 sqlite 缓存 → 每次与上一快照
对比, 打印赔率/水位变化。缓存是"源无关"的: Poly(从 HK remote-agent 拉的)、亚盘/欧盘
共识都可经 --ingest <file.json> --source <名> 喂进同一缓存, 一样缓存 + 看变化。

缓存默认 ./.cache/odds_cache.db (可用 WC_ODDS_CACHE 覆盖)。不依赖 app 的库, 不碰现有文件。

用法:
  python3 tools/odds_watch.py                  # 抓竞彩一次, 打印相对上次的变化
  python3 tools/odds_watch.py --loop 180        # 每180s 抓一次, 持续看变化
  python3 tools/odds_watch.py --all             # 连未变化的也列出
  python3 tools/odds_watch.py --history 周三051  # 看某场赔率时间线
  python3 tools/odds_watch.py --list            # 列已缓存的场次/源
  python3 tools/odds_watch.py --ingest poly.json --source poly   # 喂入外部源(如HK拉的Poly)
  python3 tools/odds_watch.py --selftest        # 离线自测对比逻辑
"""
from __future__ import annotations
import argparse, json, os, sqlite3, sys, time
from datetime import datetime, timezone, timedelta

BJ = timezone(timedelta(hours=8))
def now_bj() -> str: return datetime.now(BJ).isoformat(timespec="seconds")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)  # 让 `from backend import ...` 从任何 CWD 都可用
DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))

SCHEMA = """CREATE TABLE IF NOT EXISTS odds_cache (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, source TEXT, match_key TEXT, label TEXT, ko TEXT, payload_json TEXT);
CREATE INDEX IF NOT EXISTS idx_oc ON odds_cache(source, match_key, ts);"""

def connect(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    c = sqlite3.connect(path); c.row_factory = sqlite3.Row
    c.executescript(SCHEMA); return c

def save(c, source, match_key, label, ko, payload):
    c.execute("INSERT INTO odds_cache(ts,source,match_key,label,ko,payload_json) VALUES (?,?,?,?,?,?)",
              (now_bj(), source, match_key, label, ko, json.dumps(payload, ensure_ascii=False)))
    c.commit()

def latest(c, source, match_key):
    r = c.execute("SELECT payload_json FROM odds_cache WHERE source=? AND match_key=? ORDER BY ts DESC LIMIT 1",
                  (source, match_key)).fetchone()
    return json.loads(r["payload_json"]) if r else None

# ---------- devig 显示用 ----------
def implied3(had):
    """欧赔 → 去水% [h,d,a] 列表(显示用); 实现收敛到 backend.devig。"""
    from backend.devig import devig_from_odds
    dv = devig_from_odds(had if isinstance(had, dict) else None)
    return [dv["h"], dv["d"], dv["a"]] if dv else None

# ---------- 对比 ----------
def _arrow(old, new): return "▲升水" if new > old else "▼降水"

def diff_payload(old, new):
    """返回变化描述行列表(覆盖 had 胜平负 / hhad 让球 / ttg 总进球)。"""
    out = []
    o, n = old or {}, new or {}
    # had 胜平负
    oh, nh = o.get("had") or {}, n.get("had") or {}
    names = {"h": "主胜", "d": "平", "a": "客胜"}
    oi, ni = implied3(oh), implied3(nh)
    for i, k in enumerate(("h", "d", "a")):
        ov, nv = oh.get(k), nh.get(k)
        if ov is None or nv is None or abs(nv - ov) < 0.005: continue
        extra = f"  隐含 {oi[i]}→{ni[i]}%" if oi and ni else ""
        out.append(f"    胜平负·{names[k]}: {ov} → {nv} {_arrow(ov, nv)}{extra}")
    # hhad 让球
    ohh, nhh = o.get("hhad") or {}, n.get("hhad") or {}
    if ohh.get("line") != nhh.get("line") and (ohh or nhh):
        out.append(f"    让球线: {ohh.get('line')} → {nhh.get('line')} (盘口移动)")
    for k in ("h", "d", "a"):
        ov, nv = ohh.get(k), nhh.get(k)
        if ov is None or nv is None or abs(nv - ov) < 0.005: continue
        out.append(f"    让球·{names[k]}: {ov} → {nv} {_arrow(ov, nv)}")
    # ttg 总进球
    ott, ntt = o.get("ttg") or {}, n.get("ttg") or {}
    for k in sorted(set(list(ott.keys()) + list(ntt.keys())), key=lambda x: int(x)):
        ov, nv = ott.get(str(k), ott.get(k)), ntt.get(str(k), ntt.get(k))
        if ov is None or nv is None or abs(nv - ov) < 0.005: continue
        out.append(f"    总进球·{k}球: {ov} → {nv} {_arrow(ov, nv)}")
    return out

# ---------- 竞彩抓取 ----------
def fetch_zucai():
    from backend import sporttery
    d = sporttery.fetch({})
    items = []
    for m in sporttery.parse_matches(d):
        items.append({"match_key": m.zucai_num, "label": f"{m.home_cn} vs {m.away_cn}",
                      "ko": m.ko_bj, "payload": {"had": m.had, "hhad": m.hhad, "ttg": m.ttg}})
    return items

def fetch_consensus(league="世界杯"):
    """500 欧盘多家共识 -> ingest items(source=consensus)。欧赔存成 had 形状, 复用水位对比。"""
    import odds_consensus  # tools/ 同目录
    items = []
    for d in odds_consensus.collect(league):
        c = d.get("consensus")
        if not c:
            continue
        items.append({"match_key": f"500-{d['fixtureid']}",
                      "label": f"{d['home_cn']} vs {d['away_cn']}",
                      "ko": f"{d['date']} {d['time']}",
                      "payload": {"had": c["euro"], "devig_pct": c["devig_pct"], "n_books": c["n_books"]}})
    return items


# ---------- 命令 ----------
def run_once(c, source, items, show_all=False):
    print(f"[{now_bj()}] {source}: {len(items)} 场")
    changed = first = 0
    for it in items:
        old = latest(c, source, it["match_key"])
        save(c, source, it["match_key"], it["label"], it["ko"], it["payload"])
        if old is None:
            first += 1
            if show_all: print(f"  · {it['label']} [{it['match_key']}] 首次缓存")
            continue
        ch = diff_payload(old, it["payload"])
        if ch:
            changed += 1
            print(f"  ◆ {it['label']} [{it['match_key']}] {it['ko']}")
            for line in ch: print(line)
        elif show_all:
            print(f"  · {it['label']} [{it['match_key']}] 无变化")
    print(f"  小结: {changed} 场有变化, {first} 场首次入缓存, 共 {len(items)} 场")
    return changed

def cmd_history(c, source, key):
    rows = c.execute("SELECT ts,label,payload_json FROM odds_cache WHERE source=? AND match_key=? ORDER BY ts",
                     (source, key)).fetchall()
    if not rows: print(f"无缓存: source={source} match={key}"); return
    print(f"=== {source} {key} {rows[0]['label']} 赔率时间线 ({len(rows)} 快照) ===")
    for r in rows:
        had = (json.loads(r["payload_json"]).get("had")) or {}
        imp = implied3(had)
        s = f"{had.get('h')}/{had.get('d')}/{had.get('a')}" if had else "无胜平负"
        ex = f"  隐含 {imp[0]}/{imp[1]}/{imp[2]}%" if imp else ""
        print(f"  {r['ts']}  胜平负 {s}{ex}")

def cmd_list(c):
    rows = c.execute("""SELECT source, match_key, label, COUNT(*) n, MIN(ts) a, MAX(ts) b
                        FROM odds_cache GROUP BY source, match_key ORDER BY source, match_key""").fetchall()
    if not rows: print("缓存为空"); return
    print(f"=== 缓存内容 ({len(rows)} 场) ===")
    for r in rows:
        print(f"  [{r['source']}] {r['match_key']} {r['label']}  {r['n']}快照  {r['a']}→{r['b']}")

def selftest():
    import tempfile
    c = connect(os.path.join(tempfile.mkdtemp(), "t.db"))
    save(c, "zucai", "T1", "A vs B", "ko", {"had": {"h": 2.0, "d": 3.0, "a": 3.5}, "hhad": None, "ttg": {"2": 3.0}})
    old = latest(c, "zucai", "T1")
    new = {"had": {"h": 1.8, "d": 3.1, "a": 4.2}, "hhad": None, "ttg": {"2": 2.8}}
    ch = diff_payload(old, new)
    print("模拟变化(主胜 2.0→1.8 降水, 客胜 3.5→4.2 升水, 2球 3.0→2.8):")
    for line in ch: print(line)
    assert any("主胜" in x and "降水" in x for x in ch), ch
    assert any("客胜" in x and "升水" in x for x in ch), ch
    assert any("2球" in x for x in ch), ch
    save(c, "zucai", "T1", "A vs B", "ko", new)
    assert latest(c, "zucai", "T1")["had"]["h"] == 1.8
    print("✅ selftest pass (对比/缓存/读取均正确)")

def main():
    ap = argparse.ArgumentParser(description="盘口赔率缓存 + 看变化")
    ap.add_argument("--loop", type=int, metavar="SEC", help="每 SEC 秒抓一次")
    ap.add_argument("--once", action="store_true", help="抓一次(默认行为, 显式标注用)")
    ap.add_argument("--consensus", action="store_true", help="同时抓 500 欧盘共识(慢, ~20-40s)")
    ap.add_argument("--all", action="store_true", help="连未变化的也列出")
    ap.add_argument("--history", metavar="MATCH_KEY", help="看某场赔率时间线")
    ap.add_argument("--list", action="store_true", help="列已缓存场次")
    ap.add_argument("--ingest", metavar="FILE", help="喂入外部源 JSON(list of {match_key,label,ko,payload})")
    ap.add_argument("--source", default="zucai", help="源名(默认 zucai; ingest 时如 poly/consensus)")
    ap.add_argument("--cache", default=DEFAULT_CACHE, help=f"缓存路径(默认 {DEFAULT_CACHE})")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest: return selftest()
    c = connect(a.cache)
    if a.list: return cmd_list(c)
    if a.history: return cmd_history(c, a.source, a.history)
    if a.ingest:
        items = json.load(open(a.ingest, encoding="utf-8"))
        return (run_once(c, a.source, items, a.all), None)[1]
    def one():
        try:
            run_once(c, "zucai", fetch_zucai(), a.all)
        except Exception as e:
            print(f"  ❌ 抓竞彩失败: {e!r}")
        if a.consensus:
            try:
                run_once(c, "consensus", fetch_consensus(), a.all)
            except Exception as e:
                print(f"  ❌ 抓欧盘共识失败: {e!r}")
    if a.loop:
        print(f"循环看盘启动: 每 {a.loop}s 抓竞彩对比, Ctrl-C 停")
        while True:
            one(); time.sleep(a.loop)
    else:
        one()

if __name__ == "__main__":
    main()
