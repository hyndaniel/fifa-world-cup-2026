# -*- coding: utf-8 -*-
"""结算引擎单测(数据驱动重写版): 命中判定 / 单腿双选 / payout 组合 / 注数·odds_max 校验 /
部分开赛 / 半全场 / recommendations 层。

赛果按 (home_goals, away_goals) 注入(不再用队名硬编码比分库)。
让球沿用**竞彩整数让球胜平负三路模型**: 让球后 net 净差 >0→'W' / =0→'D'(走盘归平, 非退款) / <0→'L'。
"""
import pytest

from tools import settle_tickets as s


# ============ 命中原子: outcome / had / hcap / exact / goals ============
def test_outcome_home_draw_away():
    assert s.outcome(2, 0) == "h"
    assert s.outcome(1, 1) == "d"
    assert s.outcome(0, 1) == "a"


def test_had_hit_win_draw_loss():
    assert s.had_hit(2, 0, "h") is True
    assert s.had_hit(2, 0, "d") is False
    assert s.had_hit(1, 1, "d") is True
    assert s.had_hit(0, 1, "a") is True
    assert s.had_hit(0, 1, "h") is False


def test_hcap_full_win():
    # 3-0, 让-1 → net=(3-1)-0=2>0 → W
    assert s.hcap_hit(3, 0, -1, "W") is True
    assert s.hcap_hit(3, 0, -1, "D") is False


def test_hcap_integer_push_is_draw_not_refund():
    # 4-2, 让-2 → net=(4-2)-2=0 → D(走盘归平, 非退款)
    assert s.hcap_hit(4, 2, -2, "D") is True
    assert s.hcap_hit(4, 2, -2, "W") is False
    assert s.hcap_hit(4, 2, -2, "L") is False


def test_hcap_receive_line():
    # 1-1, 受让+1 → net=(1+1)-1=1>0 → W
    assert s.hcap_hit(1, 1, 1, "W") is True
    # 1-3, 受让+2 → net=(1+2)-3=0 → D
    assert s.hcap_hit(1, 3, 2, "D") is True
    assert s.hcap_hit(1, 3, 2, "W") is False


def test_exact_hit():
    assert s.exact_hit(2, 0, 2, 0) is True
    assert s.exact_hit(2, 0, 0, 2) is False   # 主客反了
    assert s.exact_hit(2, 0, 1, 0) is False


def test_goals_hit():
    assert s.goals_hit(2, 1, 3) is True       # 总3球
    assert s.goals_hit(0, 0, 0) is True
    assert s.goals_hit(2, 1, 2) is False


# ============ 单腿双选 eval_leg ============
def test_eval_leg_single_hit_returns_odds():
    hit, odds = s.eval_leg([{"kind": "had", "sel": "h", "odds": 2.5}], 2, 0)
    assert hit is True and odds == 2.5


def test_eval_leg_single_miss():
    hit, odds = s.eval_leg([{"kind": "had", "sel": "a", "odds": 2.5}], 2, 0)
    assert hit is False and odds is None


def test_eval_leg_double_takes_matching_pick():
    # 双选: 平/负, 实际客胜 → 取负那条赔率
    picks = [{"kind": "had", "sel": "d", "odds": 2.7},
             {"kind": "had", "sel": "a", "odds": 2.24}]
    hit, odds = s.eval_leg(picks, 0, 1)
    assert hit is True and odds == 2.24


def test_eval_leg_double_prefers_first_when_both_match():
    # 理论上单场只有一个结果, 但防御性: 两 pick 都真时取第一条
    picks = [{"kind": "goals", "n": 1, "odds": 3.0},
             {"kind": "had", "sel": "d", "odds": 5.0}]
    hit, odds = s.eval_leg(picks, 1, 0)   # 总1球 且 ... 主胜(非平) → 仅首条真
    assert hit is True and odds == 3.0


