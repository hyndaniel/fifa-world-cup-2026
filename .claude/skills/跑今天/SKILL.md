---
name: 跑今天
description: 世界杯每日预测一键编排。用户说「跑今天」「跑一下今天」「今天预测」「/跑今天」即用。按 ET 当下时刻判定该预测哪几场 → 并行派三条独立 pass(v1 比分 / v2 概率 / 价值)→ 跑 Brier 跑分卡 → 跨两库 join 成决策对象 → 写回 HK 看板。死守 v1⊥v2 红线(派 wc-forecaster-v2 时其上下文绝不含任何 v1 输出)。做掉每日 routine ~80%,赛前~1h 首发微调窗仍需临场再触发一次。
---

# 跑今天 —— 每日预测一键编排

替代「每天找 Claude 聊一长串」的日常仪式。**你(主会话)是编排者,本身不重写任何预测逻辑**——
只调度三个已有 agent + 已有脚本,把散落各处的预测/概率/价值收口成看板「每场决策卡」。

本仓库根 = worktree 根(`config.toml`、`tools/`、`reports/`、`backend/`、`.cache/` 都在此)。
所有命令用仓库根的相对/绝对路径跑;Bash 调用间 cwd 会重置,必要时先 `cd` 到仓库根。

---

## 🔴 头号红线:v1 ⊥ v2(预测时刻互不可见)

派 **wc-forecaster-v2** 时,它的 prompt **只能含**两样东西:
1. 市场基线 `baseline_market(...)`(胜平负 / 让球 / 总进球)
2. 中立事实卡 `match_fact_card(...)`(确证新闻事实)

**绝对禁止**把 **football-match-predictor(v1)** 的任何输出——比分(score)、胜平负
概率(probs)、出线诉求、依据文字——放进 v2 的 prompt / 上下文。一个字都不行。

**为什么(必须懂,不是仪式):** 项目用三方 Brier 跑分卡(`reports/预测v2.md`)对照
v1 / v2 / 市场基线谁更准。这套对照**只有在 v1 和 v2 各自独立产出、预测时刻互不污染**时
才成立。一旦 v2 看过 v1 的比分再"预测",两者就相关了,Brier 对照立刻失效、再也分不清
v2 的 market-anchored 方法到底有没有用。**这条红线护的是整套评估的有效性,不能为省事破。**

**实现保证:** v1 与 v2 作为**两个各自独立的 subagent 调用**派出(各开各的上下文)。
不要在同一个 prompt 里同时给两边、也不要把 v1 跑完的结果"顺手"塞进 v2 的派单。

**注意区分:** 这条红线只管「**预测时刻**别互看」。第 5 步把 v1/v2 并排塞进同一张决策卡、
前端并排展示——**不违红线**(那是"查看时并排",预测早已各自独立产出完毕)。

---

## 7 步流程

### 第 1 步 · ET 时区判定今天该预测哪几场

**ET 时区铁律(workflow 第 10 条,血泪):** 所有开球以 **ET** 为准。用户在东八区
CST = ET + 12/13。判断"今晚/明天/是否已开赛"**必须看 ET 当下**,不可用 CST 日历翻篇当依据。
**温哥华场用 PT = ET − 3。**

1. 取 ET 当下时刻:
   ```
   TZ=America/New_York date
   ```
2. 读进度,看已落盘到哪一天:
   - `reports/小组赛比分预测.md`——逐日 `## M.D(第 N 比赛日,X 场)` 节,看最后一节是哪天、
     该天是否已落预测(未回填实际属正常,看的是"预测写没写")。
   - 仓库根若有 `memory/wc2026-prediction-status.md` / `memory/wc2026-prediction-workflow.md`
     则一并读(赛程进度 + 13 条打法);**本 worktree 可能缺这两份,缺就以报告为准、不报错。**
3. 按 ET 当下推出"今天/明天该预测哪几场":
   - 报告里每场都标了 `Xpm ET(= 北京 M.D HH:MM)`。用 ET 当下 + 报告进度,选出
     **尚未落预测、且开球在 ET 今日(或紧邻次日)的场次**。
   - 同组末轮两场同时开球——一起算。
4. 把判定结果(场次清单 + 各场 `zucai_num`(形如 `周四055`)+ 主-客球队名)念给用户确认,
   再往下走。**拿不准是哪一轮/哪几场就先问一句**,绝不替用户编场次。

