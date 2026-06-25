# 预测 v2 Phase 4 — 全盘口扩展(让球 + 总进球/大小球)设计

日期:2026-06-25 · 分支:`feat/wc-v2-phase4-markets` · 承接 v2(`docs/superpowers/specs/2026-06-25-wc-prediction-v2-design.md` §9.1)

## 1. 背景与目标

v2 已落地**胜平负(had)**一条盘口的 market-anchored 全链路(基线引擎 → v2 偏离 agent → 三方 Brier 跑分卡)。Phase 4 把同一套机制扩到**另外两个有真实市场定价的盘口**:

- **让球(hhad)** —— 竞彩让球三选一。
- **总进球 / 大小球(ttg)** —— 竞彩总进球完整分布,大小球由分布派生。

**核心约束(决定范围):market-anchored 只对"市场真定价的盘口"成立。** 比分分布 / BTTS / 半全场在我们任何源里都没有直接定价,只能用进球模型硬建 —— 那是建模不是锚市场,违背 v2 红线(§11"缺数据降置信、绝不编"),故**明确出界**(见 §8)。

目标:让球、总进球/大小球各得一张**市场基线概率表 + 逐盘口三方/两方 Brier**,完全复用现有去水/融合/打分机制,且**对已上线的 had 路径零回归**。

## 2. 范围

| | 做 | 不做(本期) |
|---|---|---|
| 盘口 | 让球(hhad)、总进球(ttg)+ 大小球派生 | 比分分布、BTTS、半全场 |
| 大小球 | 主线 2.5 派生「可下/别碰」结论 + 概率 | 1.5/3.5 仅可选展示,**不单独计 Brier**;多线独立基线 |
| 快照 | 沿用 had 现状(取最新快照) | 「钉赛前 ~1h 快照」本期不解(had 同样未解) |
| 下注决策 | 逐盘口「可下/别碰」避雷标 | +EV / 最短腿 / 小注(仍归 `odds-value-analyst`) |

## 3. 数据现状与各源锚硬度(诚实标注)

竞彩采集已请求 `poolCode=had,hhad,ttg`(`tools/collect_zucai.py`),缓存 payload 已含三盘口;`tools/odds_watch.py` 已解析三者看线移。各盘口可用源:

| 盘口 | 竞彩 | Poly | 欧盘/亚盘共识 | 锚硬度结论 |
|---|---|---|---|---|
| 胜平负 had | ✓ 欧赔 h/d/a | ✓ moneyline | ✓ 30+ 家欧赔中位数 | 多源,可硬锚(现状) |
| 让球 hhad | ✓ `{line,h,d,a}` | ✗ | △ 亚盘让球线(**常异线**) | **竞彩单源 → 🔴软锚** |
| 总进球 ttg | ✓ 分布 `{"0"..,"7"}`("7"=7+) | ✗ | ✗ | **竞彩单源 → 🔴软锚** |

**让球的诚实点:** 竞彩 hhad 的整数让球线与亚盘让球线(如 -1.25)常非同一条,跨线不可直接平均。故让球主锚 = **竞彩单源**;亚盘 consensus **仅当与竞彩同线**时升 medium 并入融合,异线只作方向性旁证打印(「亚盘 -1.25 同向/背离」),**不混入融合数**。绝不假装让球是三源硬锚。

## 4. 架构:基线引擎泛化(盘口无关核 + 薄适配器)

现有 `backend/baseline.py` 把 `_KEYS=("h","d","a")` 焊死。抽出盘口无关核,had/hhad/ttg 当适配器:

