#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WC2026 足彩 vs Polymarket 价值表
- 拉中国体彩竞彩(胜平负had / 让球hhad / 总进球ttg) + Polymarket(聪明钱)概率
- 按对阵自动配对, 算每个可投结果的 value = 足彩欧赔 × Poly真实概率
- value>1 = +EV(标 🟢), 0.97~1 接近公允(🟡), 其余 -EV
依赖: 只需 curl + 本机 SOCKS5 代理(连 Polymarket). 足彩接口走直连.
用法: python3 wc_value.py            # 全部可配对场次
      python3 wc_value.py 西班牙 法国  # 只看含这些队的场次
"""
import json, subprocess, sys

PROXY     = "127.0.0.1:7898"
GAMMA     = "https://gamma-api.polymarket.com"
WC_TAG    = "102232"  # FIFA World Cup tag
SPORTTERY = "https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry?poolCode=had,hhad,ttg&channel=c"
UA        = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) Mobile/15E148"
HL        = 0.97  # 标黄阈值

# 中国体彩中文队名 -> Polymarket 英文名里的"独特子串"(用于配对, 按需补充)
CN2EN = {
 "西班牙":"Spain","佛得角":"Cabo Verde","比利时":"Belgium","埃及":"Egypt",
 "沙特":"Saudi Arabia","乌拉圭":"Uruguay","法国":"France","塞内加尔":"Senegal",
 "阿根廷":"Argentina","阿尔及利":"Algeria","英格兰":"England","克罗地亚":"Croatia",
 "伊朗":"Iran","新西兰":"New Zealand","伊拉克":"Iraq","挪威":"Norway",
 "奥地利":"Austria","约旦":"Jordan","葡萄牙":"Portugal","刚果金":"DR Congo",
 "加纳":"Ghana","巴拿马":"Panama","乌兹别克":"Uzbekistan","哥伦比亚":"Colombia",
 "墨西哥":"Mexico","韩国":"Korea","瑞士":"Switzerland","波黑":"Bosnia",
 "日本":"Japan","瑞典":"Sweden","土耳其":"rkiye","巴拉圭":"Paraguay",
 "苏格兰":"Scotland","摩洛哥":"Morocco","美国":"United States","澳大利亚":"Australia",
 "德国":"Germany","科特迪瓦":"te d","巴西":"Brazil","海地":"Haiti",
 "捷克":"Czechia","南非":"South Africa","加拿大":"Canada","卡塔尔":"Qatar",
 "突尼斯":"Tunisia","荷兰":"Netherlands","厄瓜多尔":"Ecuador","库拉索":"Cura",
}

def curl(args):
    r = subprocess.run(["curl","-s","--max-time","30"]+args, capture_output=True, text=True)
    return r.stdout

def poly(path):
    out = curl(["--socks5-hostname",PROXY, GAMMA+path, "-H","Accept: application/json"])
    try:    return json.loads(out) if out.strip() else []
    except: return []

def en(cn):
    return CN2EN.get(cn)

# ---------- 1. Polymarket: 列出全部世界杯 event, 按英文名建索引 ----------
def poly_index():
    idx = {}  # (slug) -> {title, slug}
    for off in (0,100,200):
        evs = poly(f"/events?closed=false&tag_id={WC_TAG}&limit=100&offset={off}&order=startDate&ascending=true")
        for e in evs:
            s = e.get("slug","") or ""
            if s.startswith("fifwc-") and "more-markets" not in s:
                idx[s] = e.get("title","")
        if len(evs) < 100: break
    return idx

def find_slug(idx, hen, aen):
    for s,t in idx.items():
        if hen in t and aen in t:
            return s, t
    return None, None

# ---------- 2. 解析 Polymarket 概率(%) ----------
def poly_probs(slug, hen, aen):
    base = poly(f"/events?slug={slug}")
    more = poly(f"/events?slug={slug}-more-markets")
    ml = {}      # team_en_substr -> win%
    draw = None
    cover = {hen:{}, aen:{}}  # team -> {line: cover%}
    ov = {}      # goalline -> over%
    def whichteam(name):
        if hen in name: return hen
        if aen in name: return aen
        return None
    if base:
        for m in base[0].get("markets",[]):
            q=m.get("question",""); o=json.loads(m.get("outcomes","[]")); p=json.loads(m.get("outcomePrices","[]"))
            kv=dict(zip(o,p))
            if "win on" in q:
                tm = whichteam(q)
                if tm and "Yes" in kv: ml[tm]=float(kv["Yes"])*100
            elif "draw" in q and "Yes" in kv:
                draw=float(kv["Yes"])*100
    if more:
        import re
        for m in more[0].get("markets",[]):
            q=m.get("question",""); o=json.loads(m.get("outcomes","[]")); p=json.loads(m.get("outcomePrices","[]"))
            kv=dict(zip(o,p))
            if q.startswith("Spread:"):
                mm=re.search(r"Spread:\s*(.+?)\s*\(([-+]?\d+\.5)\)", q)
                if mm:
                    tname=mm.group(1); line=abs(float(mm.group(2)))
                    tm=whichteam(tname)
                    if tm and tname in kv:
                        cover[tm][line]=float(kv[tname])*100
            else:
                tail=q.split(": ")[-1]
                mm=re.fullmatch(r"O/U (\d\.5)", tail)
                if mm and "Over" in kv:
                    ov[float(mm.group(1))]=float(kv["Over"])*100
    # 便捷: cover[team][0.5] = 该队净胜≥1 = 该队胜%
    if hen in ml: cover[hen][0.5]=ml[hen]
    if aen in ml: cover[aen][0.5]=ml[aen]
    return {"ml":ml,"draw":draw,"cover":cover,"ov":ov}

# ---------- 3. 足彩 outcome -> Poly概率 映射 ----------
def map_had(pp,hen,aen,which):
    if which=="h": return pp["ml"].get(hen)
    if which=="d": return pp["draw"]
    if which=="a": return pp["ml"].get(aen)

def hcov(pp,team,line):  # cover% at line; line=0.5 -> 胜%
    return pp["cover"].get(team,{}).get(line)

def map_hhad(pp,hen,aen,line,which):
    # line<0: 主队让球; line>0: 主队受让
    if line<0:
        k=abs(line)
        if which=="h": return hcov(pp,hen,k+0.5)                       # 主净胜≥k+1
        if which=="d":
            lo=hcov(pp,hen,k-0.5); hi=hcov(pp,hen,k+0.5)               # 主净胜恰好k
            return None if lo is None or hi is None else lo-hi
        if which=="a":
            lo=hcov(pp,hen,k-0.5)
            return None if lo is None else 100-lo
    else:
        k=line
        if which=="h":
            ac=hcov(pp,aen,k-0.5)                                      # 主不输k+ = 100-客净胜≥k
            return None if ac is None else 100-ac
        if which=="d":
            lo=hcov(pp,aen,k-0.5); hi=hcov(pp,aen,k+0.5)              # 主恰输k
            return None if lo is None or hi is None else lo-hi
        if which=="a":
            return hcov(pp,aen,k+0.5)                                  # 主输≥k+1

def map_ttg(pp,n):
    ov=pp["ov"]
    lo = 100.0 if n==0 else ov.get(n-0.5)
    hi = ov.get(n+0.5)
    if n>=6 and hi is None:   # 高分桶缺线 -> 不可靠, 跳过
        return None
    if lo is None or hi is None: return None
    return lo-hi

# ---------- 4. 主流程 ----------
def main():
    filt=[en(a) or a for a in sys.argv[1:]]
    print("拉取 Polymarket 世界杯赛程 ...", file=sys.stderr)
    idx=poly_index()
    print(f"  Poly 场次 {len(idx)}", file=sys.stderr)
    print("拉取 足彩盘口 ...", file=sys.stderr)
    out=curl(["-A",UA,"-H","Referer: https://m.sporttery.cn/", SPORTTERY])
    zc=json.loads(out)["value"]["matchInfoList"]

    allrows=[]
    for day in zc:
        for m in day["subMatchList"]:
            hcn=m["homeTeamAbbName"]; acn=m["awayTeamAbbName"]
            hen=en(hcn); aen=en(acn)
            if not hen or not aen:
                print(f"  ⚠ 未配对(补 CN2EN): {hcn} vs {acn}", file=sys.stderr); continue
            if filt and not any(f in (hen,aen) for f in filt): continue
            slug,title=find_slug(idx,hen,aen)
            if not slug:
                print(f"  ⚠ Poly无此场: {hcn}({hen}) vs {acn}({aen})", file=sys.stderr); continue
            pp=poly_probs(slug,hen,aen)
            rows=[]
            def add(market,outcome,odds,prob):
                if odds and prob is not None:
                    v=odds*prob/100.0
                    rows.append((market,outcome,odds,round(prob,1),round(v,3),round((v-1)*100,1)))
            had=m.get("had") or {}; hh=m.get("hhad") or {}; ttg=m.get("ttg") or {}
            if had.get("h"):
                add("胜平负","主胜",float(had["h"]),map_had(pp,hen,aen,"h"))
                add("胜平负","平",  float(had["d"]),map_had(pp,hen,aen,"d"))
                add("胜平负","客胜",float(had["a"]),map_had(pp,hen,aen,"a"))
            if hh.get("h"):
                L=int(float(hh["goalLine"]))
                tag=f"让{L:+d}"
                add(tag,"主"+("让" if L<0 else "受")+"胜",float(hh["h"]),map_hhad(pp,hen,aen,L,"h"))
                add(tag,"平",                              float(hh["d"]),map_hhad(pp,hen,aen,L,"d"))
                add(tag,"客胜",                            float(hh["a"]),map_hhad(pp,hen,aen,L,"a"))
            for n in range(0,8):
                key=f"s{n}" if n<7 else "s7"
                odds=ttg.get(key)
                lab=f"{n}球" if n<7 else "7+球"
                if odds: add("总进球",lab,float(odds),map_ttg(pp,n))
            rows.sort(key=lambda r:-r[4])
            allrows.append((f"{m['matchNumStr']} {hcn} vs {acn}",title,rows))

    # 输出
    plus=[]
    for hdr,title,rows in allrows:
        print(f"\n{'='*60}\n{hdr}   [{title}]")
        print(f"  {'盘口':<6}{'结果':<8}{'足彩':>6}{'Poly%':>7}{'value':>7}{'EV%':>7}")
        for mk,oc,od,pr,v,ev in rows:
            mark = " 🟢" if v>=1.0 else (" 🟡" if v>=HL else "")
            print(f"  {mk:<6}{oc:<8}{od:>6.2f}{pr:>7.1f}{v:>7.3f}{ev:>+7.1f}{mark}")
            if v>=HL: plus.append((hdr,mk,oc,od,pr,v,ev))
    print(f"\n{'='*60}\n🟢/🟡 可投点(value≥{HL}, 按 value 降序):")
    for hdr,mk,oc,od,pr,v,ev in sorted(plus,key=lambda x:-x[5]):
        flag="🟢真+EV" if v>=1.0 else "🟡接近公允"
        print(f"  {flag}  {hdr.split('   ')[0]:<16} {mk}{oc}  足彩{od} ×Poly{pr}% = {v} (EV{ev:+.1f}%)")
    print("\n注: Poly概率含~1-2%抽水, value仅略>1的为薄边, 视作接近公允非铁+EV. 让球高分桶/缺O-U线的总进球已跳过.")

if __name__=="__main__":
    main()
