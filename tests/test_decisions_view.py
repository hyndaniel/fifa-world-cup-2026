from datetime import datetime, timezone, timedelta

from backend.state import parse_ko_dt, ko_status, decisions_view

BJ = timezone(timedelta(hours=8))
NOW = datetime(2026, 6, 26, 23, 0, tzinfo=BJ)  # 北京 6.26 23:00


def test_parse_ko_dt_basic():
    assert parse_ko_dt("6.27 02:00", NOW) == datetime(2026, 6, 27, 2, 0, tzinfo=BJ)


def test_parse_ko_dt_bad_returns_none():
    assert parse_ko_dt("", NOW) is None
    assert parse_ko_dt("待定", NOW) is None
    assert parse_ko_dt("6.27", NOW) is None  # 缺时间段


def test_ko_status_upcoming():
    # 同晚稍后开球 → upcoming
    assert ko_status("6.26 23:30", NOW)[0] == "upcoming"
    # 跨午夜夜场(次日日期前缀)仍是 upcoming
    assert ko_status("6.27 02:00", NOW)[0] == "upcoming"


def test_ko_status_recent_within_decay():
    # 4 小时前开球, 在 6h 窗内 → recent
    assert ko_status("6.26 19:00", NOW)[0] == "recent"


def test_ko_status_expired_beyond_decay():
    # 7 小时前开球, 超 6h 窗 → expired
    assert ko_status("6.26 16:00", NOW)[0] == "expired"


def test_ko_status_unknown():
    assert ko_status("待定", NOW) == ("unknown", None)


def test_ko_status_accepts_naive_now():
    """朴素 datetime(无 tzinfo)当北京时间处理, 不抛 aware/naive 比较错。"""
    naive = datetime(2026, 6, 26, 23, 0)  # 无 tzinfo
    assert ko_status("6.26 23:30", naive)[0] == "upcoming"
    assert ko_status("6.26 16:00", naive)[0] == "expired"


def _d(mk, ko):
    return {"match_key": mk, "ko_bj": ko, "status": "Selling"}


def test_decisions_view_drops_expired_keeps_rest():
    ds = [_d("A", "6.26 16:00"),  # expired (7h 前)
          _d("B", "6.26 19:00"),  # recent
          _d("C", "6.27 02:00")]  # upcoming
    out = decisions_view(ds, NOW)
    keys = [d["match_key"] for d in out]
    assert keys == ["B", "C"]            # 升序, A 被丢
    assert out[0]["view_status"] == "recent"
    assert out[1]["view_status"] == "upcoming"


def test_decisions_view_does_not_clobber_status_field():
    out = decisions_view([_d("B", "6.26 19:00")], NOW)
    assert out[0]["status"] == "Selling"  # 售卖态不被覆盖
    assert out[0]["view_status"] == "recent"


def test_decisions_view_unknown_sorts_last():
    out = decisions_view([_d("Z", "待定"), _d("C", "6.27 02:00")], NOW)
    assert [d["match_key"] for d in out] == ["C", "Z"]
    assert out[-1]["view_status"] == "unknown"
