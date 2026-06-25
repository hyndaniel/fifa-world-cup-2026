# tests/test_v1_log.py
import os, tempfile
from backend.v1_log import record_v1, get_v1


def test_record_and_get_v1():
    d = tempfile.mkdtemp(); path = os.path.join(d, "c.db")
    record_v1(path, "M1", {"h": 25, "d": 26, "a": 49}, "0-1")
    got = get_v1(path, "M1")
    assert got["probs"]["a"] == 49 and got["score_pred"] == "0-1"
    record_v1(path, "M1", {"h": 30, "d": 30, "a": 40}, "1-1")  # 替换
    assert get_v1(path, "M1")["score_pred"] == "1-1"
    assert get_v1(path, "NONE") is None
