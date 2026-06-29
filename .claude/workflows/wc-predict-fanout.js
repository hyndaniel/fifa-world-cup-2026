export const meta = {
  name: 'wc-predict-fanout',
  description:
    '世界杯每日预测红线 fan-out:每场并行派 v1(比分)/v2(概率)/wc-odds,v1⊥v2 由脚本结构焊死(v2 prompt 是入参的纯函数,结构上不含任何 v1 输出),再派 wc-bet 综合决策。返回每场结构化预测,供主会话(跑今天 skill)join + POST 看板。',
  phases: [
    { title: '预测 fan-out', detail: '每场 parallel[v1 比分, v2 概率, wc-odds 描述];v2 prompt 只含 baseline+factCard', model: 'opus' },
    { title: '价值决策', detail: '每场 wc-bet 综合 v1/v2/odds → value/EV/分档/最不亏腿', model: 'opus' },
  ],
}

// =============================================================================
// 这是 §2「编排骨架」的红线焊点。设计依据:docs/design/2026-06-27-7agent编排-三层边界-设计.md
//   决策2(混合编排) + 决策3(数据流红线)。
//
// 🔴 v1 ⊥ v2 怎样被「代码结构」焊死(不靠 agent「记得」):
//   1. v1 与 v2 是同一个 parallel() 里两个**各自独立的 agent() 调用**(各开各的上下文)。
//      并行派出 ⇒ 派 v2 的时刻 v1 还没返回,v2 的 prompt 结构上不可能含 v1 输出。
//   2. v2Prompt(m) 是入参 m 的**纯函数**——只读 m.v2_baseline + m.v2_factcard
//      (主会话预计算的市场基线 + 中立事实卡),源里**没有任何 v1 字段**可引用。
//   3. v1 的产出只落到 v1_predictions 表 + 小组赛比分预测.md;v2 只读 odds 缓存 + enrich
//      事实卡——两套存储不相交,v2 即便自查也碰不到 v1。三重隔离。
//   wc-bet / wc-odds 是**下游**(综合三方的终点),不受 v1⊥v2 约束:bet 看 v1/v2/odds 合理。
//
// 入参契约(主会话 scout 阶段算好后传入):
//   args.matches = [{
//     match_key,            // 竞彩 zucai_num,形如 "周四055" —— odds_cache 与 wc.db 同键
//     home_cn, away_cn,     // 球队全名(看板展示用)
//     ko_et?, ko_bj?,       // 开球展示串(选填)
//     v2_baseline: { had, hhad, ttg },  // baseline_market(...) 三盘口的字符串产物(v2 唯一合法锚)
//     v2_factcard           // match_fact_card(...) 的字符串产物(v2 唯一合法偏离依据)
//   }, ...]
//
// 返回: [{ match_key, home_cn, away_cn, ko_et, ko_bj, v1, v2, odds, value }]
//   —— 主会话据此落库 + join 成 Decision + POST 看板(跑今天 第 4-7 步)。
//
// ⚠️ 已知局限(沿用现行为,本轮不在范围内):v1/v2 各自在 agent 内 Edit 共享 md 台账
//   (小组赛比分预测.md / 盘口下注复盘.md),并行时理论上有写竞争。每日场次个位数、agent 耗时长,
//   真碰撞概率低;DB 落库(record_v1/record_v2_prediction)是 keyed upsert、安全。
//   未来要彻底消除可改为「agent 只 reason + 返回 ledger_row,主会话串行落 md」。
// =============================================================================

const matches = (args && args.matches) || []
if (!matches.length) {
  log('⚠️ 无场次入参(args.matches 为空)——不派任何 agent,直接返回空。请由「跑今天」skill 先在 scout 阶段判场+预计算 v2 输入再调用本 workflow。')
  return []
}

log(`红线 fan-out 启动:${matches.length} 场 · 每场 parallel[v1⊥v2, wc-odds] → wc-bet`)

// ---- 结构化输出 schema(强制 agent 回 StructuredOutput,校验后才返回) ----

const PROBS = {
  type: 'object',
  properties: { h: { type: 'number' }, d: { type: 'number' }, a: { type: 'number' } },
  required: ['h', 'd', 'a'],
}

const V1_SCHEMA = {
  type: 'object',
  properties: {
    match_key: { type: 'string' },
    probs: PROBS,                                  // 自评胜平负 %
    score_pred: { type: 'string', description: '主-客顺序比分串,如 "0-1"' },
    rationale: { type: 'string', description: '一句出线诉求/依据,join 进决策卡 v1.rationale' },
    qualification: { type: 'string', description: '出线形势摘要(可空)' },
    persisted: { type: 'boolean', description: '是否已自跑 record_v1 + 回灌 md' },
  },
  required: ['match_key', 'probs', 'score_pred', 'rationale'],
}

