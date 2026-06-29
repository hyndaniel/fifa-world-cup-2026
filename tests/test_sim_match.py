import json
import pytest
from tools.sim_match_montecarlo import (
    resolve_config, advance_prob, build_grid, summarize, fit,
)

_FULL = {
    "match": "T", "home": "A", "away": "B",
    "p_home": 0.50, "p_draw": 0.27, "p_away": 0.23,
    "ou_line": 2.5, "p_over": 0.50,
}


def test_input_overrides_config():
    c = resolve_config(json.dumps(_FULL))
    assert c["home"] == "A" and c["away"] == "B"
    assert c["p_home"] == 0.50 and c["p_over"] == 0.50


def test_pen_home_defaults_half():
    c = resolve_config(json.dumps(_FULL))          # 没给 pen_home
    assert c["pen_home"] == 0.5


def test_pen_home_explicit_kept():
    c = resolve_config(json.dumps({**_FULL, "pen_home": 0.6}))
    assert c["pen_home"] == 0.6


def test_missing_required_raises():
    bad = {k: v for k, v in _FULL.items() if k != "p_over"}
    with pytest.raises(ValueError):
        resolve_config(json.dumps(bad))


def test_1x2_must_sum_to_one():
    with pytest.raises(ValueError):
        resolve_config(json.dumps({**_FULL, "p_home": 0.9}))   # 和≈1.4


@pytest.mark.parametrize("bad", ["5", "[1,2]", "null", '"str"'])
def test_non_object_json_raises_valueerror(bad):
    # 合法 JSON 但顶层不是对象 → 走 ValueError 通道(让 main 干净退非0,而非 AttributeError traceback)
    with pytest.raises(ValueError):
        resolve_config(bad)


def test_no_input_uses_config():
    c = resolve_config(None)                       # 走模块 CONFIG
    assert c["home"] and c["p_home"] is not None


def test_advance_prob_takes_pen_param():
    # pen 只在"加时仍平"那一小块起作用 → 越偏向主队,主队晋级条件概率越高
    hi, _ = advance_prob(1.4, 1.0, 0.9)
    lo, _ = advance_prob(1.4, 1.0, 0.1)
    assert hi > lo


def test_engine_still_fits_market():
    # 回归:拟合后 summarize 的 1X2 应贴近市场目标
    tgt = {"p_home": 0.50, "p_draw": 0.27, "p_over": 0.50}
    _, l1, l2, rho = fit(tgt, 2.5)
    s = summarize(build_grid(l1, l2, rho), 2.5)
    assert abs(s["p_home"] - 0.50) < 0.03
    assert abs(s["p_draw"] - 0.27) < 0.03
