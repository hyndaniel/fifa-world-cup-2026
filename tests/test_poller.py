"""poll_once 用注入的假 client + 真实 fixture, 不联网, 验证一轮后库里有 value_points。

注入:
- zucai_fetch:      返回 tests/fixtures/zucai_sample.json 的原始 dict (poll 内部再 parse_matches)
- poly_list:        返回 poly_events.json 解析出的 {slug: title} 索引 (同 list_events 的过滤逻辑)
- poly_fetch_event: 对 esp-cvi 的 slug 返回 (poly_esp_cvi.json, poly_more.json),
                    其他场返回 ([], []) → 让 poll_once 记 warning 跳过

断言: poll_once 返回 >=1; db.value_points() 非空; 含西佛(Spain/Cabo Verde)的 让-2 平点。
"""
import json
import pathlib

from backend.db import Db
from backend.poller import poll_once

FIX = pathlib.Path(__file__).parent / "fixtures"


def _load(name):
    return json.load(open(FIX / name, encoding="utf-8"))


ESP_CVI_SLUG = "fifwc-esp-cvi-2026-06-15"


def _poly_idx():
    """从 poly_events.json 构 {slug: title}, 复用 list_events 的过滤规则。"""
    idx = {}
    for e in _load("poly_events.json"):
        s = e.get("slug", "") or ""
        if s.startswith("fifwc-") and "more-markets" not in s:
            idx[s] = e.get("title", "")
    return idx


def _fake_fetch_event(slug):
    """只有西佛这一场有 fixture; 其余场返回空 → 被跳过。"""
    if slug == ESP_CVI_SLUG:
        return _load("poly_esp_cvi.json"), _load("poly_more.json")
    return [], []


def test_poll_once_writes_value_points(tmp_path):
    db = Db(tmp_path / "t.db")
    db.init()

    n = poll_once(
        db,
        {"value": {"devig_yellow_below": 1.03}},
        zucai_fetch=lambda: _load("zucai_sample.json"),
        poly_list=_poly_idx,
        poly_fetch_event=_fake_fetch_event,
    )

    # 至少处理了西佛这一场
    assert n >= 1

    # 库里写入了 value_points
    vps = db.value_points()
    assert len(vps) > 0

    # 西佛这场建了 match, 记了 poly_slug
    matches = db.matches()
    esp = next(m for m in matches if m["home_cn"] == "西班牙")
    assert esp["home_en"] == "Spain" and esp["away_en"] == "Cabo Verde"
    assert esp["poly_slug"] == ESP_CVI_SLUG

    # 含西佛的 让-2 平点 (compute_value 对 line=-2 产出 market="让-2")
    rang2 = [v for v in vps if v["match_id"] == esp["id"]
             and v["market"] == "让-2" and v["outcome"] == "平"]
    assert rang2, "应有西佛的 让-2 平 value point"


def test_poll_once_registers_matches_even_when_poly_unavailable(tmp_path):
    """🔴 回归: Poly 全挂(拿不到任何 slug)时, 场次仍须照常登记进 matches。

    Poly 是增强项, 竞彩才是场次的权威来源。旧实现在 `if not slug: continue` 处跳过整场,
    连 matches 都不写 —— Poly 一被墙, matches 表就永久停摆, 下游判场/预测/看板全线卡死
    (2026-07-13 实测: 半决赛竞彩早已上市, matches 却停在四分之一决赛)。
    """
    db = Db(tmp_path / "t.db")
    db.init()

    n = poll_once(
        db,
        {},
        zucai_fetch=lambda: _load("zucai_sample.json"),
        poly_list=lambda: {},                 # Poly 索引全空 → 每场都配不到 slug
        poly_fetch_event=_fake_fetch_event,
    )

    # 含 Poly 的完整处理数仍是 0, value_points 仍空(返回值语义不变)
    assert n == 0
    assert db.value_points() == []

    # 但场次必须已登记, 且竞彩字段完整
    matches = db.matches()
    assert matches, "Poly 不可用时场次仍须登记进 matches"
    esp = next(m for m in matches if m["home_cn"] == "西班牙")
    assert esp["home_en"] == "Spain" and esp["away_en"] == "Cabo Verde"
    assert esp["ko_bj"]                       # 开球时间在 → 下游判场可用
    assert esp["poly_slug"] is None           # 只是缺 poly, 不是缺场次


def test_poll_once_poly_failure_does_not_wipe_existing_slug(tmp_path):
    """🔴 回归: 先有 slug 的场, 之后某轮 Poly 挂了, 不能把已存的 poly_slug 抹成 None。

    upsert_match 是整行覆盖(ON CONFLICT DO UPDATE 用 excluded.*), 两段式里第 1 段若无脑
    传 poly_slug=None, 会在 Poly 故障轮把好不容易配上的 slug 清掉。
    """
    db = Db(tmp_path / "t.db")
    db.init()

    # 第 1 轮: Poly 正常 → 西佛配上 slug
    poll_once(
        db, {},
        zucai_fetch=lambda: _load("zucai_sample.json"),
        poly_list=_poly_idx,
        poly_fetch_event=_fake_fetch_event,
    )
    esp = next(m for m in db.matches() if m["home_cn"] == "西班牙")
    assert esp["poly_slug"] == ESP_CVI_SLUG

    # 第 2 轮: Poly 全挂 → slug 必须留着, 不被抹掉
    poll_once(
        db, {},
        zucai_fetch=lambda: _load("zucai_sample.json"),
        poly_list=lambda: {},
        poly_fetch_event=_fake_fetch_event,
    )
    esp2 = next(m for m in db.matches() if m["home_cn"] == "西班牙")
    assert esp2["poly_slug"] == ESP_CVI_SLUG, "Poly 故障轮不得清掉已存的 poly_slug"
