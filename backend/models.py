"""核心类型 (契约, 所有任务共享)。"""
# PEP604 `dict | None` 注解在 3.9 是运行期求值,无此行 import 即 TypeError;
# 加 future 让注解惰性化 → 与 results.py/mech_tag.py 一致,backend 可在 /usr/bin/python3(3.9.6) import。
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ZucaiMatch:
    zucai_num: str
    home_cn: str
    away_cn: str
    ko_bj: str
    cutoff_bj: str
    had: dict | None      # {"h":1.41,"d":3.92,"a":6.05} or None
    hhad: dict | None     # {"line":-1,"h":2.35,"d":3.40,"a":2.44} or None
    ttg: dict             # {0:38.0,1:9.8,...,7:7.0}


@dataclass
class PolyProbs:
    slug: str
    home_en: str
    away_en: str
    ml: dict              # {"home":91.5,"draw":6.5,"away":2.5} (raw %)
    home_cover: dict      # {1.5:78.5, 2.5:55.5, ...}
    away_cover: dict      # {1.5:..,2.5:..}
    ou_over: dict         # {0.5:98.4,1.5:90.5,...}


@dataclass
class ValuePoint:
    market: str
    outcome: str
    zucai_odds: float
    poly_prob_raw: float
    poly_prob_devig: float
    value_raw: float
    value_devig: float
    ev_pct: float
    flag: str
