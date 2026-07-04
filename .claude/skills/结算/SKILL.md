---
name: 结算
description: 把 `data/bet_ledger.json` 里 `settled:false` 的待结彩票结算掉。用户说「结算」「结算待结」「把票结了」「/结算」即用。两层:①recommendations 单腿胜平负——引擎读赛果全自动结;②tickets 实体票(复式/串关/比分/总进球/半全场)——本 skill(LLM)把每张 picks 文本翻成结构化 legs、写进 batch.json,交确定性引擎 `tools/settle_tickets.py` 算 payout 组合数学并幂等写回。让球/比分/双选/复式的算术一律引擎算,LLM 只翻译不算钱。半全场用竞彩 sectionsNo1 半场比分自动结(引擎按需取);缺半场才待人工。注数+odds_max 双校验兜底翻译。赛果源=`.cache/odds_cache.db` 的 match_results(半场比分实时取自竞彩接口)。🔴 写回必须落**主工作区(main 检出)**那份 ledger,不停 worktree 副本。
---

# 结算 —— 待结彩票结算

把台账里 `settled:false` 的票结掉:赛果一比 → 算命中/派彩/盈亏 → 幂等写回。
**分工铁律:LLM 只做「读票文本 → 结构化 legs」的翻译;所有 payout 组合数学交确定性引擎**
`tools/settle_tickets.py`。247 注复式手算必错——LLM 一根手指都不碰算钱。

## 🔴 硬约束:对**主工作区**那份 ledger + DB 跑,不碰 worktree 副本

- **真·当前 ledger 在主工作区**(main 检出,如 `/Users/heyining/Daniel/WorkSpace/fifa-world-cup-2026`),
  且常**领先 origin/main**(新入库的票只在本地 main、未推)。git worktree 从 origin 拉,ledger 是**旧版**,
  对它跑会**静默空转**(唯一码匹配不上,什么都不结)——2026-07-04 实测 worktree 74 张 vs 主工作区 93 张。
- 赛果 DB `.cache/odds_cache.db`、`data/wc.db` 是**未跟踪文件, 根本不在 worktree 里**,必须显式指向主工作区。
- 所以本 skill 一律用**主工作区的绝对路径**跑引擎,写回落主工作区 ledger(看板读的也是它)。沿用 `彩票入库`
  的 main-ledger 教训(2026-07-02 记账落进未合并 worktree 分支的事故)。

记 `$MAIN` = 主工作区仓库根绝对路径。

## 执行步骤

### 1. 刷新赛果
先跑 `python $MAIN/tools/backfill_results.py --once`(launchd 一般已每 5 分钟在跑,这步保底拿最新终比分
进 `match_results`)。失败不阻断,继续用现有缓存。

### 2. recommendations 层(全自动,零翻译)
引擎读 ledger 里 `recommendations` 的 `settled:false` 单腿 → 查 `match_results` outcome → 写 `result`/`settled`。
让球/歧义腿引擎会自动跳过留待结(需人工再看)。这层不需要你干预,和第 3 步同一条命令一起跑。

### 3. tickets 层:读票 → 翻成结构化 legs → 写 batch.json
读 ledger 里 `settled:false` 的 `tickets`,逐张把 `picks[]` + `type` 翻成下面的结构化 JSON,汇成**数组**
写进 `$CLAUDE_JOB_DIR/tmp/settle_batch.json`(没有 job tmp 就写仓库外临时区,**不入库**)。

**每张票的结构(引擎输入契约):**

```jsonc
{
  "唯一码": "…",              // 幂等键, 从 ledger 原样照抄(含内部空格), 写回按它匹配
  "who": "LYZ", "stake": 70, "mult": 7,   // 倍数从 type 的 "×N倍" 读
  "mode": "combo" | "single_fixed",       // 复式/串关=combo; "单场固定(每场独立)"=single_fixed
  "guan_levels": [2,3,4,5,6],  // combo 用: 2串1→[2], 3串1→[3], "M场2-K关复式"→[2..K]; single_fixed 给 []
  "legs": [
    { "match_key": "086",      // picks 里的三位数场次号
      "picks": [ {"kind":"had","sel":"a","odds":2.24},        // 双选/多选 = 同一腿多个 pick
                 {"kind":"had","sel":"d","odds":2.70} ] }
  ],
  "expect_notes": 5,           // 从 type 的 "…注" 原样照抄 —— 校验用, 别自己算
  "odds_max": 134.4            // 从 ledger 的 odds_max 字段照抄 —— 校验用; 库里为 null 就填 null(引擎跳过该校验, 仍靠注数兜底)
}
```

