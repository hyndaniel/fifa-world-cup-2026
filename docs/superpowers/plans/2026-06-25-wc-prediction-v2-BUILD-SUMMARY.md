# 预测 v2 — AFK 构建小结(2026-06-25 夜)

分支:`feat/wc-prediction-v2`(**未 push、未开 PR、未合并**,留你 review)

## 做完了什么
按 brainstorm→spec→3 份计划→subagent-driven-development 流程,AFK 用 opus 子代理逐任务实现了 Phase 1-3 的**全部代码**,每任务一个 commit,每阶段主控亲跑测试验证,最后一次 opus 整支评审。

- **Phase 1**(基线引擎+打分+回测):`backend/scoring.py`、`backend/baseline.py`、`tools/backtest_baseline.py`。
- **Phase 2**(v2 装配+剧本库+agent):`backend/scenarios.py`、`backend/v2_predict.py`、`reports/scenario_library.json`(seed 5 条剧本)、`.claude/agents/wc-forecaster-v2.md`。
- **Phase 3**(v1 并行+三方跑分卡):`backend/v1_log.py`、`backend/scorecard.py`、`tools/v2_report.py`。

提交:docs 1 个 + 任务 13 个 + 本小结 = 15。全套测试 **29 passed**。

## 最终评审裁定
**可合并(Approved)。0 Critical / 0 Important / 4 Minor。** 评审独立重跑了关键数学边界(去水残差归一、Brier 守卫、校准边界、偏离分支)+ 全套 29 passed,核了 spec 合规与跨模块一致性。

**4 Minor(不阻塞,留你决定要不要修):**
1. 跑分卡报告里 per-match 的 Brier 格式化粒度与三均值不齐(纯展示)。
2. 同上,展示层小不一致。
3. `brier_multi` 对"actual 不在 probs 里"静默按缺失处理(行为正确,可加注释)。
4. 同场多条偏离会重标前者(spec 要求偏离稀、实际不触发)。

## 留给你的(非 AFK,需你在场)
1. **首次实跑 v2 agent**:用 `wc-forecaster-v2` 对真实比赛出预测(需实时数据 + 你的判断,我没擅自跑)。
2. **"全盘口"目前只落地了胜平负**(had):让球/大小球/比分/半全场是 spec §9.1 的后续增量,要的话再开 Phase 4 计划。
3. **真实 Brier 基准数**:需真实 `.cache/odds_cache.db` + 录入实际比分后,跑 `python3 tools/backtest_baseline.py` 才有数(代码已就绪)。
4. **决定去留**:review 分支 → 合并 main(我没 push/PR);或要我改那 4 Minor。

## 怎么用(命令)
- 录入某场实际比分:`python3 -c "from backend.baseline import record_result; record_result('.cache/odds_cache.db','周三053',0,1)"`
- 跑市场基线 Brier 基准:`python3 tools/backtest_baseline.py`
- 渲染三方跑分卡:`python3 tools/v2_report.py`(写 `reports/预测v2.md`)
- 跑全套测试:`python3 -m pytest tests/test_scoring.py tests/test_baseline.py tests/test_backtest_baseline.py tests/test_scenarios.py tests/test_v2_predict.py tests/test_v1_log.py tests/test_scorecard.py tests/test_v2_report.py -v`

## 参考
- 设计:`docs/superpowers/specs/2026-06-25-wc-prediction-v2-design.md`
- 计划:`docs/superpowers/plans/2026-06-25-wc-prediction-v2-phase{1,2,3}-*.md`
- 你的既有 WIP(backend/db.py 等)全程未被触碰,仍是未提交状态。
