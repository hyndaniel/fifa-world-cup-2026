from backend.scoring import brier_multi, calibration_buckets


def test_brier_multi_perfect_confident():
    # 说主胜 100%,真主胜 → 0 罚分
    assert brier_multi({"h": 100, "d": 0, "a": 0}, "h") == 0.0


def test_brier_multi_known_value():
    # {h:60,d:30,a:10}, 真主胜 → (0.6-1)^2+(0.3)^2+(0.1)^2 = 0.16+0.09+0.01
    assert brier_multi({"h": 60, "d": 30, "a": 10}, "h") == 0.26


def test_brier_multi_normalizes_percent():
    # 输入和不为 100 也应先归一
    assert brier_multi({"h": 120, "d": 60, "a": 20}, "h") == 0.26  # 比例同上


def test_brier_multi_actual_outside_support_full_penalty():
    # 总进球真值 "7" 超出只覆盖 0..2 的盘口 → 满罚该类
    # frac=(.5,.3,.2): Σfrac²=.25+.09+.04=.38; +1 满罚 = 1.38
    b = brier_multi({"0": 50, "1": 30, "2": 20}, "7")
    assert abs(b - 1.38) < 0.001


def test_brier_multi_in_support_unchanged():
    # had 真值恒在支撑内 → 行为与原实现一致
    assert brier_multi({"h": 60, "d": 30, "a": 10}, "h") == 0.26


def test_calibration_buckets_basic():
    # 两条预测都在高桶:预测 80%,一中一不中 → freq=0.5
    preds = [(80.0, 1), (80.0, 0)]
    out = calibration_buckets(preds, n=5)
    hi_bucket = [b for b in out if b["count"] > 0][0]
    assert hi_bucket["count"] == 2
    assert hi_bucket["mean_pred"] == 80.0
    assert hi_bucket["freq"] == 0.5
