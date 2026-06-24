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
