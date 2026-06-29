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
    """mock stdlib 抓取层 _http_get_json: 验证 fetch_results 打命中端点 + 经 parse_results 返回。

    不打真网。捕获 _http_get_json 的实参 (url/headers/proxy), 断言端点/参数对齐 Task 1 notes。
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

    def _fake_get(url, headers, proxy, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["proxy"] = proxy
        captured["timeout"] = timeout
        return sample

    monkeypatch.setattr(R, "_http_get_json", _fake_get)

    rows = R.fetch_results()

    # 经 parse_results 解析
    assert any(
        r.zucai_num == "周四055" and r.home_goals == 2 and r.finished
        for r in rows
    )
    # 命中 Task 1 实测端点 (不是被 WAF 拦的 getMatchResultV1), 参数 baked 进 query string
    assert captured["url"].startswith(R.DEFAULT_API + "?")
    assert "getUniformMatchResultV1.qry" in captured["url"]
    # 关键查询参数齐备 (urlencode 后)
    assert "pageNo=1" in captured["url"]
    assert "isFix=0" in captured["url"]
    assert "matchBeginDate=" in captured["url"] and "matchEndDate=" in captured["url"]
    # Referer 过 WAF
    assert captured["headers"]["Referer"] == R.REFERER
    # 缺省直连: 无代理
    assert captured["proxy"] is None


def test_fetch_results_honors_cfg_overrides(monkeypatch):
    """cfg.results 覆盖 api/ua/proxy/日期范围 (proxy 走 http/https stdlib 路径)。"""
    import backend.results as R

    captured: dict = {}

    def _fake_get(url, headers, proxy, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["proxy"] = proxy
        return {"value": {"matchResult": []}}

    monkeypatch.setattr(R, "_http_get_json", _fake_get)

    cfg = {
        "results": {
            "api": "https://example.test/x.qry",
            "ua": "custom-ua",
            "proxy": "http://127.0.0.1:7897",
            "begin_date": "2026-06-01",
            "end_date": "2026-06-03",
        }
    }
    R.fetch_results(cfg)

    assert captured["url"].startswith("https://example.test/x.qry?")
    assert captured["headers"]["User-Agent"] == "custom-ua"
    assert captured["proxy"] == "http://127.0.0.1:7897"
    assert "matchBeginDate=2026-06-01" in captured["url"]
    assert "matchEndDate=2026-06-03" in captured["url"]


def test_parse_results_status2_but_blank_score_not_finished():
    """matchResultStatus=='2' 但 sectionsNo999 空/坏格式 → finished=False(防幻象 0-0 入账)。

    回填只录 finished;这条 AND 守卫(results.py:69 `and score is not None`)是最后防线:
    完赛标志已置但终比分暂空时,不得把 0-0 当已完赛录进 match_results 污染 Brier/台账。
    """
    data = {
        "value": {
            "matchResult": [
                {"matchNumStr": "周五099", "matchResultStatus": "2", "sectionsNo999": ""},
                {"matchNumStr": "周五100", "matchResultStatus": "2", "sectionsNo999": "2:"},
            ]
        }
    }
    rows = parse_results(data)
    assert len(rows) == 2
    assert all(r.finished is False for r in rows)
    assert all((r.home_goals, r.away_goals) == (0, 0) for r in rows)


def test_parse_results_skips_missing_num_and_handles_empty_input():
    """缺 matchNumStr 的行被跳过(防 None key 污染下游);None/{} 输入安全返回 []。"""
    data = {
        "value": {
            "matchResult": [
                {"matchResultStatus": "2", "sectionsNo999": "1:0"},  # 缺 matchNumStr → 跳过
                {"matchNumStr": "周四055", "matchResultStatus": "2", "sectionsNo999": "2:0"},
            ]
        }
    }
    rows = parse_results(data)
    assert len(rows) == 1 and rows[0].zucai_num == "周四055"
    assert parse_results(None) == []
    assert parse_results({}) == []


def test_http_get_json_proxy_selection_and_utf8_decode(monkeypatch):
    """_http_get_json(cd956cc 新加 stdlib 抓取层)直测:代理选择 + utf-8 decode + json 解析。

    不打真网:monkeypatch build_opener 截获 ProxyHandler,假 opener.open 返回 bytes。
    锚住两条行为:(a) proxy=None → ProxyHandler({}) 显式禁用环境 *_PROXY;
    (b) proxy 指定 → http/https 都走该代理。
    """
    import backend.results as R

    captured: dict = {}

    class _Resp:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return _Resp(b'{"value": {"matchResult": []}}')

    def _fake_build_opener(*handlers):
        captured["handlers"] = handlers
        return _Opener()

    monkeypatch.setattr(R, "build_opener", _fake_build_opener)

    # (a) 无代理:utf-8 bytes 正确 decode+json;ProxyHandler 显式空(禁环境代理);timeout/url 透传
    out = R._http_get_json("https://x.test/y?a=1", {"User-Agent": "ua"}, None, 9.0)
    assert out == {"value": {"matchResult": []}}
    assert captured["handlers"][0].proxies == {}
    assert captured["timeout"] == 9.0
    assert captured["req"].full_url == "https://x.test/y?a=1"

    # (b) 指定代理:http/https 均命中
    R._http_get_json("https://x.test/y", {}, "http://127.0.0.1:7897", 5.0)
    proxies = captured["handlers"][0].proxies
    assert proxies.get("http") == "http://127.0.0.1:7897"
    assert proxies.get("https") == "http://127.0.0.1:7897"
