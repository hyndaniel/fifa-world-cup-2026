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

# 完赛标志: matchResultStatus 取此值才算已完赛已开奖。
FINISHED_STATUS = "2"


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
