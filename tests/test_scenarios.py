import os, tempfile, json
from backend.scenarios import (load_library, save_library, update_hit, hit_rate, DEFAULT_LIBRARY)


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
