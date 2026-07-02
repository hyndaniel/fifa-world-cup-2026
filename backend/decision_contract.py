"""决策卡契约 —— 前后端 + /跑今天 skill 三方共用字段/枚举的单一真源。

背景: 决策卡 dict 由本地 /跑今天 skill 生成 → POST /api/ingest/predictions →
db.save_decisions 原样存 payload_json → /api/decisions 透传 → frontend/app.js
buildDecisionCard 渲染。此前三方之间零 schema, 全靠口头字段约定, 字段一改只会
静默显示空。本模块把契约落成代码:
  - 枚举常量 (TIERS / VIEW_STATUS): 后端引用这里; frontend/app.js 无法 import
    Python, 其硬编码处须注释指回本文件(见 app.js 头部契约注释)。
  - validate_decision(): ingest 时软校验 —— 只警告不拒收(跑今天产出缺可选段属
    正常降级, 拒收会断每日管道), 让脱节在日志里可见而非看板上静默空一块。

EV 字段口径(frontend evOf 同款): ev_pct_devig(去水)优先, 缺才用 ev_pct(生)。
"""
from __future__ import annotations

# 价值档位 (value.flag / best_leg.flag / bet_stats tier 共用)
TIERS = ("green", "yellow", "red", "skip")
# 结算统计只认的档位 (skip 不进 by_tier)
SETTLE_TIERS = ("green", "yellow", "red")
# decisions_view 给每张卡打的时段标签 (state.py 产, app.js 按此筛选)
VIEW_STATUS = ("upcoming", "recent", "expired", "unknown")

# 决策卡顶层字段: {字段: (是否必填, 说明)}。渲染方 app.js buildDecisionCard 消费。
DECISION_FIELDS = {
    "match_key":  (True,  "队名 label, 如 '挪威 vs 法国'; upsert 主键, 也是赔率面板 join key"),
    "label":      (False, "展示名, 通常同 match_key"),
    "ko_bj":      (False, "开球北京时间 'M.D HH:MM'; decisions_view 靠它算 view_status"),
    "home_cn":    (False, "主队中文名"),
    "away_cn":    (False, "客队中文名"),
    "v1":         (False, "wc-score-v1 比分预测段 {score, probs, ...}"),
    "v2":         (False, "wc-prob-v2 概率段 {probs, reliability, scenario, ...}"),
    "value":      (False, "价值段: legs[] 每腿 {market, outcome, zucai_odds, poly_prob_devig, "
                          "value_devig, ev_pct, ev_pct_devig, flag∈TIERS}"),
    "best_leg":   (False, "最不亏腿 (value.legs 之一的拷贝, 含 flag/ev_pct[_devig])"),
    "probs":      (False, "合成胜平负概率 {h,d,a}%"),
    "reliability": (False, "靠谱度: 稳/中/乱"),
    "scenarios":  (False, "剧本标签列表"),
}


def validate_decision(d):
    """软校验一条决策卡, 返回警告字符串列表(空=干净)。只查会导致看板静默空块的
    结构性问题, 不逐字段较真(未知字段透传是契约的一部分)。"""
    warns = []
    if not isinstance(d, dict):
        return ["decision 不是 dict"]
    mk = d.get("match_key")
    if not mk:
        warns.append("缺 match_key(会被 save_decisions 跳过)")
    for leg_src in ("best_leg",):
        leg = d.get(leg_src)
        if isinstance(leg, dict):
            flag = leg.get("flag")
            if flag is not None and flag not in TIERS:
                warns.append(f"{mk}: {leg_src}.flag={flag!r} 不在 TIERS{TIERS}")
            if leg.get("ev_pct") is None and leg.get("ev_pct_devig") is None:
                warns.append(f"{mk}: {leg_src} 缺 ev_pct/ev_pct_devig(卡上 EV chip 会空)")
    val = d.get("value")
    if isinstance(val, dict):
        for leg in val.get("legs") or []:
            if isinstance(leg, dict) and leg.get("flag") not in TIERS:
                warns.append(f"{mk}: value.legs 有腿 flag={leg.get('flag')!r} 不在 TIERS")
                break
    return warns
