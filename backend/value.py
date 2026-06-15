"""价值引擎: 足彩欧赔 × Poly真实概率。

吸收 prototype/wc_value.py 的映射逻辑:
- had(胜平负) → Poly 独赢 主/平/客
- hhad(让球)  → Poly 让分(spread) cover 派生
- ttg(总进球) → O/U 相邻线相减

新增 de-vig (spec §5.1): 只对 Poly 概率做乘法归一化, 不动足彩。
每个 ValuePoint 同时给 value_raw(生概率) 与 value_devig(去水概率), ev_pct 用 value_devig。
flag: value_devig>=1.03→green; 0.97<=value_devig<1.03→yellow;
      高分桶(6/7+)缺 O/U 高线→skip (不可靠, 不计 EV)。
"""
from .models import ZucaiMatch, PolyProbs, ValuePoint
from .devig import devig

GREEN_AT = 1.03   # value_devig>=此 → green
YELLOW_AT = 0.97  # value_devig>=此 → yellow (低于则非投点, 仍标 yellow 下界)


# ---------- 取 cover (raw / devig) ----------
def _cover_map(p: PolyProbs, side: str):
    """返回该侧 cover 字典 (含 0.5=胜%, 来自 ml)。side ∈ {'home','away'}。"""
    base = dict(p.home_cover if side == "home" else p.away_cover)
    win = p.ml.get(side)
    if win is not None and 0.5 not in base:
        base[0.5] = win
    return base


def _devig_cover_map(p: PolyProbs, side: str):
    """对某侧 cover 阶梯去水: 由 cover 阶梯 + 平 + 对侧胜 构出主队净胜分布, 归一化后再积回 cover。

    cover[line] = P(该队净胜 > line) = P(净胜 >= ceil(line))。相邻 cover 相减得恰好净胜 k 的概率,
    再加上 平(draw) 与 对侧胜(让该侧输的全部), 构成互斥全集 → 乘法归一化(去水) → 重建 cover。
    """
    raw = _cover_map(p, side)
    other = "away" if side == "home" else "home"
    lines = sorted(raw.keys())  # e.g. [0.5,1.5,2.5,...]
    draw = p.ml.get("draw", 0.0) or 0.0
    other_win = p.ml.get(other, 0.0) or 0.0

    # 恰好净胜 k 的桶: k 对应区间 (lines[i] .. lines[i+1])
    buckets = {}  # margin_floor(int) -> prob
    for i, ln in enumerate(lines):
        hi = raw[lines[i + 1]] if i + 1 < len(lines) else 0.0
        buckets[int(ln + 0.5)] = raw[ln] - hi  # 恰好净胜 = cover[ln]-cover[next]

    full = dict(buckets)
    full["draw"] = draw
    full["other"] = other_win
    dv = devig(full)  # 乘法归一化所有互斥结果

    # 重建去水 cover: cover[line] = Σ 去水后 净胜 > line 的桶
    out = {}
    for ln in lines:
        s = 0.0
        for k, prob in dv.items():
            if isinstance(k, int) and k > ln:
                s += prob
        out[ln] = s
    return out


def _ou_devig(p: PolyProbs):
    """对 O/U 阶梯去水: over[line] 与 1-over[line] 是同一互斥对; 相邻 over 相减得恰好 N 球桶,
    加上 over[max] 尾巴 与 under[min] 头巴 构互斥全集, 归一化后重建 over。"""
    lines = sorted(p.ou_over.keys())
    if not lines:
        return {}
    buckets = {}  # exact goals n -> prob
    lo = lines[0]
    buckets[("under", lo)] = 100.0 - p.ou_over[lo]  # 进球 < lo
    for i, ln in enumerate(lines):
        hi = p.ou_over[lines[i + 1]] if i + 1 < len(lines) else 0.0
        buckets[int(ln + 0.5)] = p.ou_over[ln] - hi  # 恰好 N 球 (N=ln+0.5)
    dv = devig(buckets)
    out = {}
    for ln in lines:
        s = 0.0
        for k, prob in dv.items():
            if isinstance(k, int) and k > ln:
                s += prob
        out[ln] = s
    return out


# ---------- had 映射 ----------
def _had_raw(p: PolyProbs, which):
    if which == "h":
        return p.ml.get("home")
    if which == "d":
        return p.ml.get("draw")
    if which == "a":
        return p.ml.get("away")


def _had_devig(p: PolyProbs, which):
    raw = {"h": p.ml.get("home"), "d": p.ml.get("draw"), "a": p.ml.get("away")}
    clean = {k: v for k, v in raw.items() if v is not None}
    if not clean:
        return None
    dv = devig(clean)
    return dv.get(which)


# ---------- hhad 映射 (cover 字典版) ----------
def _hcov(cov, team_side, line, home_cov, away_cov):
    m = home_cov if team_side == "home" else away_cov
    return m.get(line)


