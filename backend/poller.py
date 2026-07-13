"""poller 编排: 拉两边盘口 → 配对 → 算 value → 写库 (plan Task 6)。

一轮 (poll_once):
  1. 拉足彩 (zucai_fetch 默认 sporttery.fetch) → parse_matches → list[ZucaiMatch]
  2. 拉 poly event 列表 (poly_list 默认 polymarket.list_events) → {slug: title}
  3. 对每场:
     - en(home_cn)/en(away_cn) 取英文子串; 任一缺失 → warning 跳过
     - find_slug(idx, hen, aen); 无 slug → warning 跳过
     - poly_fetch_event(slug) (默认 polymarket.fetch_event) → (base, more)
     - parse_probs(base, more, hen, aen) → PolyProbs
     - compute_value(z, p) → list[ValuePoint]
     - db.upsert_match + db.save_snapshot(zucai/poly) + db.save_value_points
  返回成功处理的场数。

依赖注入 (zucai_fetch / poly_list / poly_fetch_event) 仅为测试: 注入 fixture 即可
不联网。生产调用 poll_once(db, cfg) 走各模块默认真实实现。
"""
from __future__ import annotations

import logging
import time

from . import polymarket, sporttery
from .teammap import en
from .value import compute_value

log = logging.getLogger(__name__)


def poll_once(
    db,
    cfg,
    *,
    zucai_fetch=None,
    poly_list=None,
    poly_fetch_event=None,
) -> int:
    """跑一轮: 拉两边盘口 → 配对 → 算 value → 写库, 返回成功处理的场数。

    依赖注入参数 (默认走真实实现, 测试时注入 fixture):
      - zucai_fetch():        足彩原始响应 dict; 默认 sporttery.fetch(cfg)
      - poly_list():          {slug: title} 索引; 默认 polymarket.list_events()
      - poly_fetch_event(slug): (base, more) 两个原始列表; 默认 polymarket.fetch_event(slug)
    """
    cfg = cfg or {}
    yellow_below = (cfg.get("value") or {}).get("devig_yellow_below", 1.03)

    # 1. 足彩
    if zucai_fetch is None:
        raw = sporttery.fetch(cfg)
    else:
        raw = zucai_fetch()
    matches = sporttery.parse_matches(raw)

    # 2. poly event 列表
    if poly_list is None:
        idx = polymarket.list_events()
    else:
        idx = poly_list()

    fetch_event = poly_fetch_event or polymarket.fetch_event

    # 3. 逐场处理
    #
    # 🔴 两段式, 且**场次登记不依赖 Poly**:
    #    Poly 是增强项(去水概率/value), 竞彩才是场次的权威来源。此前 Poly 拿不到 slug 就
    #    `continue` 掉整场, 连 matches 都不写 —— 于是 Poly 一被墙/超时, matches 表就永久
    #    停摆, 下游(跑今天判场 / v2 事实卡 / 看板决策卡)全线卡死, 哪怕竞彩数据完好无损。
    #    (2026-07-13 实测: 半决赛 101/102 竞彩早已上市, matches 却停在 84 场。)
    #    现在: 先登记场次 + 存竞彩快照, Poly 失败只损失该场的 poly 快照与 value_points。
    registered = 0   # 场次登记成功(竞彩侧)
    processed = 0    # 含 Poly 的完整处理 —— 返回值语义保持不变
    for z in matches:
        hen = en(z.home_cn)
        aen = en(z.away_cn)
        if not hen or not aen:
            # 英文名是 Poly 配对与 matches.home_en 的必填项, 无从补齐 → 仍跳过
            log.warning(
                "skip %s %s/%s: 队名未收录 (hen=%r aen=%r)",
                z.zucai_num, z.home_cn, z.away_cn, hen, aen,
            )
            continue

        # --- 第 1 段: 登记场次 + 竞彩快照 (不碰 Poly) ---
        # 单场任何异常 (脏数据/解析失败) 只跳过该场, 不拖垮整批
        try:
            # upsert 是整行覆盖(ON CONFLICT DO UPDATE 用 excluded.*), 若这里传 poly_slug=None
            # 会把已存的 slug 抹掉; 故先取旧值兜住, 待下段拿到新 slug 再覆盖。
            prev = db.match(z.zucai_num)
            prev_slug = prev["poly_slug"] if prev else None

            match_id = db.upsert_match(
                zucai_num=z.zucai_num,
                home_cn=z.home_cn, away_cn=z.away_cn,
                home_en=hen, away_en=aen,
                poly_slug=prev_slug,
                ko_bj=z.ko_bj, cutoff_bj=z.cutoff_bj,
            )
            db.save_snapshot(match_id, "zucai", z.__dict__)
            registered += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "skip %s %s/%s: 场次登记失败",
                z.zucai_num, z.home_cn, z.away_cn,
            )
            continue

        # --- 第 2 段: Poly 增强 (失败不影响上面已登记的场次) ---
        try:
            slug, _title = polymarket.find_slug(idx, hen, aen)
            if not slug:
                log.warning(
                    "%s %s/%s: poly 无对应 slug — 场次已登记, 仅缺 poly 去水/value",
                    z.zucai_num, z.home_cn, z.away_cn,
                )
                continue

            base, more = fetch_event(slug)
            if not base:
                log.warning(
                    "%s %s/%s (slug=%s): poly event 为空 — 场次已登记, 仅缺 poly 去水/value",
                    z.zucai_num, z.home_cn, z.away_cn, slug,
                )
                continue

            probs = polymarket.parse_probs(base, more, hen, aen)
            points = compute_value(z, probs, yellow_below=yellow_below)

            if slug != prev_slug:  # 补回/更新 slug
                db.upsert_match(
                    zucai_num=z.zucai_num,
                    home_cn=z.home_cn, away_cn=z.away_cn,
                    home_en=hen, away_en=aen,
                    poly_slug=slug,
                    ko_bj=z.ko_bj, cutoff_bj=z.cutoff_bj,
                )
            db.save_snapshot(match_id, "poly", probs.__dict__)
            db.save_value_points(match_id, points)
            processed += 1
        except Exception:  # noqa: BLE001 — 单场 poly 失败不应中断整批
            log.exception(
                "%s %s/%s: poly 段异常 — 场次已登记, 仅缺 poly 去水/value",
                z.zucai_num, z.home_cn, z.away_cn,
            )

    log.info(
        "poll_once: 登记 %d/%d 场(竞彩), 其中 %d 场含 Poly",
        registered, len(matches), processed,
    )
    return processed


def run_loop(db, cfg):
    """无限轮询: poll_once → sleep(cfg.poll.interval_sec) → 重复。"""
    interval = (cfg.get("poll") or {}).get("interval_sec", 180)
    while True:
        try:
            poll_once(db, cfg)
        except Exception:  # noqa: BLE001 — 单轮失败不应中断循环
            log.exception("poll_once 失败, 等待下一轮")
        time.sleep(interval)
