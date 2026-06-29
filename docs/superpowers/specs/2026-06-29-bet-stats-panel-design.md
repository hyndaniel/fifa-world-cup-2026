# 看板「下注统计」面板 — 设计文档

**日期**：2026-06-29
**作者**：wc 项目（brainstorming 产出）
**状态**：待实现

## 1. 背景与问题

下注台账 `reports/盘口下注复盘.md` 由 wc-bet（下注决策层）手维护，记录了本届世界杯
6 个比赛日（6.24→6.29）的全部推荐腿与实购票：每条腿的赔率/分档/value、✅/❌ 命中、
每日复盘统计、实购票的注码与实际盈亏。

**问题**：这些数据只躺在 markdown 里，从未被聚合，也从未推上 HK 看板。看板当前以
「每场决策卡 + 赔率面板」为中心，唯一与下注相关的是一个「钱包 A/B 盈亏/ROI」组件
（`frontend/app.js` 的 `renderLedger`），但它读 DB `bets` 表，而该表 **0 行**——从建好起
就是空的，没人往里写过票。所以看板**没有**可用的下注记录/盈亏统计。

用户诉求：在看板上展示「下注记录 + 盈亏情况」，把本届所有下过的腿总结统计。

## 2. 口径决策（已与用户确认）

台账里混着**两类「下过的腿」**，统计逻辑完全不同，已确认**两类都做、分两块**：

- **A. 推荐腿战绩**：每场我们标的「最不亏 / 守下限 / best_leg」，带 ✅/❌ 命中，覆盖
  6.24→6.29 每场。是『模型/盘口判断准不准』的跑分，**多数是假设性**（没真下钱）。
- **B. 实购票盈亏**：真金白银下的票（6.24 五张共 −232 元、6.26 6场3串1 −60 元），有
  注码 / 回报 / 真实盈亏。

**已确认的另外两个口径：**
- **数据源**：手工把 6 天台账抽成结构化 JSON（`data/bet_ledger.json`）。markdown 仍是台账
  真源；数据量小（6 天），不写脆弱的 markdown 解析器。
- **旧钱包组件**：直接替换掉那个从未填过数据的空「钱包 A/B」组件，看板只留一个下注区。

## 3. 已知诚实锚点（焊进面板，不可粉饰）

这块是硬护栏，和项目北极星一致（实证理解、非买票）。面板**不能**看着像「我们在赢钱」：

- **实购票合计：6 张票 / 总投注 292 元 / 总盈亏 −292 元 / ROI −100% / 0 张中奖。**
  （6.24 五张全归零 −232、6.26 6场3串1 颗粒无收 −60。）红字摆明，配「足彩长期 -EV」。
- **🟢green 推荐腿迄今 = 0**：6 天台账每日都明写「全场对聪明钱无 🟢green」。命中几乎全是
  🔴/🟡 的**基础概率红利**（押悬殊热门本就高命中），**非模型 edge**。
- 命中率旁固定挂一行：**「命中 ≠ 有价值」**。

## 4. 架构

三层，沿用现有 FastAPI + 静态前端 + JSON 数据文件的模式：

```
data/bet_ledger.json   ← 手整结构化数据（真源仍是 markdown 台账）
        │  读
backend/bet_stats.py    ← 聚合逻辑（纯函数，读 JSON → 算 summary）
        │  暴露
GET /api/bets/summary   ← backend/web.py 新增只读端点
        │  fetch
frontend/app.js         ← renderBetStats() 渲染「📒 下注统计」区（替换 renderLedger）
```

各单元职责单一、边界清晰：
- `bet_ledger.json`：纯数据，无逻辑。
- `bet_stats.py`：纯聚合，输入 JSON、输出 summary dict，可独立单测（不碰 DB、不碰网络）。
- `/api/bets/summary`：薄端点，调 `bet_stats.build_summary()` 返回。
- `renderBetStats()`：纯展示，不算账（后端算好）。

## 5. 数据模型 `data/bet_ledger.json`

