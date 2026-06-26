---
name: wc2026-value-betting-tool
description: 足彩↔Polymarket 价值对比工具 wc_value.py + 两个数据接口 + 代理用法
metadata: 
  node_type: memory
  type: project
  originSessionId: 7abbfede-a881-4a98-8cac-5753eed61ff1
---

iCloud 项目目录有原型 `wc_value.py`：拉中国体彩竞彩(足彩) + Polymarket(聪明钱)盘口，按对阵自动配对，算 `value=足彩欧赔×Poly概率`，标红 +EV 可投点。

**2026-06-15 演进**：正升级为部署在 AWS-HK 的**双模式实时看板**(WC Value Dashboard)。独立 git 仓库 `/Users/heyining/Daniel/WorkSpace/fifa-world-cup-2026`(**不在 iCloud**，避免 iCloud+git 互掐)，设计文档 `docs/superpowers/specs/2026-06-15-wc-value-dashboard-design.md`(已批准待写实现计划)。诚实定位:非"回血机器"，是避坑+纪律+稀有薄边+记账。双钱包(A价值单关/B清醒彩票)。手机走平台看预测(方案A:预测文件以仓库为单一源)。**de-vig 关键点(spec §5.1)**:只去 Poly 的水(归一化到100%)、足彩赔率保持原值;派生桶(让分/O-U相减)去水不干净,薄边去水后常翻到接近0。`wc_value.py` 逻辑将被后端吸收。

**Why:** 用户做世界杯投注分析，反复要对比足彩(抽水~13%)和 Polymarket(抽水1-2%，更接近真实概率)，挑差价/+EV。已沉淀成可复用脚本。

**How to apply:**
- **Polymarket 接口**(geo-blocked，必须走代理)：`gamma-api.polymarket.com/events?slug=<slug>`(单场) 或 `?closed=false&tag_id=102232&limit=100&offset=N`(全世界杯，tag=`fifa-world-cup`)。slug 格式 `fifwc-<主><客>-日期`，队码 FIFA/ISO 混用(西esp 佛cvi 比bel 沙ksa 乌**ury** 英eng 克**hrv**)。让分/大小球在 `<slug>-more-markets`。价格=概率(0-1)。
- **代理**：本机 SOCKS5 `127.0.0.1:7898`。curl 用 `--socks5-hostname 127.0.0.1:7898`(注意 zsh 不对未引用变量分词，proxy flag 别塞变量)。直连 Polymarket 被墙(HTTP 000)。Bash 联网要 `dangerouslyDisableSandbox:true`。
- **足彩接口**(国内直连，不走代理)：`webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=had,hhad,ttg&channel=c`，要 iPhone UA + `Referer: https://m.sporttery.cn/`。返回 had(胜平负)/hhad(让球,含goalLine)/ttg(总进球s0..s7)。
- **让球机制**：足彩让球是3way(胜/平/负，无退本)。让-k 主胜=Poly主净胜≥k+1=home_cover[k+0.5]；让-k 平=net 恰好k；受让+k 用 away_cover。映射已写进脚本。
- **核心规律**：足彩对**热门/大球系统性高估**(value 0.83-0.89)，价值躲在**平/冷门/特定总进球**。任何全热门串=重度-EV(6串1全热门≈-60%)。
- **陷阱**：总进球高分桶(6/7+)若 Poly 缺对应 O/U 高线，概率会被高估成假+EV，脚本已自动跳过。薄边(value 1.0~1.03)在 Poly 抽水噪声内，算接近公允非铁+EV。
- **用法**：`python3 wc_value.py [队名...]`。改 `PROXY`/`CN2EN`(已覆盖48队)/`HL` 阈值。每次开盘前重跑，盘口实时变。