- `backend/devig.py::devig(implied)` —— 已通用,**不动**。
- `backend/scoring.py::brier_multi(probs, actual)` —— 已是泛型(按 `probs` 键求和),**不动**,直接复用于任意盘口。
- `blend(sources, keys, weights)` ← 由 `blend_had` 泛化:接受任意 outcome `keys` 列表;残差并入最大项严格归一到 100 的逻辑不变。
- `confidence(sources, keys)` ← 由 `confidence` 泛化:跨源极差按传入 `keys` 求。
- `baseline_market(cache_path, match_key, market_cfg)` ← 由 `baseline_had` 泛化。`market_cfg` 声明:
  - `pool`:从 payload 取哪个字段(`had` / `hhad` / `ttg`);
  - `keys`:outcome 键列表(had/hhad = `("h","d","a")`;ttg = 该场 payload 实际出现的键,排序);
  - `weights`:各源权重(had 沿用 `{zucai:.20, poly:.45, consensus:.35}`;hhad/ttg 单源 → `{zucai:1.0}`)。
- **向后兼容:`baseline_had(...)` 保留为 `baseline_market(..., HAD_CFG)` 的瘦封装,签名/默认值不变 → 现有 had 测试全绿、零回归。**
- `backend/v2_predict.py::apply_deviations(baseline, deviations, keys=("h","d","a"))` ← 同步泛化吃 `keys`(ttg 多键);默认参数保证 had 行为不变。

> 设计取向:核函数盘口无关、纯函数、可独立测;适配器只知道"自己从缓存哪个字段取、键是什么"。新增盘口 = 加一个 `market_cfg`,不改核。

## 5. 盘口机制

### 5.1 让球(hhad)
- 取竞彩 `hhad` payload `{line, h, d, a}` → 三选一隐含去水(复用 `zucai_had_devig` 同路)→ 单源基线,结构 = 胜平负。
- 锚硬度:竞彩单源 🔴软;亚盘 consensus 同线才升 medium 入融合,异线仅旁证(§3)。
- 结算:整数让球已烤进赔率,赛后 actual = `sign(主 - 客 + line)` → h/d/a,**无 push**(竞彩让球为整数盘三分法)。

### 5.2 总进球(ttg)+ 大小球
- 取竞彩 `ttg` payload(`{"0":赔,…,"7":赔}`,"7"=7+)→ 全档隐含去水 → **完整分布基线**;多分类 Brier(`brier_multi`)直接打整条分布。
- **大小球派生**:`P(大 L) = Σ_{k>L} P(k)`(L=2.5 → Σ k≥3)。主线 2.5,可选 1.5/3.5;只出 `可下/别碰` 结论 + 派生概率,**不单独落 Brier**(避免与分布重复计分)。
  - 职责切分:**派生概率(over/under)由代码算;`verdict`(可下/别碰)是 agent 的判断**(基于靠谱度 + 锚硬度,沿用 v2 "避雷=LLM 中间判断"架构),代码只承载该字段、**不内置阈值**。
- 单源(竞彩),恒 🔴软锚。

## 6. 结果回填 & 逐盘口打分(**不动 schema**)

`match_results` 已存 `(match_key, home_goals, away_goals, outcome, ts)`;进球是唯一真值。各盘口 actual **由进球纯函数派生**,赛后无需分别录入:

| 盘口 | actual 派生 |
|---|---|
| had | `sign(主-客)` → h/d/a(现成 `_outcome_key`) |
| hhad | `sign(主-客+line)` → h/d/a;`line` 由该场缓存 hhad 快照取(同 had,赛后实时重算,无新持久化) |
| ttg | `min(主+客, 7)` → `"0".."7"` 键 |

打分:
- `scorecard.py::three_way(v1, v2, market, actual)` 已泛型,**逐盘口跑**。
- **v1 是冻结的 had 预测器,仅胜平负有 v1**;让球/总进球退化为「v2 vs 市场」两方(`three_way` 已对 `None` v1 优雅处理,返回 `v1=None`)。
- `aggregate` / `deviation_audit` 逐盘口聚合。

## 7. agent + 落库 + 报告

