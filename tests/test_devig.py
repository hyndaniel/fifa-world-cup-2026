from backend.devig import devig


def test_devig_normalizes():
    out = devig({"home": 61.5, "draw": 23.5, "away": 15.5})  # sum 100.5
    assert abs(sum(out.values()) - 100) < 1e-6
    assert abs(out["home"] - 61.5 / 1.005 * 1) < 0.05  # ~61.2
