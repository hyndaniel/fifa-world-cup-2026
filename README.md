# fifa-world-cup-2026 — WC Value Dashboard

双模式世界杯下注**价值看板**：拉中国体彩竞彩(足彩) + Polymarket(聪明钱)实时盘口，
按对阵配对算价值，标出真 +EV / 接近公允的点；叠加首发/伤停(LLM)与两平台分歧信号。

部署于 AWS-HK，手机浏览器实时访问。

## 诚实定位

- **不是**稳定盈利/回血机器——足彩长期 -EV，没有工具能改变。
- **不**自动下注、**不**碰资金、**不**构成投资建议。
- 价值 = 把"几乎必亏"改善为"大致打平、偶尔薄赚 + 守住下限 + 不上头"，并用数据保持清醒。

## 双模式

- **A 价值单关**：只推 value≥阈值的单关 + 小注 + 记账（赚小钱）。
- **B 清醒彩票**：博高赔串关时用最不亏的腿凑，但强制展示真实命中率/期望（博一把、不自欺）。

## 本地开发

```bash
pip install -r requirements.txt
python3 -m pytest -q                 # 全量单测 (fixture, 不联网)
python3 -m backend.run               # 起服务: http://localhost:8000  (本地 IP 足彩可直连)
```

## 部署到 AWS-HK

HK 机房 **Polymarket 直连可达**，但 **足彩被 EdgeOne WAF 按 IP 拦**(数据中心 IP 中招)，
需让足彩流量走一个**出口能过 WAF 的住宅代理**(如自购 JMS/vmess 节点)。

### 1. (一次性) HK 上准备足彩代理 ⚠️ 需你手动执行

足彩 client 支持 `[zucai] proxy = "socks5://..."`。在 HK 上跑一个 xray，
vmess 出站→你的住宅节点，本地 SOCKS5 入站(如 127.0.0.1:10808)。
xray 配置 + 启动由你自行部署(在共享主机装隧道属你的运维决定，工具侧只读 config)。
Polymarket 仍直连，**只有足彩走此代理**。

### 2. 部署

```bash
# 在 /opt/github 下
git clone <repo> && cd fifa-world-cup-2026     # 或 git pull
cp config.example.toml config.toml             # 编辑: 改 password、填 [zucai] proxy
docker compose up -d --build
curl -u user:<password> localhost:8000/api/state    # 冒烟: 应返回 value_radar
```

手机浏览器开 `http://<HK-IP>:8000`(建议加 Nginx + HTTPS + basic-auth)。

## 文档

- 设计文档：[`docs/superpowers/specs/2026-06-15-wc-value-dashboard-design.md`](docs/superpowers/specs/2026-06-15-wc-value-dashboard-design.md)
- 实现计划：[`docs/superpowers/plans/2026-06-15-wc-value-dashboard-v1.md`](docs/superpowers/plans/2026-06-15-wc-value-dashboard-v1.md)
- 原型：[`prototype/wc_value.py`](prototype/wc_value.py)（足彩×Poly 价值对比，已验证）

## 状态

v1 后端 + 前端完成，26 单测通过 (fixture 级)。真·端到端(实连两平台)待 HK 部署 + 足彩代理就位后验证。
v2: 两平台分歧告警 · LLM 首发叠加 · 爽一把构造器 · 追损检测。
