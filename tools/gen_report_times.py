#!/usr/bin/env python3
"""生成 reports/report_times.json: {报告名(无.md): git 最后提交 unix 秒}, 回退文件 mtime。

为什么需要: 部署 = git reset 到 tag + docker build。git reset 把所有 reports/*.md 的
文件 mtime 抹平成 checkout 时刻; 而镜像(python:3.12-slim, .dockerignore 排除 .git)里
既无 .git 也无 git 二进制, 运行时拿不到真实新旧。故在**有 .git 的地方**(本地, 或部署机
git reset 之后、docker build 之前)按 git 提交时间预生成此清单, 随 reports/ 一起 COPY 进
镜像; backend/reports.py 读它排序, 免疫 mtime 抹平。

用法:
    python3 tools/gen_report_times.py [reports_dir]   # 默认 reports
报告新增/更新后(或每次部署前)重跑一次即可。
"""
import json
import pathlib
import subprocess
import sys


def git_ts(p):
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ct", "--", p.name],
            cwd=str(p.parent), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    s = out.stdout.strip()
    return int(s) if out.returncode == 0 and s.isdigit() else None


def main(reports_dir="reports"):
    base = pathlib.Path(reports_dir)
    if not base.is_dir():
        print(f"no such dir: {reports_dir}", file=sys.stderr)
        return 1
    times = {}
    seen = {}  # stem -> 首次命中的相对路径, 用于检测跨子目录同名碰撞
    # 递归扫子目录 (agents/scoring/intel), 跳过下划线目录 (_archive/_state) —— 与 backend/reports.py 一致
    for p in sorted(base.rglob("*.md")):
        rel = p.relative_to(base)
        if any(part.startswith("_") for part in rel.parts[:-1]):
            continue
        if p.name.startswith("."):
            continue
        # name=stem 是看板的 URL 标识(见 backend/reports.py), 必须全局唯一。
        # 同 stem 跨子目录 → list_reports 出重复卡、read_report 取序不定 → 迁移漏改的早期信号。
        if p.stem in seen:
            print(f"⚠️ 报告 stem 碰撞: '{p.stem}' 同时在 {seen[p.stem]} 与 {rel} —— "
                  f"看板会出重复卡/取序不定, 请改名或迁移到位。", file=sys.stderr)
        seen[p.stem] = rel
        ts = git_ts(p)
        if ts is None:
            ts = int(p.stat().st_mtime)
        times[p.stem] = ts
    out = base / "report_times.json"
    out.write_text(
        json.dumps(times, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out} ({len(times)} reports)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "reports"))
