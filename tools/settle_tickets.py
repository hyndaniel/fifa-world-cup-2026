# -*- coding: utf-8 -*-
"""WC2026 结算引擎(数据驱动)。

设计: `结算` skill 里 LLM 把每张待结票的 picks 文本翻成结构化 legs, 本引擎只做**确定性算术**——
命中判定 / 双选 / 复式·单场固定 payout / 注数·odds_max 双校验 / 幂等写回 ledger。
让球沿用**竞彩整数让球胜平负三路模型**: net 净差 >0→'W' / =0→'D'(走盘归平, 非退款) / <0→'L'。

赛果按 (home_goals, away_goals) 注入; 场次以三位数场次号(如 "086")为键, 对齐 ledger picks。
"""
from __future__ import annotations

import math
import re
import sqlite3
from itertools import combinations
from math import prod


# ============ 命中原子(赛果注入, 无全局比分库)============
def outcome(hs: int, as_: int) -> str:
    """主队视角胜平负码: 'h' 主胜 / 'd' 平 / 'a' 客胜。"""
    return "h" if hs > as_ else ("a" if hs < as_ else "d")


def had_hit(hs: int, as_: int, sel: str) -> bool:
    """胜平负: sel ∈ {'h','d','a'}。"""
    return outcome(hs, as_) == sel


def hcap_hit(hs: int, as_: int, line: int, sel: str) -> bool:
    """竞彩整数让球胜平负: 主队让 N 记 line=-N、受让 N 记 line=+N。
    net=(hs+line)-as_ → >0 'W' / =0 'D'(走盘归平) / <0 'L'; 对 sel ∈ {'W','D','L'}。"""
    net = (hs + line) - as_
    o = "W" if net > 0 else ("L" if net < 0 else "D")
    return o == sel


def exact_hit(hs: int, as_: int, phs: int, pas: int) -> bool:
    """比分(有序主客)。"""
    return hs == phs and as_ == pas


def goals_hit(hs: int, as_: int, n: int) -> bool:
    """总进球数。"""
    return (hs + as_) == n


_HTFT_FACE = {"胜": "h", "平": "d", "负": "a"}


def htft_hit(ht_hs: int, ht_as: int, ft_hs: int, ft_as: int, sel: str) -> bool:
    """半全场胜平负: sel 两字, 首=半场结果、次=全场结果, 各 ∈ {胜,平,负}(主队视角)。"""
    ht = _HTFT_FACE.get(sel[0]); ft = _HTFT_FACE.get(sel[1])
    return outcome(ht_hs, ht_as) == ht and outcome(ft_hs, ft_as) == ft


# ============ 单腿双选 ============
def _pick_hit(pick: dict, hs: int, as_: int, ht=None) -> bool:
    k = pick["kind"]
    if k == "had":
        return had_hit(hs, as_, pick["sel"])
    if k == "hcap":
        return hcap_hit(hs, as_, pick["line"], pick["sel"])
    if k == "exact":
        return exact_hit(hs, as_, pick["hs"], pick["as_"])
    if k == "goals":
        return goals_hit(hs, as_, pick["n"])
    if k == "htft":
        if ht is None:
            raise ValueError("htft 命中判定需半场比分 ht=(ht_hs, ht_as)")
        return htft_hit(ht[0], ht[1], hs, as_, pick["sel"])
    raise ValueError(f"未知 pick.kind: {k!r}")


def eval_leg(picks: list[dict], hs: int, as_: int, ht=None):
    """一条腿(可含多个双选 pick): 返回 (是否命中, 命中赔率)。
    命中 = 任一 pick 真; 多 pick 都真时取第一条(单场只有一个结果, 防御性短路)。
    ht=(ht_hs, ht_as) 半场比分, 仅 htft pick 需要。"""
    for p in picks:
        if _pick_hit(p, hs, as_, ht):
            return True, p["odds"]
    return False, None


# ============ payout(unit = 2 × 倍数)============
def combo_payout(leg_results, guan_levels, unit) -> float:
    """复式/串关: Σ_关数k Σ_C(M,k)组合 [组合内每腿都命中]·unit·∏命中赔率。
    leg_results: list of (hit, odds)。2串1→guan=[2], N场2-K关复式→guan=[2..K]。"""
    M = len(leg_results)
    total = 0.0
    for k in guan_levels:
        if k > M:
            continue
        for combo in combinations(range(M), k):
            if all(leg_results[i][0] for i in combo):
                total += unit * prod(leg_results[i][1] for i in combo)
    return total


def single_fixed_payout(leg_results, unit) -> float:
    """单场固定(每场独立结算): 每腿命中各自派彩再求和。"""
    return sum(unit * odds for hit, odds in leg_results if hit)


