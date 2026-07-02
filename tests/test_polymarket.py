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


def test_find_slug_prefers_main_over_submarket():
    """同一对阵有子盘 event 排在主盘之前时, 仍须返回主盘(slug 以日期结尾)。

    回归: 子盘(first-to-score / halftime-result …)title 与主盘相同, 但无胜平负结构,
    选中会 parse 出 h/d/a 全 None 判 "ml 不全" 失败 —— 曾致 083/084/086/087 等场
    Poly 主线长期未刷。
    """
    idx = {
        # 子盘故意排在前面, 复现按插入序命中第一个的旧 bug
        "fifwc-aus-egy-2026-07-03-first-to-score": "Australia vs. Egypt",
        "fifwc-aus-egy-2026-07-03-halftime-result": "Australia vs. Egypt",
        "fifwc-aus-egy-2026-07-03": "Australia vs. Egypt",  # 主盘
    }
    slug, _ = find_slug(idx, "Australia", "Egypt")
    assert slug == "fifwc-aus-egy-2026-07-03"

    # 只有子盘(主盘未入索引)时退回第一个命中, 不回归成 (None, None)
    idx_only_sub = {"fifwc-can-mar-2026-07-04-halftime-result": "Canada vs. Morocco"}
    slug2, _ = find_slug(idx_only_sub, "Canada", "Morocco")
    assert slug2 == "fifwc-can-mar-2026-07-04-halftime-result"


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
