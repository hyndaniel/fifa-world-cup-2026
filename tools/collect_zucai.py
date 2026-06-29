#!/usr/bin/env python3
"""【已退役 2026-06-29】被 tools/refresh_all.py 取代(它一并刷三源 + 推竞彩 raw)。
运行时已不加载(launchctl 无 com.wc.collect-zucai);保留本文件仅作 poolCode 请求模式 /
basic-auth POST 风格的参考(collect_enrich.py、SKILL.md 仍引为模板)。新代码勿再用它。

住宅端足彩采集器: 从本机(住宅 IP, 足彩可直连)拉 getMatchCalculatorV1,
POST 到 HK 看板 /api/ingest/zucai。HK 机房被足彩 EdgeOne WAF 拦(数据中心 IP + JA3),
故由住宅中转 —— 从该能正常访问的入口取数, 不涉及任何 WAF 绕过。纯标准库。

用法:
  export WC_INGEST_URL="http://18.166.71.60:8000/api/ingest/zucai"
  export WC_INGEST_PW="<看板密码>"
  python3 tools/collect_zucai.py            # 循环(默认 180s)
  python3 tools/collect_zucai.py --once     # 只跑一次
环境变量: WC_INGEST_URL / WC_INGEST_PW / WC_INGEST_USER(默认 admin) / WC_INGEST_INTERVAL(默认180)
"""
import base64
import json
import os
import sys
import time
import urllib.request

ZUCAI = ("https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
         "?poolCode=had,hhad,ttg&channel=c")
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148"
INGEST_URL = os.environ.get("WC_INGEST_URL", "http://18.166.71.60:8000/api/ingest/zucai")
PW = os.environ.get("WC_INGEST_PW", "")
USER = os.environ.get("WC_INGEST_USER", "admin")
INTERVAL = int(os.environ.get("WC_INGEST_INTERVAL", "180"))


def fetch_zucai():
    req = urllib.request.Request(ZUCAI, headers={
        "User-Agent": UA,
        "Referer": "https://m.sporttery.cn/",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def post_ingest(raw):
    hdr = {"Content-Type": "application/json"}
    if PW:
        tok = base64.b64encode(f"{USER}:{PW}".encode()).decode()
        hdr["Authorization"] = "Basic " + tok
    req = urllib.request.Request(INGEST_URL, data=raw, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode()


def once():
    raw = fetch_zucai()
    # 校验拿到的是 JSON 而非 WAF 反爬页
    try:
        d = json.loads(raw)
        ok = d.get("success") is True or "matchInfoList" in (d.get("value") or {})
    except Exception:
        ok = False
    if not ok:
        print("足彩返回非 JSON(可能 WAF/网络), 跳过本轮")
        return
    resp = post_ingest(raw)
    print("已上送:", resp)


def main():
    if not PW:
        print("缺 WC_INGEST_PW(看板密码), 退出")
        sys.exit(1)
    if "--once" in sys.argv:
        once()
        return
    print(f"采集循环启动: 每 {INTERVAL}s 拉足彩 → POST {INGEST_URL}")
    while True:
        try:
            once()
        except Exception as e:  # noqa: BLE001
            print("本轮失败:", e)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