# ============ 校验器(注数 / 票面最高奖金反算)============
def count_notes(legs, guan_levels, mode) -> int:
    """从结构反算单倍注数。combo: Σ_k Σ_组合 ∏腿内选项数; single_fixed: Σ腿 选项数。
    legs: list of picks-lists(每腿是 pick 列表, 双选=多个 pick)。"""
    if mode == "single_fixed":
        return sum(len(leg) for leg in legs)
    M = len(legs)
    total = 0
    for k in guan_levels:
        if k > M:
            continue
        for combo in combinations(range(M), k):
            total += prod(len(legs[i]) for i in combo)
    return total


def max_payout(legs, guan_levels, mode, unit) -> float:
    """票面理论最高奖金: 每腿取其选项最高赔率(各场独立可同时命中最高档), 全组合派彩。
    combo: Σ_k Σ_组合 unit·∏腿最高赔; single_fixed: Σ腿 unit·腿最高赔。"""
    def leg_max(leg):
        return max(p["odds"] for p in leg)
    if mode == "single_fixed":
        return sum(unit * leg_max(leg) for leg in legs)
    M = len(legs)
    total = 0.0
    for k in guan_levels:
        if k > M:
            continue
        for combo in combinations(range(M), k):
            total += unit * prod(leg_max(legs[i]) for i in combo)
    return total


# ============ settle_ticket: 单张票编排 ============
# odds_max 反算与票面对账的容差: 终端中间乘积会取整, 大票会累积零点几的偏差 → 用相对容差兜。
_OMAX_REL_TOL = 2e-3
_OMAX_ABS_TOL = 0.6


def settle_ticket(ticket: dict, results: dict, ht_results: dict | None = None) -> dict:
    """给一张结构化票 + 赛果字典(场次号 → (home_goals, away_goals), 只含完赛场次),
    返回 {status, legs_hit, pnl, payout, reason}。

    半全场(htft)腿另需 ht_results(场次号 → 半场 (ht_hs, ht_as)); 缺半场则该票待人工。
    status: '已结' / '待结'(有腿未开赛) / '待人工'(半场缺失 / 注数或 odds_max 校验不过)。
    只有 '已结' 才给 pnl(其余 None)。不写库(写回见 write_back)。
    """
    ht_results = ht_results or {}
    legs = ticket["legs"]
    mode = ticket.get("mode", "combo")
    guan = ticket.get("guan_levels", [])
    unit = 2 * ticket["mult"]
    stake = ticket["stake"]
    leg_picks = [leg["picks"] for leg in legs]

    def _fail(reason):
        return {"status": "待人工", "legs_hit": "待人工", "pnl": None,
                "payout": None, "reason": reason}

    # 1) 注数校验
    n = count_notes(leg_picks, guan, mode)
    if n != ticket["expect_notes"]:
        return _fail(f"注数校验不过: 结构反算 {n} 注 ≠ 票面 {ticket['expect_notes']} 注")

    # 2) odds_max 校验(票面 odds_max 缺失/None 时跳过此校验, 仍靠注数校验兜底)
    omax = ticket.get("odds_max")
    if omax is not None:
        mp = max_payout(leg_picks, guan, mode, unit)
        if not math.isclose(mp, omax, rel_tol=_OMAX_REL_TOL, abs_tol=_OMAX_ABS_TOL):
            return _fail(f"odds_max 校验不过: 结构反算最高派彩 {mp:.2f} ≠ 票面 {omax:.2f}")

    # 3) 部分开赛: 任一腿的场次不在 results → 保持待结, 只回填进度串
    unfinished = [leg["match_key"] for leg in legs if leg["match_key"] not in results]
    if unfinished:
        toks = []
        for leg in legs:
            mk = leg["match_key"]
            if mk in results:
                hit, _ = eval_leg(leg["picks"], *results[mk], ht=ht_results.get(mk))
                toks.append(f"{mk}{'✅' if hit else '❌'}")
            else:
                toks.append(f"{mk}待结")
        return {"status": "待结", "legs_hit": " ".join(toks) + " (部分待结)",
                "pnl": None, "payout": None, "reason": ""}

    # 4) 半全场腿的半场比分是否到位(FT 已全开但半场缺 → 待人工, 不猜)
    ht_missing = [leg["match_key"] for leg in legs
                  if any(p["kind"] == "htft" for p in leg["picks"])
                  and leg["match_key"] not in ht_results]
    if ht_missing:
        return _fail(f"半场比分未获取: {','.join(ht_missing)}(半全场需上半场比分)")

    # 5) 全部完赛 → 算账
    leg_results = [eval_leg(leg["picks"], *results[leg["match_key"]],
                            ht=ht_results.get(leg["match_key"])) for leg in legs]
    if mode == "single_fixed":
        payout = single_fixed_payout(leg_results, unit)
    else:
        payout = combo_payout(leg_results, guan, unit)
    pnl = payout - stake
    M = len(legs)
    toks = [f"{leg['match_key']}{'✅' if leg_results[i][0] else '❌'}" for i, leg in enumerate(legs)]
    hit_n = sum(1 for h, _ in leg_results if h)
    return {"status": "已结", "legs_hit": f"命中{hit_n}/{M} " + " ".join(toks),
            "pnl": round(pnl, 2), "payout": round(payout, 2), "reason": ""}


