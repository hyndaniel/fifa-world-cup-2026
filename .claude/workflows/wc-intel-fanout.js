export const meta = {
  name: 'wc-intel-fanout',
  description:
    '世界杯每日「赛前情报」深度调研 fan-out:每场 × 6 角度并行 web 检索 → 抽事实 → 只把关键硬事实送 3 票对抗式证伪(2/3 证伪即毙)。返回结构化数据,主会话合成 reports/intel/*.md + 抽确证事实经 tools/save_intel.py 落 wc.db enrich(喂 v2)。',
  phases: [
    { title: 'Research', detail: '每场 × 6 角度并行检索(伤停/停赛黄牌/首发战术/场地天气裁判/盘口超算/势头路径)' },
    { title: 'Verify', detail: '关键硬事实去重封顶 30 → 各 3 票对抗式证伪' },
  ],
}

// =============================================================================
// 为什么要有这个文件:每日情报以前是「每天临时手写内联脚本」→ 天天漂
//   (5 vs 6 角度、证伪 90 vs 348 agent 全靠当天记性)。本文件把验证过的配方
//   固化下来,以后每天**只改下面 MATCHES 常量**,配方结构焊死、不再漂。
//   memory: deepsearch-workflow-orchestrated / deepsearch-intel-layer。
//
// —— 焊死的配方(依据 07-01~07-05 五版实跑收敛)————————————————————————————
//   · 6 角度 / 逐场检索(比「按信息类型打包」更厚,接近 07-01 那版力度):
//       伤停出勤 · 停赛与黄牌 · 首发轮换战术 · 场地天气裁判 · 盘口超算市场 · 势头路径交锋
//   · 证伪只给「关键硬事实」(consequential):反常/有争议/能改下注决策的
//       (主力突遭伤停或停赛、锚市场的关键统计或教练原话)。
//       背景硬事实(球场/开球时间/常规盘口/超算数字)= 单源留档,不烧证伪 token。
//       ⇒ 对齐 07-02/03「~25 条硬声明 3 票证伪 + 低风险背景降级单源」。
//   · 证伪 = 每条 3 票独立证伪 agent,2/3 说 refuted 即毙(默认存疑倾向 refuted)。
//   · VERIFY_CAP=30 硬封顶 + 跨场去重,防 research 过量产事实把证伪炸上天。
//
// —— 成本封套(务必守住)——————————————————————————————————————————————
//   research  = 场数 × 6
//   verify    = min(关键硬事实去重后, 30) × 3   ≤ 90
//   两场 ⇒ 12 + ≤90 = ≤102 agent(对齐 07-03/04/05 的 ~105-110)。
//   ⚠️ 反面教材:若把「每条可证伪 finding」都送 3 票 → 曾冲到 348 agent。别这么干。
//
// —— 每日怎么跑 ————————————————————————————————————————————————————
//   1. 主会话先 scout 今晚场次:
//        sqlite3 data/wc.db "SELECT zucai_num,home_cn,away_cn,ko_bj FROM matches
//                            WHERE ko_bj>='<today>' ORDER BY ko_bj"
//      (ET 今晚 = ko_bj 落在今天 ET 傍晚~夜里的那几场;注意 ko_bj 是北京时=ET+12)
//   2. 把这几场填进下面 MATCHES 常量(改文件,别用 args —— memory wc-fanout-run-gotchas:
//      args 无论 name/scriptPath 都不可靠,须字面量嵌脚本)。
//   3. Workflow({ scriptPath: '.../.claude/workflows/wc-intel-fanout.js' }) 后台跑。
//   4. 跑完主会话合成 reports/intel/<date>__赛前情报-<轮次>.md(**主会话合成,不在 workflow 里合成**
//      —— 上一版就是末步结构化输出重试超限崩溃,故 workflow 只返回结构化数据)。
//   5. 抽 confirmed 里的确证伤停/停赛/官宣轮换 → intel.json → 落主库:
//        python3 tools/save_intel.py --db /abs/data/wc.db --json intel.json
//      (打**主 checkout 绝对路径**那份 wc.db,v2 真读的是它;判断/出线/动机不进此口)。
//
// —— 想复用缓存省 token(改配方 relaunch 时)——————————————————————————
//   缓存按每个 agent() 的 (prompt, opts) 命中,且**必须显式带 resumeFromRunId**。
//   要让 12 个 research 秒回缓存:research 的 prompt + schema(属 opts)必须逐字节不变,
//   只改下游(证伪筛选/封顶),再 Workflow({scriptPath, resumeFromRunId})。
//   ⚠️ 往 FINDINGS_SCHEMA 加字段 = 改了 research 的 opts = research 缓存全 miss、重跑。
//   若只想调证伪逻辑,别碰 research 的 prompt/schema。
//
// 返回: { matches, research, confirmed, killed, singleSource, softFindings }
//   —— 主会话据此合成 md(§伤停/§预计首发/§被证伪表/§可喂 v2 硬事实)+ 落盘。
// =============================================================================

