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
  { num: '104', home: '西班牙', away: '阿根廷', home_en: 'Spain', away_en: 'Argentina',
    ko: '🏆 决赛(FINAL,M104) · ET 7/19(周日)15:00 / 北京 7/20 03:00 · 东卢瑟福 MetLife Stadium(赛事名 New York New Jersey Stadium),新泽西州 · 中立场 · 露天天然草(本届为世界杯改铺)'
      + ' 【本轮=本场首轮完整情报,距开球约 3 天(T-72h);首发官宣窗(约赛前 1h)还很远,一切首发均为媒体预计,请严格标注】'
      + ' **赛果背景(已确证,勿再当悬念)**:西班牙半决赛 7/14 在达拉斯 **2-0 完胜法国** 晋级;阿根廷半决赛 7/15 在亚特兰大 **2-1 击败英格兰** 晋级。两队均打完 SF、各有 4-5 天恢复期。'
      + ' **本轮请重点挖**:① 半决赛中新产生的伤停/退赛(西班牙 vs 法国、阿根廷 vs 英格兰两场里有无球员伤退、肌肉伤、加时体能透支);'
      + ' ② 🔴 **停赛**——半决赛场上有无直红/同场两黄导致决赛停赛?FIFA 单黄累计已在 QF 后清零,故「一黄在身停决赛」不成立,但 **SF 里吃到的红牌/两黄会导致决赛停赛**,请逐名核实两队有无确定停赛者及 FIFA 纪律委员会追加处罚;'
      + ' ③ 决赛前的训练/发布会原话(德拉富恩特、斯卡洛尼、梅西、亚马尔等)与轮换意图;④ 主裁/VAR/AVAR 任命是否已公布(FIFA 通常赛前 2-3 天公布决赛裁判组,若已出请给一手源与其本届执法数据);'
      + ' ⑤ 决赛盘口(胜平负/夺冠/大小球/是否进加时)、Opta 等超算夺冠概率、预测市场(Polymarket/Kalshi)与欧盘的分歧;⑥ 叙事硬统计:西班牙国家队不败纪录当前场次数与世界纪录(意大利 37 场)的关系、阿根廷能否卫冕(连霸自 1962 巴西后无人做到)、梅西是否最后一届、两队世界杯历史交锋与近年大赛交手。' },
  { num: '103', home: '法国', away: '英格兰', home_en: 'France', away_en: 'England',
    ko: '🥉 季军赛(Third-Place Play-off / Bronze Final,M103) · ET 7/18(周六)17:00 / 北京 7/19 05:00 · 迈阿密花园 Hard Rock Stadium(赛事名 Miami Stadium),佛罗里达州 · 中立场 · 迈阿密 7 月高温高湿是已知因子'
      + ' 【本轮=本场首轮完整情报,距开球约 2 天(T-48h);首发均为媒体预计,非官宣】'
      + ' **赛果背景(已确证)**:法国半决赛 7/14 **0-2 负于西班牙** 出局;英格兰半决赛 7/15 **1-2 负于阿根廷** 出局。两支输球队 3-4 天后踢这场。'
      + ' **本轮请重点挖**:① 🔴 **本场头号变量 = 动机与轮换**——季军赛历来是大轮换场,请找教练发布会对「是否派主力/如何看待这场」的**原话**,以及媒体对轮换幅度的预计(尤其姆巴佩/贝林厄姆/凯恩这类核心是否出战、有无人提前回国);'
      + ' ② 🔴 **德尚是否本场即其法国主帅生涯谢幕战**(其 14 年任期据报本届后结束)——若属实,是影响其排兵的关键动机因子,请核实一手源;图赫尔一侧同理找其对本场的定性;'
      + ' ③ 半决赛新产生的伤停/伤退与加时/高温体能账;④ **停赛**:半决赛有无红牌/两黄导致本场停赛者(单黄累计 QF 后已清零);另注意 **英格兰 Quansah 的 2 场红牌禁赛已于 SF2 服满、本场应已解禁可用**,请核实;'
      + ' ⑤ 主裁/VAR 任命;⑥ 迈阿密当日天气预报(Hard Rock Stadium 有遮阳顶棚但**非封闭、无空调**,高温高湿会真实影响强度)与草皮状况;⑦ 盘口(胜平负/大小球)与超算,以及市场如何给「双方都没动力」定价;⑧ 法英历史交锋与近况。' },
]
// 今晚的日期(ISO,ET/北京当天;research+verify 的「今天是」锚,决定「最近 1-3 天」时效窗)
const TODAY = '2026-07-16'
// ⬆⬆⬆ 每天只改这里(MATCHES + TODAY)⬆⬆⬆

// 6 角度(焊死,对齐 07-03/04/05 验证过的分类法:停赛与黄牌、裁判各自单拎),逐场检索
const ANGLES = [
  { key: '伤停出勤', prompt: '伤病、体能/带伤、出勤状态、是否报销(区分已发生确证 vs 存疑)' },
  { key: '停赛与黄牌', prompt: '停赛名单、直红/双黄罚下、黄牌累计规则(FIFA:淘汰赛 R32/R16/QF 三轮单黄合并累计、不清零,但**单黄累计在 QF 全部打完后已为安全过关者清零一次**,以保证无人因累计黄缺席决赛〈07-10 QF098 轮 3 票证伪坐实,ESPN/NBC/DraftKings 口径一致;勿回退〉。**今晚是半决赛(SF):QF 之前累积的单黄已清零,不存在「一黄在身、再吃一张即停决赛」这回事**——本轮只有「QF 场上吃到的直红/同场两黄」这类罚下才导致本场 SF 停赛;逐名点出本场有无确定停赛者(含红牌禁赛未满、FIFA 追加处罚),并明确说明单黄已清零、无累计黄停赛风险。若某源仍称某人「一黄在身将停决赛」,标为可疑待证伪)' },
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
