#!/usr/bin/env python3
"""refresh_all — 本地一把刷三源(竞彩 sporttery + 欧盘 500翻页 + Poly 7897代理)
→ 写本地 odds_watch 缓存 → 按 label 对齐到 zucai_num、算变化+分歧 → POST HK 看板
两端点(/api/ingest/zucai 竞彩raw, /api/ingest/odds 赔率面板)。取代 collect_zucai。

用法:
  python3 tools/refresh_all.py --once          # 跑一次
  python3 tools/refresh_all.py --loop 300        # 每300s
  python3 tools/refresh_all.py --once --dry-run  # 抓+对齐但不推 HK(冒烟)
环境: WC_INGEST_URL / WC_INGEST_PW / WC_INGEST_USER(admin) / WC_HTTPS_PROXY(7897)
      WC_ODDS_CACHE(覆盖本地缓存路径, 与 odds_watch 一致)
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, REPO)
import odds_watch as ow  # noqa: E402
import poly_fetch_hk as pf  # noqa: E402
from backend import sporttery  # noqa: E402

PROXY = os.environ.get("WC_HTTPS_PROXY", "http://127.0.0.1:7897")
INGEST = os.environ.get("WC_INGEST_URL", "http://18.166.71.60:8000").rstrip("/")
PW = os.environ.get("WC_INGEST_PW", "")
USER = os.environ.get("WC_INGEST_USER", "admin")


def arrow(old, new):
    """相对上一快照的水位方向: 升▲ / 降▼ / 平或缺-。"""
    if old is None or new is None:
        return "-"
    return "▲" if new > old else ("▼" if new < old else "-")


def _devig_from_had(had):
    """1X2 欧赔 → 去水% {h,d,a}; 缺/0 则 None。"""
    if not had or any(had.get(k) in (None, 0) for k in ("h", "d", "a")):
        return None
    imp = {k: 1.0 / had[k] for k in ("h", "d", "a")}
    s = sum(imp.values())
    return {k: round(imp[k] / s * 100, 1) for k in ("h", "d", "a")}


def _delta_had(prev, new_had):
    old = (prev or {}).get("had") or {}
    return {k: arrow(old.get(k), (new_had or {}).get(k)) for k in ("h", "d", "a")}


def _delta_devig(prev_key, prev, new_devig):
    old = (prev or {}).get(prev_key) or {}
    return {k: arrow(old.get(k), (new_devig or {}).get(k)) for k in ("h", "d", "a")}


def _norm_team(s):
    """归一化队名: 去括号/点/空格, 容忍 500 vs 竞彩 的写法差异。"""
    return re.sub(r"[()（）·\s]", "", s or "")


def _team_eq(a, b):
    """队名宽松相等: 归一后相等, 或一个是另一个子串(沙特 ⊂ 沙特阿拉伯)。"""
    a, b = _norm_team(a), _norm_team(b)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _split_label(label):
    parts = (label or "").split(" vs ")
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ((label or "").strip(), "")


def _find_by_teams(items, home, away):
    """在 items 里找两队都宽松相等的一项(主客同序); 无 → None。"""
    for it in items:
        ih, ia = _split_label(it.get("label"))
        if _team_eq(ih, home) and _team_eq(ia, away):
            return it
    return None


def build_panel(zucai_items, consensus_items, poly_items, prev_lookup):
    """三源 fetch items → 每场赔率面板 payload, 按队名对齐到 zucai_num
    (竞彩/500 中文队名有写法差异, 用 _team_eq 宽松对齐, 而非 exact label)。
    prev_lookup(source, match_key) → 上一快照 payload|None(算 delta)。"""
    panel = []
    for z in zucai_items:
        mk, label = z["match_key"], z["label"]
        zh, za = _split_label(label)
        zp = z.get("payload") or {}
        had = zp.get("had")
        z_devig = _devig_from_had(had)
        src = {"zucai": {"had": had, "hhad": zp.get("hhad"), "ttg": zp.get("ttg"),
                         "devig": z_devig, "delta": _delta_had(prev_lookup("zucai", mk), had),
                         "stale": had is None}}
        # consensus(按队名宽松对齐到本场)
        c = _find_by_teams(consensus_items, zh, za)
        if c:
            cp = c.get("payload") or {}
            c_devig = cp.get("devig_pct")
            src["consensus"] = {"euro": cp.get("had"), "devig": c_devig, "n_books": cp.get("n_books"),
                                "delta": _delta_devig("devig_pct", prev_lookup("consensus", c["match_key"]), c_devig),
                                "stale": c_devig is None}
        else:
            src["consensus"] = {"stale": True}
        # poly(按队名宽松对齐; 缓存里其实同 zucai_num)
        p = _find_by_teams(poly_items, zh, za)
        if p:
            pp = p.get("payload") or {}
            p_devig = pp.get("poly_devig")
            src["poly"] = {"devig": p_devig,
                           "delta": _delta_devig("poly_devig", prev_lookup("poly", mk), p_devig),
                           "stale": p_devig is None}
        else:
            src["poly"] = {"stale": True}
        # divergence: 竞彩去水 − 欧盘去水 (pp)
        cdv = src["consensus"].get("devig") if not src["consensus"].get("stale") else None
        if z_devig and cdv:
            div = {k: round(z_devig[k] - cdv[k], 1) for k in ("h", "d", "a")}
        else:
            div = {"h": None, "d": None, "a": None}
        panel.append({"match_key": mk, "label": label, "ko": z.get("ko"),
                      "fetched_at": ow.now_bj(), "sources": src, "divergence": div})
    return panel


def _post(path, body):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    hdr = {"Content-Type": "application/json"}
    if PW:
        hdr["Authorization"] = "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()
    req = urllib.request.Request(INGEST + path, data=data, headers=hdr, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001 — best-effort, 缓存已写, 下轮重推
        sys.stderr.write(f"  [warn] POST {path} -> {e!r}\n")
        return None


def _fetch_poly_local(cache_path):
    """本地代理跑 poly build-list→fetch→读 ingest items。失败返 []。"""
    pf.set_proxy(PROXY)
    try:
        ml = os.path.join(tempfile.gettempdir(), "wc_poly_matches.json")
        pi = os.path.join(tempfile.gettempdir(), "wc_poly_ingest.json")
        pf.build_list(cache_path, ml)
        pf.fetch(ml, pi, proxy=PROXY)
        return json.load(open(pi, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  [warn] poly 本地抓失败: {e!r}\n")
        return []


def run_once(dry_run=False):
    conn = ow.connect(ow.DEFAULT_CACHE)

    def prev_lookup(source, match_key):
        return ow.latest(conn, source, match_key)  # ingest 前的上一快照, 算 delta

    # 1) 抓三源
    try:
        raw_env = sporttery.fetch({})           # 竞彩 raw 信封(推 /api/ingest/zucai)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  [warn] sporttery raw 失败: {e!r}\n"); raw_env = None
    zucai_items = ow.fetch_zucai()
    try:
        consensus_items = ow.fetch_consensus()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"  [warn] consensus 失败: {e!r}\n"); consensus_items = []
    poly_items = _fetch_poly_local(ow.DEFAULT_CACHE)

    # 2) 组面板(用 ingest 前的 prev 算 delta)
    panel = build_panel(zucai_items, consensus_items, poly_items, prev_lookup)

    # 3) 写本地缓存(供本地分析 + 下轮 delta)
    for src, items in (("zucai", zucai_items), ("consensus", consensus_items), ("poly", poly_items)):
        for it in items:
            ow.save(conn, src, it["match_key"], it["label"], it.get("ko", ""), it["payload"])
    conn.commit()
    print(f"[{ow.now_bj()}] 三源: 竞彩{len(zucai_items)} 欧盘{len(consensus_items)} "
          f"Poly{len(poly_items)}; 面板{len(panel)}场")

    # 4) 推 HK
    if dry_run:
        print("  --dry-run: 不推 HK")
        return
    r1 = _post("/api/ingest/zucai", raw_env) if raw_env else None
    r2 = _post("/api/ingest/odds", {"items": panel})
    print(f"  推送: zucai={r1} odds={r2}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="本地一把刷三源 + 推 HK 看板")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, metavar="SEC", help="每 SEC 秒刷一次")
    ap.add_argument("--dry-run", action="store_true", help="抓+对齐但不推 HK")
    a = ap.parse_args()
    if a.loop:
        print(f"refresh_all 循环启动: 每 {a.loop}s, Ctrl-C 停")
        while True:
            run_once(a.dry_run)
            time.sleep(a.loop)
    else:
        run_once(a.dry_run)
