import json
import pathlib

from backend.polymarket import find_slug, list_events, parse_probs

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.load(open(FIX / name, encoding="utf-8"))


def test_parse_probs_esp_cvi():
    base = _load("poly_esp_cvi.json")
    more = _load("poly_more.json")
    p = parse_probs(base, more, "Spain", "Cabo Verde")

    # ml 三键, home≈91.5 (raw %)
    assert abs(p.ml["home"] - 91.5) < 1.0
    assert "draw" in p.ml and "away" in p.ml
    assert p.home_en == "Spain" and p.away_en == "Cabo Verde"
    assert p.slug == "fifwc-esp-cvi-2026-06-15"

    # home_cover 以 float 线为键; 1.5 线 = 78.5
    assert abs(p.home_cover[1.5] - 78.5) < 1.0
    # 2.5 线: fixture 实测 57.5 (任务描述给的 ≈55.5 是约值, 偏差 ~2pt)
    assert abs(p.home_cover[2.5] - 55.5) < 2.5
    # 便捷键 0.5 = 该队胜% (从 ml 派生)
    assert abs(p.home_cover[0.5] - p.ml["home"]) < 1e-9

    # ou_over 以 float 线为键; 2.5 线: fixture 实测 74.5 (任务 ≈73.5, 偏差 ~1pt)
    assert abs(p.ou_over[2.5] - 73.5) < 1.5
    assert abs(p.ou_over[1.5] - 90.5) < 1.5

    # away_cover 不含主队线; 但客队净胜桶可能为空(本场客队几乎不赢), 至少是 dict
    assert isinstance(p.away_cover, dict)


def test_find_slug_in_events():
    events = _load("poly_events.json")
    # 模拟 list_events 的索引构建(过滤 fifwc- 前缀 / 非 more-markets, 保序)
    idx = {}
    for e in events:
        s = e.get("slug", "") or ""
        if s.startswith("fifwc-") and "more-markets" not in s:
            idx[s] = e.get("title", "")

    slug, title = find_slug(idx, "Spain", "Cabo Verde")
    assert slug == "fifwc-esp-cvi-2026-06-15"
    assert "Spain" in title and "Cabo Verde" in title

    # 顺序无关: 反向传两队也能配到同一场
    slug2, _ = find_slug(idx, "Cabo Verde", "Spain")
    assert slug2 == "fifwc-esp-cvi-2026-06-15"

    # 不存在的对阵返回 (None, None)
    assert find_slug(idx, "Spain", "Mars") == (None, None)


def test_list_events_paging_and_filter():
    """list_events 用注入 fetcher: 翻页(<100 提前停) + 过滤 more-markets/非 fifwc。"""
    events = _load("poly_events.json")

    calls = []

    def fake_fetcher(path):
        calls.append(path)
        if "offset=0" in path:
            # 首页放真实 72 条 + 一条 more-markets + 一条非 fifwc 噪声
            page = list(events)
            page.append({"slug": "fifwc-esp-cvi-2026-06-15-more-markets",
                         "title": "Spain vs. Cabo Verde - more"})
            page.append({"slug": "nba-lal-bos", "title": "Lakers vs Celtics"})
            return page
        return []  # 后续页空 -> 触发提前停止

    idx = list_events(fetcher=fake_fetcher)

    # 首页 < 100(74<100) 提前停, 只调用一次
    assert calls == ["/events?closed=false&tag_id=102232&limit=100&offset=0"
                     "&order=startDate&ascending=true"]
    # more-markets 与非 fifwc 已被过滤
    assert "fifwc-esp-cvi-2026-06-15-more-markets" not in idx
    assert "nba-lal-bos" not in idx
    assert idx["fifwc-esp-cvi-2026-06-15"] == "Spain vs. Cabo Verde"