> **`match_key` = 竞彩 `zucai_num`(形如 `周四055`),odds_cache 与 wc.db 同键**——不是
> "X vs Y" 字符串。所有 baseline_market / match_fact_card / record_v1 / record_v2_prediction /
> get_v1 / get_v2_prediction 都用它。球队名由 `data/wc.db` 经 zucai_num 反查(home_cn/away_cn)。
> 第 5 步组 Decision 时,`Decision.match_key` 用「主队 vs 客队」串(看板展示可读),内部查库仍用 zucai_num。

### 第 2 步 · 确保盘口新鲜(三源)

价值与 v2 基线都吃盘口,陈旧盘口会伪造假黄档(见项目记忆 [Stale Poly = false yellow])。
出价值/v2 结论前必须先刷:

1. **竞彩(足彩,本地直连):**
   ```
   python3 tools/odds_watch.py --once
   ```
   抓 had/hhad/ttg → 存 `.cache/odds_cache.db`,打印相对上次水位变化。本地有 launchd
   常驻采集器在跑则已新鲜,跑 `--once` 再确认一次无妨。
2. **欧盘共识(第二交叉):**
   ```
   python3 tools/odds_watch.py --consensus
   ```
3. **Polymarket(聪明钱去水)——经 remote-agent MCP 在 aws-hk 抓,本地被墙:**
   按 `tools/poly_fetch_hk.py` 文件头的三命令流程,由**你(主会话)经 remote-agent MCP** 驱动:
   - Mac: `python3 tools/poly_fetch_hk.py build-list --out matches.json`
   - `remote_upload` 脚本 + matches.json 到 aws-hk:/tmp;`remote_exec` 跑 `fetch`;`remote_download` 回 Mac
   - Mac: `python3 tools/poly_fetch_hk.py ingest poly_ingest.json`
   - (可复用步骤见项目记忆 [Poly refresh recipe])
4. 取不到/缺位就**明说哪源缺**,不拿陈旧/缺失硬算。Poly 缺 → v2/价值的相关结论降靠谱度并标注。

### 第 3 步 · 并行派三条独立 pass(死守 v1⊥v2 红线)

对第 1 步选出的每一场,派出三个**各自独立**的 subagent。三条互不共享上下文。

**Pass v1 = `football-match-predictor`(比分 + 出线):**
- 任务:出线形势 + 比分(主-客顺序)+ 自评胜平负 % + 一句出线诉求/依据。
- 落盘:写回 `reports/小组赛比分预测.md`(它自己维护体例),并把概率+比分记进 v1 库:
  ```
  python3 -c "from backend.v1_log import record_v1; record_v1('.cache/odds_cache.db', '<match_key>', {'h':30,'d':30,'a':40}, '0-1')"
  ```
  (probs 为胜平负 %,score_pred 为主-客比分串。)
- 它**可以**看盘口/首发/出线——这些是它自己的三层叠加输入,不受红线限制。

**Pass v2 = `wc-forecaster-v2`(market-anchored 概率)—— 🔴上下文只含基线+事实卡:**
- 给它的派单**只准**含:
  1. 逐盘口基线(它自己会跑,你也可预取塞进派单,数据同源):
     ```
     python3 -c "from backend.baseline import baseline_market, HAD_CFG, HHAD_CFG, TTG_CFG; print(baseline_market('.cache/odds_cache.db','<match_key>',HAD_CFG)); print(baseline_market('.cache/odds_cache.db','<match_key>',HHAD_CFG)); print(baseline_market('.cache/odds_cache.db','<match_key>',TTG_CFG))"
     ```
  2. 中立事实卡(确证新闻,恒无首发)。**match_key 即竞彩 `zucai_num`(形如 `周四055`),
     odds_cache 与 wc.db 同键。直接传 match_key 即可——它经 `db.match(zucai_num)` 查 `data/wc.db`:**
     ```
     python3 -c "from backend.db import Db; from backend.intel import match_fact_card; from datetime import datetime, timezone, timedelta; print(match_fact_card(Db('data/wc.db'), '<match_key>', datetime.now(timezone(timedelta(hours=8)))))"
     ```
     (事实来自 enrich 表,两条入口:① watchlist 覆盖队的 `tools/collect_enrich.py` 推 Google-News
     新闻到 HK;② deep-search 确证事实经 `tools/save_intel.py`(`--db` 指主 checkout 的 `data/wc.db`、
     `--json` 为抽取后的 `{team_cn:[facts]}`)写进 enrich。v2 两者都经 `match_fact_card` 读,stale 阈值 48h。)
- **绝不**把 v1 的 score/probs/依据放进这个派单。
- v2 落库(它自己跑 build_v2_prediction → record_v2_prediction):
  ```
  record_v2_prediction('.cache/odds_cache.db', '<match_key>', <build_v2_prediction 产物>)
  ```
