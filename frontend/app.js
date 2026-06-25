/* WC 价值看板 前端逻辑
 * 契约 (见计划 /state JSON 与 API):
 *   GET  /api/state                 -> {ts, next_cutoff, value_radar[], watchlist[], ledger{A,B}, matches_today[]}
 *   GET  /api/reports               -> [{name, title}, ...]   (容错: 也支持 [string,...])
 *   GET  /api/reports/{name}        -> markdown 文本
 *   POST /api/bets        body: {wallet, legs, stake, odds, note}
 *   POST /api/watchlist   body: {kind, key, note}
 *   DELETE /api/watchlist/{id}      (容错: 也支持 ?id= )
 */
"use strict";

const POLL_INTERVAL_MS = 30_000; // 每 N 秒拉一次 /state

// ---------- 小工具 ----------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtNum(v, digits = 2) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return Number(v).toFixed(digits) + "%";
}

function fmtSignedPct(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  return (n >= 0 ? "+" : "") + n.toFixed(digits) + "%";
}

function fmtHMS(totalSec) {
  let s = Math.max(0, Math.floor(totalSec));
  const h = Math.floor(s / 3600);
  s -= h * 3600;
  const m = Math.floor(s / 60);
  s -= m * 60;
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}`;
}

async function apiGet(path) {
  const r = await fetch(path, { headers: { Accept: "application/json" } });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

async function apiSend(path, method, body) {
  const r = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body == null ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let detail = "";
    try {
      detail = JSON.stringify(await r.json());
    } catch (_) {
      /* ignore */
    }
    throw new Error(`${method} ${path} -> ${r.status} ${detail}`);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

// ---------- 全局状态 ----------
const state = {
  lastState: null,
  cutoffDeadlineMs: null, // 倒计时本地基准 (绝对时间戳)
  cdTimer: null,
  pollTimer: null,
  activeReport: null,
};

// ================= 倒计时 (本地 tick) =================
function setCutoff(nextCutoff) {
  const matchEl = $("#cd-match");
  const cutoffEl = $("#cd-cutoff");
  if (!nextCutoff || nextCutoff.countdown_sec == null) {
    state.cutoffDeadlineMs = null;
    matchEl.textContent = "今日无待停售场次";
    cutoffEl.textContent = "";
    $("#cd-timer").textContent = "--:--:--";
    return;
  }
  // 用服务端给的剩余秒数作为基准, 之后本地每秒递减
  state.cutoffDeadlineMs = Date.now() + Number(nextCutoff.countdown_sec) * 1000;
  matchEl.textContent = nextCutoff.match || "—";
  cutoffEl.textContent = nextCutoff.cutoff_bj ? `停售 ${nextCutoff.cutoff_bj}` : "";
  tickCountdown();
}

function tickCountdown() {
  const timerEl = $("#cd-timer");
  if (state.cutoffDeadlineMs == null) {
    timerEl.textContent = "--:--:--";
    return;
  }
  const remainSec = (state.cutoffDeadlineMs - Date.now()) / 1000;
  timerEl.textContent = fmtHMS(remainSec);
  const cd = $("#countdown");
  cd.classList.toggle("urgent", remainSec > 0 && remainSec <= 600); // <=10min 高亮
  cd.classList.toggle("closed", remainSec <= 0);
  if (remainSec <= 0) timerEl.textContent = "已停售";
}

// ================= 价值雷达 =================
function flagBadge(flag) {
  if (flag === "green") return { dot: "🟢", label: "真 +EV", cls: "flag-green" };
  if (flag === "yellow") return { dot: "🟡", label: "接近公允", cls: "flag-yellow" };
  if (flag === "red") return { dot: "🔴", label: "明显 -EV", cls: "flag-red" };
  return { dot: "⚪", label: "跳过", cls: "flag-skip" };
}

// 隐含概率 = 100 / 足彩赔率 (单位 %)。无效赔率 -> null
function impliedPct(zucaiOdds) {
  const o = Number(zucaiOdds);
  if (!(o > 0) || Number.isNaN(o)) return null;
  return Math.round((100 / o) * 10) / 10;
}

// 去水概率差 = poly_prob_devig - 隐含概率 (带符号, 1 位小数)。任一缺失 -> null
function probEdge(r) {
  const implied = impliedPct(r.zucai_odds);
  const poly = Number(r.poly_prob_devig);
  if (implied == null || Number.isNaN(poly) || r.poly_prob_devig == null) return null;
  return Math.round((poly - implied) * 10) / 10;
}

// 始终可见的紧凑对比行 DOM:
//   足彩 {odds} (隐含 {implied}%) · Poly {poly}% 去水 · 差 {edge}% · EV {ev}%
function buildCompareLine(r) {
  const implied = impliedPct(r.zucai_odds);
  const edge = probEdge(r);
  const evDevig = r.ev_pct_devig != null ? r.ev_pct_devig : r.ev_pct;
  const line = el("div", "ri-compare");

  const seg = (text, cls) => {
    const s = el("span", cls || "", text);
    return s;
  };

  line.appendChild(seg(`足彩 ${fmtNum(r.zucai_odds)} (隐含 ${implied == null ? "—" : fmtNum(implied, 1)}%)`));
  line.appendChild(seg(" · "));
  line.appendChild(seg(`Poly ${r.poly_prob_devig == null ? "—" : fmtNum(r.poly_prob_devig, 1)}% 去水`));
  line.appendChild(seg(" · "));
  const edgeText = `差 ${edge == null ? "—" : fmtSignedPct(edge)}`;
  line.appendChild(seg(edgeText, edge == null ? "" : edge >= 0 ? "ri-cmp-pos" : "ri-cmp-neg"));
  line.appendChild(seg(" · "));
  const evVal = Number(evDevig);
  const evText = `EV ${evDevig == null || Number.isNaN(evVal) ? "—" : fmtSignedPct(evDevig)}`;
  line.appendChild(seg(evText, evDevig == null || Number.isNaN(evVal) ? "" : evVal >= 0 ? "ri-cmp-pos" : "ri-cmp-neg"));

  return line;
}

function renderRadar(rows) {
  const list = $("#radar-list");
  list.innerHTML = "";
  if (!rows || rows.length === 0) {
    list.appendChild(el("div", "empty", "暂无可投价值点"));
    return;
  }
  // 按比赛(match + ko_bj)分组: 一场一张卡, 多条腿挂在下面。保留传入顺序
  // (后端已按 EV 去水降序), 组的先后取该场首条腿出现的次序。
  const groups = [];
  const byKey = new Map();
  for (const r of rows) {
    const key = `${r.match || "—"}|${r.ko_bj || ""}`;
    let g = byKey.get(key);
    if (!g) {
      g = { match: r.match || "—", ko_bj: r.ko_bj || "", legs: [] };
      byKey.set(key, g);
      groups.push(g);
    }
    g.legs.push(r);
  }
  for (const g of groups) {
    list.appendChild(renderRadarGroup(g));
  }
}

// 一场比赛 = 一张分组卡: 组头(队名 + 开赛时间 + 条数), 下挂各盘口腿。
function renderRadarGroup(g) {
  // 组头着色取组内最高级别: 有 green 用 green, 否则 yellow。
  const hasGreen = g.legs.some((l) => l.flag === "green");
  const wrap = el("div", `radar-group ${hasGreen ? "flag-green" : "flag-yellow"}`);

  const head = el("div", "rg-head");
  const title = el("div", "rg-title");
  title.appendChild(el("span", "rg-match", g.match));
  if (g.ko_bj) title.appendChild(el("span", "rg-ko", g.ko_bj));
  head.appendChild(title);
  head.appendChild(el("span", "rg-count", `${g.legs.length} 条`));
  wrap.appendChild(head);

  const legsWrap = el("div", "rg-legs");
  for (const r of g.legs) legsWrap.appendChild(renderRadarLeg(r));
  wrap.appendChild(legsWrap);
  return wrap;
}

// 单条盘口腿 (组内一行)。比赛名/开赛时间已在组头, 故主行改显市场/选择,
// 点开仍展开同样的去水明细。
function renderRadarLeg(r) {
  const b = flagBadge(r.flag);
  const item = el("div", `radar-item ${b.cls}`);

  const row = el("div", "radar-row");
  const left = el("div", "ri-left");
  const legLabel = `${r.market || ""} ${r.outcome || ""}`.trim() || "—";
  left.appendChild(el("div", "ri-match", legLabel));

  // 始终可见的紧凑对比行: 足彩(隐含) · Poly去水 · 差 · EV
  const cmp = buildCompareLine(r);
  if (cmp) left.appendChild(cmp);

  row.appendChild(left);

  row.appendChild(el("div", "ri-odds", fmtNum(r.zucai_odds)));
  row.appendChild(el("div", "ri-val ri-raw", fmtNum(r.value_raw != null ? r.value_raw : r.poly_prob_raw && r.zucai_odds ? (r.zucai_odds * r.poly_prob_raw) / 100 : null, 3)));
  row.appendChild(el("div", "ri-val ri-devig", fmtNum(r.value_devig != null ? r.value_devig : r.poly_prob_devig && r.zucai_odds ? (r.zucai_odds * r.poly_prob_devig) / 100 : null, 3)));

  const badge = el("div", `ri-badge ${b.cls}`);
  badge.textContent = `${b.dot} ${b.label}`;
  row.appendChild(badge);

  item.appendChild(row);

  // 点开详情
  const detail = el("div", "radar-detail");
  detail.hidden = true;
  const implied = impliedPct(r.zucai_odds);
  const edge = probEdge(r); // poly_prob_devig - implied (去水概率差)
  const edgeCls = edge == null ? "" : edge >= 0 ? "v-pos" : "v-neg";
  const evDevig = r.ev_pct_devig != null ? r.ev_pct_devig : r.ev_pct;
  const evCls = evDevig == null || Number.isNaN(Number(evDevig)) ? "" : Number(evDevig) >= 0 ? "v-pos" : "v-neg";
  detail.innerHTML = `
      <div class="rd-grid">
        <div><span class="k">隐含概率</span><span class="v">${esc(implied == null ? "—" : fmtPct(implied))}</span></div>
        <div><span class="k">概率差(去水)</span><span class="v ${edgeCls}">${esc(edge == null ? "—" : fmtSignedPct(edge))}</span></div>
        <div><span class="k">Poly 生概率</span><span class="v">${esc(fmtPct(r.poly_prob_raw))}</span></div>
        <div><span class="k">Poly 去水概率</span><span class="v">${esc(fmtPct(r.poly_prob_devig))}</span></div>
        <div><span class="k">EV 生</span><span class="v">${esc(fmtSignedPct(r.ev_pct_raw))}</span></div>
        <div><span class="k">EV 去水</span><span class="v ${evCls}">${esc(fmtSignedPct(evDevig))}</span></div>
        <div><span class="k">停售</span><span class="v">${esc(r.cutoff_bj || "—")}</span></div>
        <div><span class="k">建议</span><span class="v">${r.flag === "yellow" ? "薄边, 噪声内, 谨慎" : "单关小注"}</span></div>
      </div>
    `;
  item.appendChild(detail);

  row.addEventListener("click", () => {
    detail.hidden = !detail.hidden;
    item.classList.toggle("open", !detail.hidden);
  });

  return item;
}

// ================= 决策卡 (新 hero) =================
// 靠谱度徽章: 稳=绿 / 中=黄 / 乱=红, 与 flag 用色一致
function reliabilityBadge(rel) {
  if (rel === "稳") return { label: "稳", cls: "rel-stable" };
  if (rel === "中") return { label: "中", cls: "rel-mid" };
  if (rel === "乱") return { label: "乱", cls: "rel-chaos" };
  return null;
}

// 单条盘口明细行 (展开用): 与雷达明细同构, 复用 fmt* / flagBadge
function buildLegRow(leg) {
  const b = flagBadge(leg.flag);
  const row = el("div", `dc-leg ${b.cls}`);
  row.appendChild(el("span", "dc-leg-dot", b.dot));
  const descText = leg.desc || `${leg.market || ""} ${leg.outcome || ""}`.trim() || "—";
  row.appendChild(el("span", "dc-leg-desc", descText));
  const meta = el("span", "dc-leg-meta");
  if (leg.zucai_odds != null) meta.appendChild(el("span", "dc-leg-odds", `足彩 ${fmtNum(leg.zucai_odds)}`));
  if (leg.poly_prob_devig != null) meta.appendChild(el("span", "dc-leg-poly", `Poly ${fmtNum(leg.poly_prob_devig, 1)}%去水`));
  const ev = leg.ev_pct_devig != null ? leg.ev_pct_devig : leg.ev_pct;
  if (ev != null && !Number.isNaN(Number(ev))) {
    const evCls = Number(ev) >= 0 ? "dc-pos" : "dc-neg";
    meta.appendChild(el("span", `dc-leg-ev ${evCls}`, `EV ${fmtSignedPct(ev)}`));
  }
  row.appendChild(meta);
  return row;
}

// 一张决策卡; 任一块 (v1/v2/value) 缺整块 -> 占位"未出"不崩
function buildDecisionCard(d) {
  d = d || {};
  const card = el("div", "decision-card");

  // ---- 头: 对阵 + 国旗 + 开球 ----
  const head = el("div", "dc-head");
  const matchEl = el("div", "dc-match");
  const home = `${d.home_flag ? d.home_flag + " " : ""}${d.home_cn || ""}`.trim();
  const away = `${d.away_flag ? d.away_flag + " " : ""}${d.away_cn || ""}`.trim();
  const title = home && away ? `${home} vs ${away}` : (d.match_key || "—");
  matchEl.textContent = title;
  head.appendChild(matchEl);

  const ko = el("div", "dc-ko");
  if (d.ko_bj) {
    ko.appendChild(el("span", "dc-ko-bj", `🕐 ${d.ko_bj}`));
    if (d.ko_et) ko.appendChild(el("span", "dc-ko-et", d.ko_et));
  } else {
    ko.appendChild(el("span", "dc-ko-bj", "开球待定"));
  }
  if (d.status) ko.appendChild(el("span", "dc-status", d.status));
  head.appendChild(ko);
  card.appendChild(head);

  // ---- v1: 比分 + rationale ----
  const v1Block = el("div", "dc-block dc-v1");
  v1Block.appendChild(el("span", "dc-tag", "v1 比分"));
  if (d.v1 && (d.v1.score != null || d.v1.rationale)) {
    if (d.v1.score != null) v1Block.appendChild(el("span", "dc-score", String(d.v1.score)));
    if (d.v1.rationale) v1Block.appendChild(el("span", "dc-rationale", d.v1.rationale));
    if (d.v1.probs) {
      const p = d.v1.probs;
      v1Block.appendChild(el("span", "dc-v1-probs", `胜${fmtPct(p.h, 0)}/平${fmtPct(p.d, 0)}/负${fmtPct(p.a, 0)}`));
    }
  } else {
    v1Block.appendChild(el("span", "dc-placeholder", "— 未出"));
  }
  card.appendChild(v1Block);

  // ---- v2: 概率 + 靠谱度 + 剧本 chips ----
  const v2Block = el("div", "dc-block dc-v2");
  v2Block.appendChild(el("span", "dc-tag", "v2 概率"));
  if (d.v2 && (d.v2.probs || d.v2.reliability || (Array.isArray(d.v2.scenarios) && d.v2.scenarios.length))) {
    if (d.v2.probs) {
      const p = d.v2.probs;
      const probs = el("span", "dc-v2-probs");
      probs.appendChild(el("span", "dc-prob dc-prob-h", `胜 ${fmtPct(p.h, 0)}`));
      probs.appendChild(el("span", "dc-prob dc-prob-d", `平 ${fmtPct(p.d, 0)}`));
      probs.appendChild(el("span", "dc-prob dc-prob-a", `负 ${fmtPct(p.a, 0)}`));
      v2Block.appendChild(probs);
    }
    const rb = reliabilityBadge(d.v2.reliability);
    if (rb) {
      const badge = el("span", `dc-rel ${rb.cls}`, `靠谱度 ${rb.label}`);
      v2Block.appendChild(badge);
    }
    if (d.v2.deviated) v2Block.appendChild(el("span", "dc-deviated", "⚡偏离"));
    if (Array.isArray(d.v2.scenarios) && d.v2.scenarios.length) {
      const chips = el("div", "dc-chips");
      for (const s of d.v2.scenarios) {
        if (s == null || s === "") continue;
        chips.appendChild(el("span", "dc-chip", String(s)));
      }
      v2Block.appendChild(chips);
    }
  } else {
    v2Block.appendChild(el("span", "dc-placeholder", "— 未出"));
  }
  card.appendChild(v2Block);

  // ---- value: verdict + best_leg; 点击展开 legs ----
  const valBlock = el("div", "dc-block dc-value");
  valBlock.appendChild(el("span", "dc-tag", "价值"));
  if (d.value && (d.value.verdict || d.value.best_leg || (Array.isArray(d.value.legs) && d.value.legs.length))) {
    if (d.value.verdict) valBlock.appendChild(el("span", "dc-verdict", d.value.verdict));
    const bl = d.value.best_leg;
    if (bl) {
      const b = flagBadge(bl.flag);
      const best = el("span", `dc-best ${b.cls}`);
      best.appendChild(el("span", "dc-best-dot", b.dot));
      best.appendChild(el("span", "dc-best-desc", bl.desc || `${bl.market || ""} ${bl.outcome || ""}`.trim() || "最不亏腿"));
      if (bl.ev_pct != null && !Number.isNaN(Number(bl.ev_pct))) {
        const evCls = Number(bl.ev_pct) >= 0 ? "dc-pos" : "dc-neg";
        best.appendChild(el("span", `dc-best-ev ${evCls}`, `EV ${fmtSignedPct(bl.ev_pct)}`));
      }
      valBlock.appendChild(best);
    }
    card.appendChild(valBlock);

    // 完整盘口明细 (点击 value 块展开)
    const legs = Array.isArray(d.value.legs) ? d.value.legs : [];
    if (legs.length) {
      const detail = el("div", "dc-legs");
      detail.hidden = true;
      for (const leg of legs) detail.appendChild(buildLegRow(leg));
      card.appendChild(detail);

      valBlock.classList.add("dc-expandable");
      const hint = el("span", "dc-expand-hint", "▾ 明细");
      valBlock.appendChild(hint);
      valBlock.addEventListener("click", () => {
        detail.hidden = !detail.hidden;
        card.classList.toggle("open", !detail.hidden);
        hint.textContent = detail.hidden ? "▾ 明细" : "▴ 收起";
      });
    }
  } else {
    valBlock.appendChild(el("span", "dc-placeholder", "— 未出"));
    card.appendChild(valBlock);
  }

  return card;
}

function renderDecisions(decisions) {
  const list = $("#decision-list");
  if (!list) return;
  list.innerHTML = "";
  if (!decisions || decisions.length === 0) {
    list.appendChild(el("div", "empty", "今日暂无决策卡, 在本地跑 /跑今天 生成"));
    return;
  }
  for (const d of decisions) {
    list.appendChild(buildDecisionCard(d));
  }
}

async function loadDecisions() {
  const list = $("#decision-list");
  try {
    const resp = await apiGet("/api/decisions");
    renderDecisions((resp && resp.decisions) || []);
  } catch (err) {
    if (list) {
      list.innerHTML = "";
      list.appendChild(el("div", "empty", "决策卡加载失败: " + err.message));
    }
  }
}

// 价值"重抓+刷新": 触发后台重抓足彩快照 + 刷新 Poly 去水, 延时重拉 state + decisions
async function refreshValue(btn) {
  const original = btn ? btn.textContent : "";
  if (btn) {
    btn.disabled = true;
    btn.textContent = "重抓中…";
  }
  try {
    await apiSend("/api/refresh", "POST");
    // 给后台重算留时间 (poll_once 内重连 Poly), 随后重拉
    setTimeout(() => {
      refreshState();
      loadDecisions();
      if (btn) {
        btn.disabled = false;
        btn.textContent = original || "🔄 重抓+刷新";
      }
    }, 3000);
  } catch (err) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "刷新失败, 重试";
    }
  }
}

function setupRadarRefresh() {
  const btn = $("#radar-refresh");
  if (!btn) return;
  btn.addEventListener("click", () => refreshValue(btn));
}

// ================= 特别关注 =================
function renderWatchlist(items) {
  const wrap = $("#watch-list");
  wrap.innerHTML = "";
  if (!items || items.length === 0) {
    wrap.appendChild(el("div", "empty", "暂无关注项, 在上方添加"));
    return;
  }
  const kindLabel = { team: "队", match: "场", player: "人" };
  for (const w of items) {
    const card = el("div", "watch-card");

    const head = el("div", "wc-head");
    const tag = el("span", `wc-kind kind-${w.kind || "x"}`, kindLabel[w.kind] || w.kind || "?");
    head.appendChild(tag);
    head.appendChild(el("span", "wc-key", w.key || "—"));
    if (w.id != null) {
      const del = el("button", "wc-del", "✕");
      del.title = "取消关注";
      del.addEventListener("click", () => unpin(w.id));
      head.appendChild(del);
    }
    card.appendChild(head);

    if (w.note) card.appendChild(el("div", "wc-note", w.note));

    // 关联场次 + 价值点
    if (Array.isArray(w.matches) && w.matches.length) {
      const ml = el("div", "wc-matches");
      for (const m of w.matches) {
        const line = typeof m === "string" ? m : `${m.match || ""} ${m.ko_bj || ""}`.trim();
        ml.appendChild(el("div", "wc-match-line", line));
      }
      card.appendChild(ml);
    }

    // 首发: 阵型 + 球员 chips
    if (w.lineup) {
      const lu = el("div", "wc-lineup");
      const head = el("div", "wc-lineup-h", "首发");
      const formation = typeof w.lineup === "object" ? w.lineup.formation : null;
      if (formation) head.appendChild(el("span", "wc-formation", formation));
      lu.appendChild(head);

      const players = typeof w.lineup === "object" && Array.isArray(w.lineup.players) ? w.lineup.players : null;
      if (players && players.length) {
        const chips = el("div", "wc-lineup-chips");
        for (const p of players) {
          chips.appendChild(el("span", "wc-chip", String(p)));
        }
        lu.appendChild(chips);
      } else {
        lu.appendChild(el("div", "wc-lineup-body", typeof w.lineup === "string" ? w.lineup : JSON.stringify(w.lineup)));
      }

      const note = typeof w.lineup === "object" ? w.lineup.note : null;
      if (note) lu.appendChild(el("div", "wc-lineup-note", note));
      card.appendChild(lu);
    } else {
      card.appendChild(el("div", "wc-lineup-pending", "首发未出炉"));
    }

    // 新闻链接 (可点, 新窗口) + 时间
    if (Array.isArray(w.news) && w.news.length) {
      const nl = el("div", "wc-news");
      for (const n of w.news) {
        const url = typeof n === "string" ? n : n.url;
        const title = typeof n === "string" ? n : n.title || n.url;
        if (!url) continue;
        const item = el("div", "wc-news-item");
        const a = el("a", "wc-news-link");
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = "🔗 " + title;
        item.appendChild(a);
        const ts = typeof n === "object" ? n.ts : "";
        if (ts) item.appendChild(el("span", "wc-news-ts", ts));
        nl.appendChild(item);
      }
      card.appendChild(nl);
    }

    // 雷达命中 (radar_hits): flag 圆点 + market outcome 足彩odds EV%
    if (Array.isArray(w.radar_hits) && w.radar_hits.length) {
      const rh = el("div", "wc-radar");
      rh.appendChild(el("div", "wc-radar-h", "雷达命中"));
      for (const h of w.radar_hits) {
        const b = flagBadge(h.flag);
        const hit = el("div", `wc-radar-hit ${b.cls}`);
        hit.appendChild(el("span", "wc-hit-dot", b.dot));
        const ev = h.ev_pct_devig;
        const evCls = ev == null || Number.isNaN(Number(ev)) ? "" : Number(ev) >= 0 ? "wc-hit-pos" : "wc-hit-neg";
        const txt = `${h.market || ""} ${h.outcome || ""} 足彩 ${fmtNum(h.zucai_odds)}`.trim();
        hit.appendChild(el("span", "wc-hit-txt", txt));
        hit.appendChild(el("span", `wc-hit-ev ${evCls}`, `EV ${fmtSignedPct(ev)}`));
        rh.appendChild(hit);
      }
      card.appendChild(rh);
    }

    wrap.appendChild(card);
  }
}

async function pin(kind, key, note = "") {
  await apiSend("/api/watchlist", "POST", { kind, key, note });
  await refreshState();
}

async function unpin(id) {
  // 主路径: RESTful /api/watchlist/{id}; 失败回退到 query 形式
  try {
    await apiSend(`/api/watchlist/${encodeURIComponent(id)}`, "DELETE");
  } catch (_) {
    await apiSend(`/api/watchlist?id=${encodeURIComponent(id)}`, "DELETE");
  }
  await refreshState();
}

// ================= 账本 =================
function renderLedger(ledger) {
  const a = (ledger && ledger.A) || { stake: 0, pnl: 0, roi: 0, n: 0 };
  const b = (ledger && ledger.B) || { budget: 0, spent: 0, pnl: 0, n: 0 };

  const aRows = $("#wallet-a-rows");
  aRows.innerHTML = "";
  aRows.appendChild(ledgerRow("下注数", a.n));
  aRows.appendChild(ledgerRow("累计注码", fmtNum(a.stake)));
  aRows.appendChild(ledgerRow("盈亏", fmtNum(a.pnl), a.pnl));
  aRows.appendChild(ledgerRow("ROI", fmtSignedPct((a.roi || 0) * 100), a.roi));

  const bRows = $("#wallet-b-rows");
  bRows.innerHTML = "";
  bRows.appendChild(ledgerRow("下注数", b.n));
  bRows.appendChild(ledgerRow("周预算", fmtNum(b.budget)));
  bRows.appendChild(ledgerRow("已花", fmtNum(b.spent)));
  const remain = (Number(b.budget) || 0) - (Number(b.spent) || 0);
  bRows.appendChild(ledgerRow("剩余", fmtNum(remain), remain));
  bRows.appendChild(ledgerRow("盈亏", fmtNum(b.pnl), b.pnl));
}

function ledgerRow(k, v, signFor) {
  const row = el("div", "led-row");
  row.appendChild(el("span", "led-k", k));
  const vEl = el("span", "led-v", String(v));
  if (signFor != null && !Number.isNaN(Number(signFor))) {
    const n = Number(signFor);
    if (n > 0) vEl.classList.add("pos");
    else if (n < 0) vEl.classList.add("neg");
  }
  row.appendChild(vEl);
  return row;
}

// 记账表单
function setupBetForm() {
  const form = $("#bet-form");
  const openBtn = $("#open-bet-form");
  const cancelBtn = $("#cancel-bet");
  const msg = $("#bet-msg");

  openBtn.addEventListener("click", () => {
    form.hidden = !form.hidden;
  });
  cancelBtn.addEventListener("click", () => {
    form.hidden = true;
    msg.textContent = "";
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    msg.textContent = "";
    const wallet = $("#bet-wallet").value;
    const stake = parseFloat($("#bet-stake").value);
    const odds = parseFloat($("#bet-odds").value);
    const legsText = $("#bet-legs").value.trim();
    const note = $("#bet-note").value.trim();

    if (!(stake > 0) || !(odds >= 1)) {
      msg.textContent = "注码需 >0, 赔率需 >=1";
      msg.className = "bet-msg err";
      return;
    }
    const legs = legsText
      ? legsText.split(",").map((s) => ({ desc: s.trim() })).filter((l) => l.desc)
      : [];
    try {
      await apiSend("/api/bets", "POST", { wallet, legs, stake, odds, note });
      msg.textContent = "已记账";
      msg.className = "bet-msg ok";
      form.reset();
      await refreshState();
      setTimeout(() => {
        form.hidden = true;
        msg.textContent = "";
      }, 800);
    } catch (err) {
      msg.textContent = "失败: " + err.message;
      msg.className = "bet-msg err";
    }
  });
}

// ================= 报告 tab =================
let md = null;
function getMd() {
  if (md) return md;
  if (window.markdownit) {
    md = window.markdownit({ html: false, linkify: true, breaks: true });
  }
  return md;
}

function reportName(r) {
  return typeof r === "string" ? r : r.name;
}
function reportTitle(r) {
  if (typeof r === "string") return r;
  return r.title || r.name;
}

async function loadReportsList() {
  const tabsEl = $("#report-tabs");
  try {
    const list = await apiGet("/api/reports");
    tabsEl.innerHTML = "";
    if (!Array.isArray(list) || list.length === 0) {
      tabsEl.appendChild(el("div", "empty", "暂无报告"));
      return;
    }
    list.forEach((r, i) => {
      const name = reportName(r);
      const btn = el("button", "report-tab", reportTitle(r));
      btn.dataset.name = name;
      btn.addEventListener("click", () => openReport(name));
      tabsEl.appendChild(btn);
    });
    // 默认打开第一篇 (或之前选中的)
    const target = state.activeReport || reportName(list[0]);
    openReport(target);
  } catch (err) {
    tabsEl.innerHTML = "";
    tabsEl.appendChild(el("div", "empty", "报告列表加载失败: " + err.message));
  }
}

async function openReport(name) {
  state.activeReport = name;
  $$(".report-tab").forEach((b) => b.classList.toggle("active", b.dataset.name === name));
  const body = $("#report-body");
  body.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const text = await apiGet(`/api/reports/${encodeURIComponent(name)}`);
    const mdText = typeof text === "string" ? text : text.content || text.markdown || "";
    const renderer = getMd();
    if (renderer) {
      body.innerHTML = renderer.render(mdText);
    } else {
      // markdown-it 未就绪: 退化为纯文本
      body.innerHTML = "";
      const pre = el("pre", "report-raw");
      pre.textContent = mdText;
      body.appendChild(pre);
    }
  } catch (err) {
    body.innerHTML = "";
    body.appendChild(el("div", "empty", "报告加载失败: " + err.message));
  }
}

// ================= tab 切换 =================
function setupTabs() {
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const tab = btn.dataset.tab;
      $$(".tab-btn").forEach((b) => b.classList.toggle("active", b === btn));
      $$(".view").forEach((v) => v.classList.remove("active"));
      const view = $(`#view-${tab}`);
      if (view) view.classList.add("active");
      if (tab === "reports") loadReportsList();
      if (tab === "decisions") loadDecisions();
    });
  });
}

