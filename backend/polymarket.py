"""Polymarket (Gamma API) client + 对阵配对。

逻辑 1:1 移植自 prototype/wc_value.py 的 poly_index / find_slug / poly_probs,
网络层抽成可注入的 fetcher(path)->list 以便测试。

契约 (backend/models.py):
    PolyProbs(slug, home_en, away_en, ml, home_cover, away_cover, ou_over)
    - ml:        {"home":%, "draw":%, "away":%}                (raw %, 含抽水)
    - home_cover/away_cover: {line(float): cover%}  例 {1.5:78.5, 2.5:55.5}
    - ou_over:   {goalline(float): over%}           例 {0.5:98.4, 2.5:73.5}
"""
import json
import logging
import re

import httpx

from .models import PolyProbs

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
WC_TAG = "102232"  # FIFA World Cup tag
# gamma 走 Cloudflare, 默认 Python-urllib UA 会 403; 用浏览器 UA + httpx(自带 certifi)。
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"


def _default_fetcher(path: str) -> list:
    """默认网络实现: 直连 Gamma API, 返回解析后的 JSON(失败返回 [])。

    生产环境(HK VPS 直连)无需代理。需走代理时由调用方注入自定义 fetcher。
    """
    url = GAMMA + path
    try:
        r = httpx.get(url, headers={"Accept": "application/json", "User-Agent": _UA},
                      timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as e:  # noqa: BLE001 — 降级返回 [], 但必须留痕:
        # 否则 403/超时/DNS 故障在下游全变成 "poly 无对应 slug" 假象, 排障无从下手
        log.warning("poly fetch 失败 %s: %s: %s", url, type(e).__name__, e)
        return []


# ---------- 1. 列出全部世界杯 event, 按 slug->title 建索引 ----------
def list_events(fetcher=_default_fetcher) -> dict:
    """翻页拉取世界杯 event 列表, 返回 {slug: title}。

    翻页 offset 0/100/200(limit=100); 只留 slug 以 fifwc- 前缀且非 more-markets 的。
    """
    idx = {}  # slug -> title
    for off in (0, 100, 200):
        evs = fetcher(
            f"/events?closed=false&tag_id={WC_TAG}&limit=100&offset={off}"
            "&order=startDate&ascending=true"
        )
        for e in evs:
            s = e.get("slug", "") or ""
            if s.startswith("fifwc-") and "more-markets" not in s:
                idx[s] = e.get("title", "")
        if len(evs) < 100:
            break
    return idx


def find_slug(idx: dict, hen: str, aen: str):
    """在 {slug: title} 索引里找 title 同时含两队英文子串的第一个 event(顺序无关)。

    返回 (slug, title) 或 (None, None)。
    """
    for s, t in idx.items():
        if hen in t and aen in t:
            return s, t
    return None, None


# ---------- 2. 拉某场 base / more-markets 原始 event ----------
def fetch_event(slug: str, fetcher=_default_fetcher):
    """拉单场 base event 与 its more-markets, 返回 (base, more) 两个原始列表。"""
    base = fetcher(f"/events?slug={slug}")
    more = fetcher(f"/events?slug={slug}-more-markets")
    return base, more


# ---------- 3. 解析 Polymarket 概率(%) -> PolyProbs ----------
def parse_probs(base, more, hen: str, aen: str) -> PolyProbs:
    """1:1 移植 prototype poly_probs。

    base : "Will X win on ..."/"... draw" -> ml(home/draw/away)
    more : "Spread: TEAM (-X.5)" -> home_cover/away_cover(以 float 线为键),
           "...: O/U X.5"        -> ou_over(以 float 线为键)
    便捷: cover[team][0.5] = 该队净胜≥1 = 该队胜% (从 ml 派生)。
    """
    ml_home = ml_draw = ml_away = None
    cover = {hen: {}, aen: {}}  # team_substr -> {line: cover%}
    ov = {}                     # goalline -> over%

    def whichteam(name):
        if hen in name:
            return hen
        if aen in name:
            return aen
        return None

    if base:
        for m in base[0].get("markets", []):
            q = m.get("question", "")
            o = json.loads(m.get("outcomes", "[]"))
            p = json.loads(m.get("outcomePrices", "[]"))
            kv = dict(zip(o, p))
            if "win on" in q:
                tm = whichteam(q)
                if tm and "Yes" in kv:
                    val = float(kv["Yes"]) * 100
                    if tm == hen:
                        ml_home = val
                    elif tm == aen:
                        ml_away = val
            elif "draw" in q and "Yes" in kv:
                ml_draw = float(kv["Yes"]) * 100

    if more:
        for m in more[0].get("markets", []):
            q = m.get("question", "")
            o = json.loads(m.get("outcomes", "[]"))
            p = json.loads(m.get("outcomePrices", "[]"))
            kv = dict(zip(o, p))
            if q.startswith("Spread:"):
                mm = re.search(r"Spread:\s*(.+?)\s*\(([-+]?\d+\.5)\)", q)
                if mm:
                    tname = mm.group(1)
                    line = abs(float(mm.group(2)))
                    tm = whichteam(tname)
                    if tm and tname in kv:
                        cover[tm][line] = float(kv[tname]) * 100
            else:
                tail = q.split(": ")[-1]
                mm = re.fullmatch(r"O/U (\d\.5)", tail)
                if mm and "Over" in kv:
                    ov[float(mm.group(1))] = float(kv["Over"]) * 100

    # 便捷: cover[team][0.5] = 该队净胜≥1 = 该队胜%
    if ml_home is not None:
        cover[hen][0.5] = ml_home
    if ml_away is not None:
        cover[aen][0.5] = ml_away

    ml = {}
    if ml_home is not None:
        ml["home"] = ml_home
    if ml_draw is not None:
        ml["draw"] = ml_draw
    if ml_away is not None:
        ml["away"] = ml_away

    return PolyProbs(
        slug=base[0].get("slug", "") if base else "",
        home_en=hen,
        away_en=aen,
        ml=ml,
        home_cover=cover[hen],
        away_cover=cover[aen],
        ou_over=ov,
    )
