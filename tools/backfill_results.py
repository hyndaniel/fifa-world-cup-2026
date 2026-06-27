#!/usr/bin/env python3
"""赛果回填闭环编排:抓终比分 → record_result(幂等)→ 机械对错标台账 → 重跑跑分卡。

--once 是唯一运行模式(单跑单退),供 launchd 每 5 分钟无害重跑:已录的场次 upsert
不重复;跑分卡每次无条件重跑(确定性、内容不变即无 diff),故上次崩了下次自愈。
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
# 作为脚本被 launchd 直接调时 sys.path[0] 是 tools/,补上仓库根以便 import backend。
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.results import MatchResult, fetch_results  # noqa: E402
from backend.baseline import get_result_goals, record_result  # noqa: E402
from backend.mech_tag import mech_tags  # noqa: E402

DEFAULT_CACHE = str(REPO / ".cache" / "odds_cache.db")
DEFAULT_TAGS_OUT = str(REPO / "reports" / "复盘对错标.md")

_LEDGER_HEADER = (
    "# 复盘对错标(机械·纯事实)\n\n"
    "> 每场实际 outcome 对三方 had argmax 的对错:✅中 / ❌错 / —无预测。\n\n"
    "| ts | 场次 | 实际 | v1 | v2 | 市场 |\n|---|---|---|---|---|---|\n"
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


def write_mech_tags(cache_path: str, keys: list[str], out_path: str) -> None:
    """把这些 key 的 mech_tags 追加进台账 markdown(带时间戳表格行)。

    文件不存在则先写表头;存在则只追加行(launchd 重跑只增量,不重复表头)。
    """
    if not keys:
        return
    p = pathlib.Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    if not p.exists():
        lines.append(_LEDGER_HEADER)
    for k in keys:
        t = mech_tags(cache_path, k)
        lines.append(
            f"| {ts} | {k} | {t['actual']} | {t['v1']} | {t['v2']} | {t['market']} |\n"
        )
    with p.open("a", encoding="utf-8") as f:
        f.write("".join(lines))


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
    args = ap.parse_args(argv)

    try:
        results = fetch_results()
    except Exception as e:  # noqa: BLE001 网络/解析任何失败都判 1
        print(f"[backfill] 抓终比分失败: {e}", file=sys.stderr)
        return 1

    keys = backfill(args.cache, results)
    # 台账写入只在有新 keys 时做(append、非幂等,不能每次刷)。
    if keys:
        write_mech_tags(args.cache, keys, args.tags_out)
        print(f"[backfill] 已录 {len(keys)} 场: {keys}")
    else:
        print("[backfill] 无新赛果可录")

    # 跑分卡重跑无条件执行(自愈):它是确定性的(同一 db → 同样的预测v2.md,
    # 内容不变即无害)。即便上次重跑崩了进程退,本次也会重新生成——这正是
    # launchd 每 5 分钟自愈的意义,故不被"本次有无新赛果"门控。
    # 用 try 包裹:失败 signal 非 0,但不吞掉前面已成功的 record/台账。
    try:
        _rerun_scorecard(args.cache)
    except Exception as e:  # noqa: BLE001 重跑任何失败都 signal 1,record/台账已落地
        print(f"[backfill] 重跑跑分卡失败: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
