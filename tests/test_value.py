from backend.models import ZucaiMatch, PolyProbs
from backend.value import compute_value


def test_esp_cvi_known():
    z = ZucaiMatch("周一013", "西班牙", "佛得角", "6.16 00:00", "23:00",
        had=None, hhad={"line": -2, "h": 1.54, "d": 4.55, "a": 3.85},
        ttg={0: 38, 1: 9.8, 2: 5.55, 3: 4.1, 4: 4.1, 5: 5.5, 6: 7.5, 7: 7.0})
    p = PolyProbs("fifwc-esp-cvi-2026-06-15", "Spain", "Cabo Verde",
        ml={"home": 91.5, "draw": 6.5, "away": 2.5},
        home_cover={1.5: 78.5, 2.5: 55.5, 3.5: 33.5, 4.5: 23.5}, away_cover={},
        ou_over={0.5: 98.4, 1.5: 90.5, 2.5: 73.5, 3.5: 50.5, 4.5: 31.5, 5.5: 18.5})
    pts = compute_value(z, p)
    rang2 = next(x for x in pts if x.market == "让-2" and x.outcome == "平")
    assert abs(rang2.poly_prob_raw - 23.0) < 0.1        # 78.5-55.5
    assert abs(rang2.value_raw - 1.046) < 0.01
    assert rang2.value_devig < rang2.value_raw        # 去水后更低
    # 高分桶缺线→跳过
    assert not any(x.market == "总进球" and x.outcome == "7+球" and x.flag != "skip" for x in pts)
