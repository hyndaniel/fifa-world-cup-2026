---
name: 部署看板
description: 把 WC 价值看板部署到 HK 实盘机(aws-hk)。触发词:部署看板/部署/上线/deploy/推到 HK/更新看板/把改动同步到线上/看板部署。前端改动 git pull 即生效不用重启;后端改动需先确认进程再重启。依赖 remote-agent MCP(remote_exec 可用)。
---

# 部署看板到 HK

把本地 `main` 的改动同步到 HK 实盘看板 `http://18.166.71.60:8000`。

**前置**:remote-agent MCP 已加载(`remote_exec` 可用,host=`aws-hk`)。不可用就先 ToolSearch 加载 `mcp__remote-agent__remote_exec`、`remote_health`,并 `remote_health aws-hk` 确认在线。

**关键事实(已核)**:
- HK 仓库:`/opt/github/fifa-world-cup-2026`,本地分支 `master`(内容 == origin/main)。
- 前端经 `backend/web.py` 的 `StaticFiles(frontend/)` 磁盘静态服务 → 纯前端改动 `git pull` 即生效、不用重启,浏览器刷新即见。
- HK 的 git 有 dubious-ownership 报错,需 `safe.directory`(幂等,加一次即可)。
- `data/wc.db`、`config.toml` 是 gitignored,`pull` 不会动它们。

## 流程

### 0. 本地先推
`git push origin main`

### 1. HK 拉取(任何部署都做)— 经 remote_exec(host aws-hk)
```
cd /opt/github/fifa-world-cup-2026
git config --global --add safe.directory /opt/github/fifa-world-cup-2026
git -c safe.directory='*' pull origin main
```
预期 fast-forward 到最新 commit。HK 上 `._.DS_Store` 之类未跟踪 junk 不挡快进。`safe.directory` 那行幂等、治 dubious ownership。

### 2A. 纯前端改动(frontend/*)→ 完事
StaticFiles 每次请求读磁盘,无需重启。让用户 hard-refresh(Cmd+Shift+R)看新效果。
验证「改的内容已上 HK」:`grep -n '<你这次改的标志串>' frontend/app.js`(在 HK 仓库目录下)。

### 2B. 后端改动(backend/*.py)→ 需重启 ⚠️ 别盲杀进程
HK 上跑着两个 python 进程(`uvicorn main:app --port 8000` 与 `python -m backend.run`),哪个真服务 8000 尚未完全确认。杀错会把实盘看板打挂。重启前先查清:
```
ps -eo pid,etime,cmd | grep -iE 'uvicorn|backend.run' | grep -v grep
ss -ltnp | grep ':8000'
ls -l /proc/<pid>/cwd
```
确认 8000 的 owner pid + 其 cwd = /opt/github/fifa-world-cup-2026 后,按其原 cmd/cwd 重启(kill 旧 + `nohup <原启动命令> >> 日志 2>&1 &`)。拿不准就停下问用户、别赌——这是实盘。

## 验证(部署后)
`curl -s -o /dev/null -w '%{http_code}\n' http://18.166.71.60:8000/`(期望 200);前端改动再让用户 hard-refresh 眼检。

## 红线
- 后端重启拓扑没确认前绝不盲杀进程(实盘看板)。
- 只 `git pull` 同步,不在 HK 上手改代码(改本地 → 推 origin → HK 拉)。
- 部署前确认本地改动已 commit + push,别把未提交的本地态当"已部署"。