def test_eval_leg_mixed_kinds():
    picks = [{"kind": "exact", "hs": 2, "as_": 1, "odds": 8.0}]
    assert s.eval_leg(picks, 2, 1) == (True, 8.0)
    assert s.eval_leg(picks, 1, 1) == (False, None)


# ============ payout: 复式组合 / 单场固定 ============
def test_combo_payout_2guan_all_win():
    # unit=2, 2串1全中 → 2*(2.0*3.0)=12
    lr = [(True, 2.0), (True, 3.0)]
    assert s.combo_payout(lr, [2], unit=2) == pytest.approx(12.0)


def test_combo_payout_one_miss_zero():
    lr = [(True, 2.0), (False, None)]
    assert s.combo_payout(lr, [2], unit=2) == pytest.approx(0.0)


def test_combo_payout_fushi_3choose2_all_hit():
    # 3选2复式全中: unit=2; 2*3+2*5+3*5=31 → 62
    lr = [(True, 2.0), (True, 3.0), (True, 5.0)]
    assert s.combo_payout(lr, [2], unit=2) == pytest.approx(62.0)


def test_combo_payout_fushi_partial_only_valid_combos():
    # 3腿2中1不中, guan=[2,3]: 仅(0,1)全中→2*3=6; unit=2 → 12
    lr = [(True, 2.0), (True, 3.0), (False, None)]
    assert s.combo_payout(lr, [2, 3], unit=2) == pytest.approx(12.0)


def test_combo_payout_guan_gt_M_skipped():
    lr = [(True, 2.0), (True, 3.0)]
    assert s.combo_payout(lr, [4], unit=2) == pytest.approx(0.0)


def test_single_fixed_payout_sums_independent_hits():
    # 单场固定每腿独立: unit=2; 腿1中@2.5→5, 腿2不中→0, 腿3中@3→6 → 11
    lr = [(True, 2.5), (False, None), (True, 3.0)]
    assert s.single_fixed_payout(lr, unit=2) == pytest.approx(11.0)


# ============ 校验器: 注数 / odds_max(真实票反算)============
def test_count_notes_combo_full_2to8_is_247():
    legs = [[{"odds": 1.0}]] * 8    # 8腿各单选
    assert s.count_notes(legs, list(range(2, 9)), "combo") == 247


def test_count_notes_combo_with_double_selection():
    # 总进球3场2-3关: 086单选, 087双选, 088单选 → 真实票"7注"
    legs = [[{"odds": 2.75}], [{"odds": 4.0}, {"odds": 3.0}], [{"odds": 2.8}]]
    assert s.count_notes(legs, [2, 3], "combo") == 7


def test_count_notes_single_fixed_sums_selections():
    # 单场固定: 086三档 + 088两档 → 5注
    legs = [[{"odds": 6.5}, {"odds": 3.7}, {"odds": 2.75}], [{"odds": 2.8}, {"odds": 3.1}]]
    assert s.count_notes(legs, [], "single_fixed") == 5


def test_max_payout_combo_matches_ticket_face():
    # LYZ 总进球2串1复式×5倍 unit=10 → 票面 201.5
    legs = [[{"odds": 6.5}, {"odds": 3.7}, {"odds": 2.75}], [{"odds": 2.8}, {"odds": 3.1}]]
    assert s.max_payout(legs, [2], "combo", unit=10) == pytest.approx(201.5)


def test_max_payout_single_fixed_matches_ticket_face():
    # LYZ 单场固定×7倍 unit=14 → 票面 134.4
    legs = [[{"odds": 6.5}, {"odds": 3.7}, {"odds": 2.75}], [{"odds": 2.8}, {"odds": 3.1}]]
    assert s.max_payout(legs, [], "single_fixed", unit=14) == pytest.approx(134.4)


# ============ settle_ticket: 编排(校验/部分开赛/半全场/算账)============
def _leg(mk, *picks):
    return {"match_key": mk, "picks": list(picks)}


