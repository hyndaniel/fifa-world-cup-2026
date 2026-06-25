---
name: fifa-deploy
description: 把 WC 价值看板部署到 HK 实盘机(aws-hk)。触发词:fifa-deploy/部署/上线/deploy/推到 HK/更新看板/把改动同步到线上/看板部署。看板跑在 docker-compose 容器、代码烤进镜像 → 部署 = git reset 到日期式 tag + sudo docker compose build + up -d。依赖 remote-agent MCP(remote_exec)。
---

# fifa-deploy — 部署看板到 HK(Docker)

把 main 上一个**已 review+merge、打了日期式 tag 的 commit** 部署到 HK 实盘看板 `http://18.166.71.60:8000`。

**部署架构(已核 2026-06-25):** 看板跑在 **docker-compose** 容器 `fifa-world-cup-2026-app-1`(镜像 `wc-value-dashboard:latest`),**代码 `COPY` 进镜像**(见 Dockerfile),`network_mode: host`(直接占宿主 8000)。compose 只 bind-mount `./data`(SQLite)、`./reports`(报告 md)、`./config.toml`(配置含 secrets)→ 这三样 rebuild 不丢;且 `reports/*.md` 报告内容因挂卷**改了即生效、无需 rebuild**。**但 backend/frontend 代码烤进镜像 → 改代码必须 rebuild 镜像 + recreate 容器**;"改主机文件即生效"对代码**不适用**。

**权限:** remote-agent MCP 已加载(`remote_exec`,host=`aws-hk`)。HK 上是 `ec2-user`、**有免密 sudo**;repo `/opt/github/fifa-world-cup-2026` 的 `.git` 归 root → **git 和 docker 都要 `sudo`**。

## 流程

### 0. 打 tag(本地,在 main 上)
PR 已 merge 进 main 后:
```
git checkout main && git pull
python3 -m pytest -q
git tag fifa-deploy/<当天日期>-<N>          # 例 fifa-deploy/2026-06-26-1
git push origin fifa-deploy/<当天日期>-<N>
```
**先 pytest 绿、再打 tag 发布**(红就别 push tag)。

### 1. HK rebuild + recreate — remote_exec(host aws-hk)
```
cd /opt/github/fifa-world-cup-2026
sudo git fetch origin --tags
sudo git reset --hard fifa-deploy/<当天日期>-<N>     # host 文件 = docker build 上下文
sudo git describe --tags                              # 确认 == 该 tag
sudo docker compose build                             # 把新代码烤进镜像
sudo docker compose up -d                             # recreate 容器(network_mode:host → 几秒 downtime)
```

### 2. 验证
```
CID=$(sudo docker ps -qf name=fifa-world-cup-2026-app)
sudo docker exec $CID grep -c mtime /app/backend/reports.py    # 核容器内跑的是新代码(随改动换标志串)
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000/   # 期望 200
```
让用户 hard-refresh(Cmd+Shift+R)眼检前端。`/api/reports` 返回 `Not authenticated` 是正常的(API 要 Basic auth)。

## 红线
- 只部署 main 上打了 tag 的 commit(已 review+merge),不部署 feature 分支或 main 漂移的 HEAD。
- 改代码**必须 rebuild + recreate**——代码烤进镜像,只 `git reset` 主机不 rebuild = 容器还是旧代码,白改。
- `data`/`config.toml`/`reports` 是 bind-mount,rebuild 不丢;别删/覆盖它们。
- 同机还有别的容器(teslamate / csy-distill / lets-drink 等),`docker compose` 只动本 repo 的 `app`;别误杀其它容器。
