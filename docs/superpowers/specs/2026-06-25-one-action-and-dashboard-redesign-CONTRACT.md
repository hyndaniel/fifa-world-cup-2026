# 实现契约：一键跑日 + 看板重定心

配套设计文档：`2026-06-25-one-action-and-dashboard-redesign-design.md`。本文件锁定三组件
（A skill / B 前端 / C 后端）共同依赖的接口形状与边界，是实现期的单一事实源。

## 0. 数据架构（关键，决定 join 在哪做）

两个独立 SQLite 库：

- **本地 `.cache/odds_cache.db`**（仅本地 Mac）：`odds_cache`(盘口快照) · `v1_predictions`(probs+score_pred) · `v2_predictions`(prediction_json) · `match_results`。键 = `match_key`（字符串，如 `"韩国 vs 南非"`）。
- **看板 `data/wc.db`**（部署在 HK）：`matches`(键 zucai_num, 含 home_cn/away_cn/ko_bj/cutoff_bj/status) · `value_points` · `watchlist` · `bets` · `enrich` · `app_config`。

**铁则：所有跨库 join 由本地 `/跑今天` skill 完成。** 看板**不认识** `match_key`、不读 `.cache/odds_cache.db`。skill 把 v1 比分 + v2 概率 + 价值结论汇成"决策对象"，POST 给看板；看板**原样存储 + 原样回吐**。

## 1. 决策对象（Decision，A/B/C 共同的单元）

```jsonc
{
  "match_key": "韩国 vs 南非",        // 必填: upsert 主键(替换语义)
  "home_cn": "韩国", "away_cn": "南非", // 必填
  "home_flag": "🇰🇷", "away_flag": "🇿🇦", // 选填(emoji)
  "ko_bj": "6.27 02:00",              // 选填: 北京时间开球(展示用字符串)
  "ko_et": "ET 6.26 14:00",           // 选填
  "cutoff_bj": "01:00",               // 选填
  "status": "Selling",                // 选填: Selling/Stopped/...

  "v1": {                              // 选填整块; football-match-predictor 产
    "score": "0-1",                    // 比分预测字符串
    "rationale": "韩平即出线",          // 一句依据/出线诉求
    "probs": {"h": 30, "d": 30, "a": 40} // 选填 胜平负 %(和≈100)
  },
  "v2": {                              // 选填整块; wc-forecaster-v2 产
    "probs": {"h": 38, "d": 30, "a": 32}, // 胜平负 %
    "reliability": "乱",                // 稳/中/乱
    "scenarios": ["默契平"],            // 剧本标签数组(可空)
    "deviated": true                    // 选填: 是否对基线偏离过
  },
  "value": {                           // 选填整块; odds-value-analyst 产
    "verdict": "该场别碰",             // 一句价值结论
    "best_leg": {                      // 选填: 最不亏腿
      "market": "hhad", "outcome": "a",
      "desc": "南非 +0.5", "flag": "yellow", "ev_pct": -1.2
    },
    "legs": [                          // 选填: 完整盘口明细(展开用)
      {"market": "had", "outcome": "a", "desc": "南非胜",
       "zucai_odds": 3.10, "poly_prob_devig": 32.0, "ev_pct": -0.8, "flag": "yellow"}
    ]
  },

  "updated_at": "2026-06-25T16:00:00+08:00" // 选填: 该卡生成时间
}
```

字段缺失约定：任一块（v1/v2/value）可整块缺失 → 前端该区显示"—"或"未出"，**不崩**。`probs` 可缺。`flag` ∈ {green,yellow,red,skip}，沿用 `flagBadge` 语义（🟢真+EV/🟡接近公允/🔴明显-EV/⚪跳过；`value.py` 实产四档，red 别折叠成 skip）。

## 2. 看板端点（C 实现，FastAPI，沿用 `auth_dep` 鉴权）

### `POST /api/ingest/predictions`
- body: `{"decisions": [<Decision>, ...], "ts": "<iso, 选填>"}`
- 行为：对每个 Decision 按 `match_key` upsert 进新表 `decisions`（替换语义）。**不**删除未在本批出现的旧 decision（保留历史卡，避免误清）。
- 返回：`{"accepted": true, "n": <写入条数>}`
- 校验：缺 `match_key` 的条目跳过并计入 `skipped`；返回 `{"accepted": true, "n": .., "skipped": ..}`。

### `GET /api/decisions`
- 返回：`{"ts": "<iso, 服务端当前北京时间>", "decisions": [<Decision>, ...]}`
- 排序：按 `ko_bj` 升序（字符串排序即可，格式 `"M.D HH:MM"`；缺 ko_bj 的排末尾）。

### `POST /api/refresh`（价值"重抓+刷新"按钮用）
- 行为：取最新一条 `source='zucai'` 的 `odds_snapshots.payload_json`，在后台线程经 `poller.poll_once(db, cfg, zucai_fetch=lambda: <payload>)` 重跑（poll_once 内部会**重新直连 Poly** → 刷新去水概率 → 重算 value_points，治"陈旧 Poly 假黄档")。
- 无 zucai 快照时：`{"accepted": false, "reason": "no zucai snapshot"}`，不抛错。
- 有则即时返回 `{"accepted": true}`（异步重算，前端随后重拉 `/api/state` + `/api/decisions`）。

## 3. 看板存储（C 实现，`backend/db.py`）