**pick.kind 与选项(让球方向票面已写死进 picks,别猜):**

| 票面 picks 文本 | kind | 字段 |
|---|---|---|
| `086 …胜@` / `平@` / `负@` | `had` | `sel`: 主胜`h` / 平`d` / 客胜`a` |
| `087让2 …负(主队让2.0球)@` | `hcap` | `line`: 主队让 N→`-N`、受让 N→`+N`; `sel`: 让后主胜`W`/走盘平`D`/负`L` |
| `086 …2:1@` / `1:0@` | `exact` | `hs`,`as_`: 主客比分 |
| `086 …总3球@` / `总0球@` | `goals` | `n`: 总进球数 |
| `087 …胜胜@` / `平胜@`(半全场) | `htft` | `sel`: 两字, 首=半场、次=全场, 各 ∈ 胜/平/负(主队视角), 如 `胜胜`/`平胜` |

- **双选/多选**:picks 文本里 `/` 分隔的每个选项 = 该腿 picks 数组里一个 pick,各带自己的 `@赔率`。
- **让球 W/D/L 语义**:竞彩整数让球胜平负三路——让球后净差 >0 判 `W`、=0 判 `D`(走盘归平,非退款)、<0 判 `L`。
- **半全场 `htft`**:照常翻(sel 两字如 `胜胜`)。引擎检测到 batch 里有 htft 腿,会**自动从竞彩接口(`sectionsNo1` 上半场比分)按需取一次半场**再结算——不用你手动找半场比分。若某场半场比分暂时取不到,该票判待人工(不猜),等赛后重跑即可。

### 4. 跑引擎(校验 → 算账 → 幂等写回)

```bash
python $MAIN/tools/settle_tickets.py \
  --ledger $MAIN/data/bet_ledger.json \
  --struct $CLAUDE_JOB_DIR/tmp/settle_batch.json \
  --cache  $MAIN/.cache/odds_cache.db \
  --wcdb   $MAIN/data/wc.db
```

引擎对每张票:**先注数校验**(反算注数 == `expect_notes`?)→ **再 odds_max 校验**(反算最高派彩 ≈ `odds_max`?)
→ 过了才算 payout。任一不过 → 标 **待人工** 并打印原因(不静默给错 pnl)。已开赛不全的票 → **待结**,只回填
已开腿进度串、保持 `settled:false`。全开且校验过 → **已结**,写 `pnl`/`legs_hit`/`settled:true`。已结票再跑**跳过**(幂等)。

> **校验报警 = 翻译错了,不是引擎错。** 若某票报"注数校验不过 / odds_max 校验不过",回到第 3 步核对该票的
> 双选个数、关数、赔率、mode——注数和 odds_max 是从票面照抄的真值,反算对不上说明 legs 翻错了,改对再跑。
> 先 `--dry-run` 看一遍全绿再去掉 `--dry-run` 落盘,是稳妥做法。

### 5. 收尾报告
给用户:
- **已结**票清单(每张 who / 玩法 / 命中几腿 / pnl)+ 按人本轮盈亏;
- **待结**票(哪几场还没开赛);
- **待人工**票(半全场 / 校验不过的,附原因),请用户定夺或补数据。
- 确认写回落在 **主工作区 `data/bet_ledger.json`**(看板读的那份),不是 worktree 副本。

## 边界

- 让球方向、比分、赔率一律照 ledger 已存 picks 读,不猜、不改(入库时已把方向写死进 picks)。
- 本 skill 不重读票面图片(picks 文本已由 `彩票入库` 存库),只消费文本。
- 半全场用竞彩 `sectionsNo1` 上半场比分自动结(引擎按需实时取);仅当某场半场比分取不到时才该票待人工,不瞎判。
- 算术全在引擎,LLM 不手算任何 payout;引擎的注数/odds_max 双校验是翻译对错的最后防线。
- 结构化中间文件不入库(写 job tmp / 仓库外),ledger 只存最终 pnl/legs_hit/settled。
- 设计与引擎契约详见 `docs/superpowers/specs/2026-07-04-jiesuan-settlement-skill-design.md`。