def _t(**kw):
    """构造结构化票, 带合理默认。"""
    base = {"唯一码": "X", "who": "T", "stake": 2, "mult": 1,
            "mode": "combo", "guan_levels": [2], "legs": [], "expect_notes": 0,
            "odds_max": 0.0}
    base.update(kw)
    return base


def test_settle_ticket_combo_all_win():
    t = _t(stake=4, mult=1, guan_levels=[2], expect_notes=1, odds_max=12.0,
           legs=[_leg("086", {"kind": "had", "sel": "h", "odds": 2.0}),
                 _leg("088", {"kind": "had", "sel": "h", "odds": 3.0})])
    r = s.settle_ticket(t, {"086": (2, 0), "088": (1, 0)})
    assert r["status"] == "已结"
    assert r["pnl"] == pytest.approx(12.0 - 4)   # unit2 * 2*3 =12
    assert "086" in r["legs_hit"]


def test_settle_ticket_串关_one_miss_total_loss():
    t = _t(stake=4, mult=1, guan_levels=[2], expect_notes=1, odds_max=12.0,
           legs=[_leg("086", {"kind": "had", "sel": "h", "odds": 2.0}),
                 _leg("088", {"kind": "had", "sel": "h", "odds": 3.0})])
    r = s.settle_ticket(t, {"086": (2, 0), "088": (0, 1)})   # 088 客胜, 不中
    assert r["status"] == "已结"
    assert r["pnl"] == pytest.approx(-4.0)


def test_settle_ticket_partial_unfinished_stays_pending():
    t = _t(stake=4, mult=1, guan_levels=[2], expect_notes=1, odds_max=12.0,
           legs=[_leg("086", {"kind": "had", "sel": "h", "odds": 2.0}),
                 _leg("088", {"kind": "had", "sel": "h", "odds": 3.0})])
    r = s.settle_ticket(t, {"086": (2, 0)})   # 088 未开赛(不在 results)
    assert r["status"] == "待结"
    assert r["pnl"] is None
    assert "086" in r["legs_hit"] and "088" in r["legs_hit"]


def test_settle_ticket_notes_mismatch_flags_manual():
    # expect_notes 故意写错 → 校验不过, 标待人工, 不给 pnl
    t = _t(stake=4, mult=1, guan_levels=[2], expect_notes=99, odds_max=12.0,
           legs=[_leg("086", {"kind": "had", "sel": "h", "odds": 2.0}),
                 _leg("088", {"kind": "had", "sel": "h", "odds": 3.0})])
    r = s.settle_ticket(t, {"086": (2, 0), "088": (1, 0)})
    assert r["status"] == "待人工"
    assert r["pnl"] is None
    assert "注数" in r["reason"]


def test_settle_ticket_oddsmax_mismatch_flags_manual():
    t = _t(stake=4, mult=1, guan_levels=[2], expect_notes=1, odds_max=999.0,
           legs=[_leg("086", {"kind": "had", "sel": "h", "odds": 2.0}),
                 _leg("088", {"kind": "had", "sel": "h", "odds": 3.0})])
    r = s.settle_ticket(t, {"086": (2, 0), "088": (1, 0)})
    assert r["status"] == "待人工"
    assert "odds_max" in r["reason"] or "最高" in r["reason"]


def test_settle_ticket_htft_flags_manual():
    t = _t(stake=100, mult=25, mode="single_fixed", guan_levels=[],
           expect_notes=2, odds_max=202.5,
           legs=[_leg("087", {"kind": "htft", "sel": "胜胜", "odds": 1.32},
                             {"kind": "htft", "sel": "平胜", "odds": 4.05})])
    r = s.settle_ticket(t, {"087": (3, 1)})
    assert r["status"] == "待人工"
    assert "半全场" in r["reason"]


