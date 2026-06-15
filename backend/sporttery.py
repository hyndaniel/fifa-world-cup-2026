"""足彩 client: 拉中国体彩竞彩盘口 (胜平负 had / 让球 hhad / 总进球 ttg) 并解析。

契约 (backend/models.py ZucaiMatch):
    ZucaiMatch(zucai_num, home_cn, away_cn, ko_bj, cutoff_bj,
               had: {"h","d","a"} | None,
               hhad: {"line": int, "h","d","a"} | None,
               ttg: {0..7: float})

注意:
- hhad 用键 "line" (int)，不是上游响应里的 "goalLine"(str)。
- ttg 用 int 键 0..7，值来自上游 s0..s7 (str → float)。
- had/hhad 上游为空 dict 时 → None。
"""
from __future__ import annotations

import httpx

from .models import ZucaiMatch

# 上游接口 (与 prototype/wc_value.py 一致)。
DEFAULT_API = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148"
DEFAULT_POOLS = "had,hhad,ttg"
REFERER = "https://m.sporttery.cn/"


def fetch(cfg: dict, timeout: float = 30.0) -> dict:
    """GET 足彩盘口，返回解析后的 JSON dict。

    cfg 形如 backend.config.load_config() 的输出；缺字段时回退默认值。
    """
    zucai = (cfg or {}).get("zucai", {})
    poll = (cfg or {}).get("poll", {})
    api = zucai.get("api") or DEFAULT_API
    ua = zucai.get("ua") or DEFAULT_UA
    pools = poll.get("zucai_pools") or DEFAULT_POOLS
    # 足彩被 EdgeOne WAF 按 IP 拦截 (数据中心 IP 中招)。部署到海外机房时，
    # 给 [zucai] proxy 配一个出口能过 WAF 的代理 (如住宅节点的本地 SOCKS5
    # "socks5://127.0.0.1:10808")。空 → 直连。仅足彩走此代理，Polymarket 直连。
    proxy = zucai.get("proxy") or None
    headers = {
        "User-Agent": ua,
        "Referer": REFERER,
        "Accept": "application/json",
    }
    params = {"poolCode": pools, "channel": "c"}
    client_kwargs: dict = {"timeout": timeout}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as client:
        resp = client.get(api, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _to_float(v) -> float | None:
    """上游赔率多为字符串；空串/None → None。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_had(raw) -> dict | None:
    """{"h","d","a"} 浮点；上游空 dict 或缺主胜赔率 → None。"""
    if not raw:
        return None
    h = _to_float(raw.get("h"))
    d = _to_float(raw.get("d"))
    a = _to_float(raw.get("a"))
    if h is None and d is None and a is None:
        return None
    return {"h": h, "d": d, "a": a}


def _parse_hhad(raw) -> dict | None:
    """{"line": int, "h","d","a"} 浮点；line 来自上游 goalLine(str→int)。"""
    if not raw:
        return None
    h = _to_float(raw.get("h"))
    d = _to_float(raw.get("d"))
    a = _to_float(raw.get("a"))
    line_raw = raw.get("goalLine")
    line = None
    if line_raw is not None and str(line_raw).strip():
        try:
            line = int(float(line_raw))
        except (TypeError, ValueError):
            line = None
    if h is None and d is None and a is None:
        return None
    return {"line": line, "h": h, "d": d, "a": a}


def _parse_ttg(raw) -> dict:
    """s0..s7 (str) → {0..7: float}；缺/空值的桶不写入。"""
    out: dict[int, float] = {}
    if not raw:
        return out
    for n in range(0, 8):
        v = _to_float(raw.get(f"s{n}"))
        if v is not None:
            out[n] = v
    return out


def _ko_bj(sub: dict) -> str:
    """开球北京时间: 拼 matchDate + matchTime；缺则尽量返回已有部分。"""
    date = (sub.get("matchDate") or "").strip()
    time = (sub.get("matchTime") or "").strip()
    if date and time:
        return f"{date} {time}"
    return date or time or ""


def parse_matches(data: dict) -> list[ZucaiMatch]:
    """遍历 value.matchInfoList[].subMatchList[] → list[ZucaiMatch]。"""
    out: list[ZucaiMatch] = []
    value = (data or {}).get("value") or {}
    for day in value.get("matchInfoList") or []:
        for sub in day.get("subMatchList") or []:
            home_cn = sub.get("homeTeamAbbName") or ""
            away_cn = sub.get("awayTeamAbbName") or ""
            out.append(
                ZucaiMatch(
                    zucai_num=sub.get("matchNumStr") or "",
                    home_cn=home_cn,
                    away_cn=away_cn,
                    ko_bj=_ko_bj(sub),
                    cutoff_bj="",  # v1: 停售时间占位，留空字符串
                    had=_parse_had(sub.get("had")),
                    hhad=_parse_hhad(sub.get("hhad")),
                    ttg=_parse_ttg(sub.get("ttg")),
                )
            )
    return out
