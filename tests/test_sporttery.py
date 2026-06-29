"""parse_matches 用真实 fixture (tests/fixtures/zucai_sample.json) 验证契约。

契约 (backend/models.py ZucaiMatch):
- had: {"h","d","a"} | None
- hhad: {"line": int, "h","d","a"} | None   (键是 "line" 不是 "goalLine")
- ttg: {0..7: float}                          (int 键)
"""
import json
import pathlib

from backend import sporttery
from backend.sporttery import parse_matches

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "zucai_sample.json"


def _load():
    return json.loads(FIXTURE.read_text())


def test_parse_spain_match():
    """西班牙 vs 佛得角: had 空 → None；hhad line -2；ttg int 键。"""
    ms = parse_matches(_load())
    esp = next(m for m in ms if m.home_cn == "西班牙")

    # 上游 had 为空 dict → None
    assert esp.had is None

    # hhad 用键 "line" (int)，不是 "goalLine"
    assert esp.hhad["line"] == -2
    assert isinstance(esp.hhad["line"], int)
    assert esp.hhad["h"] == 1.50

    # ttg 用 int 键，值为 float
    assert esp.ttg[2] == 5.70
    assert isinstance(esp.ttg[2], float)


def test_parse_populated_had():
    """有让球+胜平负的场次: had 三路俱在，hhad line 为 int。"""
    ms = parse_matches(_load())
    bel = next(m for m in ms if m.home_cn == "比利时")
    assert bel.had == {"h": 1.43, "d": 3.85, "a": 5.86}
    assert bel.hhad["line"] == -1
    assert bel.ttg[2] == 3.40


def test_parse_count_and_fields():
    """fixture 全部 12 场都解析出来，基础字段非空。"""
    ms = parse_matches(_load())
    assert len(ms) == 12
    for m in ms:
        assert m.zucai_num and m.home_cn and m.away_cn
        assert m.ko_bj  # 拼了 matchDate + matchTime
        assert isinstance(m.ttg, dict)


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def test_fetch_stdlib_no_httpx(monkeypatch):
    """直连 fetch 走纯 stdlib:不 import httpx，正确拼 query + headers + 解析 JSON。"""
    captured = {}

    class _FakeOpener:
        def open(self, req, timeout=None):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.headers)
            captured["timeout"] = timeout
            return _FakeResp(b'{"value": {"matchInfoList": []}}')

    monkeypatch.setattr(
        sporttery.urllib.request, "build_opener", lambda *a, **k: _FakeOpener()
    )
    out = sporttery.fetch({}, timeout=12.0)

    assert out == {"value": {"matchInfoList": []}}
    assert captured["timeout"] == 12.0
    assert "poolCode=had%2Chhad%2Cttg" in captured["url"]
    assert "channel=c" in captured["url"]
    # urllib 把 header 键首字母大写余小写:User-Agent → User-agent
    assert captured["headers"]["User-agent"].startswith("Mozilla")
    assert captured["headers"]["Referer"] == "https://m.sporttery.cn/"
