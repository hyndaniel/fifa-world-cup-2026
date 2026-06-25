---
name: fifa-deploy
description: 把 WC 价值看板部署到 HK 实盘机(aws-hk)。触发词:fifa-deploy/部署/上线/deploy/推到 HK/更新看板/把改动同步到线上/看板部署。部署 main 上打的一个「日期式 tag」(已 review+merge 的 commit),前端改动 reset 即生效不重启,后端改动需先确认进程再重启。依赖 remote-agent MCP(remote_exec 可用)。
---

# fifa-deploy — 部署看板到 HK

把 main 上一个**已 review+merge、打了 tag 的 commit** 部署到 HK 实盘看板 `http://18.166.71.60:8000`。

**前提**:代码走 PR → review → merge main,所以 **main 是随时可部署的干净线**;部署单位是 main 上的一个 tag(不是 main 漂移的 HEAD、不是某个 feature 分支)。

**依赖**:remote-agent MCP 已加载(`remote_exec`,host=`aws-hk`)。不可用先 ToolSearch 加载 `mcp__remote-agent__remote_exec`/`remote_health`,`remote_health aws-hk` 确认在线。

**关键事实(已核)**:
- HK 仓库 `/opt/github/fifa-world-cup-2026`,本地分支 `master`。
- 前端 `StaticFiles(frontend/)` 磁盘静态服务 → 纯前端改动同步后即生效、不用重启。
- HK git 有 dubious-ownership,需 `safe.directory`(幂等)。`data/wc.db`、`config.toml` gitignored,部署不动它们。

## 流程

### 0. 打 tag(本地,在 main 上)
确认要部署的 PR 已 merge 进 main。给该 commit 打日期式 tag(N = 当天第几次部署):
```
git checkout main && git pull
python3 -m pytest -q
git tag fifa-deploy/<当天日期>-<N>
git push origin fifa-deploy/<当天日期>-<N>
```
例:`fifa-deploy/2026-06-26-1`。**先 pytest 绿、再打 tag 发布**(测试红就别 push tag,免得不可部署的 tag 上了 origin)。

### 1. HK 钉到该 tag — remote_exec(host aws-hk)
```
cd /opt/github/fifa-world-cup-2026
git config --global --add safe.directory /opt/github/fifa-world-cup-2026   # 一次性, 幂等; 治 dubious ownership
git fetch origin --tags
git reset --hard fifa-deploy/<当天日期>-<N>
git describe --tags
```
`reset --hard <tag>` 钉到不可变 tag → 部署确定、有"哪天发了哪个"的历史。最后一行确认 HEAD == 该 tag。HK 无本地代码改动(data/config gitignored,untracked junk 不影响)。

### 2A. 纯前端改动(frontend/*)→ 完事
StaticFiles 每请求读磁盘,无需重启。让用户 hard-refresh(Cmd+Shift+R)看新。
验证已上 HK:`grep -n '<你这次改的标志串>' frontend/app.js`(HK 仓库目录下)。

### 2B. 后端改动(backend/*.py)→ 需重启 ⚠️ 别盲杀进程
HK 跑着两个 python 进程(`uvicorn main:app --port 8000` 与 `python -m backend.run`),哪个真服务 8000 尚未完全确认,杀错会把实盘打挂。先查清:
```
ps -eo pid,etime,cmd | grep -iE 'uvicorn|backend.run' | grep -v grep
ss -ltnp | grep ':8000'
ls -l /proc/<pid>/cwd
```
确认 8000 owner pid + cwd = /opt/github/fifa-world-cup-2026 后,按原 cmd 重启(kill 旧 + `nohup <原启动命令> >> 日志 2>&1 &`)。拿不准就停下问用户、别赌。

## 验证(部署后)
`curl -s -o /dev/null -w '%{http_code}\n' http://18.166.71.60:8000/`(期望 200);前端改动让用户 hard-refresh 眼检。

## 红线
- 只部署 main 上打了 tag 的 commit(已 review+merge),不部署 feature 分支或 main 漂移的 HEAD。
- 后端重启拓扑没确认前绝不盲杀进程(实盘看板)。
- 只 git 同步,不在 HK 手改代码(改本地 → PR → merge → tag → HK reset 到 tag)。