def test_settle_ticket_single_fixed_independent():
    # 单场固定2场: 086三档总进球 + 088两档; 只 088 命中
    t = _t(stake=70, mult=7, mode="single_fixed", guan_levels=[], expect_notes=5,
           odds_max=134.4,
           legs=[_leg("086", {"kind": "goals", "n": 0, "odds": 6.5},
                             {"kind": "goals", "n": 1, "odds": 3.7},
                             {"kind": "goals", "n": 2, "odds": 2.75}),
                 _leg("088", {"kind": "goals", "n": 2, "odds": 2.8},
                             {"kind": "goals", "n": 3, "odds": 3.1})])
    # 086 实 3球(不在086档) 不中; 088 实 2球 → 中@2.8, unit=14 → 39.2
    r = s.settle_ticket(t, {"086": (2, 1), "088": (1, 1)})
    assert r["status"] == "已结"
    assert r["pnl"] == pytest.approx(14 * 2.8 - 70)


# ============ 赛果加载 load_results(临时 sqlite)============
def _mk_cache(tmp_path):
    import sqlite3
    p = tmp_path / "odds_cache.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE match_results (match_key TEXT PRIMARY KEY, home_goals INTEGER, "
              "away_goals INTEGER, outcome TEXT, ts TEXT)")
    c.executemany("INSERT INTO match_results VALUES (?,?,?,?,?)",
                  [("周四085", 2, 0, "h", "t"), ("周六203", 0, 2, "a", "t")])
    c.commit(); c.close()
    return str(p)


def test_load_results_keys_by_3digit_suffix(tmp_path):
    r = s.load_results(_mk_cache(tmp_path))
    assert r["085"] == (2, 0)
    assert r["203"] == (0, 2)


def test_load_results_missing_table_or_db_returns_empty(tmp_path):
    assert s.load_results(str(tmp_path / "nope.db")) == {}


# ============ 幂等写回 write_back ============
def _ledger_with(*tickets):
    return {"updated": "x", "recommendations": [], "tickets": list(tickets), "people": []}


def test_write_back_settles_finished_ticket():
    led = _ledger_with({"唯一码": "A", "who": "HYN", "settled": False, "pnl": None, "legs_hit": "待结"})
    res = {("A", "HYN"): {"status": "已结", "pnl": 8.0, "legs_hit": "命中2/2 086✅ 088✅", "reason": ""}}
    n = s.write_back(led, res)
    t = led["tickets"][0]
    assert t["settled"] is True and t["pnl"] == 8.0 and "命中" in t["legs_hit"]
    assert n["settled"] == 1


def test_write_back_partial_updates_progress_keeps_pending():
    led = _ledger_with({"唯一码": "A", "who": "HYN", "settled": False, "pnl": None, "legs_hit": "待结"})
    res = {("A", "HYN"): {"status": "待结", "pnl": None, "legs_hit": "086✅ 088待结 (部分待结)", "reason": ""}}
    s.write_back(led, res)
    t = led["tickets"][0]
    assert t["settled"] is False and t["pnl"] is None and "待结" in t["legs_hit"]


def test_write_back_idempotent_skips_already_settled():
    led = _ledger_with({"唯一码": "A", "who": "HYN", "settled": True, "pnl": 5.0, "legs_hit": "命中1/1"})
    res = {("A", "HYN"): {"status": "已结", "pnl": 999.0, "legs_hit": "changed", "reason": ""}}
    n = s.write_back(led, res)
    assert led["tickets"][0]["pnl"] == 5.0   # 不覆盖已结
    assert n["skipped"] == 1


def test_write_back_manual_flag_records_reason_keeps_pending():
    led = _ledger_with({"唯一码": "A", "who": "HYN", "settled": False, "pnl": None, "legs_hit": "待结"})
    res = {("A", "HYN"): {"status": "待人工", "pnl": None, "legs_hit": "待人工", "reason": "半全场无源"}}
    s.write_back(led, res)
    assert led["tickets"][0]["settled"] is False


