# v2 intel-feed — 给 wc-forecaster-v2 喂确证事实以触发偏离 · 设计

日期:2026-06-25 · 分支:`feat/v2-intel-feed` · 承接 handoff `docs/superpowers/handoffs/2026-06-25-v2-intel-feed-handoff.md`

## 1. 背景与目标

v2 概率预测脑(`wc-forecaster-v2`)口径是「市场基线默认照抄,只在有**能写下来的具体理由**时才偏离」。首跑(周四 6 场)**0 偏离** —— 纪律正确,但它手里没有任何确证事实,所以「偏离」这一半饿着,**无法检验「我们的足球判断能否跑赢市场」**。

目标:把库里已有的客观事实(主要是各队**新闻**,首发槽位预留)装成一张**本场事实卡**喂给 v2,让它能**有据偏离**;每条偏离记录其**因子来源**,供赛后归因对账。

## 2. 范围

| 做 | 不做(本期) |
|---|---|
| `intel.py` 事实卡装配器(确定性,纯 plumbing) | 规则/关键词抽「结构化因子」(方向/置信推断归 v2) |
| v2 读卡 → 自判偏离 → 每条偏离记 `factor_source` | 给 v2 加 WebSearch / 新工具 / 自研 |
| `factor_source` 随现有管线持久化(零装配改动)+ round-trip 测试 | 接首发(lineup)源(槽位留空,恒 null) |
| `tools/v2_report.py` 轻量「偏离归因」列表 | 因子分析切片 / 命中率统计(只记录+列出) |

## 3. 现状与约束(载荷性)

**单库拓扑(简化关键):** 事实卡是**纯 app 库(`data/wc.db`)**的事,不跨 odds_cache。app 库的 `matches` 表(`zucai_num` PK,含 `home_cn/away_cn`)给「场次→两队」,`enrich` 表(`team_cn` PK,`lineup_json/news_json/ts`)给事实。`match_key` == `zucai_num`,与 odds_cache 的 v2_predictions 同键空间。

**事实源现状:**
- `enrich` 由 `tools/collect_enrich.py`(Mac)+ `backend/news_enrich.py`(HK 后台轮询 Google News RSS)写入;`db.save_enrich/latest_enrich`(已有)。
- **首发恒缺**:`collect_enrich` 无可靠免费首发源,`lineup` 永远 `null`(占位)。本期事实卡实质 = **新闻标题 + pubDate**。
- **覆盖有限**:`news_enrich` 只抓 **watchlist 覆盖**的队;未覆盖队无 enrich。
- **新闻条目**:`fetch_team_news` 产 `[{title, url, ts(pubDate, RFC-2822)}]`。

**红线(handoff 已定,别推翻):**
1. **绝不喂 v1**(football-match-predictor)的预测/概率/比分 —— spec §7「v1↔v2 互不读」,否则三方 Brier 对照被污染。喂的必须是**中立事实**。
2. v2 **不自研、不加 WebSearch**;职责是「市场 vs 确证事实,该不该偏离」。
3. 缺数据/陈旧 → 不偏离,绝不编。

## 4. 架构 + 数据流

```
app 库 matches(场次→两队) + enrich(各队新闻/首发)
   │  [intel.py·代码:取两队最新 enrich, 标龄/截最近N/标 stale]
   ▼
本场事实卡(JSON: teams[].news[{title,url,age_h,stale}], lineup=null, has_intel)
   │  [wc-forecaster-v2·LLM:读卡 + 逐盘口基线 → 自判偏离]
   ▼
v2 预测(每条偏离 {outcome,to,reason,factor_source})── 随 v2_predictions JSON 落库(零装配改动)
   │  [tools/v2_report.py·代码:列「偏离归因」]
   ▼
跑分卡 + 偏离归因(每偏离场: outcome→to · factor_source) ── 赛后对账「哪条事实驱动了哪条偏离」
```

**不变量:** 代码钉两端(事实卡装配 = 确定性;偏离审计 = 确定性),LLM 只做中间判断(读卡→偏不偏离)。判断绝不下沉进代码(不规则抽因子)。

## 5. 组件 1:事实卡装配器(`backend/intel.py`,代码)

**职责:把「一场比赛」装成「两队最新事实卡」,无方向/置信推断。**

```python
match_fact_card(db, match_key, now_bj, cap=5, stale_hours=48) -> dict
```
- `db`:`backend.db.Db` 实例(持有 `match`/`latest_enrich`)。`now_bj`:北京时区 datetime(注入,便于测试)。
- 步骤:
  1. `db.match(match_key)`(**新增 getter**:按 `zucai_num` 查 `matches` 一行 → `{home_cn, away_cn, ...}`;无 → 返回 `{match_key, match:None, teams:[], note:"无此场"}`)。
  2. 每队 `db.latest_enrich(team_cn)` → `{lineup, news, ts}`(无 → `has_intel:False, news:[]`)。
  3. 每条 news:`email.utils.parsedate_to_datetime(ts)` 解析 pubDate → `age_h = (now_bj − pub)` 小时(保留 1 位小数);**解析失败 → `age_h=None`**。`stale = (age_h is None) or (age_h > stale_hours)`。按 pub 新近降序、截最近 `cap` 条。
