# 结算 skill 设计文档

> 2026-07-04 · 把"结算待结的彩票"沉淀成可复用 skill `结算`。

## 1. 背景与现状

`data/bet_ledger.json` 有两个数组,是两套不同难度的结算路径:

- **`recommendations`**(每日胜平负/让球**单腿**价值票):在库里本就结构化(match / leg / odds)。
  结算 = 单腿对比赛 outcome 一比 → 写 `result: win/loss` + `settled: true`。**纯结构化、可完全自动化**。
- **`tickets`**(实体购彩票:复式/串关/比分/总进球/半全场):`picks` 文本已由 `彩票入库` skill 存入库。
  结算需要把 picks 文本 → 结构化 → 精确算复式/双选/让球/总进球 **payout 组合数学**。

**历史做法(都是手工,没有自动引擎):**
- recommendations:跑每日流程时人工判、手写 `result`/`settled`。代码里无任何工具自动写(`bet_stats.py` 只**读**)。
- tickets:老 `tools/settle_tickets.py` 是**纯 print 一次性脚本**(不 `open`、不读写 ledger)。当年把每张票的 picks
  手翻成脚本里的 `settle(lambda…)`,跑一遍看 print 出的 pnl,**再手抄回 ledger**。只覆盖 049-072 那批,
  无总进球/半全场,不可复用、不自动写回。

当前 `tickets` 共 93 条,**21 条待结**(`settled:false`),集中在 serial `2607031`/`2607051`、场次 086-092,
玩法含胜平负 / 让球 / 比分 / 总进球 / 半全场,大量是**复式**(2-8 关全组合,单张最多 247 注)+ **双选**。

## 2. 赛果数据源

- **终场比分**:`.cache/odds_cache.db` 的 `match_results` 表(`match_key, home_goals, away_goals, outcome, ts`),
  由 `tools/backfill_results.py --once`(launchd 每 5 分钟无害重跑)自动抓 upsert。`match_key` 形如 `周四085`,
  **尾三位数**对齐 ledger picks 里的 `086`。**只有完赛(finished)的场次才在表里。**
- **半场比分**:竞彩开奖接口(与 FT 同一个 `getUniformMatchResultV1.qry`)的 `sectionsNo1` 字段即上半场
  比分(`sectionsNo999` 是全场)。引擎在遇到半全场票时**按需实时取一次**半场比分自动结算;某场半场暂取不到才待人工。
  *(2026-07-04 补:原设计以为无半场源、半全场一律待人工;实测同接口已带 `sectionsNo1`,遂改为自动结。)*

## 3. 决策(已与用户敲定)

> **LLM 管翻译,Python 管所有算术,注数 / odds_max 双校验兜底两边。**

- **翻译(picks 文本 → 结构化 legs)交给 LLM(主会话)**:量小(每批几张)、抗格式漂移(出新玩法不改代码);
  票面在入库时已把让球方向写死进 picks(`主队让2.0球`/`主队受让2.0球`),方向不含糊。
- **所有算术交给确定性引擎**(重写后的 `tools/settle_tickets.py`):复式组合、双选、让球命中、总进球、
  单场固定独立结算、payout、pnl、写回。LLM 不碰任何数。
- **注数 + odds_max 双校验**:引擎从解析出的结构反算注数,断言 `== 票面注数`;反算理论最高派彩,断言 `≈ odds_max`。
  任一不过 → **拒绝算、标待人工**,不静默给错 pnl。把 LLM 翻译错 / 引擎解析错都变成响亮报警。
- skill 名 **`结算`**;引擎**重写覆盖**老 `tools/settle_tickets.py`(纯 print、无人 import,可安全替换)。

## 4. 架构

两个单元,职责隔离:

### 4.1 结算引擎 `tools/settle_tickets.py`(重写,数据驱动 + CLI)

**纯函数库 + CLI**,不含任何硬编码的具体票。对外提供:

- `settle_ticket(ticket_struct, results) -> SettleResult`:核心。给一张结构化票 + 赛果字典,
  返回 `{status, legs_hit, pnl, notes_check, payout, reason}`。
- CLI:`python tools/settle_tickets.py --ledger <path> [--struct <batch.json>] [--dry-run]`
  - **总是**结 recommendations 层(直接从 ledger + `match_results` 判,无需 struct)。
  - `--struct <batch.json>`:主会话/LLM 把本批待结 tickets 翻成 §4.1 结构化 JSON 的**数组**写进这个文件
    (放 `$CLAUDE_JOB_DIR/tmp` 或仓库外临时区,不入库),引擎读它逐张 `settle_ticket` 并按 `唯一码` 幂等写回 ledger。
    不给 `--struct` 就只结 recommendations 层。
  - `--dry-run`:只打印将写回什么,不落盘。

**输入契约(每张 ticket 的结构化形式,由主会话/LLM 产出):**

```jsonc
{
  "唯一码": "…",              // 写回时按此匹配 ledger 里的票(幂等键)
  "who": "LYZ",
  "stake": 70,
  "mult": 7,                  // 倍数
  "mode": "combo" | "single_fixed",   // 复式/串关 vs 单场固定(每场独立结算)
  "guan_levels": [2,3,4,5,6], // 关数列表; combo 用; 2串1→[2], 3串1→[3]; single_fixed 忽略
  "legs": [
    { "match_key": "086",
      "picks": [ {"kind":"had","sel":"a","odds":2.24},          // 双选=picks 里多个
                 {"kind":"had","sel":"d","odds":2.70} ] },
    …
  ],
  "expect_notes": 5,          // 票面注数(校验)
  "odds_max": 134.4           // 票面最高奖金(校验)
}
```

