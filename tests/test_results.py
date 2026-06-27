"""parse_results 用真实 fixture (tests/fixtures/sporttery_results.json) 验证契约。

字段路径以 Task 1 spike (docs/design/notes-sporttery-result-endpoint.md) 实测为准:
- 列表:    value.matchResult (数组)
- 组彩编号: matchResult[].matchNumStr  (如 "周四055")  -> zucai_num
- 终比分:   matchResult[].sectionsNo999 ("主:客", 如 "2:1") -> home/away_goals
- 完赛判定: matchResult[].matchResultStatus == "2"  -> finished=True
            (不信 winFlag: 让球/单关池未结算时为空但比分有效)
"""
import json
import pathlib

from backend.results import MatchResult, parse_results

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sporttery_results.json"


def _load():
    return json.loads(FIXTURE.read_text())


def test_parse_results_extracts_finished_scores():
    """真 fixture 全是已完赛场次 -> 解析出整数比分且 finished=True。"""
    rows = parse_results(_load())
    assert rows, "应解析出至少一场赛果"

    # fixture 里 12 场全已完赛
    assert len(rows) == 12
    assert all(isinstance(r, MatchResult) for r in rows)
    assert all(r.finished is True for r in rows)
    assert all(
        r.zucai_num and isinstance(r.zucai_num, str) for r in rows
    )
    assert all(
        isinstance(r.home_goals, int) and isinstance(r.away_goals, int)
        for r in rows
    )

    # 简报点名: 周四055 厄瓜多尔 2:1 德国
    r055 = next(r for r in rows if r.zucai_num == "周四055")
    assert r055.home_goals == 2
    assert r055.away_goals == 1
    assert r055.finished is True


def test_parse_results_trusts_score_not_winflag():
    """周四057: winFlag="" 但 sectionsNo999="1:3" -> 仍解析为有效完赛比分。"""
    rows = parse_results(_load())
    r057 = next(r for r in rows if r.zucai_num == "周四057")
    assert r057.home_goals == 1
    assert r057.away_goals == 3
    assert r057.finished is True


def test_parse_results_skips_unfinished():
    """合成一条未完赛行 (matchResultStatus != "2"、比分缺失) -> 不算 finished。

    真 fixture 里未完赛场次整条缺席, 故此处自造合成行验证门控。
    """
    data = {
        "value": {
            "matchResult": [
                {
                    "matchNumStr": "周五099",
                    "matchResultStatus": "1",
                    "sectionsNo999": "",
                    "winFlag": "",
                }
            ]
        }
    }
    finished = [r for r in parse_results(data) if r.finished]
    assert all(r.zucai_num != "周五099" for r in finished)


def test_fetch_results_calls_endpoint_and_parses(monkeypatch):
    """mock httpx.Client: 验证 fetch_results 打命中端点 + 经 parse_results 返回。

    不打真网。捕获 client.get 的实参, 断言端点/参数对齐 Task 1 notes。
    """
    import backend.results as R

    sample = {
        "value": {
            "matchResult": [
                {
                    "matchNumStr": "周四055",
                    "sectionsNo999": "2:0",
                    "matchResultStatus": "2",
                }
            ]
        }
    }
    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return sample

    class _Client:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(R.httpx, "Client", _Client)

    rows = R.fetch_results()

    # 经 parse_results 解析
    assert any(
        r.zucai_num == "周四055" and r.home_goals == 2 and r.finished
        for r in rows
    )
    # 命中 Task 1 实测端点 (不是被 WAF 拦的 getMatchResultV1)
    assert captured["url"] == R.DEFAULT_API
    assert "getUniformMatchResultV1.qry" in captured["url"]
    # 关键查询参数齐备
    p = captured["params"]
    assert p["pageNo"] == "1"
    assert p["isFix"] == "0"
    assert "matchBeginDate" in p and "matchEndDate" in p
    # Referer 过 WAF
    assert captured["headers"]["Referer"] == R.REFERER


def test_fetch_results_honors_cfg_overrides(monkeypatch):
    """cfg.results 覆盖 api/ua/proxy/日期范围。"""
    import backend.results as R

    captured: dict = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"value": {"matchResult": []}}

    class _Client:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(R.httpx, "Client", _Client)

    cfg = {
        "results": {
            "api": "https://example.test/x.qry",
            "ua": "custom-ua",
            "proxy": "socks5://127.0.0.1:7897",
            "begin_date": "2026-06-01",
            "end_date": "2026-06-03",
        }
    }
    R.fetch_results(cfg)

    assert captured["url"] == "https://example.test/x.qry"
    assert captured["headers"]["User-Agent"] == "custom-ua"
    assert captured["client_kwargs"].get("proxy") == "socks5://127.0.0.1:7897"
    assert captured["params"]["matchBeginDate"] == "2026-06-01"
    assert captured["params"]["matchEndDate"] == "2026-06-03"