- 产物含:逐盘口 baseline + 有据偏离(每条带 `factor_source`)、整场靠谱度(稳/中/乱)、剧本标签。

**Pass 价值 = `odds-value-analyst`(green/yellow + 最不亏腿):**
- 任务:对每场每选项算 value = 竞彩欧赔 × Poly去水(p_true),EV%,分档(🟢green≥1.03 / 🟡yellow0.97–1.03 /
  🔴red<0.97明显-EV;⚪skip=陷阱桶/缺对应 Poly 线自动跳过),点出"最不亏"那条腿。它从 `.cache/odds_cache.db` 读 Poly(不自抓)。
- 落盘:它自己维护 `reports/盘口下注复盘.md`。

> 三条独立性的实现 = 三个独立 subagent 调用。派 v2 前**自检**:这份 prompt 里有没有任何
> 来自 v1 的比分/概率/文字?有 → 删掉再派。

### 第 4 步 · 跑 v2 Brier 跑分卡

```
python3 tools/v2_report.py
```
→ 渲染 `reports/预测v2.md`(v1 / v2 / 市场基线 三方平均 Brier + 偏离审计 + 归因)。
只对**已回填实际比分**的场次计分;今天刚预测、未开赛的场不计入(正常)。

### 第 5 步 · 跨两库 join → 决策对象列表 → POST 看板

**数据架构铁则:所有跨库 join 在本地这一步做。** 看板不认识 match_key、不读
`.cache/odds_cache.db`;你把 v1 比分 + v2 概率 + 价值结论汇成「决策对象」POST 给看板,
看板**原样存、原样回吐**。

