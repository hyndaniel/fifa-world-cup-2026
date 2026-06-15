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

## 文档

设计文档见 [`docs/superpowers/specs/2026-06-15-wc-value-dashboard-design.md`](docs/superpowers/specs/2026-06-15-wc-value-dashboard-design.md)。

原型：[`prototype/wc_value.py`](prototype/wc_value.py)（足彩×Poly 价值对比，已验证）。
