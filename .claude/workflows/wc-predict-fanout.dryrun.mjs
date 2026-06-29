// wc-predict-fanout 的红线 dry-run 复核(纯 stdlib node,不派真 agent)。
// 跑法:  node .claude/workflows/wc-predict-fanout.dryrun.mjs
// 作用:  用 stub 替身把 agent()/parallel() 打桩,真实跑一遍脚本控制流,断言:
//        ① 返回结构齐全  ② 每场派 4 个 agent  ③🔴 v2 prompt 含锚+事实卡且**绝不**含 v1 输出。
// 改了 wc-predict-fanout.js 的派单逻辑后,务必重跑本测确认红线没破。
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const scriptPath = process.argv[2] || path.join(here, 'wc-predict-fanout.js')
const body = fs.readFileSync(scriptPath, 'utf8').replace('export const meta', 'const meta')

const recorded = []
const agent = async (prompt, opts) => {
  recorded.push({ label: opts && opts.label, agentType: opts && opts.agentType, prompt })
  const t = opts && opts.agentType
  // probs 用可检测 sentinel(917/918/919),抓「只把 v1 胜平负% 当先验塞进 v2」这种部分泄漏
  if (t === 'wc-score-v1') return { match_key: '周四055', probs: { h: 917, d: 918, a: 919 }, score_pred: 'V1SENTINEL-9-9', rationale: 'V1SECRET-RATIONALE-XYZ', qualification: 'V1SECRET-QUAL', persisted: true }
  if (t === 'wc-prob-v2') return { match_key: '周四055', probs: { h: 40, d: 30, a: 30 }, reliability: '中', scenarios: ['默契平'], deviated: false, deviations: [], persisted: true }
  if (t === 'wc-odds') return { match_key: '周四055', consensus: '主胜略占优', divergence: '竞彩主胜更慷慨 +3pp', moves: '无', poly_fresh: true, note: '' }
  if (t === 'wc-bet') return { match_key: '周四055', verdict: '无真价值,空仓最优', best_leg: { market: 'hhad', outcome: 'a', desc: '美国 +0.5', flag: 'yellow', ev_pct: -1.1 }, legs: [], persisted: true }
  return {}
}
const parallel = async (thunks) => Promise.all(thunks.map((t) => t()))
const pipeline = async () => { throw new Error('pipeline 不应被本脚本调用') }
const log = (m) => console.error('[log]', m)
const phase = () => {}
const workflow = async () => { throw new Error('nested workflow 不应被调用') }
const budget = { total: null, spent: () => 0, remaining: () => Infinity }
const args = {
  matches: [{
    match_key: '周四055', home_cn: '土耳其', away_cn: '美国', ko_et: 'ET 6.25 21:00', ko_bj: '6.26 09:00',
    v2_baseline: { had: 'HADBASE-uniq', hhad: 'HHADBASE-uniq', ttg: 'TTGBASE-uniq' },
    v2_factcard: 'FACTCARD-NEUTRAL-uniq',
  }],
}

const AsyncFunction = (async () => {}).constructor
const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'log', 'phase', 'workflow', 'budget', 'args', body)
const out = await fn(agent, parallel, pipeline, log, phase, workflow, budget, args)

const fail = []
if (!Array.isArray(out) || out.length !== 1) fail.push(`返回应为长度1数组,实得 ${JSON.stringify(out)}`)
const r0 = out[0] || {}
for (const k of ['match_key', 'home_cn', 'away_cn', 'v1', 'v2', 'odds', 'value']) if (!(k in r0)) fail.push(`返回对象缺字段 ${k}`)
const v2rec = recorded.find((r) => r.label && r.label.startsWith('v2:'))
const v1rec = recorded.find((r) => r.label && r.label.startsWith('v1:'))
const betrec = recorded.find((r) => r.label && r.label.startsWith('bet:'))
if (!v2rec) fail.push('没记录到 v2 派单')
if (!v1rec) fail.push('没记录到 v1 派单')
if (recorded.length !== 4) fail.push(`每场应派 4 个 agent(v1/v2/odds/bet),实得 ${recorded.length}`)
const v2HasBaseline = v2rec && v2rec.prompt.includes('HADBASE-uniq') && v2rec.prompt.includes('FACTCARD-NEUTRAL-uniq')
// 红线泄漏:字符串字段(score/rationale)或 probs sentinel(917/918/919)任一现身 v2 prompt = 破
const v2LeaksV1 = v2rec && (v2rec.prompt.includes('V1SENTINEL') || v2rec.prompt.includes('V1SECRET') ||
  v2rec.prompt.includes('917') || v2rec.prompt.includes('918') || v2rec.prompt.includes('919'))
if (!v2HasBaseline) fail.push('v2 prompt 未含 baseline/factcard')
if (v2LeaksV1) fail.push('🔴🔴 v2 prompt 泄漏了 v1 输出(含 probs 先验)—— 红线破!')
const betSeesAll = betrec && betrec.prompt.includes('V1SENTINEL') && betrec.prompt.includes('默契平') && betrec.prompt.includes('主胜略占优')
if (!betSeesAll) fail.push('bet prompt 未见三方(v1/v2/odds 综合退化)')

console.log('=== wc-predict-fanout dry-run(stub agent)===')
console.log('返回场数            :', Array.isArray(out) ? out.length : 'N/A')
console.log('返回字段齐全        :', !['match_key', 'home_cn', 'away_cn', 'v1', 'v2', 'odds', 'value'].some((k) => !(k in r0)))
console.log('派单总数(应=4)      :', recorded.length, recorded.map((r) => r.label).join(' '))
console.log('v2 prompt 含锚+事实卡 :', !!v2HasBaseline)
console.log('🔴 v2 prompt 泄漏 v1  :', !!v2LeaksV1, '  <- 必须 false')
console.log('bet prompt 见三方     :', !!betSeesAll, '  <- 下游应 true')
console.log()
if (fail.length) { console.log('❌ 校验失败:'); fail.forEach((f) => console.log('  -', f)); process.exit(1) }
console.log('✅ 全部断言通过')