1. **取看板地址 + 密码(现取,不打印、不入库):**
   从仓库根 `config.toml` 读 `[server] password`,与看板基址组 basic auth。
   - `WC_INGEST_URL` 在本步取**看板基址、不含路径**(如 `http://18.166.71.60:8000`),与
     `tools/collect_enrich.py` 一致。**注意 `tools/collect_zucai.py` 把 `WC_INGEST_URL` 当成含
     `/api/ingest/zucai` 的全路径用,语义不同——本步勿沿用 zucai 那份导出值;** 下面 POST 脚本
     已兜底:先 `split("/api/ingest")[0]` 剥掉可能误带的路径再拼。`WC_INGEST_PW` = 看板密码。
   - 密码当作 secret:**绝不 print 到对话、绝不写进任何 reports/*.md 或库**(见项目记忆惯例)。
   - 用 `os.environ["WC_INGEST_PW"]` 或临时读 `config.toml` 进变量,只在 POST 的 Authorization 头里用一次。

2. **三库三源 join 成 Decision 列表**(读 v1_predictions + v2_predictions + 价值结论)。
   每个 Decision 形状**严格按契约 §1**(任一块可整块缺,前端降级不崩):
   ```jsonc
   {
     "match_key": "韩国 vs 南非",          // 必填,upsert 主键(替换语义)
     "home_cn": "韩国", "away_cn": "南非",  // 必填
     "home_flag": "🇰🇷", "away_flag": "🇿🇦", // 选填
     "ko_bj": "6.27 02:00", "ko_et": "ET 6.26 14:00", // 选填(展示串)
     "cutoff_bj": "01:00", "status": "Selling",       // 选填
     "v1": {"score": "0-1", "rationale": "韩平即出线", "probs": {"h":30,"d":30,"a":40}},
     "v2": {"probs": {"h":38,"d":30,"a":32}, "reliability": "乱",
            "scenarios": ["默契平"], "deviated": true},
     "value": {
       "verdict": "该场别碰",
       "best_leg": {"market":"hhad","outcome":"a","desc":"南非 +0.5","flag":"yellow","ev_pct":-1.2},
       "legs": [{"market":"had","outcome":"a","desc":"南非胜","zucai_odds":3.10,
                 "poly_prob_devig":32.0,"ev_pct":-0.8,"flag":"yellow"}]
     },
     "updated_at": "2026-06-25T16:00:00+08:00"        // 选填
   }
   ```
   - `v1` 块取自 `get_v1('.cache/odds_cache.db', key)`(probs + score_pred);rationale 从 v1 这场依据摘一句。
   - `v2` 块取自 `get_v2_prediction('.cache/odds_cache.db', key)`:`probs` = had 的 `markets.had.v2`,
     `reliability` = `reliability`,`scenarios` = `scenarios`,`deviated` = had 有无 `deviations`。
   - `value` 块取自 odds-value-analyst 这场的结论(verdict / best_leg / legs)。
   - `flag` ∈ {green, yellow, red, skip}(`value.py` 实产四档:≥1.03 green / 0.97–1.03 yellow /
     <0.97 red(明显-EV) / 陷阱桶 skip);**别把 red 折叠成 skip**——前端 🔴 单独显示,守诚实定位。
   - 缺哪块就**省略哪块**(别塞空壳),前端会显示"未出/—"。

3. **POST 到看板:**
   ```
   POST <看板基址>/api/ingest/predictions
   body: {"decisions": [<Decision>, ...], "ts": "<iso, 选填>"}
   header: Authorization: Basic base64(admin:<password>), Content-Type: application/json
   ```
   返回 `{"accepted": true, "n": <写入条数>, "skipped": <缺 match_key 跳过数>}`。
   看板按 match_key upsert(替换语义),**不**删未在本批出现的旧决策(保留历史卡)。

   纯 stdlib 一次性 POST(镜像 collect_zucai.py),`<...>` 处填实际值:
   ```
   python3 - <<'PY'
   import base64, json, os, urllib.request
   base = os.environ["WC_INGEST_URL"].split("/api/ingest")[0].rstrip("/")  # 剥误带路径→纯基址
   pw   = os.environ["WC_INGEST_PW"]                 # 从 config.toml [server].password 现取,不打印
   user = os.environ.get("WC_INGEST_USER", "admin")
   decisions = [ ... ]                               # 上面 join 出的 Decision 列表
   body = json.dumps({"decisions": decisions}, ensure_ascii=False).encode()
   hdr = {"Content-Type": "application/json",
          "Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}
   req = urllib.request.Request(base + "/api/ingest/predictions", data=body, headers=hdr, method="POST")
   with urllib.request.urlopen(req, timeout=60) as r:
       print(r.status, r.read().decode())
   PY
   ```
   (`WC_INGEST_PW` 从 config.toml 现取后 export 进环境再跑,跑完即弃;**别把密码写进脚本字面量、别 echo。**)

### 第 6 步 · 给用户一段扫读摘要

一段话,让用户不打开看板也能扫:
- 今天预测了哪几场(对阵 + 开球 ET/北京);
- 每场一行:v1 比分 / v2 胜平负 % + 靠谱度 + 剧本 / 价值结论(有无真价值,最不亏腿);
- Brier 跑分卡有无新变化(若有已回填的赛果);
- 已 POST 看板 `n` 条决策卡,可在「决策」tab(手机)看;
- 哪些场因缺源(Poly 缺/盘口未出)降了靠谱度或暂缺某块。

### 第 7 步 · 诚实边界(必须告诉用户,不假装全自动)

**本 skill 做掉每日 routine 的约 80%。剩下 20% 是边际收益最高、却无法预先自动化的:**

- **赛前 ~1h 首发微调窗(workflow 第 11 条):** 首发名单临场才出炉。开赛前约一小时,
  WebSearch 首发 + 盘口最后水位,若首发与依据冲突且盘口跟进(独赢/让球/大小任一跳水)→ 该改预测。
  这一步**必须临场再触发一次**——要么用户届时再喊一遍「跑今天」(或单场重跑),
  要么本 skill 在摘要末尾**明确提示**:"X 场开球 ET HH:MM,约 ET HH:MM(赛前~1h)记得回来跑首发微调窗。"
- **末轮/动机倒挂场**变量最难量化(已出线大轮换、默契平、生死战)——这些场更要靠首发窗临场核。
- 保留聊天通道:要改某场预测,继续找 Claude 聊即可(本 skill 不锁死)。

**绝不**在摘要里宣称"今天全自动搞定了"。明确区分:routine 这趟跑完了 / 首发窗待临场。

---

## 红线 / 纪律速查

- 🔴 **v1⊥v2:** 派 wc-forecaster-v2 的 prompt 只含 baseline_market + match_fact_card,
  **零** v1 输出。三个独立 subagent 调用。护三方 Brier 对照。
- 🔒 **密码 secret:** 看板密码从 config.toml 现取,**不 print、不入库、不写报告**。
- 📏 **加不删:** 本 skill 只编排现有 agent/脚本,不重写预测/价值逻辑;不改 `.claude/agents/*.md`。
- ⏱ **ET 铁律:** 开球判定一律看 ET 当下,温哥华场 PT = ET − 3。
- 🧊 **不拿陈旧硬算:** Poly/盘口缺或旧 → 明说、降靠谱度,不伪造黄档。
- 🚫 **不自动下注、不碰资金。** 价值结论永远是"若要下,这条最不亏",不是"去下"。
