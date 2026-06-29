import os, tempfile, json
from backend.scenarios import (load_library, save_library, update_hit, hit_rate,
                               scenario_hit, rebuild_hits, DEFAULT_LIBRARY)


def test_load_default_when_missing():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    lib = load_library(path)
    assert isinstance(lib, list) and len(lib) == len(DEFAULT_LIBRARY)
    assert {"name", "trigger", "effect", "triggered", "hits"} <= set(lib[0])


def test_save_then_load_roundtrip():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    save_library(path, [{"name": "X", "trigger": "t", "effect": "e", "triggered": 0, "hits": 0}])
    assert load_library(path)[0]["name"] == "X"


def test_update_hit_increments():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    save_library(path, [{"name": "默契平", "trigger": "t", "effect": "平↑", "triggered": 0, "hits": 0}])
    s = update_hit(path, "默契平", True)
    assert s["triggered"] == 1 and s["hits"] == 1
    s = update_hit(path, "默契平", False)
    assert s["triggered"] == 2 and s["hits"] == 1
    assert hit_rate(s) == 0.5


def test_update_hit_unknown_raises():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    save_library(path, [])
    try:
        update_hit(path, "不存在", True)
        assert False, "should raise"
    except KeyError:
        pass


# --- scenario_hit:每个种子剧本的机械命中判据(只靠比分[+市场热门]) ---

def test_scenario_hit_draw_scenarios():
    # 效果"平↑":真打平=命中,非平=不中
    for name in ("默契平", "大热门被摆大巴逼平"):
        assert scenario_hit(name, 1, 1) is True
        assert scenario_hit(name, 2, 0) is False


def test_scenario_hit_decisive():
    # "生死战必有胜负" 效果"平↓":非平=命中,平=不中
    assert scenario_hit("生死战必有胜负", 2, 1) is True
    assert scenario_hit("生死战必有胜负", 0, 0) is False


def test_scenario_hit_goals():
    # "强队刷净胜球" 效果"大球":总进球≥3=命中(大2.5)
    assert scenario_hit("强队刷净胜球", 2, 1) is True       # 3 球
    assert scenario_hit("强队刷净胜球", 1, 1) is False      # 2 球


def test_scenario_hit_upset_needs_favorite():
    # "死亡橡皮擦轮换" 效果"冷门/平↑":平 或 爆冷(实际≠市场热门)=命中
    assert scenario_hit("死亡橡皮擦轮换", 1, 1, market_fav="h") is True   # 平→命中
    assert scenario_hit("死亡橡皮擦轮换", 0, 2, market_fav="h") is True   # 客胜≠热门h→爆冷命中
    assert scenario_hit("死亡橡皮擦轮换", 2, 0, market_fav="h") is False  # 主胜=热门→不中
    assert scenario_hit("死亡橡皮擦轮换", 2, 0, market_fav=None) is None  # 没热门信息又非平→无法判


def test_scenario_hit_unknown_returns_none():
    assert scenario_hit("查无此剧本", 1, 1) is None


def test_rebuild_hits_counts_and_idempotent():
    d = tempfile.mkdtemp(); path = os.path.join(d, "lib.json")
    events = [("默契平", True), ("默契平", False), ("强队刷净胜球", True), ("查无此剧本", True)]
    lib = rebuild_hits(path, events)
    by = {s["name"]: s for s in lib}
    assert by["默契平"]["triggered"] == 2 and by["默契平"]["hits"] == 1
    assert by["强队刷净胜球"]["triggered"] == 1 and by["强队刷净胜球"]["hits"] == 1
    assert by["生死战必有胜负"]["triggered"] == 0          # 未触发剧本归零
    assert "查无此剧本" not in by                          # 不在库的名字跳过,不新增

    # 幂等:同样 events 再跑一遍,计数不翻倍(launchd 每5分钟重跑安全)
    lib2 = rebuild_hits(path, events)
    by2 = {s["name"]: s for s in lib2}
    assert by2["默契平"]["triggered"] == 2 and by2["默契平"]["hits"] == 1
