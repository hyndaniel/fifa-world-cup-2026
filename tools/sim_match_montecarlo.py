#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比赛模拟 · 市场锚定的二元泊松 + Dixon-Coles 低比分修正
=========================================================
从「去水后的市场 1X2(90分钟) + 大小球主盘」反解双方期望进球 λ,
建完整比分概率网格(解析解 = 跑无限次蒙卡的极限,无抽样噪声),
再附一层「加时(30')+点球」晋级模型,用来和 Polymarket 的晋级盘交叉验证。

口径(诚实声明):
  λ 由**市场赔率**反解,不是独立训练的 xG 引擎 → 这是「市场一致的模拟」,
  不是独立神谕。比分分布的形状(DC ρ 调低比分)是模型假设,非实测。
  ⚠️ Brier 红线: 本工具市场锚定、与 v2 同锚,非独立信号 —— 输出只供 wc-bet 决策内部用
     (比分网格/热力图/晋级%), 永不当 v1/v2 式预测、永不进 Brier 跑分卡。

纯 stdlib(math 即可),无 numpy 依赖 → 任何机器/worktree 都能跑。

用法:
  改下面 CONFIG 里的 match / 目标概率(从 wc-odds 的去水数字填),然后:
    python3 tools/sim_match_montecarlo.py
  会打印完整报告;加 --json <path> 另存比分网格 JSON。
  编排化(wc-bet 用): python3 tools/sim_match_montecarlo.py --input '<json>' --json <path>
    input JSON: {match,home,away,p_home,p_draw,p_away,ou_line,p_over,pen_home?}; pen_home 默认 0.5
  注:导出的网格用**字符串键** "i-j": prob(非元组键),供仓外 handicap/净胜球分析
  自行把键拆回 (i,j) 用;它**不是**项目 .cache/_grid.pkl 的元组键 pickle 同构体。
"""
import math
import sys
import json
import argparse

# ============================ CONFIG ============================
# 填 wc-odds 返回的**去水后(devig)**数字。这些是市场共识,不是我编的。
CONFIG = {
    "match":  "R32-76 Brazil vs Japan",
    "home":   "Brazil",
    "away":   "Japan",
    # —— 90 分钟赛果 去水后概率(三者和≈1) ——
    # 主锚 = sharp 共识(Poly 聪明钱 57.2/25.4/17.4 + 52家欧盘 53.9/26.2/19.9 的均值)。
    # 刻意剔除竞彩(59.4/23.8/16.8):wc-odds 实测这两天竞彩单边压巴西 +6.1pp,
    # 而欧盘共识钉死不动、Poly 没跟 → 竞彩是零售压热门的离群值,不进主锚。
    # 数据抓取:北京 2026-06-29 14:52(距开赛约10h)。
    "p_home": 0.555,  # 巴西胜(90')
    "p_draw": 0.258,  # 平(90')
    "p_away": 0.187,  # 日本胜(90')
    # —— 大小球主盘 —— sharp 侧 ~0.46(Poly exact 46.7 / web单簿 45.8;竞彩 ttg 53.4 又是高离群,不取)
    "ou_line":  2.5,
    "p_over":   0.46,
    # —— 点球大战:平局后强队胜点的条件概率(经验值,质量/经验略偏巴西) ——
    "pen_home": 0.55,
}
MAXGOALS = 10          # 0..10 球,P(>10) ≈ 0
ET_SCALE = 30.0 / 90.0 # 加时 30 分钟 ≈ 联赛进球率的 1/3
# ===============================================================

REQUIRED = ("p_home", "p_draw", "p_away", "ou_line", "p_over")


def resolve_config(input_json=None):
    """返回校验过的配置 dict。

    input_json 为 JSON 字符串时覆盖 CONFIG('-' 从 stdin 读);为 None 走模块 CONFIG。
    补 pen_home 默认 0.5;校验必填字段与 1X2 和≈1。失败 raise ValueError/JSONDecodeError。
    """
    if input_json is None:
        cfg = dict(CONFIG)
    else:
        if input_json == "-":
            input_json = sys.stdin.read()
        cfg = json.loads(input_json)
        if not isinstance(cfg, dict):
            raise ValueError("input 必须是 JSON 对象 {...}")
    cfg.setdefault("pen_home", 0.5)
    cfg.setdefault("home", "Home")
    cfg.setdefault("away", "Away")
    cfg.setdefault("match", f"{cfg['home']} vs {cfg['away']}")
    missing = [k for k in REQUIRED if cfg.get(k) is None]
    if missing:
        raise ValueError(f"缺必填字段: {', '.join(missing)}(去水概率请从 wc-odds 取)")
    s = cfg["p_home"] + cfg["p_draw"] + cfg["p_away"]
    if abs(s - 1.0) > 0.05:
        raise ValueError(f"p_home+p_draw+p_away={s:.3f} 偏离 1 太多(应去水后≈1)")
    return cfg


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def dc_tau(i, j, l1, l2, rho):
    """Dixon-Coles 低比分相关修正(只动 0-0/1-0/0-1/1-1)。"""
    if i == 0 and j == 0:
        return 1.0 - l1 * l2 * rho
    if i == 0 and j == 1:
        return 1.0 + l1 * rho
    if i == 1 and j == 0:
        return 1.0 + l2 * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def build_grid(l1, l2, rho, n=MAXGOALS):
    """返回 {(i,j): prob},已归一化。i=主队进球, j=客队进球。"""
    ph = [poisson_pmf(k, l1) for k in range(n + 1)]
    pa = [poisson_pmf(k, l2) for k in range(n + 1)]
    grid = {}
    s = 0.0
    for i in range(n + 1):
        for j in range(n + 1):
            p = ph[i] * pa[j] * dc_tau(i, j, l1, l2, rho)
            if p < 0:
                p = 0.0  # DC τ 在极端 ρ 下可能轻微为负,夹到 0
            grid[(i, j)] = p
            s += p
    for k in grid:
        grid[k] /= s
    return grid


def summarize(grid, line):
    """从比分网格算各类市场量。line=大小球盘口线;over 判定 i+j>line 仅对半线(x.5)
    正确,整数盘需另行单列走盘(push)——本工具约定 ou_line 用半线。"""
    p_home = p_draw = p_away = 0.0
    p_over = p_btts = 0.0
    eg_h = eg_a = 0.0
    for (i, j), p in grid.items():
        if i > j:
            p_home += p
        elif i == j:
            p_draw += p
        else:
            p_away += p
        if i + j > line:
            p_over += p
        if i >= 1 and j >= 1:
            p_btts += p
        eg_h += i * p
        eg_a += j * p
    return {
        "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
        "p_over": p_over, "p_under": 1 - p_over, "p_btts": p_btts,
        "eg_home": eg_h, "eg_away": eg_a, "eg_total": eg_h + eg_a,
    }


def loss(grid, tgt, line):
    """拟合损失:命中 p_home / p_draw / p_over(p_away 自动落地)。"""
    s = summarize(grid, line)
    l = (s["p_home"] - tgt["p_home"]) ** 2
    l += (s["p_draw"] - tgt["p_draw"]) ** 2
    l += (s["p_over"] - tgt["p_over"]) ** 2
    return l


def frange(a, b, step):
    out, x = [], a
    while x <= b + 1e-9:
        out.append(round(x, 4))
        x += step
    return out


def fit(tgt, line):
    """粗到细网格搜索 (λ_home, λ_away, ρ),命中市场目标。"""
    best = (1e9, 1.5, 1.0, 0.0)
    # 粗
    for l1 in frange(0.4, 4.0, 0.1):
        for l2 in frange(0.2, 3.0, 0.1):
            for rho in (-0.15, -0.1, -0.05, 0.0, 0.05, 0.1):
                lv = loss(build_grid(l1, l2, rho), tgt, line)
                if lv < best[0]:
                    best = (lv, l1, l2, rho)
    # 细(围绕粗解)
    _, b1, b2, br = best
    for l1 in frange(max(0.2, b1 - 0.12), b1 + 0.12, 0.02):
        for l2 in frange(max(0.1, b2 - 0.12), b2 + 0.12, 0.02):
            for rho in frange(br - 0.05, br + 0.05, 0.01):
                lv = loss(build_grid(l1, l2, rho), tgt, line)
                if lv < best[0]:
                    best = (lv, l1, l2, rho)
    return best  # (loss, λ_home, λ_away, ρ)


def advance_prob(l1, l2, pen_home):
    """90分钟平局后:加时(λ/3)→仍平则点球。返回主队晋级的条件概率。"""
    et = build_grid(l1 * ET_SCALE, l2 * ET_SCALE, 0.0)
    et_home = et_draw = et_away = 0.0
    for (i, j), p in et.items():
        if i > j:
            et_home += p
        elif i == j:
            et_draw += p
        else:
            et_away += p
    pen = pen_home
    # 给定 90' 平:主队晋级 = 加时赢 + 加时平*点球赢
    p_home_adv_given_draw = et_home + et_draw * pen
    return p_home_adv_given_draw, {
        "et_home": et_home, "et_draw": et_draw, "et_away": et_away,
    }


def main():
    ap = argparse.ArgumentParser(
        description="比赛模拟 · 市场锚定二元泊松+Dixon-Coles(改 CONFIG 切换场次)")
    ap.add_argument("--json", metavar="PATH",
                    help="另存比分网格 JSON(字符串键 'i-j': prob)")
    ap.add_argument("--input", metavar="JSON",
                    help="JSON 配置覆盖 CONFIG('-' 从 stdin 读);供 wc-bet 编排化调用")
    args = ap.parse_args()

    try:
        c = resolve_config(args.input)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"✗ 输入无效: {e}", file=sys.stderr)
        return 2
    line = c["ou_line"]
    tgt = {"p_home": c["p_home"], "p_draw": c["p_draw"], "p_over": c["p_over"]}
    lv, l1, l2, rho = fit(tgt, line)
    grid = build_grid(l1, l2, rho)
    s = summarize(grid, line)

    p_adv_given_draw, et = advance_prob(l1, l2, c["pen_home"])
    adv_home = s["p_home"] + s["p_draw"] * p_adv_given_draw
    adv_away = 1 - adv_home

    H, A = c["home"], c["away"]
    print("=" * 64)
    print(f"  比赛模拟 · {c['match']}")
    print("  市场锚定二元泊松 + Dixon-Coles(λ 由去水赔率反解,非训练引擎)")
    print("=" * 64)
    print(f"\n【拟合】 λ_{H}={l1:.3f}  λ_{A}={l2:.3f}  DC_ρ={rho:+.3f}  (拟合残差²={lv:.2e})")
    print(f"  期望进球: {H} {s['eg_home']:.2f}  -  {s['eg_away']:.2f} {A}   "
          f"总进球期望 {s['eg_total']:.2f}")

    print("\n【90分钟 赛果】(对照市场目标)")
    print(f"  {H}胜  {s['p_home']*100:5.1f}%   (市场 {tgt['p_home']*100:.1f}%)")
    print(f"  平    {s['p_draw']*100:5.1f}%   (市场 {tgt['p_draw']*100:.1f}%)")
    print(f"  {A}胜  {s['p_away']*100:5.1f}%   (市场 {c['p_away']*100:.1f}%)")
    print(f"\n【大小球 {c['ou_line']}】 大 {s['p_over']*100:.1f}%  小 {s['p_under']*100:.1f}%   "
          f"(市场大 {c['p_over']*100:.1f}%)")
    print(f"【双方进球 BTTS】 是 {s['p_btts']*100:.1f}%  否 {(1-s['p_btts'])*100:.1f}%")

    # 最可能比分 Top
    top = sorted(grid.items(), key=lambda kv: kv[1], reverse=True)[:10]
    print("\n【最可能比分 Top10】(主-客 = {}-{})".format(H, A))
    for (i, j), p in top:
        bar = "█" * int(round(p * 100))
        print(f"  {i}-{j}  {p*100:5.2f}%  {bar}")

    # 比分热力网格(0..5)
    print("\n【比分概率热力网格 %】行=主队({}) 列=客队({})".format(H, A))
    print("        " + "".join(f"{j:>7}" for j in range(6)))
    for i in range(6):
        row = "".join(f"{grid[(i,j)]*100:7.2f}" for j in range(6))
        print(f"   {i} |{row}")

    print("\n【晋级(含加时+点球)】")
    print(f"  给定90'平,加时: {H}赢 {et['et_home']*100:.0f}% / 平 {et['et_draw']*100:.0f}% / "
          f"{A}赢 {et['et_away']*100:.0f}%;点球偏 {H} {c['pen_home']*100:.0f}%")
    print(f"  → {H} 晋级 {adv_home*100:.1f}%   {A} 晋级 {adv_away*100:.1f}%")
    print("=" * 64)

    # 另存网格(字符串键 'i-j': prob,供仓外 handicap/净胜球分析)
    if args.json:
        out = {f"{i}-{j}": grid[(i, j)] for i in range(MAXGOALS + 1)
               for j in range(MAXGOALS + 1)}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"match": c["match"], "lambda_home": l1, "lambda_away": l2,
                       "dc_rho": rho, "grid": out,
                       "summary": s,
                       "advance": {"home": adv_home, "away": adv_away}}, f,
                      ensure_ascii=False, indent=2)
        print(f"网格已存 → {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