`kind` 取值与命中判定:
- `had`(胜平负):`sel ∈ {h,d,a}`,对 outcome。
- `hcap`(让球胜平负):`{line, sel}`,`(hs+line) - as_` 的符号 → W/D/L,对 `sel`。`line` 主队让 N=`-N`、受让 N=`+N`。
- `exact`(比分):`{hs, as_}`,对终比分。
- `goals`(总进球):`{n}`,对 `hs+as_`。
- `htft`(半全场):`sel` 两字(首=半场、次=全场,各 胜/平/负)。引擎按需取 `sectionsNo1` 半场比分结算;
  半场取不到才待人工。

**payout 算法(`unit = 2 × mult`):**
- `combo`:`Σ_{k∈guan_levels} Σ_{C(M,k) 组合} [组合内每腿都命中] · unit · ∏赔率`(命中赔率取该腿命中的那个 pick 的 odds)。
- `single_fixed`:每腿独立,`Σ_腿 [该腿任一 pick 命中] · unit · 命中赔率`。
- `pnl = payout − stake`。

**行为约束:**
- **校验优先**:算 payout 前先跑注数/odds_max 校验,不过就 `status="待人工"` 带 reason,不写 pnl。
- **部分开赛**:若某腿的 `match_key` 不在 `results`(未完赛),**保持 `settled:false`**,`legs_hit` 只回填已开腿的
  ✅/❌ 进度串(沿用现有 HYN 票 note 风格,如 `命中082/083/085,失080/081/084;086/087待结`)。
- **半全场 / 校验不过 / 缺终比分** → `status="待人工"`,列清单,不猜。
- **幂等写回**:按 `唯一码` 定位 ledger 里的票,只在全腿结清且校验通过时写 `pnl` + `settled:true` + 最终 `legs_hit`;
  重复跑不改已结票。写回字段口径对齐现有已结票(`legs_hit` 如 `中{056,059,060}` 或 `1/2`,`pnl` 数值)。

### 4.2 recommendations 层结算(引擎内的一段,纯脚本零 LLM)

读 ledger `recommendations` 里 `settled:false` 的单腿 → 查 `match_results` outcome →
按该腿 `leg` 文字判 win/loss(单腿胜平负/让球)→ 写 `result` + `settled:true`。缺终比分则跳过留待结。

### 4.3 skill `.claude/skills/结算/SKILL.md`(编排)

触发词:`结算` / `结算待结` / `把票结了` / `/结算`。流程:

1. 跑 `tools/backfill_results.py --once` 刷新 `match_results`。
2. 引擎结 **recommendations 层**(全自动)。
3. **tickets 层**:主会话读 ledger 里 `settled:false` 的票,把每张 `picks`+`type` 翻成 §4.1 结构化 JSON,
   汇成数组写进 `$CLAUDE_JOB_DIR/tmp/settle_batch.json`;半全场票直接标待人工、不翻不进 batch。
4. 调引擎 `python tools/settle_tickets.py --ledger data/bet_ledger.json --struct <batch.json>`:
   校验 → 算 payout/pnl → 幂等写回。
5. 汇总:已结清票的 per-person 盈亏小结 + 待人工清单(半全场 / 校验不过 / 未开赛)+ 本次写回了哪几张。

## 5. 🔴 硬约束:写回落 main 台账,不停在 worktree

沿用 `彩票入库` 的教训(2026-07-02 事故:记账 commit 落进未合并 worktree,main 台账停滞):
**结算写回的 `data/bet_ledger.json` 必须是 main 跟踪、HK 看板读取的那一份规范台账。**
即使 skill 跑在后台 job 的隔离 worktree 里,写回也要落到**主工作区(main 检出)**那份,或写完**立即**经
`数据快推` / 合并推送同步进 main。收尾自检必须确认改动出现在 main 台账或已同步看板,**不是**只在 worktree 副本。

## 6. 测试

- **引擎单测**(`tests/`):用**已结的历史票**当回归夹具——取 ledger 里若干已结 `tickets`(带已知 `pnl`/`legs_hit`),
  手工造出其结构化输入,断言 `settle_ticket` 复算出的 pnl == 台账已记 pnl。覆盖:combo 复式、single_fixed、
  双选、hcap、exact、goals 各一。
- **校验器单测**:注数反算(2-8 关全组合 247 注等)、odds_max 反算,故意造错断言报待人工。
- **部分开赛单测**:缺一腿 → 保持待结 + 进度串正确。

## 7. 范围外(YAGNI)

- 不抓半场比分、不引入新数据源(半全场一律待人工)。
- 不重新读票面图片:picks 文本已在库(入库 skill 的职责),结算只消费文本。
- 不改 recommendations/tickets 的 schema,不动看板前端。
- 不做自动 picks 硬解析器(翻译交 LLM);若将来想自动化,再单立一个解析器单元,不在本次范围。
