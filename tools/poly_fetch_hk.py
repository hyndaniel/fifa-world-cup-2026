#!/usr/bin/env python3
"""poly_fetch_hk — 经 aws-hk 抓 Polymarket 聪明钱 → odds_watch 缓存(source=poly)。

本地(CN)直连 Polymarket 被墙, 子 agent 又有护栏不能自抓; 而 aws-hk 出口直连 gamma
可达。故 Poly 刷新固定走: 在 aws-hk 上抓 → 拿回 JSON → ingest 进同一 odds_watch 缓存。
aws-hk 是 Python 3.9 且 **没装 httpx、repo 也不在该机**, 所以 `fetch` 子命令是
**纯 stdlib(urllib)**、不 import backend; 默认 urllib UA 会被 gamma/Cloudflare 403,
必须带浏览器 UA。`build-list` 子命令在 **Mac** 跑、用 backend.teammap 配英文名(懒加载)。

三个子命令(分跑在两台机):
  # ① Mac: 从 odds_watch 缓存的 zucai 场次导出待抓清单(含英文名, 供 find_slug 配对)
  python3 tools/poly_fetch_hk.py build-list --out matches.json
  # ② aws-hk: 纯 stdlib 抓 Poly → ingest JSON(payload 形状与现有缓存一致)
  python3 poly_fetch_hk.py fetch matches.json poly_ingest.json
  # ③ Mac: 喂回 odds_watch 缓存
  python3 tools/poly_fetch_hk.py ingest poly_ingest.json     # = odds_watch --ingest --source poly

②③ 之间的文件搬运(Mac→aws-hk 上传脚本+清单, aws-hk→Mac 下载结果)由**主会话(Claude)
经 remote-agent MCP** 驱动 —— 脚本本身调不了 MCP。主会话整套流程:
  1. build-list(Mac) → matches.json
  2. remote_upload 本文件 + matches.json 到 aws-hk:/tmp
  3. remote_exec: python3 /tmp/poly_fetch_hk.py fetch /tmp/matches.json /tmp/poly_ingest.json
  4. remote_download poly_ingest.json 回 Mac
  5. ingest(Mac)
  6. 再派 odds-value-analyst 用新 Poly 重算(它从缓存读、不自抓)

注意: odds_watch 的 diff_payload 只 diff had/hhad/ttg, 对 poly payload 永远报"0 变化"
——数据照常存, 旧→新 delta 需自己读缓存算。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request

GAMMA = "https://gamma-api.polymarket.com"
WC_TAG = "102232"  # FIFA World Cup tag
# gamma 走 Cloudflare, 默认 Python-urllib UA 会 403; 用浏览器 UA。
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/16.0 Safari/605.1.15")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CACHE = os.environ.get("WC_ODDS_CACHE", os.path.join(REPO, ".cache", "odds_cache.db"))


# ============ ① build-list (Mac, 用 backend.teammap) ============
def build_list(cache_path: str, out_path: str):
    """从 odds_watch 缓存里 source='zucai' 的场次导出待抓清单。

    清单项: {match_key, label, ko, home_cn, away_cn, home_en, away_en}
    英文名经 backend.teammap.en() (find_slug 用它在 Poly title 里做子串配对)。
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from backend.teammap import en  # 懒加载: 仅 Mac 路径需要, aws-hk 无 repo

    conn = sqlite3.connect(cache_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT match_key, label, ko, MAX(ts) FROM odds_cache
           WHERE source='zucai' GROUP BY match_key ORDER BY match_key"""
    ).fetchall()
    conn.close()

    out, missing = [], []
    for r in rows:
        label = r["label"]
        if " vs " in label:
            h_cn, a_cn = [x.strip() for x in label.split(" vs ", 1)]
        else:
            h_cn, a_cn = label, ""
        hen, aen = en(h_cn), en(a_cn)
        if not hen or not aen:
            missing.append((r["match_key"], h_cn, a_cn, hen, aen))
        out.append({"match_key": r["match_key"], "label": label, "ko": r["ko"],
                    "home_cn": h_cn, "away_cn": a_cn, "home_en": hen, "away_en": aen})

    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"导出 {len(out)} 场 → {out_path}")
    if missing:
        print("⚠️ 英文名缺失(无法配对 Poly, 需补 teammap):")
        for k, h, a, hen, aen in missing:
            print(f"  {k} {h}/{a}  en={hen!r}/{aen!r}")
    else:
        print("英文名齐全。")
    return out


# ============ ② fetch (aws-hk, 纯 stdlib) ============
def _get(path: str):
    req = urllib.request.Request(GAMMA + path, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001 — 单次失败返空, 由调用方记 miss
        sys.stderr.write(f"  [warn] fetch {path[:64]} -> {e!r}\n")
        return []


def list_events() -> dict:
    """翻页拉世界杯 event, 返回 {slug: title}; 只留 fifwc- 前缀且非 more-markets。"""
    idx = {}
    for off in (0, 100, 200, 300):
        evs = _get(f"/events?closed=false&tag_id={WC_TAG}&limit=100&offset={off}"
                   "&order=startDate&ascending=true")
        for e in evs:
            s = e.get("slug", "") or ""
            if s.startswith("fifwc-") and "more-markets" not in s:
                idx[s] = e.get("title", "")
        if len(evs) < 100:
            break
    return idx


def find_slug(idx: dict, hen: str, aen: str):
    """title 同时含两队英文子串的第一个 event(顺序无关)。返回 (slug, title)。"""
    for s, t in idx.items():
        if hen in t and aen in t:
            return s, t
    return None, None


def parse_ml(base, hen: str, aen: str):
    """从 base event 的 "win on X"/"draw" 市场的 Yes 价解析 ml(home/draw/away) raw %。"""
    if not base:
        return None
    ml_home = ml_draw = ml_away = None
    for m in base[0].get("markets", []):
        q = m.get("question", "")
        try:
            o = json.loads(m.get("outcomes", "[]"))
            p = json.loads(m.get("outcomePrices", "[]"))
        except Exception:  # noqa: BLE001
            continue
        kv = dict(zip(o, p))
        if "win on" in q:
            if hen in q and "Yes" in kv:
                ml_home = float(kv["Yes"]) * 100
            elif aen in q and "Yes" in kv:
                ml_away = float(kv["Yes"]) * 100
        elif "draw" in q and "Yes" in kv:
            ml_draw = float(kv["Yes"]) * 100
    return {"h": ml_home, "d": ml_draw, "a": ml_away}


def devig(raw):
    vals = [raw.get("h"), raw.get("d"), raw.get("a")] if raw else [None, None, None]
    if any(v is None for v in vals):
        return None
    s = sum(vals)
    if s <= 0:
        return None
    return {"h": round(vals[0] / s * 100, 1), "d": round(vals[1] / s * 100, 1),
            "a": round(vals[2] / s * 100, 1)}


def fetch(list_path: str, out_path: str):
    matches = json.load(open(list_path, encoding="utf-8"))
    print("拉取 WC event 列表 ...")
    idx = list_events()
    print(f"  fifwc- event 共 {len(idx)} 个")
    out, hit, miss = [], [], []
    for m in matches:
        hen, aen = m.get("home_en"), m.get("away_en")
        if not hen or not aen:
            miss.append((m["match_key"], "无英文名")); continue
        slug, _title = find_slug(idx, hen, aen)
        if not slug:
            miss.append((m["match_key"], f"无 slug ({hen}/{aen})")); continue
        raw = parse_ml(_get(f"/events?slug={slug}"), hen, aen)
        if not raw or any(raw.get(k) is None for k in ("h", "d", "a")):
            miss.append((m["match_key"], f"ml 不全 slug={slug} raw={raw}")); continue
        raw_r = {k: round(v, 1) for k, v in raw.items()}
        out.append({"match_key": m["match_key"], "label": m["label"], "ko": m["ko"],
                    "payload": {"poly_ml_raw": raw_r, "poly_devig": devig(raw)}})
        hit.append(f"{m['match_key']} {m['label']}: raw {raw_r} devig {devig(raw)}  [{slug}]")
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n命中 {len(hit)} 场 → {out_path}")
    for h in hit:
        print("  ✓", h)
    if miss:
        print(f"\n未命中 {len(miss)} 场:")
        for k, why in miss:
            print("  ✗", k, why)
    return out


# ============ ③ ingest (Mac, 转调 odds_watch 单一真源) ============
def ingest(ingest_path: str, cache_path: str):
    """转调 tools/odds_watch.py --ingest --source poly(ingest 逻辑单一真源, 不重复实现)。"""
    import subprocess
    ow = os.path.join(REPO, "tools", "odds_watch.py")
    r = subprocess.run([sys.executable, ow, "--ingest", ingest_path, "--source", "poly",
                        "--cache", cache_path])
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description="经 aws-hk 抓 Polymarket → odds_watch 缓存")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_bl = sub.add_parser("build-list", help="[Mac] 从 zucai 缓存导出待抓清单(含英文名)")
    p_bl.add_argument("--cache", default=DEFAULT_CACHE)
    p_bl.add_argument("--out", default=os.path.join(REPO, ".cache", "poly_matches.json"))

    p_f = sub.add_parser("fetch", help="[aws-hk] 纯 stdlib 抓 Poly → ingest JSON")
    p_f.add_argument("list_path")
    p_f.add_argument("out_path")

    p_i = sub.add_parser("ingest", help="[Mac] 喂回 odds_watch 缓存(source=poly)")
    p_i.add_argument("ingest_path")
    p_i.add_argument("--cache", default=DEFAULT_CACHE)

    a = ap.parse_args()
    if a.cmd == "build-list":
        build_list(a.cache, a.out)
    elif a.cmd == "fetch":
        fetch(a.list_path, a.out_path)
    elif a.cmd == "ingest":
        sys.exit(ingest(a.ingest_path, a.cache))


if __name__ == "__main__":
    main()
