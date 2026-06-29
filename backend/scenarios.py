"""剧本库:命名套路 + 触发条件 + 历史命中(会自我证伪)。"""
import json
import os

DEFAULT_LIBRARY = [
    {"name": "大热门被摆大巴逼平", "trigger": "强队 vs 龟缩弱旅,弱旅无出线压力/只想守",
     "effect": "平/0-0↑、进球↓", "triggered": 0, "hits": 0},
    {"name": "死亡橡皮擦轮换", "trigger": "已出线队大轮换",
     "effect": "冷门/平↑", "triggered": 0, "hits": 0},
    {"name": "默契平", "trigger": "双方平即出线",
     "effect": "平↑、进球↓", "triggered": 0, "hits": 0},
    {"name": "生死战必有胜负", "trigger": "平=双亡,无平局动机",
     "effect": "平↓", "triggered": 0, "hits": 0},
    {"name": "强队刷净胜球", "trigger": "热门为出线/种子位需狂攻",
     "effect": "大球、让球↑", "triggered": 0, "hits": 0},
]


def load_library(path):
    if not os.path.exists(path):
        return [dict(s) for s in DEFAULT_LIBRARY]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_library(path, lib):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(lib, f, ensure_ascii=False, indent=2)


def update_hit(path, name, hit):
    lib = load_library(path)
    for s in lib:
        if s["name"] == name:
            s["triggered"] += 1
            if hit:
                s["hits"] += 1
            save_library(path, lib)
            return s
    raise KeyError(name)


def hit_rate(scenario):
    t = scenario.get("triggered", 0)
    return round(scenario["hits"] / t, 3) if t else None


def scenario_hit(name, home_goals, away_goals, market_fav=None):
    """某剧本在一场实际结果里是否"命中"(其方向效果是否兑现)。机械判、确定性。

    只靠 (主进球, 客进球[, 市场热门 outcome])。返回 True/False;无法判 → None
    (未知剧本,或需要市场热门却没给)。None 表示"不计入统计",避免伪命中率。
    market_fav: 市场基线 had argmax,∈{h,d,a};仅"爆冷"类剧本需要。
    """
    is_draw = home_goals == away_goals
    if name in ("默契平", "大热门被摆大巴逼平"):
        return is_draw                          # 效果"平↑" → 真打平=命中
    if name == "生死战必有胜负":
        return not is_draw                      # 效果"平↓" → 非平=命中
    if name == "强队刷净胜球":
        return (home_goals + away_goals) >= 3   # 效果"大球" → 大 2.5=命中
    if name == "死亡橡皮擦轮换":                # 效果"冷门/平↑" → 平 或 爆冷
        if is_draw:
            return True
        if market_fav is None:
            return None                         # 非平又无市场热门 → 判不了爆冷
        outcome = "h" if home_goals > away_goals else "a"
        return outcome != market_fav            # 实际≠市场热门=爆冷=命中
    return None                                 # 未知剧本不判


def rebuild_hits(path, events):
    """从种子全量重建剧本命中台账(幂等):先重置为 DEFAULT seed(计数归零),
    再按 events 逐条 update_hit 累计。每次都从 seed 重建,故 launchd 每 5 分钟
    重跑不会翻倍累计——与赛果回填的台账/跑分卡同为"确定性重生成"。

    events: iterable of (scenario_name, hit_bool);不在库中的名字跳过。返回新库。
    """
    save_library(path, [dict(s) for s in DEFAULT_LIBRARY])   # 重置:triggered/hits→0
    known = {s["name"] for s in DEFAULT_LIBRARY}
    for name, hit in events:
        if name in known:
            update_hit(path, name, hit)
    return load_library(path)