def test_write_back_same_code_split_between_people_does_not_cross_write():
    """一叠同款票按人拆成两条、共用同一唯一码时, 各写各的 pnl, 不互相覆盖。"""
    led = _ledger_with(
        {"唯一码": "A", "who": "HYN", "stake": 800, "settled": False, "pnl": None, "legs_hit": "待结"},
        {"唯一码": "A", "who": "YBB", "stake": 200, "settled": False, "pnl": None, "legs_hit": "待结"},
    )
    res = {
        ("A", "HYN"): {"status": "已结", "pnl": 858.56, "legs_hit": "命中2/2 097✅ 098✅", "reason": ""},
        ("A", "YBB"): {"status": "已结", "pnl": 214.64, "legs_hit": "命中2/2 097✅ 098✅", "reason": ""},
    }
    n = s.write_back(led, res)
    assert led["tickets"][0]["pnl"] == 858.56
    assert led["tickets"][1]["pnl"] == 214.64
    assert n["settled"] == 2


def test_settle_struct_batch_rejects_duplicate_code_and_who():
    led = _ledger_with({"唯一码": "A", "who": "HYN", "settled": False, "pnl": None, "legs_hit": "待结"})
    leg = {"match_key": "086", "picks": [{"kind": "had", "sel": "h", "odds": 2.0}]}
    dup = {"唯一码": "A", "who": "HYN", "stake": 2, "mult": 1, "mode": "combo",
           "guan_levels": [], "legs": [leg], "expect_notes": 1, "odds_max": None}
    with pytest.raises(ValueError, match="重复"):
        s._settle_struct_batch(led, [dup, dict(dup)], {"086": (1, 0)})


# ============ recommendations 层: 单腿胜平负判定 ============
def test_rec_sel_pure_had():
    assert s.rec_sel("主胜(南非)", "南非", "韩国") == "h"
    assert s.rec_sel("客胜(卡塔尔)", "波黑", "卡塔尔") == "a"
    assert s.rec_sel("平", "日本", "瑞典") == "d"


def test_rec_sel_team_name_resolves_side():
    assert s.rec_sel("哥伦比亚胜", "哥伦比亚", "葡萄牙") == "h"
    assert s.rec_sel("阿尔及利亚胜", "奥地利", "阿尔及利亚") == "a"


def test_rec_sel_handicap_returns_none():
    assert s.rec_sel("主-1负(平或日本)", "巴西", "日本") is None


# ============ recommendations 层编排 settle_recommendations ============
def test_settle_recommendations_marks_win_loss():
    led = {"recommendations": [
        {"date": "d", "match": "南非vs韩国", "leg": "主胜(南非)", "settled": False},
        {"date": "d", "match": "日本vs瑞典", "leg": "平", "settled": False},
    ], "tickets": []}
    results = {"059": (1, 0), "062": (1, 1)}          # 南非1-0胜, 日本1-1平
    name_to_num = {("南非", "韩国"): "059", ("日本", "瑞典"): "062"}
    n = s.settle_recommendations(led, results, name_to_num)
    assert led["recommendations"][0]["result"] == "win" and led["recommendations"][0]["settled"] is True
    assert led["recommendations"][1]["result"] == "win" and led["recommendations"][1]["settled"] is True
    assert n["settled"] == 2


def test_settle_recommendations_loss_and_unknown_and_handicap():
    led = {"recommendations": [
        {"match": "波黑vs卡塔尔", "leg": "客胜(卡塔尔)", "settled": False},   # 实主胜→loss
        {"match": "巴西vs日本", "leg": "主-1负(平或日本)", "settled": False},   # 让球→跳过
        {"match": "未知vs对阵", "leg": "平", "settled": False},               # 无场次号→跳过
    ], "tickets": []}
    results = {"050": (3, 1)}
    name_to_num = {("波黑", "卡塔尔"): "050", ("巴西", "日本"): "051"}
    n = s.settle_recommendations(led, results, name_to_num)
    assert led["recommendations"][0]["result"] == "loss" and led["recommendations"][0]["settled"] is True
    assert led["recommendations"][1]["settled"] is False   # 让球跳过
    assert led["recommendations"][2]["settled"] is False   # 无号跳过
    assert n["settled"] == 1 and n["skipped"] == 2


