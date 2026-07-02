# -*- coding: utf-8 -*-
"""odds_consensus 解析/共识单测 —— 全离线(monkeypatch 掉网络 _get)。

覆盖:
  - _parse_rows: 即时前3列、ttl/data-time 行门控、赔率<1 过滤、<3 列跳过、公司名缺失→"?"
  - _book_rows: **翻页补全全量**(历史踩坑: 首屏只 ~30 家漏后续)、按公司名去重、
    "?" 行不参与去重、无新家/到底则停(不死循环)、缺 _total 时不翻页
  - consensus: 中位数(奇/偶数)、去水%、overround、<3 家返回 None

用内嵌最小 HTML fixture(仿 500 真实结构: <tr ttl>...<td klfc onclick=OZ.r> + span.quancheng)。
不改被测代码。
"""
import pytest

from tools import odds_consensus as oc


# ---- HTML fixture 生成器(仿真实结构) ----
def _cell(v):
    return f'<td onclick="OZ.r(this)" klfc="1">{v}</td>'


def _row(company, h, d, a, chupan=None):
    """一行公司欧赔。chupan 给出则追加"初始"3列(应被忽略, 只取前3=即时)。"""
    tds = _cell(h) + _cell(d) + _cell(a)
    if chupan:
        tds += _cell(chupan[0]) + _cell(chupan[1]) + _cell(chupan[2])
    name = f'<td><span class="quancheng">{company}</span></td>'
    return f'<tr ttl="1" data-cid="x">{name}{tds}</tr>'


def _page(rows_html, total=None):
    head = f"<html><script>var _total = {total};</script>" if total is not None else "<html>"
    return head + "<table id='datatb'>" + rows_html + "</table></html>"


# ============ _parse_rows ============
def test_parse_rows_basic():
    html = _page(_row("BookA", "1.50", "4.00", "6.00") + _row("BookB", "1.60", "4.20", "6.50"))
    rows = oc._parse_rows(html)
    assert rows == [("BookA", 1.5, 4.0, 6.0), ("BookB", 1.6, 4.2, 6.5)]


def test_parse_rows_takes_first_three_klfc_as_instant():
    """一行有 即时3 + 初始3 共6个 klfc 单元格 → 只取前3(即时), 初始被忽略。"""
    html = _page(_row("Bk", "1.50", "4.00", "6.00", chupan=("9.90", "9.90", "9.90")))
    rows = oc._parse_rows(html)
    assert rows == [("Bk", 1.5, 4.0, 6.0)]


def test_parse_rows_skips_row_without_ttl_or_datatime():
    """行门控: <tr> 不带 ttl=" / data-time=" → 不当作赔率行。"""
    bad = '<tr class="foo">' + '<td><span class="quancheng">X</span></td>' \
          + _cell("1.50") + _cell("4.00") + _cell("6.00") + "</tr>"
    assert oc._parse_rows(_page(bad)) == []


def test_parse_rows_accepts_data_time_gate():
    """data-time=" 也算合法赔率行(与 ttl 二选一)。"""
    row = '<tr data-time="2026">' + '<td><span class="quancheng">Y</span></td>' \
          + _cell("2.00") + _cell("3.00") + _cell("4.00") + "</tr>"
    assert oc._parse_rows(_page(row)) == [("Y", 2.0, 3.0, 4.0)]


def test_parse_rows_filters_sub_one_odds():
    """任一即时赔率 <1(异常/占位) → 整行丢弃。"""
    html = _page(_row("Z", "0.90", "4.00", "6.00"))
    assert oc._parse_rows(html) == []


def test_parse_rows_skips_when_fewer_than_three():
    """不足3个赔率单元格 → 跳过。"""
    row = '<tr ttl="1">' + '<td><span class="quancheng">W</span></td>' \
          + _cell("1.50") + _cell("4.00") + "</tr>"
    assert oc._parse_rows(_page(row)) == []


def test_parse_rows_missing_company_name_is_question_mark():
    """行内无 span.quancheng → 公司名记为 '?'(仍取赔率)。"""
    row = '<tr ttl="1">' + _cell("1.50") + _cell("4.00") + _cell("6.00") + "</tr>"
    rows = oc._parse_rows(_page(row))
    assert rows == [("?", 1.5, 4.0, 6.0)]


# ============ _book_rows: 翻页补全(核心回归) ============
class _FakeGet:
    """按 URL 分发的假 _get: 首页 vs AJAX 翻页(start=N)。记录调用便于断言。"""
    def __init__(self, first_page, ajax_by_start):
        self.first_page = first_page
        self.ajax_by_start = ajax_by_start   # {start:int -> html}
        self.calls = []

    def __call__(self, url, referer=None):
        self.calls.append(url)
        if "fenxi1/ouzhi.php" in url:
            m = oc.re.search(r"start=(\d+)", url)
            start = int(m.group(1))
            return self.ajax_by_start.get(start, _page(""))   # 未配置 → 空页
        return self.first_page


def test_book_rows_paginates_to_total(monkeypatch):
    """回归核心: 首屏3家但 _total=5 → 走 AJAX 翻页(start=3)补齐剩余2家, 不漏。"""
    first = _page(
        _row("A", "1.50", "4.00", "6.00") + _row("B", "1.60", "4.10", "6.20")
        + _row("C", "1.70", "4.20", "6.40"),
        total=5,
    )
    ajax = {3: _page(_row("D", "1.80", "4.30", "6.60") + _row("E", "1.90", "4.40", "6.80"))}
    fake = _FakeGet(first, ajax)
    monkeypatch.setattr(oc, "_get", fake)

    rows = oc._book_rows("999")
    assert [r[0] for r in rows] == ["A", "B", "C", "D", "E"]
    # 确实发起了翻页请求(start=3)
    assert any("start=3" in u for u in fake.calls)