function setupWatchAdd() {
  $("#watch-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    const kind = $("#watch-kind").value;
    const key = $("#watch-key").value.trim();
    if (!key) return;
    try {
      await pin(kind, key, "");
      $("#watch-key").value = "";
    } catch (err) {
      alert("关注失败: " + err.message);
    }
  });
}

// ================= /state 轮询 =================
function setConn(ok) {
  const dot = $("#conn-dot");
  dot.classList.toggle("ok", ok);
  dot.classList.toggle("bad", !ok);
}

async function refreshState() {
  try {
    const s = await apiGet("/api/state");
    state.lastState = s;
    setConn(true);

    setCutoff(s.next_cutoff);
    renderRadar(s.value_radar || []);
    renderWatchlist(s.watchlist || []);
    renderLedger(s.ledger || {});

    const upd = $("#updated-at");
    upd.textContent = "刷新 " + (s.ts ? s.ts.replace("T", " ").slice(0, 19) : new Date().toLocaleTimeString());
  } catch (err) {
    setConn(false);
    $("#updated-at").textContent = "离线: " + err.message;
  }
  // 决策卡走独立端点, 与 state 同节奏刷新 (失败不影响 state 渲染)
  loadDecisions();
}

// ================= 启动 =================
function init() {
  setupTabs();
  setupWatchAdd();
  setupBetForm();
  setupRadarRefresh();

  // 倒计时本地每秒 tick (与服务端轮询解耦)
  state.cdTimer = setInterval(tickCountdown, 1000);

  refreshState();
  state.pollTimer = setInterval(refreshState, POLL_INTERVAL_MS);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
