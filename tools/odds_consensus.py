#!/usr/bin/env python3
"""odds_consensus — 爬 500.com 取世界杯各场 欧盘(多家博彩公司)共识 + 亚盘让球线。

数据源(服务端直出 GBK HTML, best-effort 爬虫, 结构变即需维护):
  - 竞彩交易页 https://trade.500.com/jczq/  -> <tr class="bet-tb-tr" data-fixtureid/homesxname/awaysxname/...>
  - 欧指明细   https://odds.500.com/fenxi/ouzhi-<fid>.shtml -> table#datatb 各公司 即时 主/平/客 欧赔
    页面初始仅渲染首 ~30 家; 全量(页内 _total, 实测 47 家)其余经 AJAX 翻页接口补全:
      https://odds.500.com/fenxi1/ouzhi.php?id=<fid>&ctype=1&start=<N>&style=0&guojia=0&chupan=0
    纯 GET 即可(curl 可达, 无需登录)。注意: 那个"赔率下载"按钮是把屏幕数据 POST 给
    europe_xls.php 套壳的表单(无可复制 URL、对 curl 返空), 别走它 —— 翻页接口才是正道。
共识 = 全量各公司即时欧赔的中位数(抗离群); 另给去水隐含%。CN 队名直接对竞彩。
  自校验: 全量抓齐后, 各公司即时赔率的"算术平均"应与 500 页面"平均值"行逐位吻合。

用法:
  python3 tools/odds_consensus.py                 # 打印今晚(最近)世界杯场欧盘共识
  python3 tools/odds_consensus.py --ingest out.json  # 写 odds_watch ingest 格式(source=consensus)
  python3 tools/odds_consensus.py --self 1359199  # 单场自验(打印各家欧赔)
依赖: 系统 curl(--compressed 解压) + stdlib。
"""
from __future__ import annotations
import json, re, subprocess, sys, statistics

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
TRADE = "https://trade.500.com/jczq/"
OUZHI = "https://odds.500.com/fenxi/ouzhi-{fid}.shtml"
OUZHI_PAGE = ("https://odds.500.com/fenxi1/ouzhi.php?id={fid}&ctype=1&start={start}"
              "&r=1&style=0&guojia=0&chupan=0&currentIndex={start}")  # AJAX 翻页补全全量


def _get(url: str, referer: str | None = None) -> str:
    cmd = ["curl", "-sS", "--compressed", "-m", "20", "-A", UA]
    if referer:
        cmd += ["-e", referer]
    raw = subprocess.run(cmd + [url], capture_output=True).stdout
    return raw.decode("gbk", "ignore")


def fixtures(league: str = "世界杯") -> list[dict]:
    """解析竞彩交易页, 返回指定联赛的场次列表。"""
    html = _get(TRADE)
    out = []
    for r in re.findall(r'<tr class="bet-tb-tr"([^>]+)>', html):
        def at(k):
            m = re.search(rf'data-{k}="([^"]*)"', r)
            return m.group(1) if m else ""
        if at("simpleleague") == league:
            out.append({"fixtureid": at("fixtureid"), "home_cn": at("homesxname"),
                        "away_cn": at("awaysxname"), "date": at("matchdate"),
                        "time": at("matchtime"), "rangqiu": at("rangqiu")})
    return out


def _parse_rows(html: str):
    """从欧指 HTML 片段抽各公司 即时(company,h,d,a)。页面与 AJAX 翻页响应结构一致, 共用此解析。
    即时主/平/客 = 每行内带 klfc= 属性的 <td onclick="OZ.r(this)"> 单元格文本前3个;
    公司名取 <span class="quancheng">。500 对名称做星号打码, 不影响取赔率。"""
    rows = []
    for tr in re.findall(r'<tr[^>]*(?:ttl="|data-time=")[^>]*>.*?</tr>', html, re.S):
        odds = re.findall(r'<td[^>]*klfc="[^"]*"[^>]*>\s*([0-9]+\.[0-9]+)\s*</td>', tr)
        if len(odds) < 3:
            continue
        h, d, a = float(odds[0]), float(odds[1]), float(odds[2])  # 前3=即时主/平/客
        if not (h >= 1 and d >= 1 and a >= 1):
            continue
        cm = re.search(r'class="quancheng"[^>]*>\s*([^<]+?)\s*<', tr)
        rows.append(((cm.group(1).strip() if cm else "?"), h, d, a))
    return rows