# ============ 赛果加载(odds_cache.db → {场次号: (hs,as_)})============
def load_results(cache_db_path: str) -> dict:
    """读 `.cache/odds_cache.db` 的 match_results, 按 match_key 尾三位数字建键。
    表/库缺失一律返回 {}(不抛), 便于赛前空跑。"""
    out: dict = {}
    try:
        c = sqlite3.connect(f"file:{cache_db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return out
    try:
        rows = c.execute("SELECT match_key, home_goals, away_goals FROM match_results").fetchall()
    except sqlite3.OperationalError:
        return out
    finally:
        c.close()
    for mk, hg, ag in rows:
        m = re.search(r"(\d+)$", mk or "")
        if m and hg is not None and ag is not None:
            out[m.group(1)] = (int(hg), int(ag))
    return out


# ============ 半场比分加载(竞彩 sectionsNo1, 半全场结算用)============
def load_ht_results(cfg: dict | None = None) -> dict:
    """从竞彩开奖接口(与 FT 同一接口)取**上半场**比分 → {场次号: (ht_hs, ht_as)}。
    仅含完赛且有半场数据的场次。抓取失败/无 backend → {}(半全场票会因此判待人工, 不猜)。"""
    import pathlib
    import sys
    repo = pathlib.Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from backend.results import fetch_results
        rows = fetch_results(cfg)
    except Exception:
        return {}
    out: dict = {}
    for r in rows:
        if r.finished and r.ht_home is not None:
            m = re.search(r"(\d+)$", r.zucai_num or "")
            if m:
                out[m.group(1)] = (r.ht_home, r.ht_away)
    return out


# ============ 幂等写回 ledger ============
def write_back(ledger: dict, results_by_code: dict) -> dict:
    """把 {唯一码: SettleResult} 写回 ledger['tickets](按唯一码匹配)。幂等: 已结票跳过不覆盖。
    已结→写 pnl/settled/legs_hit; 待结→只更新进度串保持待结; 待人工→记 reason 保持待结。"""
    counts = {"settled": 0, "pending": 0, "manual": 0, "skipped": 0}
    for t in ledger.get("tickets", []):
        code = t.get("唯一码")
        if code not in results_by_code:
            continue
        if t.get("settled"):
            counts["skipped"] += 1
            continue
        r = results_by_code[code]
        st = r["status"]
        if st == "已结":
            t["pnl"] = r["pnl"]
            t["legs_hit"] = r["legs_hit"]
            t["settled"] = True
            counts["settled"] += 1
        elif st == "待结":
            t["legs_hit"] = r["legs_hit"]
            t["settled"] = False
            counts["pending"] += 1
        else:  # 待人工
            t["legs_hit"] = f"待人工: {r.get('reason', '')}"
            t["settled"] = False
            counts["manual"] += 1
    return counts


# ============ recommendations 层: 单腿胜平负 → 'h'/'d'/'a' 或 None(需人工)============
def rec_sel(leg_text: str, home_cn: str, away_cn: str):
    """把 recommendation 的 leg 文字判成主队视角胜平负码。
    让球/含 '-'/'让' 等结构一律返回 None(需走结构化人工路径, 不在此瞎猜)。"""
    if "-" in leg_text or "让" in leg_text:
        return None
    if "主胜" in leg_text:
        return "h"
    if "客胜" in leg_text:
        return "a"
    if leg_text.strip() == "平" or leg_text.startswith("平"):
        return "d"
    if home_cn and home_cn in leg_text and "胜" in leg_text:
        return "h"
    if away_cn and away_cn in leg_text and "胜" in leg_text:
        return "a"
    return None


def settle_recommendations(ledger: dict, results: dict, name_to_num: dict) -> dict:
    """结 recommendations 里 settled:False 的单腿(纯胜平负)。
    name_to_num: {(home_cn, away_cn): 场次号}(CLI 由 wc.db 构建)。
    对齐不到场次号 / 未完赛 / 让球等 rec_sel 返回 None 的一律跳过留待结。幂等。"""
    counts = {"settled": 0, "skipped": 0}
    for r in ledger.get("recommendations", []):
        if r.get("settled"):
            continue
        home, _, away = (r.get("match", "")).partition("vs")
        home, away = home.strip(), away.strip()
        num = name_to_num.get((home, away))
        if not num or num not in results:
            counts["skipped"] += 1
            continue
        sel = rec_sel(r.get("leg", ""), home, away)
        if sel is None:
            counts["skipped"] += 1
            continue
        hs, as_ = results[num]
        r["result"] = "win" if had_hit(hs, as_, sel) else "loss"
        r["settled"] = True
        counts["settled"] += 1
    return counts


# ============ CLI 编排 ============
def _build_name_to_num(wcdb_path: str) -> dict:
    """从 wc.db matches 建 {(home_cn, away_cn): 场次号尾三位}。库缺失返回 {}。"""
    out: dict = {}
    try:
        c = sqlite3.connect(f"file:{wcdb_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return out
    try:
        rows = c.execute("SELECT zucai_num, home_cn, away_cn FROM matches").fetchall()
    except sqlite3.OperationalError:
        return out
    finally:
        c.close()
    for zn, h, a in rows:
        m = re.search(r"(\d+)$", zn or "")
        if m:
            out[(h, a)] = m.group(1)
    return out


def _settle_struct_batch(ledger: dict, struct_batch: list, results: dict,
                         ht_results: dict | None = None) -> dict:
    """对结构化 batch 逐张 settle_ticket, 幂等写回。返回 write_back 计数 + 明细。"""
    by_code = {}
    detail = []
    for t in struct_batch:
        r = settle_ticket(t, results, ht_results)
        by_code[t["唯一码"]] = r
        detail.append((t.get("who", "?"), t.get("type", t["唯一码"]), r))
    counts = write_back(ledger, by_code)
    counts["_detail"] = detail
    return counts


def main(argv=None) -> int:
    import argparse
    import json
    import pathlib

    ap = argparse.ArgumentParser(description="WC2026 结算引擎: 结 recommendations 单腿 + 结构化 tickets")
    ap.add_argument("--ledger", required=True, help="data/bet_ledger.json 路径")
    ap.add_argument("--struct", help="结构化待结 tickets 的 batch.json(数组); 不给则只结 recommendations")
    ap.add_argument("--cache", default=".cache/odds_cache.db", help="odds_cache.db(match_results 源)")
    ap.add_argument("--wcdb", default="data/wc.db", help="wc.db(队名→场次号)")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写回")
    a = ap.parse_args(argv)

    ledger = json.loads(pathlib.Path(a.ledger).read_text(encoding="utf-8"))
    results = load_results(a.cache)
    name_to_num = _build_name_to_num(a.wcdb)

    print(f"赛果已加载 {len(results)} 场; 队名映射 {len(name_to_num)} 条")
    rec_counts = settle_recommendations(ledger, results, name_to_num)
    print(f"recommendations 层: 结 {rec_counts['settled']} 条, 跳过 {rec_counts['skipped']} 条")

    if a.struct:
        batch = json.loads(pathlib.Path(a.struct).read_text(encoding="utf-8"))
        # 半全场票需上半场比分 → 有则从竞彩接口(sectionsNo1)按需取一次
        need_ht = any(p.get("kind") == "htft"
                      for t in batch for leg in t.get("legs", []) for p in leg.get("picks", []))
        ht_results = {}
        if need_ht:
            ht_results = load_ht_results()
            print(f"半全场票检测到 → 已取半场比分 {len(ht_results)} 场")
        tc = _settle_struct_batch(ledger, batch, results, ht_results)
        print(f"tickets 层: 已结 {tc['settled']} / 待结 {tc['pending']} / "
              f"待人工 {tc['manual']} / 跳过(已结) {tc['skipped']}")
        for who, typ, r in tc["_detail"]:
            tag = {"已结": "✅", "待结": "⏳", "待人工": "⚠️"}.get(r["status"], "?")
            extra = f" pnl={r['pnl']:+.2f}" if r["status"] == "已结" else (
                f" {r['reason']}" if r["reason"] else "")
            print(f"  {tag} [{who}] {typ}: {r['legs_hit']}{extra}")

    # 已结 tickets 按人盈亏小结
    by_person: dict = {}
    for t in ledger.get("tickets", []):
        if t.get("settled") and isinstance(t.get("pnl"), (int, float)):
            by_person[t["who"]] = by_person.get(t["who"], 0.0) + t["pnl"]
    if by_person:
        print("已结 tickets 累计盈亏(全量):")
        for k in sorted(by_person, key=lambda x: -by_person[x]):
            print(f"  {k:6s} {by_person[k]:+10.2f}")
        print(f"  合计   {sum(by_person.values()):+10.2f}")

    if not a.dry_run:
        pathlib.Path(a.ledger).write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写回 {a.ledger}")
    else:
        print("(dry-run: 未写回)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
