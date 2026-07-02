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


def test_parse_ko_dt_iso_format():
    """matches 表(赛程/赔率导入写入)用带年份的 ISO 格式, 与 decisions 表的
    "M.D HH:MM" 并存; 回归: 此前只认后者, matches 表记录一律解析失败落
    "unknown", 过期过滤形同虚设(南非vs韩国等陈旧场次永久霸榜 value_radar)。"""
    assert parse_ko_dt("2026-06-25 09:00:00", NOW) == datetime(2026, 6, 25, 9, 0, tzinfo=BJ)
    assert parse_ko_dt("2026-06-27 02:00", NOW) == datetime(2026, 6, 27, 2, 0, tzinfo=BJ)


def test_ko_status_expired_iso_format():
    # 南非vs韩国式场景: matches 表 ISO 格式 ko_bj, 开球已过 6h 衰减窗 → expired(而非 unknown)
    assert ko_status("2026-06-25 09:00:00", NOW)[0] == "expired"


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


def test_decisions_view_include_expired_keeps_and_tags():
    # include_expired=True: 过期场不丢, tag=expired, 供"全部"筛选用
    ds = [_d("A", "6.26 16:00"),  # expired (7h 前)
          _d("B", "6.26 19:00"),  # recent
          _d("C", "6.27 02:00")]  # upcoming
    out = decisions_view(ds, NOW, include_expired=True)
    keys = [d["match_key"] for d in out]
    assert keys == ["A", "B", "C"]            # 升序, A 不再被丢
    assert out[0]["view_status"] == "expired"
    assert out[1]["view_status"] == "recent"
    assert out[2]["view_status"] == "upcoming"


def test_decisions_view_does_not_clobber_status_field():
    out = decisions_view([_d("B", "6.26 19:00")], NOW)
    assert out[0]["status"] == "Selling"  # 售卖态不被覆盖
    assert out[0]["view_status"] == "recent"


def test_decisions_view_unknown_sorts_last():
    out = decisions_view([_d("Z", "待定"), _d("C", "6.27 02:00")], NOW)
    assert [d["match_key"] for d in out] == ["C", "Z"]
    assert out[-1]["view_status"] == "unknown"


def test_decisions_view_joins_odds():
    now = datetime(2026, 6, 26, 9, 0, tzinfo=BJ)
    decs = [{"match_key": "周四055", "ko_bj": "6.26 23:30"}]
    odds_map = {"周四055": {"sources": {"zucai": {"had": {"h": 2.7}}}}}
    out = decisions_view(decs, now, odds_map=odds_map)
    assert out[0]["odds"]["sources"]["zucai"]["had"]["h"] == 2.7


def test_decisions_view_odds_none_when_absent():
    now = datetime(2026, 6, 26, 9, 0, tzinfo=BJ)
    out = decisions_view([{"match_key": "周四099", "ko_bj": "6.26 23:30"}], now, odds_map={})
    assert out[0]["odds"] is None