def _book_rows(fid: str):
    """各公司即时欧赔 [(company,h,d,a)], 翻页拉全 _total 家。
    首页只渲染 ~30 家; 读页内 _total, 再用 AJAX 翻页(start=已抓数)续拉, 直到补齐或无新增。
    去重以公司名为键(打码后仍稳定), 兼作翻页防卡死的安全网。"""
    ref = OUZHI.format(fid=fid)
    html = _get(ref)
    rows = _parse_rows(html)
    m = re.search(r'\b_total\s*=\s*(\d+)', html)  # \b 防误命中 page_total 之类子串
    total = int(m.group(1)) if m else len(rows)
    seen = {r[0] for r in rows}
    guard = 0
    while len(rows) < total and guard < 20:
        guard += 1
        more = _parse_rows(_get(OUZHI_PAGE.format(fid=fid, start=len(rows)), referer=ref))
        # 名缺失("?")不参与去重 —— 否则首页一个"?"会把后续所有解析失败的公司误判为重复而丢弃
        fresh = [r for r in more if r[0] == "?" or r[0] not in seen]
        if not fresh:
            break  # 翻页无新公司 -> 到底/重复, 停, 不死循环
        seen.update(r[0] for r in fresh)
        rows += fresh
    return rows


def consensus(fid: str) -> dict | None:
    """各公司即时欧赔的中位数共识 + 去水隐含%。"""
    rows = _book_rows(fid)
    if len(rows) < 3:
        return None
    H = statistics.median(r[1] for r in rows)
    D = statistics.median(r[2] for r in rows)
    A = statistics.median(r[3] for r in rows)
    imp = [1/H, 1/D, 1/A]; s = sum(imp)
    return {"n_books": len(rows),
            "euro": {"h": round(H, 2), "d": round(D, 2), "a": round(A, 2)},
            "devig_pct": {"h": round(imp[0]/s*100, 1), "d": round(imp[1]/s*100, 1),
                          "a": round(imp[2]/s*100, 1)},
            "overround": round(s, 4),
            "sample": rows[:4]}


def collect(league="世界杯") -> list[dict]:
    out = []
    for fx in fixtures(league):
        c = consensus(fx["fixtureid"])
        out.append({**fx, "consensus": c})
    return out


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--self":
        rows = _book_rows(sys.argv[2])
        print(f"fid={sys.argv[2]} 公司数={len(rows)}")
        for r in rows[:12]:
            print(f"  {r[0]:<12} 主{r[1]} 平{r[2]} 客{r[3]}")
        if rows:  # 算术平均: 应与 500 页面"平均值"行逐位吻合, 即全量抓齐的硬证据
            mean = [round(statistics.mean(x), 3) for x in zip(*[(r[1], r[2], r[3]) for r in rows])]
            print(f"算术平均(对账 500 平均值行): 主{mean[0]} 平{mean[1]} 客{mean[2]}")
        c = consensus(sys.argv[2])
        print("中位共识:", c["euro"], "去水%", c["devig_pct"], "返还%", round(100/c["overround"],1)) if c else print("共识不足")
        sys.exit(0)
    data = collect()
    if len(sys.argv) > 2 and sys.argv[1] == "--ingest":
        ing = [{"match_key": f"500-{d['fixtureid']}", "label": f"{d['home_cn']} vs {d['away_cn']}",
                "ko": f"{d['date']} {d['time']}",
                "payload": {"euro_consensus": d["consensus"]["euro"] if d["consensus"] else None,
                            "devig_pct": d["consensus"]["devig_pct"] if d["consensus"] else None,
                            "n_books": d["consensus"]["n_books"] if d["consensus"] else 0}}
               for d in data]
        json.dump(ing, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
        print(f"写出 {len(ing)} 场 -> {sys.argv[2]}")
    else:
        print(f"{'场次':<20}{'公司数':>5}  欧盘共识(主/平/客)        去水%(主/平/客)   返还%")
        for d in data:
            c = d["consensus"]
            lab = f"{d['home_cn']} vs {d['away_cn']}"
            if c:
                e, dv = c["euro"], c["devig_pct"]
                print(f"{lab:<20}{c['n_books']:>5}  {e['h']}/{e['d']}/{e['a']:<6}  {dv['h']}/{dv['d']}/{dv['a']}%   {round(100/c['overround'],1)}")
            else:
                print(f"{lab:<20}{'?':>5}  (公司数不足/解析失败)")