- 返回(形状):
```json
{"match_key":"周四055","match":"南非 vs 韩国","as_of_bj":"2026-06-25T20:00:00+08:00",
 "teams":[
   {"team":"南非","lineup":null,"has_intel":true,
    "news":[{"title":"...","url":"...","age_h":3.0,"stale":false}]},
   {"team":"韩国","lineup":null,"has_intel":false,"news":[]}],
 "note":"首发源暂缺(恒 null);新闻>48h 或时间不可解析 标 stale;仅 watchlist 覆盖队有情报"}
```
- **诚实点写进 `note`**:首发恒缺、覆盖有限、stale 只标不替判。

## 6. 组件 2:v2 读卡判断 + `factor_source`(agent doc,行为)

`.claude/agents/wc-forecaster-v2.md` 每场工作流在「取基线」后加一步「取事实卡」:
- `python3 -c "from backend.db import Db; from backend.intel import match_fact_card; from datetime import datetime, timezone, timedelta; print(match_fact_card(Db('data/wc.db'), '<key>', datetime.now(timezone(timedelta(hours=8)))))"`
- 读卡 + 逐盘口基线 → **自判**偏不偏离。纪律(写进 doc):
  - **只认事实,绝不读 v1**;**`stale:true` / 无关 / `has_intel:false` → 不据此偏离**;
  - 每条偏离必带 **`factor_source`**(短引用串,如 `"南非主帅停赛官宣[GoogleNews 3h]"`);无确证因子 → 照抄基线(0 偏离 = 正确)。

**持久化 = 零装配改动:** 偏离字典加 `factor_source` 字段即可 —— `apply_deviations` 只读 `outcome/to`(无视多余键),`build_v2_prediction` 原样落 `deviations` → `factor_source` 随 `v2_predictions` JSON 持久化。仅加 **round-trip 测试**锁住。

## 7. 组件 3:偏离归因(`tools/v2_report.py`,轻量)

各盘口节下加「偏离归因」小列表:遍历该盘口偏离了的场,打印 `match_key:outcome→to% · factor_source`(无 `factor_source` 标「⚠无因子来源」以暴露失纪律)。数据来自 `collect()` 已读的 `v2_predictions`,只多读 `deviations[].factor_source`。不做命中率切片。

## 8. 测试

- **intel.py 装配器**(纯函数):age 计算正确;cap 截断;`stale` 在 >48h 与 pubDate 解析失败时为真;空队 `has_intel:false/news:[]`;缺场返回 `note:"无此场"`;注入 `now_bj` 与罐头 enrich。
- **`db.match` getter**:命中返回行、未命中返回 None。
- **factor_source round-trip**:带 `factor_source` 的偏离 → `build_v2_prediction`→`record/get_v2_prediction` 原样取回;`apply_deviations` 对带 `factor_source` 的偏离结果与不带时一致(证明无视多余键)。
- **偏离归因渲染**:`v2_report` 对带/不带 `factor_source` 的偏离分别渲染引用串 / ⚠ 标记。
- **零回归**:全套测试保绿。

## 9. 红线 / 非目标

- 绝不喂 v1 预测/概率/比分(护三方 Brier);v2 不加 WebSearch/新工具/自研。
- 不接首发源(lineup 恒 null);不做规则/关键词抽因子(判断归 v2)。
- 不建因子命中率/切片分析(只记录 `factor_source` + 列出)。
- 缺数据/陈旧 → 不偏离,绝不编。

## 10. 成功标准

1. 对一场**有确证事实**(如某队官宣大轮换/主力停赛的新闻在卡内且非 stale)的比赛,v2 产出**一条带 `factor_source` 的偏离**;**无情报/全 stale** 的场仍 **0 偏离**。
2. 该偏离的 `factor_source` 落进 `v2_predictions`,并在跑分卡「偏离归因」列出。
3. had / 全套测试零回归;v1↔v2 互不读红线不破(代码层 v2 不读任何 v1 接口)。

## 11. 受影响文件

| 文件 | 改动 |
|---|---|
| `backend/intel.py` | 新建:`match_fact_card`(确定性事实卡装配) |
| `backend/db.py` | 新增 `match(zucai_num)` 单场 getter |
| `.claude/agents/wc-forecaster-v2.md` | 工作流加「取事实卡→自判」步 + factor_source 纪律 + 红线 |
| `tools/v2_report.py` | 各盘口节加「偏离归因」列表(读 `factor_source`) |
| `tests/test_intel.py`(新)`/ test_db.py / test_v2_predict.py / test_v2_report.py` | 装配器 + getter + round-trip + 归因渲染 + 零回归 |

— 红线:喂事实不喂预测;v2 = 市场 vs 确证事实的偏离判断,不自研、不碰 v1。
