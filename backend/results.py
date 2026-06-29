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

import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

# 抓取重试默认值:launchd 启动早期偶发 DNS 失败(getaddrinfo "nodename nor
# servname",见运维记忆),指数退避重试可自愈;HTTP 4xx/5xx 不在此列。
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2.0

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


def _http_get_json(url: str, headers: dict, proxy: str | None, timeout: float) -> dict:
    """GET url → 解析 JSON,纯 stdlib(urllib)。无第三方依赖,任何 python3 可跑。

    proxy 仅支持 http/https(urllib ProxyHandler);为空时显式禁用代理(传空 dict
    给 ProxyHandler)→ 确定性直连,不被环境里的 *_PROXY 变量劫持(回填闭环要稳)。
    socks5 不被 stdlib 原生支持(需 PySocks);results 在实机为直连,故不需要——
    若将来要给 results 走 socks5 过 WAF,须另引依赖或改回 httpx,见 [zucai] 走 sporttery.py。
    HTTP 4xx/5xx 由 opener.open 抛 HTTPError(等价 httpx raise_for_status),
    交由调用方(backfill_results.main)按"抓取失败 → signal 1"优雅处理。
    """
    req = Request(url, headers=headers)
    handler = ProxyHandler({"http": proxy, "https": proxy}) if proxy else ProxyHandler({})
    opener = build_opener(handler)
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_get_json_retry(url: str, headers: dict, proxy: str | None, timeout: float,
                         retries: int = DEFAULT_RETRIES,
                         backoff: float = DEFAULT_BACKOFF) -> dict:
    """_http_get_json 外套指数退避重试,只救瞬时网络错(DNS/连接/超时)。

    HTTPError(4xx/5xx,如 WAF 403)重试无益 → 立即上抛;URLError/超时/OSError
    (含 getaddrinfo gaierror)退避后重试。退避 = backoff·2^attempt(2s,4s,…)。
    用尽 retries 仍失败 → 抛最后一个错,交 backfill_results.main 按"抓取失败 → rc=1"处理。
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _http_get_json(url, headers, proxy, timeout)
        except HTTPError:
            raise                                  # 状态码错不重试
        except (URLError, TimeoutError, OSError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise last  # type: ignore[misc]


def fetch_results(cfg: dict | None = None, timeout: float = 30.0) -> list[MatchResult]:
    """GET sporttery 开奖接口 → parse_results → list[MatchResult]。

    从 cfg["results"] 取 api/ua/proxy/日期范围, 缺则用模块默认 (端点/UA/Referer
    与 Task 1 实测对齐, 见 docs/design/notes-sporttery-result-endpoint.md)。
    日期窗默认 = 今天往前 DEFAULT_LOOKBACK_DAYS 天 ~ 今天 (相对日期), 可经
    cfg["results"]["begin_date"]/["end_date"] (YYYY-MM-DD) 覆盖。

    抓取走 _http_get_json (纯 stdlib urllib): 回填闭环的 launchd 解释器无需装 httpx,
    /usr/bin/python3 也能跑——刻意不依赖第三方,承接项目 stdlib-first 规范 (§7.2)。
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
    retries = int(rcfg.get("retries") or DEFAULT_RETRIES)
    backoff = float(rcfg.get("backoff") or DEFAULT_BACKOFF)
    url = f"{api}?{urlencode(params)}"
    return parse_results(_http_get_json_retry(url, headers, proxy, timeout, retries, backoff))
