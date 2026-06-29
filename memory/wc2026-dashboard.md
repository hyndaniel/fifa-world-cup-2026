---
name: wc2026-dashboard
description: WC价值看板部署架构 — HK FastAPI + 双采集器(GFW分置) + 价值雷达/富化
metadata: 
  node_type: memory
  type: project
  originSessionId: 7abbfede-a881-4a98-8cac-5753eed61ff1
---

WC 价值看板 (可视化下注辅助), 区别于 [[wc2026-value-betting-tool]] 的 CLI 脚本。

- **仓库**: `/Users/heyining/Daniel/WorkSpace/fifa-world-cup-2026` (git, 非 iCloud)。
- **线上**: `http://18.166.71.60:8000` (AWS-HK, ap-east-1, ssh 别名 `AWS`, docker compose, `network_mode: host`, 部署在 `/opt/github/fifa-world-cup-2026`, root 拥有 → rsync 用 `--rsync-path="sudo rsync"`, 排除 `data/` 别覆盖生产库)。重建: `ssh AWS 'cd /opt/github/... && sudo docker compose up -d --build'`。
- **认证密码**: 只在 HK `config.toml` (`[server] password`)。**不入记忆**(分类器要求)。脚本里用 `grep+sed` 现取, 不打印。

**双采集器按"哪边能连"分置 (核心约束, 代码看不出来)**:
- 足彩 → **必须 Mac 住宅 IP** (HK 机房 IP 被 EdgeOne WAF + JA3 拦)。常驻 = **launchd** `com.wc.refresh-all`(2026-06-29 起取代旧 `collect_zucai`/`com.wc.collect-zucai`,后者已退役、launchctl 不再加载)。`refresh_all --once` 一并刷三源,POST `/api/ingest/zucai` 竞彩raw + `/api/ingest/odds` 赔率面板;实机间隔 30min。
- 新闻富化 → **必须 HK 抓** (Google News RSS 在大陆被墙, Mac 直连超时)。常驻 = **app 内置后台线程** `backend/news_enrich.py::run_enrich_loop` (run.py 启动, 每 600s, 无主机 cron)。抓 `news.google.com/rss/search`。

**功能面**: 价值雷达只留 green/yellow (去水 EV); 每行常驻"足彩(隐含%)·Poly去水·概率差·EV", 展开看全格。特别关注: watchlist 项富化 matches/lineup/news/radar_hits。**首发无免费可靠源 → 恒 None(未出炉)**, 用户已接受。

**已知降级**: `sporttery.parse_matches` 的 `cutoff_bj` 仍是 "" 占位 → 顶栏倒计时/next_cutoff/matches_today 不活。待补。