- **agent(`.claude/agents/wc-forecaster-v2.md`)**:从读单一 had 基线 → 读 had/hhad/ttg 三张基线;逐盘口「默认照抄、有据才偏离(带理由)」+ 逐盘口 `可下/别碰`;**靠谱度(稳/中/乱)与剧本标签仍每场一个**(非逐盘口)。更新 agent 定义描述多盘口流程与红线。
- **`v2_predictions` 落库 JSON** 由"单盘口"扩为按盘口分组(不改表结构,仍 `match_key/ts/prediction_json`):
  ```json
  {"match_key":"周三051","reliability":"中","scenarios":["..."],
   "markets":{
     "had":  {"baseline":{...},"v2":{...},"deviations":[...]},
     "hhad": {"line":-1,"baseline":{...},"v2":{...},"deviations":[...]},
     "ttg":  {"baseline":{"0":..},"v2":{...},"deviations":[...],
              "ou":{"2.5":{"over":..,"under":..,"verdict":"别碰"}}}}}
  ```
  `build_v2_prediction` 泛化产出该形状;`record/get_v2_prediction` 不变(存取整 JSON)。
- **`tools/v2_report.py`**:`collect()` 循环 盘口×场次;`render()` **按盘口分节**(胜平负 / 让球 / 总进球 各一张三方/两方 Brier 表 + 大小球可下/别碰小结);`_fmt` 复用。

## 8. 非目标 / 红线

- **比分分布 / BTTS / 半全场不做** —— 无市场定价,属建模,违背 market-anchored,留作未来并明确出界。
- 大小球只 2.5 派生计结论,1.5/3.5 仅可选展示、不单独计分。
- 「钉赛前快照」沿用 had 现状(最新快照),本期不解。
- +EV / 最短腿 / 小注仍归 `odds-value-analyst`;v2 = 预测 + 避雷,不碰下注决策。
- 缺数据降置信、绝不编(让球/总进球单源就老实标 🔴软锚)。

## 9. 测试(沿用现有体例)

- **数学自测**:N 路去水/融合/归一 Σ=1(ttg 多档)、`brier_multi` 多分类已知输入→已知分、大小球求和派生正确;`apply_deviations` 多键(ttg)偏离 + 单键 had 不变。
- **样例**:罐头 hhad/ttg payload → 核对基线表;让球结算 `sign(主-客+line)` 边界(含 line 致平)。
- **回测**:重放已踢完场,**逐盘口**市场基线 Brier = v2 要打败的靶子(扩 `tools/backtest_baseline.py`)。
- **had 回归**:现有 had 全部测试默认参数下全绿、零回归(硬性门槛)。

## 10. 成功标准

1. 让球、总进球各得市场基线表 + 逐盘口 Brier;had 路径零回归(全测试绿)。
2. 回测立起三盘口各自「市场 Brier」基准线。
3. 上线后 ~20-30 场:各盘口 v2 Brier ≤ 市场 Brier(至少不更差);偏离审计显示偏离平均不拉高 Brier。
4. 若某盘口 v2 长期跑不赢市场基线 → 按红线认怂,该盘口回归"只用市场基线 + 避雷器"。

## 11. 受影响文件清单

| 文件 | 改动 |
|---|---|
| `backend/baseline.py` | 抽 `blend`/`confidence` 泛化 + `baseline_market(market_cfg)`;`baseline_had` 变瘦封装;加 `HAD_CFG/HHAD_CFG/TTG_CFG` |
| `backend/v2_predict.py` | `apply_deviations(keys=...)` 泛化;`build_v2_prediction` 产 `markets` 形状 |
| `backend/scorecard.py` | 逐盘口 `aggregate`/`deviation_audit`(`three_way` 已够用) |
| `tools/v2_report.py` | `collect` 盘口×场次;`render` 按盘口分节 + 大小球小结 |
| `tools/backtest_baseline.py` | 逐盘口市场 Brier 基准 |
| `.claude/agents/wc-forecaster-v2.md` | 多盘口读基线/偏离/可下别碰;红线 |
| `tests/test_baseline.py` 等 | 新增 hhad/ttg 样例+数学自测;新增大小球派生、让球结算测试;had 回归保绿 |

— 红线:概率预测非投注建议;v2 跑不赢市场就回归市场基线 + 避雷器。
