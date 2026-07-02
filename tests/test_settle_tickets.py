# -*- coding: utf-8 -*-
"""settle_tickets 结算引擎单测: 让球(hcap)/胜平负(x12)/比分(exact)/双选(leg_eval)/复式派彩(settle)。

按现有实现的**实际行为**断言 —— 不改 settle_tickets.py 本身。
注意: 该模块在 import 时会跑一大段实盘票据的 settle 并打印(顶层副作用),
这里只调用其纯函数, 那些打印无害(pytest 会捕获)。

关键认知(见文末报告): 本引擎的"让球"是**竞彩让球胜平负三路模型**(整数盘, 让球后当作
主胜/平/客胜三选一), 不是亚盘半赢半输 —— 整数盘"走盘"= net 净差为 0 时归为 'D',
并非退款。所以下面的"走盘"用例断言的是 net0 → 'D' 命中, 而非退本金。
"""
import pytest

from tools import settle_tickets as s


# ============ 比分库 score / exact ============
def test_score_lookup_exact():
    """score 按队名(有序对)取比分, 编号无关。"""
    assert s.score("墨西哥", "南非") == (2, 0)
    assert s.score("加拿大", "波黑") == (1, 1)


def test_score_order_sensitive_raises():
    """比分库键是有序 (home,away); 主客对调不在库中 → KeyError(不做对称回退)。"""
    with pytest.raises(KeyError):
        s.score("南非", "墨西哥")


def test_score_unknown_raises():
    with pytest.raises(KeyError):
        s.score("火星", "月球")


def test_exact_hit_and_miss():
    assert s.exact("墨西哥", "南非", 2, 0) is True
    assert s.exact("墨西哥", "南非", 1, 0) is False      # 比分不符
    assert s.exact("墨西哥", "南非", 0, 2) is False      # 主客反了


# ============ 胜平负 x12 (= hcap line 0) ============
def test_x12_home_win():
    # 墨西哥 2-0 南非 → 主胜
    assert s.x12("墨西哥", "南非", "W") is True
    assert s.x12("墨西哥", "南非", "D") is False
    assert s.x12("墨西哥", "南非", "L") is False


def test_x12_draw():
    # 加拿大 1-1 波黑 → 平
    assert s.x12("加拿大", "波黑", "D") is True
    assert s.x12("加拿大", "波黑", "W") is False


def test_x12_away_win():
    # 海地 0-1 苏格兰 → 客胜
    assert s.x12("海地", "苏格兰", "L") is True
    assert s.x12("海地", "苏格兰", "W") is False


# ============ 让球 hcap 各档 ============
def test_hcap_full_win():
    """让胜(全赢): 巴西 3-0 海地, 让 -1 → net=(3-1)-0=2>0 → 'W' 命中。"""
    assert s.hcap("巴西", "海地", -1, "W") is True
    assert s.hcap("巴西", "海地", -1, "D") is False
    assert s.hcap("巴西", "海地", -1, "L") is False


def test_hcap_full_loss():
    """让负(全输): 苏格兰 0-3 巴西, 让 -1 → net=(0-1)-3=-4<0 → 'L'。"""
    assert s.hcap("苏格兰", "巴西", -1, "L") is True
    assert s.hcap("苏格兰", "巴西", -1, "W") is False


def test_hcap_negative_line_push_is_draw():
    """整数盘走盘(net=0): 摩洛哥 4-2 海地, 让 -2 → net=(4-2)-2=0 → 'D'(非退款)。"""
    assert s.hcap("摩洛哥", "海地", -2, "D") is True
    assert s.hcap("摩洛哥", "海地", -2, "W") is False
    assert s.hcap("摩洛哥", "海地", -2, "L") is False


def test_hcap_positive_line_receive_win():
    """受让方(正盘): 卡塔尔 1-1 瑞士, 卡受让 +1 → net=(1+1)-1=1>0 → 'W'。"""
    assert s.hcap("卡塔尔", "瑞士", 1, "W") is True
    assert s.hcap("卡塔尔", "瑞士", 1, "D") is False


def test_hcap_positive_line_push_is_draw():
    """正盘走盘: 乌兹别克 1-3 哥伦比亚, 受让 +2 → net=(1+2)-3=0 → 'D'。"""
    assert s.hcap("乌兹别克", "哥伦比亚", 2, "D") is True
    assert s.hcap("乌兹别克", "哥伦比亚", 2, "W") is False
    assert s.hcap("乌兹别克", "哥伦比亚", 2, "L") is False


def test_hcap_unknown_want_never_matches():
    """want 不是 W/D/L 之一 → 恒不命中(o 只会是三者之一)。"""
    assert s.hcap("巴西", "海地", -1, "X") is False


# ============ 一条腿 leg_eval / 双选 ============
def test_leg_eval_single_hit():
    ok, odds = s.leg_eval([(lambda: True, 3.5)])
    assert ok is True and odds == 3.5


def test_leg_eval_single_miss():
    ok, odds = s.leg_eval([(lambda: False, 3.5)])
    assert ok is False and odds is None


def test_leg_eval_double_returns_first_matching_odds():
    """双选: 首选不中、次选中 → 取次选赔率。"""
    ok, odds = s.leg_eval([(lambda: False, 3.0), (lambda: True, 5.0)])
    assert ok is True and odds == 5.0


def test_leg_eval_double_prefers_first_when_both_hit():
    """两选都中 → 取第一条(短路返回), 不取更高赔那条。"""
    ok, odds = s.leg_eval([(lambda: True, 3.0), (lambda: True, 5.0)])
    assert ok is True and odds == 3.0


def test_leg_eval_empty_picks():
    """空 picks → (False, None)(不抛错)。"""
    assert s.leg_eval([]) == (False, None)