const V2_SCHEMA = {
  type: 'object',
  properties: {
    match_key: { type: 'string' },
    probs: PROBS,                                  // had baseline(+有据偏离)后的胜平负 %
    reliability: { type: 'string', enum: ['稳', '中', '乱'] },
    scenarios: { type: 'array', items: { type: 'string' } },
    deviated: { type: 'boolean', description: 'had 是否有 deviations' },
    deviations: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          market: { type: 'string' },
          outcome: { type: 'string' },
          to: { type: 'number' },
          reason: { type: 'string' },
          factor_source: { type: 'string' },
        },
        required: ['market', 'outcome', 'factor_source'],
      },
    },
    persisted: { type: 'boolean', description: '是否已自跑 build_v2_prediction + record_v2_prediction' },
  },
  required: ['match_key', 'probs', 'reliability', 'scenarios', 'deviated'],
}

const ODDS_SCHEMA = {
  type: 'object',
  properties: {
    match_key: { type: 'string' },
    consensus: { type: 'string', description: '三源去水后市场共识赛果' },
    divergence: { type: 'string', description: '竞彩 vs 欧盘共识分歧(竞彩更慷慨处)' },
    moves: { type: 'string', description: '盘口异动(线/水位变化),无则写"无"' },
    poly_fresh: { type: 'boolean', description: 'Poly 是否今天的新鲜数据' },
    note: { type: 'string', description: '陷阱结构/结算机制/缺源提示(可空)' },
  },
  required: ['match_key', 'consensus', 'poly_fresh'],
}

const LEG = {
  type: 'object',
  properties: {
    market: { type: 'string', description: 'had / hhad / ttg' },
    outcome: { type: 'string' },
    desc: { type: 'string' },
    zucai_odds: { type: 'number' },
    poly_prob_devig: { type: 'number' },
    ev_pct: { type: 'number' },
    flag: { type: 'string', enum: ['green', 'yellow', 'red', 'skip'] },
  },
  required: ['market', 'outcome', 'desc', 'flag'],
}

const BET_SCHEMA = {
  type: 'object',
  properties: {
    match_key: { type: 'string' },
    verdict: { type: 'string', description: '一句决策结论(有无真价值/空仓最优/最不亏腿)' },
    best_leg: LEG,
    legs: { type: 'array', items: LEG },
    persisted: { type: 'boolean', description: '是否已自维护 盘口下注复盘.md 当日行' },
  },
  required: ['match_key', 'verdict'],
}

// ---- prompt 构造器(v2Prompt 是 m 的纯函数——红线的可审计证据) ----

function v1Prompt(m) {
  return [
    `你是 wc-score-v1(比分/出线·独立推理脑)。为下面这**一场**做完整预测并自落库。`,
    `场次:${m.home_cn} vs ${m.away_cn}(match_key=${m.match_key}${m.ko_et ? `,开球 ${m.ko_et}` : ''})。`,
    ``,
    `要做:`,
    `1) 出线形势(积分/净胜球/出线诉求/默契平或大轮换风险)。`,
    `2) 比分预测**主-客顺序**(主队进球-客队进球)+ 自评胜平负 %(诚实标"自评")+ 主比分 + 2-3 备选。`,
    `3) 自落库(你自己跑 Bash):`,
    `   record_v1('.cache/odds_cache.db','${m.match_key}', {'h':..,'d':..,'a':..}, '主-客比分串')`,
    `   并按你的体例把这场回灌 reports/小组赛比分预测.md(该 match_key 这一行)。`,
    ``,
    `你**可以**看盘口/首发/出线/赛前情报——那是你三层叠加(先验/盘口/首发微调)的正常输入,不受红线限制。`,
    `完成后**只返回**结构化 JSON(StructuredOutput);probs 为自评胜平负 %、score_pred 为主-客比分串、rationale 为一句依据。`,
  ].join('\n')
}

