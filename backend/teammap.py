"""中国体彩中文队名 -> Polymarket 英文名里的"独特子串"(用于对阵配对)。

CN2EN 原样复制自 prototype/wc_value.py 的 48 队字典 (按需补充)。
注意: 部分值是英文名里的独特子串而非全名 (如 "rkiye"→Türkiye, "te d"→Côte d'Ivoire,
"Cura"→Curaçao), 以规避非 ASCII 字符匹配问题。
"""

# 中国体彩中文队名 -> Polymarket 英文名里的"独特子串"(用于配对, 按需补充)
CN2EN = {
 "西班牙": "Spain", "佛得角": "Cabo Verde", "比利时": "Belgium", "埃及": "Egypt",
 "沙特": "Saudi Arabia", "乌拉圭": "Uruguay", "法国": "France", "塞内加尔": "Senegal",
 "阿根廷": "Argentina", "阿尔及利": "Algeria", "英格兰": "England", "克罗地亚": "Croatia",
 "伊朗": "Iran", "新西兰": "New Zealand", "伊拉克": "Iraq", "挪威": "Norway",
 "奥地利": "Austria", "约旦": "Jordan", "葡萄牙": "Portugal", "刚果金": "DR Congo",
 "加纳": "Ghana", "巴拿马": "Panama", "乌兹别克": "Uzbekistan", "哥伦比亚": "Colombia",
 "墨西哥": "Mexico", "韩国": "Korea", "瑞士": "Switzerland", "波黑": "Bosnia",
 "日本": "Japan", "瑞典": "Sweden", "土耳其": "rkiye", "巴拉圭": "Paraguay",
 "苏格兰": "Scotland", "摩洛哥": "Morocco", "美国": "United States", "澳大利亚": "Australia",
 "德国": "Germany", "科特迪瓦": "te d", "巴西": "Brazil", "海地": "Haiti",
 "捷克": "Czechia", "南非": "South Africa", "加拿大": "Canada", "卡塔尔": "Qatar",
 "突尼斯": "Tunisia", "荷兰": "Netherlands", "厄瓜多尔": "Ecuador", "库拉索": "Cura",
}


def en(cn: str) -> str | None:
    """中文队名 -> Polymarket 英文子串; 未收录返回 None。"""
    return CN2EN.get(cn)