# ============ settle 派彩汇总 ============
def _hit(odds):
    return [(lambda: True, odds)]


def _miss(odds):
    return [(lambda: False, odds)]


def test_settle_single_all_win_2guan():
    """2 串 1 全中: payout = unit(=2*mult) * o1*o2。"""
    legs = [_hit(2.0), _hit(3.0)]
    pnl = s.settle("t", legs, [2], mult=1, stake=2)
    # unit=2, payout=2*(2.0*3.0)=12, pnl=12-2=10
    assert pnl == pytest.approx(10.0)


def test_settle_single_all_win_with_mult():
    """倍数放大: unit=2*mult。mult=15, stake=30 → payout=30*6=180。"""
    legs = [_hit(2.0), _hit(3.0)]
    pnl = s.settle("t", legs, [2], mult=15, stake=30)
    assert pnl == pytest.approx(150.0)   # 180-30


def test_settle_串关_one_leg_miss_total_loss():
    """串关任一腿不中 → 无满足组合 → 派彩 0, 全损。"""
    legs = [_hit(2.0), _miss(3.0)]
    pnl = s.settle("t", legs, [2], mult=15, stake=30)
    assert pnl == pytest.approx(-30.0)


def test_settle_fushi_2guan_from_3_all_hit():
    """3 选 2 复式(guan=[2]) 全中: 3 个两两组合都派彩。"""
    legs = [_hit(2.0), _hit(3.0), _hit(5.0)]
    # unit=2; combos: 2*3 + 2*5 + 3*5 = 6+10+15=31; payout=2*31=62; stake=6 → pnl=56
    pnl = s.settle("t", legs, [2], mult=1, stake=6)
    assert pnl == pytest.approx(56.0)


def test_settle_fushi_partial_hit_only_valid_combos_pay():
    """复式部分命中: 仅"全中腿"构成的组合派彩。3 腿中 2 中 1 不中, guan=[2,3]。"""
    legs = [_hit(2.0), _hit(3.0), _miss(5.0)]
    # k=2: (0,1)全中→2*3=6; (0,2)(1,2)含不中→跳过。k=3: 含不中→跳过。
    # payout=unit*6=2*6=12; stake=6 → pnl=6
    pnl = s.settle("t", legs, [2, 3], mult=1, stake=6)
    assert pnl == pytest.approx(6.0)


def test_settle_guan_level_gt_M_skipped():
    """关数 k > 腿数 M → continue 跳过, 不派彩。"""
    legs = [_hit(2.0), _hit(3.0)]
    pnl = s.settle("t", legs, [4], mult=1, stake=2)   # 只有2腿却要4串
    assert pnl == pytest.approx(-2.0)


def test_settle_empty_guan_levels():
    """关数列表为空 → 不进循环, 派彩恒 0(无论命中)。"""
    legs = [_hit(2.0), _hit(3.0)]
    pnl = s.settle("t", legs, [], mult=1, stake=2)
    assert pnl == pytest.approx(-2.0)


def test_settle_mult_zero_yields_zero_payout():
    """mult=0 → unit=0 → 即便全中派彩也是 0。"""
    legs = [_hit(2.0), _hit(3.0)]
    pnl = s.settle("t", legs, [2], mult=0, stake=2)
    assert pnl == pytest.approx(-2.0)


def test_settle_double_leg_uses_matched_odds_in_product():
    """双选腿命中时, 组合乘积用"命中那条"的赔率(此处次选 5.0)。"""
    legs = [
        _hit(2.0),
        [(lambda: False, 3.0), (lambda: True, 5.0)],   # 双选, 取 5.0
    ]
    # unit=2; payout=2*(2.0*5.0)=20; stake=2 → pnl=18
    pnl = s.settle("t", legs, [2], mult=1, stake=2)
    assert pnl == pytest.approx(18.0)


def test_settle_single_relay_1guan():
    """单关(guan=[1]) 命中: payout=unit*odds。"""
    legs = [_hit(2.5)]
    pnl = s.settle("t", legs, [1], mult=1, stake=2)   # 2*2.5=5, pnl=3
    assert pnl == pytest.approx(3.0)


# ============ 边界 / 容错(断言"现有实现的实际行为") ============
def test_settle_sub_one_odds_can_lose_despite_hit():
    """奇葩赔率<1 时, 即便命中, 派彩也可能低于投注 → 账面亏(无任何保护)。"""
    legs = [_hit(0.5)]
    pnl = s.settle("t", legs, [1], mult=1, stake=2)   # 2*0.5=1, pnl=-1
    assert pnl == pytest.approx(-1.0)


def test_settle_missing_score_propagates_keyerror():
    """腿引用了比分库缺失的场次 → KeyError 直接抛出(settle 不吞异常, 无容错)。"""
    legs = [[(lambda: s.exact("火星", "月球", 1, 0), 2.0)]]
    with pytest.raises(KeyError):
        s.settle("t", legs, [1], mult=1, stake=2)


def test_settle_returns_pnl_not_payout():
    """settle 返回的是盈亏(payout-stake), 不是派彩本身。"""
    legs = [_hit(3.0)]
    pnl = s.settle("t", legs, [1], mult=1, stake=2)   # payout=6, 返回 6-2=4
    assert pnl == pytest.approx(4.0)


def test_add_accumulates_person_pnl():
    """add 累加到全局 P(测完隔离恢复), 验证累加语义。"""
    key = "__unittest_person__"
    s.P.pop(key, None)
    try:
        s.add(key, 10.0)
        s.add(key, -3.5)
        assert s.P[key] == pytest.approx(6.5)
    finally:
        s.P.pop(key, None)