def test_book_rows_dedup_by_company(monkeypatch):
    """翻页返回的重复公司(名相同)被去重, 不重复计入。"""
    first = _page(_row("A", "1.50", "4.00", "6.00") + _row("B", "1.60", "4.10", "6.20")
                  + _row("C", "1.70", "4.20", "6.40"), total=5)
    # AJAX 回来 B(重复) + D(新) → 只加 D
    ajax = {3: _page(_row("B", "1.61", "4.11", "6.21") + _row("D", "1.80", "4.30", "6.60"))}
    monkeypatch.setattr(oc, "_get", _FakeGet(first, ajax))
    rows = oc._book_rows("999")
    names = [r[0] for r in rows]
    assert names.count("B") == 1
    assert "D" in names


def test_book_rows_question_mark_not_deduped(monkeypatch):
    """'?'(解析失败名缺失)不参与去重 —— 否则首页一个'?'会误杀后续所有解析失败家。"""
    first = _page(_row("?", "1.50", "4.00", "6.00") + _row("A", "1.60", "4.10", "6.20")
                  + _row("B", "1.70", "4.20", "6.40"), total=5)
    ajax = {3: _page(_row("?", "1.55", "4.05", "6.05") + _row("C", "1.80", "4.30", "6.60"))}
    monkeypatch.setattr(oc, "_get", _FakeGet(first, ajax))
    rows = oc._book_rows("999")
    # 两个 '?' 都保留(不因重名被丢)
    assert [r[0] for r in rows].count("?") == 2
    assert "C" in [r[0] for r in rows]


def test_book_rows_stops_when_no_fresh(monkeypatch):
    """AJAX 翻页无新家(全重复) → break, 不死循环(哪怕 _total 还没到)。"""
    first = _page(_row("A", "1.50", "4.00", "6.00") + _row("B", "1.60", "4.10", "6.20")
                  + _row("C", "1.70", "4.20", "6.40"), total=99)  # total 故意很大
    ajax = {3: _page(_row("A", "1.50", "4.00", "6.00"))}   # 只回已见的 A
    fake = _FakeGet(first, ajax)
    monkeypatch.setattr(oc, "_get", fake)
    rows = oc._book_rows("999")
    assert [r[0] for r in rows] == ["A", "B", "C"]     # 停在首屏3家
    # guard 有效: AJAX 请求次数远小于 total(不会打满)
    ajax_calls = [u for u in fake.calls if "fenxi1/ouzhi.php" in u]
    assert len(ajax_calls) == 1


def test_book_rows_no_total_means_no_pagination(monkeypatch):
    """页面无 _total → total=首屏家数 → while 不成立, 不翻页。"""
    first = _page(_row("A", "1.50", "4.00", "6.00") + _row("B", "1.60", "4.10", "6.20")
                  + _row("C", "1.70", "4.20", "6.40"))   # 无 _total
    fake = _FakeGet(first, {})
    monkeypatch.setattr(oc, "_get", fake)
    rows = oc._book_rows("999")
    assert len(rows) == 3
    assert not any("fenxi1/ouzhi.php" in u for u in fake.calls)   # 没发翻页


# ============ consensus: 中位数 + 去水 ============
def test_consensus_median_odd_count(monkeypatch):
    """3家 → 中位数取中间值; 去水%和≈100; overround>1。"""
    rows = [("A", 1.50, 4.0, 6.0), ("B", 1.60, 4.2, 6.5), ("C", 1.70, 4.4, 7.0)]
    monkeypatch.setattr(oc, "_book_rows", lambda fid: rows)
    c = oc.consensus("x")
    assert c["n_books"] == 3
    assert c["euro"] == {"h": 1.6, "d": 4.2, "a": 6.5}   # 三者各自中位
    # 去水: 三路百分比之和应≈100
    dv = c["devig_pct"]
    assert dv["h"] + dv["d"] + dv["a"] == pytest.approx(100.0, abs=0.2)
    assert c["overround"] > 1.0
    assert c["devig_pct"]["h"] == pytest.approx(61.5, abs=0.2)


def test_consensus_median_even_count_averages_middle(monkeypatch):
    """偶数家 → 中位数为中间两个的均值(statistics.median 语义)。"""
    rows = [("A", 1.50, 4.0, 6.0), ("B", 1.60, 4.0, 6.0),
            ("C", 1.80, 4.0, 6.0), ("D", 2.00, 4.0, 6.0)]
    monkeypatch.setattr(oc, "_book_rows", lambda fid: rows)
    c = oc.consensus("x")
    assert c["euro"]["h"] == pytest.approx(1.70)   # (1.60+1.80)/2
    assert c["n_books"] == 4


def test_consensus_returns_none_when_too_few(monkeypatch):
    """不足3家 → None(样本不够不给共识)。"""
    monkeypatch.setattr(oc, "_book_rows", lambda fid: [("A", 1.5, 4.0, 6.0), ("B", 1.6, 4.1, 6.2)])
    assert oc.consensus("x") is None


def test_consensus_sample_capped_at_four(monkeypatch):
    """sample 只留前4家(体检用)。"""
    rows = [(chr(65 + i), 1.5, 4.0, 6.0) for i in range(6)]
    monkeypatch.setattr(oc, "_book_rows", lambda fid: rows)
    c = oc.consensus("x")
    assert len(c["sample"]) == 4
