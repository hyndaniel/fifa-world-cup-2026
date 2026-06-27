#!/usr/bin/env python3
"""把 deep-search 抽出的确证事实写进 enrich 表, 供 wc-prob-v2 的 match_fact_card 读取。

设计 (B2 共享情报层 · v2 那条):
- 上游 `/deep-research` → reports/deep-search-*.md (中立赛前情报)。
- 一个 LLM 抽取步把每队的 ✅确证 停赛/伤停/官宣轮换 抽成 JSON (本脚本的输入),
  只留确证事实, 剥掉出线形势/动机/叙事 (那些给 v1 / odds-analyst, 不进 v2)。
- 本脚本把每条事实当一条 "news" 写进 enrich.news_json:
    {"ts": <RFC-2822>, "title": <事实短句>, "url": <信源>}
  这复用了 enrich 既有的 news 通道 —— match_fact_card 只读 ts/title/url,
  ts 决定龄/stale (阈值 48h), title 成为 v2 偏离的 factor_source 短引用。
- ts 默认盖成"现在"(age_h=0) → 非 stale → 可驱动 v2 偏离;
  ⚠️未坐实的事实**不要**写进来 (或给一个 >48h 的 age_h 让它 stale 沉底)。

红线: 本脚本只搬"确证事实"。判断 (出线/动机/价值) 不经此口入 v2。

输入 JSON 格式:
    {
      "厄瓜多尔": [{"title": "组织核心 Páez 停赛缺阵", "url": "https://...", "age_h": 0}],
      "日本":     [{"title": "久保建英膝伤确认缺阵", "url": "https://...", "age_h": 0}]
    }
  age_h 可省 (默认 0 = 现在/非 stale)。

用法:
    python3 tools/save_intel.py --db /abs/path/data/wc.db --json intel.json [--dry-run]
  注意: data/wc.db 被 gitignore, 主 checkout 那个是 v2 真读的库 → 用绝对路径打主库。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)  # 让 `from backend import ...` 从任何 CWD 都可用

from backend.db import Db  # noqa: E402

BJ = timezone(timedelta(hours=8))


def build_news(facts, now):
    """[{title,url[,age_h]}] → [{ts(RFC-2822), title, url}], ts = now - age_h 小时。"""
    out = []
    for f in facts:
        title = (f.get("title") or "").strip()
        if not title:
            continue
        age_h = float(f.get("age_h", 0))
        ts = format_datetime((now - timedelta(hours=age_h)).astimezone(timezone.utc))
        out.append({"ts": ts, "title": title, "url": f.get("url", "")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="写 deep-search 确证事实进 enrich 表")
    ap.add_argument("--db", required=True, help="wc.db 路径 (用主 checkout 绝对路径)")
    ap.add_argument("--json", required=True, help="抽取出的事实 JSON (team_cn -> [facts])")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写库")
    args = ap.parse_args(argv)

    now = datetime.now(BJ)
    with open(args.json, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        print("输入必须是 {team_cn: [facts]} 对象", file=sys.stderr)
        return 2

    db = Db(args.db)
    total = 0
    teams = 0
    for team_cn, facts in data.items():
        if team_cn.startswith("_") or not isinstance(facts, list):
            continue  # 跳过 _meta 等元数据键
        news = build_news(facts, now)
        if not news:
            print(f"  skip {team_cn}: 无有效事实")
            continue
        teams += 1
        total += len(news)
        if args.dry_run:
            print(f"  [dry] {team_cn}: {len(news)} 条")
            for n in news:
                print(f"        - {n['title']}  [{n['ts']}]")
        else:
            db.save_enrich(team_cn, None, news)
            print(f"  wrote {team_cn}: {len(news)} 条确证事实 → enrich")
    print(f"{'(dry) ' if args.dry_run else ''}完成: {total} 条事实, {teams} 队")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