// ⬇⬇⬇ 每天只改这里:今晚要检的场次(从 wc.db scout 出来后填)⬇⬇⬇
const MATCHES = [
  { num: '098', home: '西班牙', away: '比利时', home_en: 'Spain', away_en: 'Belgium',
    ko: 'QF · ET 7/10 15:00(洛杉矶当地 12:00 PT)/ 北京 7/11 03:00 · 洛杉矶英格尔伍德 SoFi Stadium(固定顶棚、侧面开放)中立场' },
]
// 今晚的日期(ISO,ET/北京当天;research+verify 的「今天是」锚,决定「最近 1-3 天」时效窗)
const TODAY = '2026-07-10'
// ⬆⬆⬆ 每天只改这里(MATCHES + TODAY)⬆⬆⬆

// 6 角度(焊死,对齐 07-03/04/05 验证过的分类法:停赛与黄牌、裁判各自单拎),逐场检索
const ANGLES = [
  { key: '伤停出勤', prompt: '伤病、体能/带伤、出勤状态、是否报销(区分已发生确证 vs 存疑)' },
  { key: '停赛与黄牌', prompt: '停赛名单、直红/双黄罚下、黄牌累计规则(FIFA:淘汰赛 R32/R16/QF 三轮单黄合并累计、不清零;R32 或 R16 吃到的单黄带入今晚 QF,若 QF 场上再吃一张=累计两黄、停赛缺席半决赛〈07-10 QF098 轮 3 票证伪坐实,ESPN/NBC/DraftKings 口径一致;此前本行写反过,勿回退〉;单黄累计仅在 QF 全部打完后为安全过关者清零一次、保证无人因累计黄缺席决赛;同场两黄或直红照常=下一场停赛);逐名点出谁一黄在身、本场有无确定停赛者' },
  { key: '首发轮换战术', prompt: '前瞻媒体预计首发 XI、阵型、可能轮换、教练用人倾向,以及赛前发布会教练/球员原话、战术意图、关键对位(区分官宣 vs 媒体预测)' },
  { key: '场地天气裁判', prompt: '球场/城市/海拔、天气预报、主裁及 VAR 任命(有则引一手源,无则明说缺口)' },
  { key: '盘口超算市场', prompt: '博彩盘口(胜平负/晋级)、Opta 及各家超算晋级概率、市场共识倾向' },
  { key: '势头路径交锋', prompt: '双方近况势头、R32 怎么赢的(路径)、历史交锋、不败/连胜等硬统计' },
]

