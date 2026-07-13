---
name: 数据快推
description: 把台账 `data/bet_ledger.json`、报告 `reports/**/*.md` 这类纯数据改动,直接 POST 到 HK 看板的 `/api/ingest/tickets` / `/api/ingest/reports` 接口,完全不走 git commit/PR/SSH/部署那一套。用户说「数据快推」「推数据上看板」「/数据快推」即用。前提:HK 看板得已经部署过带这两个接口的后端版本(PR #61 起),否则会 404——如果还没部署过,先走一次 fifa-deploy。只适用于纯数据;一旦改动涉及 backend/frontend/Dockerfile/docker-compose 代码,必须走 fifa-deploy。
---

# 数据快推 —— 台账/报告直推看板(HTTP ingest,不经 git)

给"只改了数据,没改代码"这类场景用的最短路径:本地改完 `data/bet_ledger.json` 或
`reports/**/*.md` 后,跑一个脚本直接 POST 给 HK 看板,立刻生效——**不用 git commit、
不用 PR、不用 SSH 登 HK、不用 docker rebuild/restart**。

**为什么能这么简单:** 看板早就给赔率/预测数据开了 `/api/ingest/predictions`、
`/api/ingest/odds` 这类直推接口(本地 POST、看板落库即生效)。本项目 2026-07-01 补上了
同款的 `/api/ingest/tickets`(台账)和 `/api/ingest/reports`(报告),让"改数据"和
"改代码才需要走部署"彻底解耦——**git 从此只管代码,不再是数据上线的必经之路。**

## 前提:接口本身得先部署过一次

`/api/ingest/tickets`、`/api/ingest/reports` 是**后端代码**(`backend/web.py` 等),
跟其它 backend 代码一样烤进镜像。如果 HK 上跑的还是这俩接口合并(main 上 PR #61)
**之前**的旧镜像,POST 会 404。判断办法:
```
curl -s -o /dev/null -w '%{http_code}\n' http://18.166.71.60:8000/api/ingest/tickets -X POST -d '{}'
```
404/405 之外的响应(如 401 需要鉴权、或 200)说明接口已在跑;如果确认接口还没部署过,
先走一次 `fifa-deploy`(带上这次的代码改动一起发)。**这是一次性的**——接口部署好之后,
后续所有纯数据改动都走这条快推通道,不用再碰 fifa-deploy。

## 流程

### 1. 本地照常改数据、照常 git commit(留个人历史,可选是否推 GitHub)

```
git add data/bet_ledger.json   # 或 reports/xxx.md
git commit -m "..."
```

这一步纯粹是给本地留版本历史(`wc-bet` 等 agent 也读本地文件),**跟"数据能不能上看板"
无关**——上看板走第 2 步的 HTTP POST,不依赖这次 commit 有没有推到 GitHub。要不要顺手
`git push`/开 PR 由你自己定,不是必须的前置步骤(仓库的推送闸不管这个)。

### 2. 直推 HK 看板

```
# 密码/基址/用户名从 launchd plist 现取(见下方红线), 用完即弃, 不打印:
PLIST=~/Library/LaunchAgents/com.wc.refresh-all.plist
export WC_INGEST_PW=$(plutil   -extract EnvironmentVariables.WC_INGEST_PW   raw "$PLIST")
export WC_INGEST_URL=$(plutil  -extract EnvironmentVariables.WC_INGEST_URL  raw "$PLIST")
export WC_INGEST_USER=$(plutil -extract EnvironmentVariables.WC_INGEST_USER raw "$PLIST")

# 台账整份推(会覆盖服务器上 tickets/recommendations/people 等顶层字段, 未在
# 本地文件里出现的顶层字段服务器保留原值, 不会被清空):
python3 tools/push_data.py tickets

# 报告(可给具体文件, 也可 --all 推 reports/ 下所有 md):
python3 tools/push_data.py reports reports/agents/wc-bet__下注复盘.md
python3 tools/push_data.py reports --all
```

> **🔴 密码不在 `config.toml`——那个文件在本机根本不存在。** 真实来源是 launchd 定时任务的 plist
> `~/Library/LaunchAgents/com.wc.refresh-all.plist`,其 `EnvironmentVariables` 段里有
> `WC_INGEST_PW` / `WC_INGEST_URL` / `WC_INGEST_USER` 三个键,用 `plutil -extract … raw` 取
> (2026-07-13 实测踩坑;本文档此前写的"从 `config.toml` `[server].password` 取"是错的)。

取来 `export` 进环境变量给脚本用,**不打印、不写进任何报告/对话**(跟 fifa-deploy/refresh_all
一致的规矩)。

### 3. 验证

```
curl -s -u admin:$WC_INGEST_PW http://18.166.71.60:8000/api/bets/summary | head -c 300
curl -s -u admin:$WC_INGEST_PW http://18.166.71.60:8000/api/reports | head -c 300
```
看到刚推的数值/报告标题已经出现即成功。

## 适用范围(判断错了就找错工具了)

- **适用**:只改了 `data/bet_ledger.json`(或未来其它挂在 `/api/ingest/tickets` 下的数据)、
  或 `reports/**/*.md`。
- **不适用、改走 `fifa-deploy`**:凡是碰了 `backend/`、`frontend/`、`Dockerfile`、
  `docker-compose.yml`、`requirements.txt`——这些是烤进镜像的代码,数据快推**不会**让
  它们生效,会造成"以为推了其实没生效"的假象。

## 红线 / 边界

- **只管数据,不碰代码**——发现改动牵涉 backend/frontend/Dockerfile/compose,停下来
  改走 `fifa-deploy`。
- `/api/ingest/tickets` 是**按顶层字段覆盖**语义(`updated`/`recommendations`/`tickets`/
  `people`/`ticket_note`,POST 里给了哪个字段就覆盖哪个,没给的字段服务器保留原值)——
  推的时候确认本地 `bet_ledger.json` 里这几个字段都是你想要的最终状态,不是只改了一部分
  就手工裁剪 body(脚本是整份文件直接推,天然没有这个坑,除非你自己改脚本只传部分字段)。
- `/api/ingest/reports` 的 `name` 决定新报告落哪个子目录(如 `agents/xxx`);已存在的报告
  按这个路径覆盖原内容——名字写错会在错的位置新建一份,而不是报错(只挡穿越写法,不挡
  "写错子目录"这种业务级失误),推之前自己核对一下路径。
- 密码类 secret 不打印、不入库、不写进任何报告或对话。
