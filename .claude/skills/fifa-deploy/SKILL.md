---
name: fifa-deploy
description: 把 WC 价值看板部署到 HK 实盘机(aws-hk)。触发词:fifa-deploy/部署/上线/deploy/推到 HK/更新看板/把改动同步到线上/看板部署。部署一个「钉死的、已验证的 commit」(非 main 最新),前端改动 reset 即生效不重启,后端改动需先确认进程再重启。依赖 remote-agent MCP(remote_exec 可用)。
---

# fifa-deploy — 部署看板到 HK

把一个**钉死的、已验证的 commit** 部署到 HK 实盘看板 `http://18.166.71.60:8000`。

**前置**:remote-agent MCP 已加载(`remote_exec`,host=`aws-hk`)。不可用先 ToolSearch 加载 `mcp__remote-agent__remote_exec`/`remote_health`,`remote_health aws-hk` 确认在线。

**关键事实(已核)**:
- HK 仓库 `/opt/github/fifa-world-cup-2026`,本地分支 `master`。
- 前端 `StaticFiles(frontend/)` 磁盘静态服务 → 纯前端改动同步后即生效、不用重启。
- HK git 有 dubious-ownership,需 `safe.directory`(幂等)。`data/wc.db`、`config.toml` 是 gitignored,部署不动它们。

## ⚠️ 为什么部署「钉死的 SHA」而不是「main 最新」
本仓库**多 session/agent 直推 main、无 PR 闸**。`git pull main` 会把**别人在飞的半成品**一起上线;你 push 与 HK 拉之间别人再推,实际部署的 commit 还会漂。**所以永远部署你亲手验过的那个固定 SHA。**

## 流程

### 0. 定准要部署的 commit(本地)
```
git push origin main            # 你的改动必须已 commit + push
git rev-parse HEAD              # ← 记下这个 SHA, 这就是要部署的目标
python3 -m pytest -q            # 绿
git log --oneline origin/main -8   # 眼检: 这段里有没有别的 session 在飞的意外提交?
```
若看到不认识/没做完的提交混在你的 SHA 之前,别带它们上线 —— 要么等其完成,要么把目标 SHA 钉在你这条干净的提交上(下一步用该 SHA)。

### 1. HK 钉到该 SHA(任何部署都做)— remote_exec(host aws-hk)
```
cd /opt/github/fifa-world-cup-2026
git config --global --add safe.directory /opt/github/fifa-world-cup-2026
git -c safe.directory='*' fetch origin
git -c safe.directory='*' reset --hard <第0步记下的 SHA>
git -c safe.directory='*' rev-parse --short HEAD     # 确认 == 目标 SHA
```
`reset --hard <SHA>` 钉到精确 commit → 部署确定性、免疫并发推送。HK 无本地代码改动(data/config gitignored,untracked junk 不受影响)。

### 2A. 纯前端改动(frontend/*)→ 完事
StaticFiles 每请求读磁盘,无需重启。让用户 hard-refresh(Cmd+Shift+R)看新。
验证已上 HK:`grep -n '<你这次改的标志串>' frontend/app.js`(HK 仓库目录下)。

### 2B. 后端改动(backend/*.py)→ 需重启 ⚠️ 别盲杀进程
HK 跑着两个 python 进程(`uvicorn main:app --port 8000` 与 `python -m backend.run`),哪个真服务 8000 **尚未完全确认**,杀错会把实盘打挂。先查清:
```
ps -eo pid,etime,cmd | grep -iE 'uvicorn|backend.run' | grep -v grep
ss -ltnp | grep ':8000'
ls -l /proc/<pid>/cwd        # 确认 cwd = /opt/github/fifa-world-cup-2026
```
确认 8000 的 owner pid + cwd 后再按原 cmd 重启(kill 旧 + `nohup <原启动命令> >> 日志 2>&1 &`)。**拿不准就停下问用户、别赌。**

## 验证(部署后)
`curl -s -o /dev/null -w '%{http_code}\n' http://18.166.71.60:8000/`(期望 200);前端改动让用户 hard-refresh 眼检。

## 红线
- 永远部署**钉死的已验证 SHA**,不部署 main 漂移的 HEAD。
- 后端重启拓扑没确认前**绝不盲杀进程**(实盘看板)。
- 只 git 同步,不在 HK 手改代码(改本地 → 推 → HK reset 到 SHA)。
