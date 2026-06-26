"""从 sporttery 开奖接口解析 FT 终比分。key 天然 = zucai_num (与预测同键)。

字段路径以 Task 1 spike 实测为准 (docs/design/notes-sporttery-result-endpoint.md):
- 列表:    value.matchResult (数组)
- 组彩编号: matchResult[].matchNumStr  (如 "周四055")  -> zucai_num
- 终比分:   matchResult[].sectionsNo999 ("主:客", 如 "2:1") -> home/away_goals
- 完赛判定: matchResult[].matchResultStatus == "2"  -> finished=True
            该端点只返回已完赛场次 (未完赛整条缺席); winFlag/poolStatus 为空
            不代表未完赛 (让球/单关池未结算), 故不拿它们当门控。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import httpx

# 完赛标志: matchResultStatus 取此值才算已完赛已开奖。
FINISHED_STATUS = "2"

# 开奖(FT 终比分)端点。Task 1 实测命中 (docs/design/notes-sporttery-result-endpoint.md):
# 必须用 getUniformMatchResultV1.qry —— getMatchResultV1.qry 被 EdgeOne WAF 硬拦 403。
DEFAULT_API = (
    "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
)
DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148"
REFERER = "https://m.sporttery.cn/"
# 默认日期窗: 今天往前 2 天 ~ 今天 (相对日期, 别硬编码某一天)。
DEFAULT_LOOKBACK_DAYS = 2


@dataclass(frozen=True)
class MatchResult:
    zucai_num: str
    home_goals: int
    away_goals: int
    finished: bool


def _parse_score(sections: object) -> tuple[int, int] | None:
    """拆 "主:客" 终比分串为 (home, away) int; 空/缺失/格式异常 -> None。"""
    if not isinstance(sections, str):
        return None
    s = sections.strip()
    if ":" not in s:
        return None
    home_str, _, away_str = s.partition(":")
    try:
        return int(home_str.strip()), int(away_str.strip())
    except ValueError:
        return None


def parse_results(data: dict) -> list[MatchResult]:
    """解析开奖响应为 MatchResult 列表。

    只把 matchResultStatus == "2" 且 sectionsNo999 为有效 "主:客" 的场次记为
    finished=True; 其余 (合成/防御性兜底的未完赛行) finished=False。
    无 matchNumStr 的行跳过。
    """
    rows = (((data or {}).get("value") or {}).get("matchResult")) or []
    out: list[MatchResult] = []
    for m in rows:
        num = (m.get("matchNumStr") or "").strip()
        if not num:
            continue
        score = _parse_score(m.get("sectionsNo999"))
        finished = m.get("matchResultStatus") == FINISHED_STATUS and score is not None
        home, away = score if score is not None else (0, 0)
        out.append(MatchResult(num, home, away, finished))
    return out


def fetch_results(cfg: dict | None = None, timeout: float = 30.0) -> list[MatchResult]:
    """GET sporttery 开奖接口 → parse_results → list[MatchResult]。

    从 cfg["results"] 取 api/ua/proxy/日期范围, 缺则用模块默认 (端点/UA/Referer
    与 Task 1 实测对齐, 见 docs/design/notes-sporttery-result-endpoint.md)。
    日期窗默认 = 今天往前 DEFAULT_LOOKBACK_DAYS 天 ~ 今天 (相对日期), 可经
    cfg["results"]["begin_date"]/["end_date"] (YYYY-MM-DD) 覆盖。

    模式严格参照 backend.sporttery.fetch: httpx.Client、proxy 可选、raise_for_status。
    """
    rcfg = (cfg or {}).get("results") or {}
    api = rcfg.get("api") or DEFAULT_API
    ua = rcfg.get("ua") or DEFAULT_UA
    proxy = rcfg.get("proxy") or None

    today = date.today()
    begin = rcfg.get("begin_date") or (
        today - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    ).isoformat()
    end = rcfg.get("end_date") or today.isoformat()

    headers = {
        "User-Agent": ua,
        "Referer": REFERER,
        "Accept": "application/json, text/plain, */*",
    }
    params = {
        "matchBeginDate": begin,
        "matchEndDate": end,
        "leagueId": "",
        "pageSize": "100",
        "pageNo": "1",
        "isFix": "0",
        "matchPage": "1",
        "pcOrWap": "1",
    }
    client_kwargs: dict = {"timeout": timeout}
    if proxy:
        client_kwargs["proxy"] = proxy
    with httpx.Client(**client_kwargs) as client:
        resp = client.get(api, params=params, headers=headers)
        resp.raise_for_status()
        return parse_results(resp.json())
