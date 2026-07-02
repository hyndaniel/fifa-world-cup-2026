#!/usr/bin/env python3
"""push_data — 本地台账/报告直推 HK 看板, 走 /api/ingest/tickets 与 /api/ingest/reports,
不碰 git/部署(两个端点落盘的 data/reports 是 bind-mount + 后端每次请求现读, 落盘即生效)。

用法:
  python3 tools/push_data.py tickets                                   # 推整份 data/bet_ledger.json
  python3 tools/push_data.py reports reports/agents/wc-bet__下注复盘.md  # 推指定报告(可给多个)
  python3 tools/push_data.py reports --all                             # 推 reports/ 下所有 .md
                                                                        # (跳过下划线目录, 量大慎用)
环境: WC_INGEST_URL / WC_INGEST_PW / WC_INGEST_USER(admin), 与 refresh_all.py 一致。
"""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import ingest_client as ic  # noqa: E402

# 失败抛异常语义(推失败该让用户看到); 保留 _post 名字供测试打桩。实现在 ingest_client。
_post = ic.post


def push_tickets(ledger_path):
    with open(ledger_path, encoding="utf-8") as f:
        ledger = json.load(f)
    resp = _post("/api/ingest/tickets", ledger)
    print(f"tickets -> {resp}")


def _report_name(path, reports_root):
    """文件绝对/相对路径 -> ingest 用的 name(相对 reports_root、不含 .md 后缀)。"""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(reports_root))
    if rel.endswith(".md"):
        rel = rel[:-3]
    return rel.replace(os.sep, "/")


def _collect_all_reports(reports_root):
    base = pathlib.Path(reports_root)
    out = []
    for p in sorted(base.rglob("*.md")):
        rel = p.relative_to(base)
        if any(part.startswith("_") for part in rel.parts[:-1]) or p.name.startswith("."):
            continue
        out.append(p)
    return out


def push_reports(paths, reports_root):
    items = []
    for p in paths:
        p = pathlib.Path(p)
        content = p.read_text(encoding="utf-8")
        items.append({"name": _report_name(p, reports_root), "content": content})
    if not items:
        print("reports -> 没有要推的文件")
        return
    resp = _post("/api/ingest/reports", {"reports": items})
    print(f"reports -> {resp}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tickets", help="推整份 data/bet_ledger.json")

    rp = sub.add_parser("reports", help="推指定/全部报告 md")
    rp.add_argument("files", nargs="*", help="要推的 reports/**/*.md 文件路径(相对或绝对)")
    rp.add_argument("--all", action="store_true", help="推 reports/ 下所有 .md(跳过下划线目录)")

    ap.add_argument("--ledger", default=os.path.join(REPO, "data", "bet_ledger.json"))
    ap.add_argument("--reports-root", default=os.path.join(REPO, "reports"))

    args = ap.parse_args()

    if args.cmd == "tickets":
        push_tickets(args.ledger)
    elif args.cmd == "reports":
        if args.all:
            if args.files:
                sys.exit("reports: --all 与显式文件列表二选一, 不要同时给")
            paths = _collect_all_reports(args.reports_root)
        else:
            if not args.files:
                sys.exit("reports: 至少给一个文件路径, 或用 --all")
            paths = args.files
        push_reports(paths, args.reports_root)


if __name__ == "__main__":
    main()