// 🔴 v2Prompt:纯函数,只读 m.v2_baseline + m.v2_factcard,源里没有任何 v1 字段。
function v2Prompt(m) {
  return [
    `你是 wc-prob-v2(market-anchored 概率脑)。`,
    `🔴 硬红线:你**只能**依据下面给出的【市场基线】+【中立事实卡】这两样。本派单结构上也**不含**任何 wc-score-v1(v1)的比分/概率/依据,你也**绝不**去找。护的是三方 Brier 对照的有效性。`,
    ``,
    `match_key=${m.match_key}(${m.home_cn} vs ${m.away_cn})。`,
    ``,
    `【逐盘口市场基线 —— 你的锚,默认照抄】`,
    `· 胜平负(had): ${m.v2_baseline && m.v2_baseline.had}`,
    `· 让球(hhad,软锚): ${m.v2_baseline && m.v2_baseline.hhad}`,
    `· 总进球(ttg,软锚): ${m.v2_baseline && m.v2_baseline.ttg}`,
    ``,
    `【中立事实卡 —— 唯一可驱动偏离的依据(确证新闻,恒无首发)】`,
    `${m.v2_factcard}`,
    ``,
    `要做:默认照抄每盘口基线;仅当事实卡里有**非 stale、相关**的确证事实(如官宣大轮换/主力停赛)才对某盘口提偏离,每条带 {outcome,to,reason,factor_source}(factor_source = 该事实短引用)。无确证事实 → 0 偏离(= 正确)。打整场靠谱度(稳/中/乱)+ 命中的剧本标签。`,
    `自落库(你自己跑 Bash):build_v2_prediction(...) → record_v2_prediction('.cache/odds_cache.db','${m.match_key}', 预测)。`,
    `完成后**只返回**结构化 JSON(StructuredOutput);probs 为 had 基线(+有据偏离)后的胜平负 %。`,
  ].join('\n')
}

function oddsPrompt(m) {
  return [
    `你是 wc-odds(盘口描述层)。为 match_key=${m.match_key}(${m.home_cn} vs ${m.away_cn})从 .cache/odds_cache.db 读**竞彩 / Polymarket 去水 / 欧盘共识**三源,去水算隐含概率。`,
    `报:① 市场共识赛果 ② 竞彩 vs 欧盘共识分歧(竞彩更慷慨处)③ 盘口异动(线/水位变化,无则"无")④ 陷阱结构/结算机制(可空)。`,
    `Poly 从缓存读、不自抓;Poly 缺/旧就把 poly_fresh 标 false 并在 note 说明。**只描述、不判价值、不选腿**(那是 wc-bet)。`,
    `只返回结构化 JSON(StructuredOutput)。`,
  ].join('\n')
}

function betPrompt(m, v1, v2, odds) {
  return [
    `你是 wc-bet(下注决策层·综合三方的终点)。为 match_key=${m.match_key}(${m.home_cn} vs ${m.away_cn})做价值决策。`,
    `算 value = 竞彩欧赔 × Poly去水(p_true),EV% = (value-1)×100,分档 🟢green≥1.03 / 🟡yellow0.97-1.03 / 🔴red<0.97(明显-EV)/ ⚪skip(陷阱桶或缺对应 Poly 线)。选"最不亏"那条腿,讲结算/陷阱。`,
    `自落库(你自己跑):维护 reports/盘口下注复盘.md 当日行(赛前推荐,实际列留待回填)。`,
    `诚实定位:足彩长期 -EV,多数选项红档是常态,别粉饰;全场无真价值 → verdict 首句写"无真价值,空仓最优"。`,
    ``,
    `综合下列三方(它们已各自独立产出完毕,你在下游看它们合理):`,
    `【v1 比分/自评概率】${JSON.stringify(v1)}`,
    `【v2 概率/靠谱度/剧本】${JSON.stringify(v2)}`,
    `【wc-odds 市场描述】${JSON.stringify(odds)}`,
    ``,
    `只返回结构化 JSON(StructuredOutput):verdict + best_leg + legs[]。`,
  ].join('\n')
}

// ---- 每场:parallel[v1⊥v2, odds] → bet ----

async function runMatch(m) {
  const [v1, v2, odds] = await parallel([
    () => agent(v1Prompt(m), { agentType: 'wc-score-v1', model: 'opus', phase: '预测 fan-out', label: `v1:${m.match_key}`, schema: V1_SCHEMA }),
    () => agent(v2Prompt(m), { agentType: 'wc-prob-v2', model: 'opus', phase: '预测 fan-out', label: `v2:${m.match_key}`, schema: V2_SCHEMA }),
    () => agent(oddsPrompt(m), { agentType: 'wc-odds', model: 'opus', phase: '预测 fan-out', label: `odds:${m.match_key}`, schema: ODDS_SCHEMA }),
  ])

  // wc-bet 需要三方齐了才能综合 → 串在 fan-out 之后
  const value = await agent(betPrompt(m, v1, v2, odds), {
    agentType: 'wc-bet', model: 'opus', phase: '价值决策', label: `bet:${m.match_key}`, schema: BET_SCHEMA,
  })

  return {
    match_key: m.match_key,
    home_cn: m.home_cn,
    away_cn: m.away_cn,
    ko_et: m.ko_et || null,
    ko_bj: m.ko_bj || null,
    v1, v2, odds, value,
  }
}

// 每场之间相互独立、无 barrier → 直接 parallel 跑全部场次(并发受 workflow 全局 cap 管)
const results = await parallel(matches.map((m) => () => runMatch(m)))
const ok = results.filter(Boolean)
log(`fan-out 完成:${ok.length}/${matches.length} 场返回结构化预测。`)
return ok