```jsonc
{
  "updated": "2026-06-29",
  "recommendations": [
    // 每条 = 一条「最不亏/守下限/best_leg」推荐腿
    {
      "date": "2026-06-24",
      "match": "南非vs韩国",
      "leg": "主胜(南非)",
      "odds": 5.65,
      "tier": "yellow",          // green | yellow | red
      "value_poly": 0.972,
      "result": "win",           // win | loss | pending
      "settled": true
    }
    // ... 6.24→6.28 每场已结；6.29 074/075/076 三条 settled:false(pending)
  ],
  "tickets": [
    // 每条 = 一张实购票（真金白银）
    {
      "date": "2026-06-24",
      "who": "朋友B",
      "type": "4串1复式(6腿)",
      "stake": 82,
      "legs_hit": "2/6",
      "pnl": -82,
      "settled": true
    }
    // 6.24 五张（楼主注1 −30 / 注2 −30 / 朋友A票1 −50 / 朋友A票2 −40 / 朋友B −82）
    // 6.26 用户 6场3串1（stake 60 / 1/6 腿 / pnl −60）
  ]
}
```

**去重口径**：6.24 有 19:52 与 23:53 两轮对同几场的推荐（re-rank）。结构化时**只取每个
比赛日最终那一轮的推荐单关**，避免同场重复计数。每条 `tier` 用赛前最终分档。

## 6. 后端 `backend/bet_stats.py` + 端点

`build_summary(ledger: dict) -> dict` 产出：

```jsonc
{
  "recommendations": {
    "total": 14, "settled": 11, "win": 7,          // 数字示意，实抽时定
    "hit_rate": 0.636,                              // win / settled
    "by_tier": {                                    // 诚实拆档
      "green":  { "total": 0, "win": 0 },
      "yellow": { "total": 3, "win": 1 },
      "red":    { "total": 11, "win": 6 }
    },
    "by_date": [ { "date": "2026-06-24", "win": 1, "settled": 3 }, ... ],
    "hypo_unit_pnl": -1.2                           // 假设每条 best_leg 投 1 注的累计单位盈亏
  },
  "tickets": {
    "count": 6, "won": 0,
    "total_stake": 292, "total_pnl": -292, "roi": -1.0,
    "rows": [ { "date","who","type","stake","legs_hit","pnl" }, ... ]
  }
}
```

`hypo_unit_pnl` 必须在前端标注「**假设**每条 best_leg 投 1 注，非真钱」。

端点：`GET /api/bets/summary`（`dependencies=[Depends(auth_dep)]`，与现有端点一致），
读 `data/bet_ledger.json` → `build_summary` → 返回。文件缺失时返回空 summary（容错，不 500）。

## 7. 前端 `renderBetStats()`（替换 `renderLedger`）

- 新增可折叠「📒 下注统计」区，放在决策卡区下方（原钱包组件位置）。
- **上半区 · 推荐腿战绩**：大数命中率（X/Y）+ 分档三色条（🟢0 / 🟡 / 🔴）+ 按日小表 +
  假设单位盈亏（带「假设非真钱」标注）+ 固定「命中≠有价值」诚实行。
- **下半区 · 实购票盈亏**：逐票表（日期/谁/票型/注码/命中/盈亏）+ 合计行
  （投 292 / 盈亏 **−292**(红) / ROI −100% / 0 中）+「足彩长期 -EV」标注。
- 移除 `renderLedger`、`#wallet-a-rows`/`#wallet-b-rows` 相关 DOM 与对 `s.ledger` 的依赖。
  DB `bets` 表与 `POST /api/bets` **保留不动**（以后若要手动记真钱票仍可用）。

## 8. 维护（going forward）

wc-bet 每天落盘 `reports/盘口下注复盘.md` 后，顺手往 `data/bet_ledger.json` 追加当日
推荐腿（赛前 `settled:false`，赛后回填 `result`）。把这条写进 wc-bet 的台账约定
（`.claude/agents/wc-bet.md` §维护台账，属用户授权范围）。

## 9. 测试

- `tests/` 加 `test_bet_stats.py`：喂构造 ledger，断言 `build_summary` 的命中率/分档/
  合计/ROI 正确；断言 green=0、pending 不计入 settled、ticket pnl 合计 = −292。
- `/api/bets/summary` 走 `tests/test_web.py` 既有 TestClient 模式加一个用例。

## 10. 非目标（YAGNI）

- **不**写 markdown 自动解析器（数据量小，手整更稳）。
- **不**灌 DB / 不点亮旧钱包组件 / 不改 wc-bet 写 DB 流程。
- **不**做真钱记账表单、不做实时下注录入（保留 POST /api/bets 但不在本次接前端）。
- **不**碰决策卡 / 赔率面板 / 价值雷达等现有区块。