def _map_hhad(home_cov, away_cov, line, which):
    """line<0: 主队让球; line>0: 主队受让。返回概率% (None=缺线)。"""
    if line < 0:
        k = abs(line)
        if which == "h":
            return home_cov.get(k + 0.5)                       # 主净胜 >= k+1
        if which == "d":
            lo = home_cov.get(k - 0.5)
            hi = home_cov.get(k + 0.5)                          # 主净胜恰好 k
            return None if lo is None or hi is None else lo - hi
        if which == "a":
            lo = home_cov.get(k - 0.5)
            return None if lo is None else 100 - lo
    else:
        k = line
        if which == "h":
            ac = away_cov.get(k - 0.5)                          # 主不输k+ = 100-客净胜>=k
            return None if ac is None else 100 - ac
        if which == "d":
            lo = away_cov.get(k - 0.5)
            hi = away_cov.get(k + 0.5)                          # 主恰输k
            return None if lo is None or hi is None else lo - hi
        if which == "a":
            return away_cov.get(k + 0.5)                        # 主输 >= k+1
    return None


# ---------- ttg 映射 ----------
def _map_ttg(ou, n):
    lo = 100.0 if n == 0 else ou.get(n - 0.5)
    hi = ou.get(n + 0.5)
    if n >= 6 and hi is None:   # 高分桶缺线 → 不可靠
        return None
    if lo is None or hi is None:
        return None
    return lo - hi


# ---------- flag ----------
def _flag(value_devig, yellow_below):
    if value_devig >= yellow_below:
        return "green"
    if value_devig >= YELLOW_AT:
        return "yellow"
    return "yellow"  # 低于 0.97 仍展示为 yellow(下界), skip 由调用处单独标


def compute_value(z: ZucaiMatch, p: PolyProbs, yellow_below=1.03) -> list[ValuePoint]:
    """对一场比赛, 输出所有可投结果的 ValuePoint 列表。

    yellow_below: value_devig>=此 → green; 否则 yellow; 高分桶缺线 → skip。
    """
    pts: list[ValuePoint] = []

    home_cov_raw = _cover_map(p, "home")
    away_cov_raw = _cover_map(p, "away")
    home_cov_dv = _devig_cover_map(p, "home")
    away_cov_dv = _devig_cover_map(p, "away")
    ou_dv = _ou_devig(p)

    def add(market, outcome, odds, raw, dv, skip=False):
        if not odds or raw is None:
            return
        odds = float(odds)
        value_raw = round(odds * raw / 100.0, 3)
        if dv is None:
            dv = raw
        value_devig = round(odds * dv / 100.0, 3)
        ev_pct = round((value_devig - 1) * 100, 1)
        if skip:
            flag = "skip"
        elif value_devig >= yellow_below:
            flag = "green"
        else:
            flag = "yellow"
        pts.append(ValuePoint(
            market=market, outcome=outcome, zucai_odds=odds,
            poly_prob_raw=round(raw, 1), poly_prob_devig=round(dv, 1),
            value_raw=value_raw, value_devig=value_devig,
            ev_pct=ev_pct, flag=flag,
        ))

    # --- had 胜平负 ---
    had = z.had or {}
    if had.get("h"):
        add("胜平负", "主胜", had["h"], _had_raw(p, "h"), _had_devig(p, "h"))
        add("胜平负", "平",   had["d"], _had_raw(p, "d"), _had_devig(p, "d"))
        add("胜平负", "客胜", had["a"], _had_raw(p, "a"), _had_devig(p, "a"))

    # --- hhad 让球 ---
    hh = z.hhad or {}
    if hh.get("h"):
        L = int(hh["line"])
        tag = f"让{L:+d}"
        win_lbl = "主" + ("让" if L < 0 else "受") + "胜"
        for which, lbl in (("h", win_lbl), ("d", "平"), ("a", "客胜")):
            raw = _map_hhad(home_cov_raw, away_cov_raw, L, which)
            dv = _map_hhad(home_cov_dv, away_cov_dv, L, which)
            add(tag, lbl, hh.get({"h": "h", "d": "d", "a": "a"}[which]), raw, dv)

    # --- ttg 总进球 ---
    ttg = z.ttg or {}
    for n in range(0, 8):
        odds = ttg.get(n)
        if not odds:
            continue
        lbl = f"{n}球" if n < 7 else "7+球"
        raw = _map_ttg(p.ou_over, n)
        dv = _map_ttg(ou_dv, n)
        if raw is None:
            # 高分桶缺线 → skip (展示但不计 EV)
            if n >= 6:
                pts.append(ValuePoint(
                    market="总进球", outcome=lbl, zucai_odds=float(odds),
                    poly_prob_raw=0.0, poly_prob_devig=0.0,
                    value_raw=0.0, value_devig=0.0, ev_pct=0.0, flag="skip",
                ))
            continue
        add("总进球", lbl, odds, raw, dv)

    return pts
