#!/usr/bin/env python3
"""赛果回填闭环编排:抓终比分 → record_result(幂等)→ 机械对错标台账 → 重跑跑分卡。

--once 是唯一运行模式(单跑单退),供 launchd 每 5 分钟无害重跑:已录的场次 upsert
不重复;台账与跑分卡每次都从 db 全量重生成(确定性、内容不变即无 diff),故上次崩了
下次自愈。台账(复盘对错标)从 match_results 重建,带对阵队名(查 wc.db)+ h/d/a 图例。
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
# 作为脚本被 launchd 直接调时 sys.path[0] 是 tools/,补上仓库根以便 import backend。
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.results import MatchResult, fetch_results  # noqa: E402
from backend.baseline import HAD_CFG, baseline_market, get_result_goals, record_result  # noqa: E402
from backend.mech_tag import mech_tags  # noqa: E402
from backend.scenarios import rebuild_hits, scenario_hit  # noqa: E402
from backend.v2_predict import get_v2_prediction  # noqa: E402

DEFAULT_CACHE = str(REPO / ".cache" / "odds_cache.db")
DEFAULT_TAGS_OUT = str(REPO / "reports" / "scoring" / "复盘对错标.md")  # §3 命名迁移:scoring/ 子目录
DEFAULT_WC_DB = str(REPO / "data" / "wc.db")
DEFAULT_SCENARIO_LIB = str(REPO / "reports" / "scenario_library.json")

_RESULTS_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS match_results (match_key TEXT PRIMARY KEY, "
    "home_goals INTEGER, away_goals INTEGER, outcome TEXT, ts TEXT)"
)

_LEDGER_HEADER = (
    "# 复盘对错标(机械·纯事实)\n\n"
    "> 每场实际 outcome(胜平负 had)对三方 had argmax 预测的对错:✅中 / ❌错 / —无预测。\n"
    "> 结果代码(主队视角):**h**=主胜 / **d**=平 / **a**=客胜。\n\n"
    "| ts | 场次 | 对阵 | 实际 | v1 | v2 | 市场 |\n|---|---|---|---|---|---|---|\n"
)


def backfill(cache_path: str, results: list[MatchResult]) -> list[str]:
    """对每个 finished 的 result 调 record_result(upsert),返回**本次新录或改动**的 match_key。

    已存在且比分一致的场次跳过——这样 launchd 每 5 分钟重跑时(终比分会在抓取窗口里
    停留两天)不会反复刷台账/重跑跑分卡,真正做到无害重跑。比分被更正则重录并返回。
    """
    done: list[str] = []
    for r in results:
        if not r.finished:
            continue
        if get_result_goals(cache_path, r.zucai_num) == (r.home_goals, r.away_goals):
            continue  # 已录且未变,幂等跳过
        record_result(cache_path, r.zucai_num, r.home_goals, r.away_goals)
        done.append(r.zucai_num)
    return done


def _team_names(wc_db_path: str) -> dict:
    """zucai_num → "主队 vs 客队"(中文),取自 wc.db matches;库/表缺失 → 空 dict(队名留空)。"""
    names: dict = {}
    try:
        # 真只读(mode=ro):库缺失直接报错走 graceful,绝不创建空 wc.db。
        conn = sqlite3.connect(f"file:{wc_db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return names
    try:
        for num, home, away in conn.execute("SELECT zucai_num, home_cn, away_cn FROM matches"):
            if num:
                names[num] = f"{home or ''} vs {away or ''}"
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return names


def _wc_db_ready(wc_db_path: str, cache_path: str) -> bool:
    """重写前自检:防偶发退化。返回 False = 本轮别重写台账/跑分卡(保上一版好数据)。

    队名读得到 → True(正常放行)。队名整个读空时分两种:
      · match_results 也空(全新库/清库)→ True,放行走首次自愈生成(不阻塞冷启动);
      · match_results 有赛果却读不到任何队名 → False,判定 wc.db matches 正被别的进程
        重建的瞬时竞争(_team_names 只读打开失败 or 表被重建到空),跳过本轮重写以免
        『空白队名 + 掉末轮/非末轮桶』的坏版本覆盖已有好文件;下一轮 wc.db 可读时自愈。
    """
    if _team_names(wc_db_path):
        return True
    try:
        conn = sqlite3.connect(cache_path)
        conn.execute(_RESULTS_SCHEMA)  # 表不存在时防炸
        n = conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        return True  # 连 cache 都读不到 → 非本护栏场景, 放行走原有异常处理
    return n == 0


def render_mech_tags(cache_path: str, out_path: str, wc_db_path: str = DEFAULT_WC_DB) -> None:
    """从 match_results 全量重生成复盘对错标台账(确定性、自愈,同跑分卡)。

    每行:记录时间 ts + 场次 + 对阵(查 wc.db 队名)+ 实际 outcome + 三方 had argmax 对错。
    内容仅取决于 match_results + 各方预测,故重跑稳定无 diff;一次性补全历史行的队名。
    """
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)  # 防御:从未录过赛果时表不存在
        rows = conn.execute(
            "SELECT match_key, ts FROM match_results ORDER BY ts, match_key"
        ).fetchall()
    finally:
        conn.close()
    names = _team_names(wc_db_path)
    lines = [_LEDGER_HEADER]
    for key, ts in rows:
        t = mech_tags(cache_path, key)
        lines.append(
            f"| {ts} | {key} | {names.get(key, '')} | "
            f"{t['actual']} | {t['v1']} | {t['v2']} | {t['market']} |\n"
        )
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(lines), encoding="utf-8")


def _scenario_names(v2rec) -> list:
    """v2 记录里贴的剧本名(scenarios 可为字符串或 {name:..} dict)。"""
    scen = (v2rec or {}).get("scenarios") or []
    return [s.get("name") if isinstance(s, dict) else s for s in scen]


def rebuild_scenario_hits(cache_path: str, lib_out: str = DEFAULT_SCENARIO_LIB) -> None:
    """从 match_results × v2 剧本标签全量重建剧本命中台账(确定性、自愈,同台账/跑分卡)。

    每场:取实际比分 + v2 贴的剧本 + 市场 had 热门 → scenario_hit 机械判命中 →
    rebuild_hits 从种子重建(幂等,每 5 分钟重跑不翻倍)。无 v2 标签的场不贡献事件。
    """
    conn = sqlite3.connect(cache_path)
    try:
        conn.execute(_RESULTS_SCHEMA)  # 防御:从未录过赛果时表不存在
        keys = [r[0] for r in conn.execute("SELECT match_key FROM match_results").fetchall()]
    finally:
        conn.close()
    events = []
    for mk in keys:
        goals = get_result_goals(cache_path, mk)
        if not goals:
            continue
        hg, ag = goals
        names = _scenario_names(get_v2_prediction(cache_path, mk))
        if not names:
            continue
        bl = baseline_market(cache_path, mk, HAD_CFG)
        # baseline_market 可能返回 truthy 但 baseline={}(某场 had 赔率全 null/部分抓取),
        # 故须显式判空,否则 max({}) 抛 ValueError → 回填每5分钟 rc=1、剧本台账冻住。
        fav = max(bl["baseline"], key=bl["baseline"].get) if (bl and bl["baseline"]) else None
        for name in names:
            hit = scenario_hit(name, hg, ag, fav)
            if hit is None:
                continue
            events.append((name, hit))
    rebuild_hits(lib_out, events)


def _rerun_scorecard(cache_path: str) -> None:
    """重跑 v2 跑分卡。v2_report.main() 不收 argv 而读 sys.argv,故临时改写 argv,
    把刚回填的同一个 cache 透传过去(否则它会读自己的默认库,与本次回填脱节)。"""
    from tools import v2_report

    saved = sys.argv
    sys.argv = ["v2_report", "--cache", cache_path]
    try:
        v2_report.main()
    finally:
        sys.argv = saved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赛果回填闭环编排")
    # --once 是唯一运行模式(单跑单退,供 launchd 每 5 分钟调度);接受该 flag
    # 以兼容 plist 调用,但不引入循环模式——本脚本本就只跑一次就退。
    ap.add_argument("--once", action="store_true",
                    help="唯一运行模式:单跑单退(供 launchd);无此 flag 行为相同")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--tags-out", default=DEFAULT_TAGS_OUT)
    ap.add_argument("--wc-db", default=DEFAULT_WC_DB, help="队名来源(wc.db matches)")
    ap.add_argument("--scenario-lib", default=DEFAULT_SCENARIO_LIB,
                    help="剧本命中台账输出(从 v2 标签×赛果全量重建)")
    args = ap.parse_args(argv)

    try:
        results = fetch_results()
    except Exception as e:  # noqa: BLE001 网络/解析任何失败都判 1
        print(f"[backfill] 抓终比分失败: {e}", file=sys.stderr)
        return 1

    keys = backfill(args.cache, results)
    print(f"[backfill] 已录 {len(keys)} 场: {keys}" if keys else "[backfill] 无新赛果可录")

    # 护栏:wc.db matches 正被别的进程重建的瞬时竞争里,队名会整个读空 → 若此时重写,
    # 台账队名被清空、跑分卡掉末轮/非末轮桶,坏版本盖掉好文件(偶发但真出过)。有赛果却
    # 读不到队名时跳过本轮重写、保上一版,rc=0 自愈——下一轮 wc.db 可读时补。空库照放行。
    if not _wc_db_ready(args.wc_db, args.cache):
        print("[backfill] wc.db 队名读空但已有赛果(多半 matches 表正被重建的瞬时竞争)——"
              "跳过本轮台账/跑分卡重写,保留上一版好数据,下一轮自愈", file=sys.stderr)
        return 0

    # 台账 + 跑分卡都从 db 全量重生成:确定性、自愈,不被"本次有无新赛果"门控
    # (上次崩了本次补)。台账重生成同时一次性补全历史行的队名。
    # try 包裹:失败 signal 1,但前面已成功的 record 不被吞。
    try:
        render_mech_tags(args.cache, args.tags_out, args.wc_db)
        _rerun_scorecard(args.cache)
        rebuild_scenario_hits(args.cache, args.scenario_lib)
    except Exception as e:  # noqa: BLE001 重生成台账/跑分卡/剧本台账任何失败都 signal 1
        print(f"[backfill] 重生成台账/跑分卡失败: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