新表（加进 `SCHEMA`）：
```sql
CREATE TABLE IF NOT EXISTS decisions (
    match_key TEXT PRIMARY KEY,
    ts TEXT,
    payload_json TEXT
);
```
新方法：
- `save_decisions(self, decisions: list) -> int`：逐条 `INSERT ... ON CONFLICT(match_key) DO UPDATE`，存整个 Decision dict 为 `payload_json`，`ts=_now_bj()`；返回写入条数；跳过无 match_key 的。
- `get_decisions(self) -> list`：取全部，`json.loads(payload_json)`，按 `ko_bj` 升序（缺末尾）返回 Decision dict 列表。
- `latest_snapshot(self, source: str) -> dict | None`：取该 source 最新一条 `odds_snapshots`，返回 `json.loads(payload_json)`（无 → None）。供 `/api/refresh` 用。

Pydantic（`backend/web.py`，宽松即可，未知字段透传）：用 `dict`/`list` 接 body 或定义 `PredictionsIn(BaseModel): decisions: list = []; ts: str = ""`，存储前只校验 `match_key` 存在。

## 4. 前端（B 实现，复用 vanilla JS + 现有 CSS，**加不删**）

**信息架构重排**（`frontend/index.html` 的 `<nav class="tabs">`）：
- 新增并默认激活 **决策** tab（`data-tab="decisions"`），对应新 `<section id="view-decisions" class="view active">`，内含 `<div id="decision-list">`。
- 现有"看板"tab（雷达+关注+账本）降为次级，去掉其 `active` 默认。
- 现有"📄 报告"tab 保留不动。
- tab 顺序建议：`决策 | 看板 | 📄 报告`。

**`frontend/app.js`**：
- 新增 `renderDecisions(decisions)`：每个 Decision 渲染一张"决策卡"（DOM 构造，复用 `el()`/`esc()`/`fmtPct` 等现有工具与 `flagBadge`）。卡片含：对阵+国旗+开球(ko_bj，有 ko_et 则附)、v1 比分+rationale、v2 概率(h/d/a %)+靠谱度徽章(稳/中/乱)+剧本标签 chips、价值 verdict + best_leg；点击展开 `value.legs` 完整明细。任一块缺 → 显示占位不崩。空列表 → `empty` 提示。
- 新增 `loadDecisions()`：`apiGet('/api/decisions')` → `renderDecisions(resp.decisions)`；纳入 `refreshState()` 或独立轮询（沿用 30s 节奏）。
- 价值刷新按钮：在看板雷达面板加一个按钮，点击 `apiSend('/api/refresh','POST')` 后延时重拉 state（给后台重算留时间）。
- tab 切换逻辑 `setupTabs()` 扩展：切到 decisions 时确保已 `loadDecisions()`。
- **保留** renderRadar/renderWatchlist/renderLedger/报告 全部现有逻辑不变。

**`frontend/style.css`**：新增 `.decision-card` 等样式，复用现有 dark 主题变量/配色（🟢🟡 flag 配色沿用），移动优先。**加不删**现有规则。

## 5. `/跑今天` 编排 skill（A 实现）

位置：`.claude/skills/跑今天/SKILL.md`（项目级 skill；新建文件，**不**改 `.claude/agents/*.md`）。中文正文。

职责（编排，本身不重写预测逻辑，调度三个现有 agent + 现有脚本）：
1. 读 `reports/小组赛比分预测.md` 看进度；按 **ET 当下时刻**（`TZ=America/New_York date`）判定今天/明天该预测哪几场（时区纪律见 `wc2026-prediction-workflow` 第 10 条）。
2. 确保盘口新鲜：本地足彩采集器在跑 + Poly 去水已刷新（提示用户或触发）。
3. **并行派三条独立 pass（硬红线：v2 上下文绝不含 v1 任何输出）**：
   - v1 = `football-match-predictor`：比分+出线 → 写回 `小组赛比分预测.md` + `record_v1(...)`。
   - v2 = `wc-forecaster-v2`：**只喂** `baseline_market(...)` + `match_fact_card(...)` → `record_v2_prediction(...)`。**绝不**把 v1 的 score/probs 放进 v2 的 prompt。
   - 价值 = `odds-value-analyst`：green/yellow + 最不亏腿。
4. 跑 `tools/v2_report.py` → `reports/预测v2.md`（v1/v2/市场 Brier 跑分卡）。
5. **汇总 join**（读 v1_predictions + v2_predictions + value 结论，跨两库）→ 组 Decision 列表 → `POST <看板>/api/ingest/predictions`（地址/密码从 `config.toml` 现取，不打印、不入库）。
6. 给用户一段摘要供扫读。
7. **诚实边界**写进 skill：做掉 routine ~80%；**赛前 ~1h 首发微调**窗口（最高边际收益，见 workflow 第 11 条）仍需临场再触发一次或由 skill 提示——不假装全自动。

**红线实现保证**：v1、v2 作为**各自独立的 subagent 调用**，v2 的 prompt 仅注入市场基线与中立事实卡。展示层（决策卡）并排显示 v1/v2 不违反红线（红线管"预测时刻互不可见"，不管"查看时并排"）。

## 6. 测试（C，沿用 fixture 级 pytest，不联网）

- `tests/test_db.py`：扩 `decisions` 表 save/get（含 upsert 替换、ko_bj 排序、缺 match_key 跳过）、`latest_snapshot`。
- `tests/test_web.py`：扩 `POST /api/ingest/predictions`（含 skipped 计数）、`GET /api/decisions`（含排序与空）、`POST /api/refresh`（无快照分支 + 有快照触发，poll_once 用 monkeypatch/stub 防联网）。
- 全量现有 26+ 测试必须仍绿（回归闸）。

## 7. 边界（明确不做）
- HK 后端**不**调 Claude API；**不**搭任务队列/worker；**不**上前端框架；**不**改部署/代理；**不**自动下注。