**2026-06-15 晚 v1 建成 + 部署进度**：
- **代码仓库**：`/Users/heyining/Daniel/WorkSpace/fifa-world-cup-2026`(本地, 未推远端, 5 commit 到 4c742e1)。v1 后端+前端完整, **26 单测全绿**(fixture级), `/api/state` `/api/reports` 冒烟过。模块: config/db/models/devig/value(地基) + sporttery(足彩,**支持 [zucai] proxy**)/polymarket+teammap/poller/state/web+reports/前端/run入口/docker。
- **部署目标**: AWS-HK, SSH 别名 **`ssh AWS`**(18.166.71.60, ap-east-1, ec2-user, .pem 在 ~/Daniel/AWS/), 部署到 `/opt/github`。docker/git/py 齐, 磁盘 **91% 满(剩2.8G,紧)**。
- **关键阻塞——足彩被 EdgeOne WAF 按 IP 拦**: Polymarket 从 HK 直连 200, 但足彩从 AWS 机房 IP 返 **567(WAF反爬页)**。解法: HK 跑 xray, vmess 出站→用户 JMS 日本节点(`c65s4.portablesubmarines.com:6619`, uuid `233a9871-7dd2-49d0-953f-e59e98054ec9`, aid0/tcp/无tls), 本地 SOCKS5 127.0.0.1:10808, config.toml 填 `[zucai] proxy="socks5://127.0.0.1:10808"`。**装隧道这步用户自己做**(安全策略禁止 AI 在共享主机装代理隧道)。
- **待办(到家继续)**: ① HK xray 起来(systemd 默认读 `/usr/local/etc/xray/config.json` 不是 /opt/xray, 用户卡在 10808 connection refused)→验证足彩过墙 ② cp config.example.toml config.toml 填 proxy+密码 ③ rsync/推码到 /opt/github + `docker compose up -d --build` ④ 真端到端冒烟 ⑤ 可选推 GitHub 拿链接。
- **remote-agent MCP**: 已 `claude mcp add` 进 ccss 账号配置(`CLAUDE_CONFIG_DIR` 指向账号目录), `claude mcp list` ✔Connected, 本会话已加载, 可用 `remote_exec/remote_upload/remote_health` 直接操作 HK。
**2026-06-15 夜 已部署上线 HK**：
- **看板上线**: http://18.166.71.60:8000 (安全组放行 8000, 外网可达)。登录用户名 **admin**, 密码=强随机已设(存在 HK 的 `/opt/github/fifa-world-cup-2026/config.toml` 的 `password`, 默认 change-me 已弃用; 改后 `docker compose restart`)。
- **现状**: 容器 `fifa-world-cup-2026-app-1` 在 `/opt/github/fifa-world-cup-2026`(host 网络模式), /api/state /api/reports 前端全 200, poller 运行。**足彩仍 567 失败**(proxy 空, xray 未通) → value_radar 暂空; Polymarket 侧就绪。
- **部署方式**: `ssh AWS`(ec2-user, docker 需 sudo) → scp tar(116K) → `sudo docker compose up -d --build`。**remote-agent 的 aws-hk 守护(8989)离线**, 走 SSH。SSH 跨境抖, 用 `-o ServerAliveInterval=5 -o ConnectTimeout=25` 才稳。
- **部署中修的 bug**: macOS tar 带入 AppleDouble `._*.md` → reports.py glob 当报告读 → UTF-8 500。已改 reports.py 跳点文件 + catch UnicodeDecodeError, 打包用 `COPYFILE_DISABLE=1 tar --exclude='._*'`。已提交。
- **待用户到家做**: ① 修 xray(systemd 读 `/usr/local/etc/xray/config.json` 是空{}, 真配置在 `/opt/xray/config.json`, `cp` 过去+`systemctl restart xray`, 验证 10808 监听+足彩过墙——**此步安全策略禁 AI 代做, 用户手动**) ② `sed -i 's|^proxy = .*|proxy = "socks5://127.0.0.1:10808"|' config.toml` + `docker compose restart` ③ 等一轮 value_radar 出数。
- **安全待办**: 安全组把 8000 限到用户 IP(现对全网开) / 加 Nginx+HTTPS。
- **v2 待做**: 两平台分歧告警 · LLM 首发叠加 · 爽一把构造器 · 追损检测。诚实定位/双钱包见上文。

相关：[[wc2026-prediction-workflow]] [[wc2026-prediction-status]]