def test_settle_recommendations_idempotent():
    led = {"recommendations": [{"match": "南非vs韩国", "leg": "主胜(南非)",
                                "result": "win", "settled": True}], "tickets": []}
    n = s.settle_recommendations(led, {"059": (1, 0)}, {("南非", "韩国"): "059"})
    assert n["settled"] == 0


# ============ odds_max 缺失(None)容忍 ============
def test_settle_ticket_none_oddsmax_skips_that_check():
    # 部分 ledger 票 odds_max 为 None → 跳过 odds_max 校验(仍做注数校验), 正常结算
    t = _t(stake=24, mult=12, mode="single_fixed", guan_levels=[], expect_notes=1,
           odds_max=None,
           legs=[_leg("087", {"kind": "exact", "hs": 0, "as_": 1, "odds": 45.0})])
    r = s.settle_ticket(t, {"087": (1, 1)})
    assert r["status"] == "已结"
    assert r["pnl"] == pytest.approx(-24.0)   # 0:1 未中, unit=24


# ============ 半全场(HT+FT)结算: htft_hit / eval_leg 带半场 / settle_ticket ============
def test_htft_hit_ht_and_ft():
    # HT 主胜, FT 平: '胜平' 中; '胜胜' 不中(FT非胜); '平胜' 不中(HT非平)
    assert s.htft_hit(1, 0, 1, 1, "胜平") is True
    assert s.htft_hit(1, 0, 1, 1, "胜胜") is False
    assert s.htft_hit(1, 0, 1, 1, "平胜") is False


def test_htft_hit_all_nine_faces():
    # HT 0:1(客胜/负), FT 2:1(主胜) → '负胜'
    assert s.htft_hit(0, 1, 2, 1, "负胜") is True
    assert s.htft_hit(0, 1, 2, 1, "负负") is False


def test_eval_leg_htft_uses_ht():
    picks = [{"kind": "htft", "sel": "胜胜", "odds": 1.32},
             {"kind": "htft", "sel": "平胜", "odds": 4.05}]
    # HT 1:0(胜) FT 1:1(平) → 两个都要 FT=胜, 全不中
    hit, odds = s.eval_leg(picks, 1, 1, ht=(1, 0))
    assert hit is False and odds is None
    # HT 1:0(胜) FT 2:1(胜) → '胜胜' 中
    hit, odds = s.eval_leg(picks, 2, 1, ht=(1, 0))
    assert hit is True and odds == 1.32


def test_settle_ticket_htft_settles_with_ht():
    t = _t(stake=100, mult=25, mode="single_fixed", guan_levels=[], expect_notes=2,
           odds_max=202.5,
           legs=[_leg("087", {"kind": "htft", "sel": "胜胜", "odds": 1.32},
                             {"kind": "htft", "sel": "平胜", "odds": 4.05})])
    # FT 1:1, HT 1:0 → 全不中 → 全损
    r = s.settle_ticket(t, {"087": (1, 1)}, ht_results={"087": (1, 0)})
    assert r["status"] == "已结"
    assert r["pnl"] == pytest.approx(-100.0)


def test_settle_ticket_htft_pending_when_ht_missing():
    t = _t(stake=100, mult=25, mode="single_fixed", guan_levels=[], expect_notes=2,
           odds_max=202.5,
           legs=[_leg("087", {"kind": "htft", "sel": "胜胜", "odds": 1.32},
                             {"kind": "htft", "sel": "平胜", "odds": 4.05})])
    # 有 FT 无 HT → 待人工(半场未获取), 不猜
    r = s.settle_ticket(t, {"087": (1, 1)}, ht_results={})
    assert r["status"] == "待人工"
    assert "半场" in r["reason"]