// ⚠️ 改 FINDINGS_SCHEMA 会让 research 缓存全 miss(见头部「复用缓存」)。非必要别动。
const FINDINGS_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          claim: { type: 'string', description: '一句话事实/说法,含具体主体与内容' },
          confidence: { type: 'string', enum: ['高', '中', '低'] },
          category: { type: 'string', enum: ['伤停', '停赛', '首发', '战术', '发布会原话', '盘口', '超算', '环境', '裁判', '纪律', '势头', '历史', '其他'] },
          falsifiable: { type: 'boolean', description: '是否为可被证伪的硬事实(伤停/停赛/具体统计/教练原话=true;泛叙事/主观势头=false)' },
          consequential: { type: 'boolean', description: '是否值得 3 票证伪:反常/有争议/能改下注决策的硬事实(主力突遭伤停或停赛、锚市场的关键统计或教练原话)=true;低风险背景事实(球场/时间/常规盘口/超算数字)=false,这类单源留档即可' },
          sources: { type: 'array', items: { type: 'string' }, description: '信源 URL,尽量一手' },
        },
        required: ['claim', 'confidence', 'category', 'falsifiable', 'consequential', 'sources'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT_SCHEMA = {
  type: 'object',
  properties: {
    refuted: { type: 'boolean' },
    reason: { type: 'string', description: '证伪或坐实的依据' },
    latest_source: { type: 'string', description: '你核查用的最新源 URL' },
  },
  required: ['refuted', 'reason'],
}

const VERIFY_CAP = 30

phase('Research')
log(`今晚 ${MATCHES.length} 场:${MATCHES.map(m => `${m.home}vs${m.away}`).join(' / ')} — ${MATCHES.length} × ${ANGLES.length} = ${MATCHES.length * ANGLES.length} 个 research agent`)

const research = (await parallel(
  MATCHES.flatMap(m => ANGLES.map(a => () =>
    agent(
      `今天是 ${TODAY}。深度调研 2026 世界杯淘汰赛「${m.home} vs ${m.away}」`
      + `(${m.home_en} vs ${m.away_en},KO ${m.ko})的【${a.key}】维度:${a.prompt}。\n`
      + `用 WebSearch 搜最近 1–3 天的最新英文/中文源(Sports Mole / ESPN / Goal / Sky / Yahoo / BBC / 官方社媒等),尽量一手。\n`
      + `严格区分:✅确证(已发生的伤停/停赛/教练本人原话)、预计(媒体预测 XI/倾向)、叙事(主观势头)。\n`
      + `每条 finding 给:claim(具体)、confidence(高/中/低)、category、falsifiable(伤停/停赛/统计/原话=true)、`
      + `consequential(反常/能改决策=true,背景事实=false)、sources(URL)。\n`
      + `只报真找到源的内容,查不到就少报,不要编。`,
      { label: `${m.num}:${a.key}`, phase: 'Research', schema: FINDINGS_SCHEMA }
    ).then(r => r ? { match: m.num, matchLabel: `${m.home} vs ${m.away}`, angle: a.key, findings: r.findings || [] } : null)
  ))
)).filter(Boolean)

const allFindings = research.flatMap(r => r.findings.map(f => ({ ...f, match: r.match, matchLabel: r.matchLabel, angle: r.angle })))
const hardFacts = allFindings.filter(f => f.falsifiable && (f.claim || '').trim())

// 只把「关键硬事实」送 3 票证伪(对齐 07-02/03:~25 条硬声明 + 背景降级单源留档)
const norm = s => (s || '').toLowerCase().replace(/[\s，。,.、·:：\-—()（）]/g, '').slice(0, 24)
const seen = new Set()
const rank = { 高: 0, 中: 1, 低: 2 }  // 高置信的反常断言最该核 → 先证
const verifyTargets = hardFacts
  .filter(f => f.consequential)
  .sort((a, b) => (rank[a.confidence] ?? 3) - (rank[b.confidence] ?? 3))
  .filter(f => { const k = `${f.match}|${norm(f.claim)}`; if (seen.has(k)) return false; seen.add(k); return true })
  .slice(0, VERIFY_CAP)
const notVerified = hardFacts.filter(f => !verifyTargets.includes(f))  // 关键超帽 + 背景硬事实 → 单源留档
log(`research 完成:${research.length} 组、${allFindings.length} 条 findings(硬事实 ${hardFacts.length});关键硬事实 ${verifyTargets.length} 条送 3 票证伪(≈${verifyTargets.length * 3} agent),其余 ${notVerified.length} 条单源留档`)

phase('Verify')
const verified = (await parallel(verifyTargets.map((f, idx) => () =>
  parallel([0, 1, 2].map(v => () =>
    agent(
      `今天是 ${TODAY}。对抗式证伪以下关于 2026 世界杯淘汰赛「${f.matchLabel}」的断言 —— 尽力反驳它:\n`
      + `断言:「${f.claim}」\n`
      + `声称信源:${(f.sources || []).join(' , ') || '(无)'}\n`
      + `用 WebSearch 核查最新(近 1–3 天)源。判定规则:查无实据 / 已过时被覆盖 / 过度解读 / 二手加戏 / 把通用规则说成本场特例 → refuted=true;`
      + `确有可靠一手源支撑且未过时 → refuted=false。存疑时倾向 refuted=true。给 reason + latest_source。`,
      { label: `verify#${idx}:v${v}`, phase: 'Verify', schema: VERDICT_SCHEMA }
    )
  )).then(votes => {
    const vs = votes.filter(Boolean)
    const refutes = vs.filter(x => x.refuted).length
    return { ...f, refuted: refutes >= 2, refuteVotes: refutes, totalVotes: vs.length, votes: vs }
  })
)))

const confirmed = verified.filter(f => !f.refuted)
const killed = verified.filter(f => f.refuted)
log(`证伪完成:关键硬事实 ${verified.length} 条 → 确证 ${confirmed.length} / 证伪 ${killed.length}`)

return {
  matches: MATCHES,
  research,          // 全部分组 findings(含叙事,供合成)
  confirmed,         // 通过 3 票对抗式证伪的关键硬事实
  killed,            // 被证伪的说法 → §被证伪表
  singleSource: notVerified,                              // 硬事实但未三票核(背景/超帽)→ 单源留档、合成时标注
  softFindings: allFindings.filter(f => !f.falsifiable),  // 叙事/势头等未验证软信息
}
