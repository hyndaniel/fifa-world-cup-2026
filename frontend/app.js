/* WC 价值看板 前端逻辑 — "Reading Room" 主题
 * 契约 (与后端 API 不变):
 *   GET  /api/state                 -> {ts, next_cutoff, value_radar[], watchlist[], ledger{A,B}, matches_today[]}
 *   GET  /api/decisions             -> {ts, decisions[]}
 *   GET  /api/reports               -> [{name, title}, ...]   (容错: 也支持 [string,...])
 *   GET  /api/reports/{name}        -> markdown 文本
 *   POST /api/refresh               (重抓+刷新)
 *   POST /api/bets        body: {wallet, legs, stake, odds, note}
 *   POST /api/watchlist   body: {kind, key, note}
 *   DELETE /api/watchlist/{id}      (容错: 也支持 ?id= )
 *
 * 署名件: 公允轴 —— 把"去水价值"画成一条标尺上的落点, 1.00 居中, 左亏右赢。
 */
"use strict";

const POLL_INTERVAL_MS = 30_000;

// 公允轴定义域: [0.85, 1.15], 价值 1.00 -> 轴中点 (与 CSS --axis-* 对齐)
const AXIS_LO = 0.85;
const AXIS_HI = 1.15;

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
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);   s -= m * 60;
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
    try { detail = JSON.stringify(await r.json()); } catch (_) {}
    throw new Error(`${method} ${path} -> ${r.status} ${detail}`);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

// ---------- 全局状态 ----------
const state = {
  lastState: null,
  cutoffDeadlineMs: null,
  cdTimer: null,
  pollTimer: null,
  activeReport: null,
  decFilter: "upcoming",   // 决策面板默认筛选: 未结束
  decSel: null,            // 当前选中场 match_key
  _lastDecisions: [],      // 末次 decisions, 供筛选切换重渲染
};

// ================= 信号语言 (flag -> 标签/类/点) =================
function flagBadge(flag) {
  if (flag === "green")  return { label: "真 +EV", cls: "flag-green",  dotKey: "edge" };
  if (flag === "yellow") return { label: "接近公允", cls: "flag-yellow", dotKey: "fair" };
  if (flag === "red")    return { label: "明显 -EV", cls: "flag-red",    dotKey: "bleed" };
  return { label: "跳过", cls: "flag-skip", dotKey: "skip" };
}
function signalDot(flag) {
  return el("i", `dot dot-${flagBadge(flag).dotKey}`);
}
// 取一组 flag 里最高级别 (green > yellow > red > skip)
function topFlag(flags) {
  const order = ["green", "yellow", "red"];
  for (const f of order) if (flags.includes(f)) return f;
  return flags.length ? flags[0] : "skip";
}

// ================= 公允轴 (署名件) =================
// 去水价值: 优先 value_devig; 否则 odds×poly去水/100; 再否则 1+EV/100
function legValue(o) {
  if (!o) return null;
  if (o.value_devig != null && !Number.isNaN(Number(o.value_devig))) return Number(o.value_devig);
  const od = Number(o.zucai_odds), p = Number(o.poly_prob_devig);
  if (od > 0 && p > 0) return (od * p) / 100;
  let ev = o.ev_pct_devig != null ? o.ev_pct_devig : o.ev_pct;
  if (ev != null && !Number.isNaN(Number(ev))) return 1 + Number(ev) / 100;
  return null;
}
function axisPct(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  const pct = ((Number(v) - AXIS_LO) / (AXIS_HI - AXIS_LO)) * 100;
  return Math.max(3, Math.min(97, pct));
}
// ticks: [{ value, flag, best }]; showLabels=true 时在两端标 0.85 / 1.15
function buildAxisEl(ticks, showLabels) {
  const wrap = el("div");
  const axis = el("div", "axis");
  axis.appendChild(el("div", "axis-track"));
  axis.appendChild(el("div", "axis-mid"));
  for (const t of ticks || []) {
    const pct = axisPct(t.value);
    if (pct == null) continue;
    const b = flagBadge(t.flag);
    const tick = el("span", `axis-tick ${b.cls}${t.best ? " is-best" : ""}`);
    tick.style.left = pct + "%";
    tick.title = `去水价值 ${fmtNum(t.value, 3)}`;
    axis.appendChild(tick);
  }
  wrap.appendChild(axis);
  if (showLabels) {
    const ends = el("div", "axis-ends");
    ends.appendChild(el("span", null, AXIS_LO.toFixed(2)));
    ends.appendChild(el("span", null, AXIS_HI.toFixed(2)));
    wrap.appendChild(ends);
  }
  return wrap;
}

// ================= 倒计时 =================
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
  state.cutoffDeadlineMs = Date.now() + Number(nextCutoff.countdown_sec) * 1000;
  matchEl.textContent = nextCutoff.match || "—";
  cutoffEl.textContent = nextCutoff.cutoff_bj ? `停售 ${nextCutoff.cutoff_bj}` : "";
  tickCountdown();
}
function tickCountdown() {
  const timerEl = $("#cd-timer");
  if (state.cutoffDeadlineMs == null) { timerEl.textContent = "--:--:--"; return; }
  const remainSec = (state.cutoffDeadlineMs - Date.now()) / 1000;
  timerEl.textContent = fmtHMS(remainSec);
  const cd = $("#countdown");
  cd.classList.toggle("urgent", remainSec > 0 && remainSec <= 600);
  cd.classList.toggle("closed", remainSec <= 0);
  if (remainSec <= 0) timerEl.textContent = "已停售";
}

// ================= 价值雷达 =================
// 始终可见的紧凑对比行: 足彩(隐含) · Poly去水 · 差 · EV
function impliedPct(zucaiOdds) {
  const o = Number(zucaiOdds);
  if (!(o > 0) || Number.isNaN(o)) return null;
  return Math.round((100 / o) * 10) / 10;
}
function probEdge(r) {
  const implied = impliedPct(r.zucai_odds);
  const poly = Number(r.poly_prob_devig);
  if (implied == null || Number.isNaN(poly) || r.poly_prob_devig == null) return null;
  return Math.round((poly - implied) * 10) / 10;
}
function buildCompareLine(r) {
  const implied = impliedPct(r.zucai_odds);
  const edge = probEdge(r);
  const evDevig = r.ev_pct_devig != null ? r.ev_pct_devig : r.ev_pct;
  const line = el("div", "ri-compare");
  const seg = (text, cls) => el("span", cls || "", text);

  line.appendChild(seg(`足彩 ${fmtNum(r.zucai_odds)} (隐含 ${implied == null ? "—" : fmtNum(implied, 1)}%)`));
  line.appendChild(seg(" · "));
  line.appendChild(seg(`Poly ${r.poly_prob_devig == null ? "—" : fmtNum(r.poly_prob_devig, 1)}% 去水`));
  line.appendChild(seg(" · "));
  line.appendChild(seg(`差 ${edge == null ? "—" : fmtSignedPct(edge)}`, edge == null ? "" : edge >= 0 ? "ri-cmp-pos" : "ri-cmp-neg"));
  line.appendChild(seg(" · "));
  const evVal = Number(evDevig);
  line.appendChild(seg(
    `EV ${evDevig == null || Number.isNaN(evVal) ? "—" : fmtSignedPct(evDevig)}`,
    evDevig == null || Number.isNaN(evVal) ? "" : evVal >= 0 ? "ri-cmp-pos" : "ri-cmp-neg"
  ));
  return line;
}

function renderRadar(rows) {
  const list = $("#radar-list");
  list.innerHTML = "";
  if (!rows || rows.length === 0) {
    list.appendChild(el("div", "empty", "暂无可投价值点"));
    return;
  }
  // 按比赛(match + ko_bj)分组, 保留传入顺序 (后端已按去水 EV 降序)
  const groups = [];
  const byKey = new Map();
  for (const r of rows) {
    const key = `${r.match || "—"}|${r.ko_bj || ""}`;
    let g = byKey.get(key);
    if (!g) { g = { match: r.match || "—", ko_bj: r.ko_bj || "", legs: [] }; byKey.set(key, g); groups.push(g); }
    g.legs.push(r);
  }
  for (const g of groups) list.appendChild(renderRadarGroup(g));
}

function renderRadarGroup(g) {
  const gFlag = topFlag(g.legs.map((l) => l.flag));
  const wrap = el("div", `radar-group flag-${gFlag === "skip" ? "skip" : gFlag}`);

  const head = el("div", "rg-head");
  head.appendChild(el("span", "rg-flag"));
  head.appendChild(el("span", "rg-match", g.match));
  if (g.ko_bj) head.appendChild(el("span", "rg-ko", g.ko_bj));
  head.appendChild(el("span", "rg-count", `${g.legs.length} 条`));
  wrap.appendChild(head);

  for (const r of g.legs) wrap.appendChild(renderRadarLeg(r));
  return wrap;
}

function renderRadarLeg(r) {
  const b = flagBadge(r.flag);
  const item = el("div", `radar-item ${b.cls}`);

  const row = el("div", "radar-row");

  const left = el("div", "ri-left");
  const legLabel = `${r.market || ""} ${r.outcome || ""}`.trim() || "—";
  left.appendChild(el("div", "ri-leg", legLabel));
  left.appendChild(buildCompareLine(r));
  row.appendChild(left);

  // 迷你公允轴: 这条腿的去水价值落点
  const value = legValue(r);
  const axisCell = el("div", "ri-axis");
  axisCell.appendChild(buildAxisEl([{ value, flag: r.flag, best: true }], false));
  row.appendChild(axisCell);

  row.appendChild(el("div", "ri-value", value == null ? "—" : fmtNum(value, 3)));
  item.appendChild(row);

  // 展开明细
  const detail = el("div", "radar-detail");
  detail.hidden = true;
  const implied = impliedPct(r.zucai_odds);
  const edge = probEdge(r);
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
        <div><span class="k">建议</span><span class="v">${r.flag === "yellow" ? "薄边,噪声内,谨慎" : r.flag === "green" ? "单关小注" : "别碰"}</span></div>
      </div>`;
  item.appendChild(detail);

  row.addEventListener("click", () => {
    detail.hidden = !detail.hidden;
    item.classList.toggle("open", !detail.hidden);
  });
  return item;
}

// ================= 决策卡 (hero) =================
function reliabilityBadge(rel) {
  if (rel === "稳") return { label: "稳", cls: "rel-stable" };
  if (rel === "中") return { label: "中", cls: "rel-mid" };
  if (rel === "乱") return { label: "乱", cls: "rel-chaos" };
  return null;
}

function buildLegRow(leg) {
  const b = flagBadge(leg.flag);
  const row = el("div", `dc-leg ${b.cls}`);
  row.appendChild(signalDot(leg.flag));
  const descText = leg.desc || `${leg.market || ""} ${leg.outcome || ""}`.trim() || "—";
  row.appendChild(el("span", "dc-leg-desc", descText));
  const meta = el("span", "dc-leg-meta");
  if (leg.zucai_odds != null) meta.appendChild(el("span", "dc-leg-odds", `足彩 ${fmtNum(leg.zucai_odds)}`));
  if (leg.poly_prob_devig != null) meta.appendChild(el("span", "dc-leg-poly", `Poly ${fmtNum(leg.poly_prob_devig, 1)}%去水`));
  const ev = leg.ev_pct_devig != null ? leg.ev_pct_devig : leg.ev_pct;
  if (ev != null && !Number.isNaN(Number(ev))) {
    meta.appendChild(el("span", `dc-leg-ev ${Number(ev) >= 0 ? "dc-pos" : "dc-neg"}`, `EV ${fmtSignedPct(ev)}`));
  }
  row.appendChild(meta);
  return row;
}

// 读数行: 左标签 + 内容
function dcRow(label, contentEl, extraCls) {
  const row = el("div", `dc-row${extraCls ? " " + extraCls : ""}`);
  row.appendChild(el("span", "dc-label", label));
  const c = el("div", "dc-content");
  if (contentEl) c.appendChild(contentEl);
  row.appendChild(c);
  return { row, content: c };
}

function oddsArrow(sym) {
  if (sym === "▲") return el("span", "od-up", "▲");      // 升水
  if (sym === "▼") return el("span", "od-down", "▼");    // 降水
  return el("span", "od-flat", "");
}

// 决策卡内可折叠赔率面板: 三源(竞彩/欧盘/Poly) × 主平负 + 变化 + 让球/总进球 + 分歧
function renderOddsPanel(odds) {
  const box = el("div", "odds-panel");
  const head = el("div", "odds-head", "赔率(三源)");
  box.appendChild(head);
  if (!odds || !odds.sources) { box.appendChild(el("div", "odds-empty", "赔率待刷新")); return box; }
  const body = el("div", "odds-body");
  const s = odds.sources;
  const rows = [["竞彩", s.zucai, "had", false], ["欧盘", s.consensus, "devig", true], ["Poly", s.poly, "devig", true]];
  const tbl = el("div", "odds-tbl");
  tbl.appendChild(el("div", "ot-h", ""));
  for (const c of ["主", "平", "负"]) tbl.appendChild(el("div", "ot-h", c));
  for (const [name, src, key, isPct] of rows) {
    tbl.appendChild(el("div", "ot-name", name));
    if (!src || src.stale) { for (const _k of ["h", "d", "a"]) tbl.appendChild(el("div", "ot-cell muted", "—")); continue; }
    const vals = src[key] || {};
    const dl = src.delta || {};
    for (const k of ["h", "d", "a"]) {
      const cell = el("div", "ot-cell");
      const v = vals[k];
      cell.appendChild(el("span", null, v == null ? "—" : (isPct ? fmtNum(v, 1) + "%" : fmtNum(v, 2))));
      cell.appendChild(oddsArrow(dl[k]));
      tbl.appendChild(cell);
    }
  }
  body.appendChild(tbl);
  if (s.zucai && s.zucai.hhad) {
    const h = s.zucai.hhad;
    body.appendChild(el("div", "odds-extra", `让球(${h.line > 0 ? "+" : ""}${h.line}): 主 ${fmtNum(h.h)} / 客 ${fmtNum(h.a)}`));
  }
  if (s.zucai && s.zucai.ttg && Object.keys(s.zucai.ttg).length) {
    const ttg = s.zucai.ttg;
    const parts = Object.keys(ttg).slice(0, 6).map((g) => `${g}球 ${fmtNum(ttg[g])}`);
    body.appendChild(el("div", "odds-extra", "总进球: " + parts.join(" / ")));
  }
  const dv = odds.divergence || {};
  if (["h", "d", "a"].some((k) => dv[k] != null && Math.abs(dv[k]) >= 3)) {
    body.appendChild(el("div", "odds-diverge", "⚠ 竞彩偏离欧盘 ≥3pp"));
  }
  if (odds.fetched_at) body.appendChild(el("div", "odds-ts", "抓取 " + esc(String(odds.fetched_at)).slice(11, 16)));
  head.addEventListener("click", () => box.classList.toggle("open"));
  box.appendChild(body);
  return box;
}

function buildDecisionCard(d) {
  d = d || {};
  const card = el("div", "decision-card");

  // ---- 头 ----
  const head = el("div", "dc-head");
  const home = `${d.home_flag ? d.home_flag + " " : ""}${d.home_cn || ""}`.trim();
  const away = `${d.away_flag ? d.away_flag + " " : ""}${d.away_cn || ""}`.trim();
  const title = home && away ? `${home} vs ${away}` : (d.match_key || "—");
  head.appendChild(el("div", "dc-match", title));
  if (d.status) head.appendChild(el("span", "dc-status", d.status));
  card.appendChild(head);

  const ko = el("div", "dc-ko");
  if (d.ko_bj) {
    ko.appendChild(el("span", "dc-ko-bj", d.ko_bj));
    if (d.ko_et) ko.appendChild(el("span", "dc-ko-et", d.ko_et));
  } else {
    ko.appendChild(el("span", "dc-ko-bj", "开球待定"));
  }
  card.appendChild(ko);

  // ---- 公允轴 (署名) ----
  const legs = d.value && Array.isArray(d.value.legs) ? d.value.legs : [];
  const best = d.value && d.value.best_leg ? d.value.best_leg : null;
  const ticks = [];
  const flags = [];
  for (const lg of legs) {
    const v = legValue(lg);
    if (v != null) ticks.push({ value: v, flag: lg.flag, best: false });
    if (lg.flag) flags.push(lg.flag);
  }
  // best_leg: 若不在 legs 里也单独画一个落点
  let bestVal = null;
  if (best) {
    bestVal = legValue(best);
    if (best.flag) flags.push(best.flag);
    // 标记最接近 best 的 tick 为 is-best; 否则补一个
    if (bestVal != null) {
      let marked = false;
      for (const t of ticks) {
        if (Math.abs(t.value - bestVal) < 1e-6) { t.best = true; marked = true; break; }
      }
      if (!marked) ticks.push({ value: bestVal, flag: best.flag, best: true });
    }
  } else if (ticks.length) {
    ticks[0].best = true;
  }

  const cardFlag = flags.length ? topFlag(flags) : null;
  if (cardFlag && cardFlag !== "skip") card.classList.add(`flag-${cardFlag}`);

  const axisWrap = el("div", "dc-axis-wrap");
  if (ticks.length) {
    axisWrap.appendChild(buildAxisEl(ticks, true));
    const cap = el("div", "dc-axis-caption");
    const bsrc = best || legs[0];
    const bdesc = bsrc ? (bsrc.desc || `${bsrc.market || ""} ${bsrc.outcome || ""}`.trim()) : "";
    const bv = bestVal != null ? bestVal : legValue(bsrc);
    const ev = bsrc ? (bsrc.ev_pct_devig != null ? bsrc.ev_pct_devig : bsrc.ev_pct) : null;
    cap.appendChild(el("span", null, `最不亏 ${bdesc || "—"} · 去水 ${fmtNum(bv, 3)} · `));
    cap.appendChild(el("span", ev != null && Number(ev) >= 0 ? "cap-pos" : "cap-neg", `EV ${fmtSignedPct(ev)}`));
    axisWrap.appendChild(cap);
  } else {
    axisWrap.appendChild(el("div", "dc-axis-empty", "价值未出 / 该场无可投点"));
  }
  card.appendChild(axisWrap);

  // ---- 读数行 ----
  const rows = el("div", "dc-rows");

  // v1 比分
  {
    const { content } = dcRow("v1", null);
    if (d.v1 && (d.v1.score != null || d.v1.rationale)) {
      if (d.v1.score != null) content.appendChild(el("span", "dc-score", String(d.v1.score)));
      if (d.v1.rationale) content.appendChild(el("span", "dc-rationale", d.v1.rationale));
      if (d.v1.probs) {
        const p = d.v1.probs;
        content.appendChild(el("span", "dc-probs", `胜${fmtPct(p.h, 0)} 平${fmtPct(p.d, 0)} 负${fmtPct(p.a, 0)}`));
      }
    } else {
      content.appendChild(el("span", "dc-placeholder", "未出"));
    }
    rows.appendChild(content.parentElement);
  }

  // v2 概率
  {
    const { content } = dcRow("v2", null);
    if (d.v2 && (d.v2.probs || d.v2.reliability || (Array.isArray(d.v2.scenarios) && d.v2.scenarios.length))) {
      if (d.v2.probs) {
        const p = d.v2.probs;
        const hda = el("span", "dc-hda");
        const vals = { h: Number(p.h), d: Number(p.d), a: Number(p.a) };
        const maxKey = ["h", "d", "a"].reduce((m, k) => (vals[k] > vals[m] ? k : m), "h");
        const cell = (k, lab) => {
          const c = el("span", `hda-cell${k === maxKey ? " is-max" : ""}`, `${lab} ${fmtPct(p[k], 0)}`);
          return c;
        };
        hda.appendChild(cell("h", "胜"));
        hda.appendChild(cell("d", "平"));
        hda.appendChild(cell("a", "负"));
        content.appendChild(hda);
      }
      const rb = reliabilityBadge(d.v2.reliability);
      if (rb) content.appendChild(el("span", `dc-rel ${rb.cls}`, `靠谱度 ${rb.label}`));
      if (d.v2.deviated) {
        const dev = el("span", "dc-deviated", "▲偏离");
        dev.appendChild(el("span", "dc-deviated-info", " ⓘ"));
        // 鼠标指到弹说明(原生 title: 永不被卡片 overflow 裁切、多行稳)
        dev.title =
          "偏离 = 这场 v2 没照抄市场、据确证事实改了数。\n" +
          "默认照抄市场(聪明钱、稳);偏离是 v2 自己的判断,该带怀疑看 —— 还没证明能跑赢市场,赛后用 Brier 验。\n" +
          "点「明细」看:改了哪条腿、从多少→多少、引的什么因子。";
        content.appendChild(dev);
      }
      if (Array.isArray(d.v2.scenarios) && d.v2.scenarios.length) {
        const chips = el("div", "dc-chips");
        for (const s of d.v2.scenarios) {
          if (s == null || s === "") continue;
          chips.appendChild(el("span", "dc-chip", String(s)));
        }
        content.appendChild(chips);
      }
    } else {
      content.appendChild(el("span", "dc-placeholder", "未出"));
    }
    rows.appendChild(content.parentElement);
  }

  // value 行 (+ 展开)
  let legsDetail = null;
  {
    const { row, content } = dcRow("价值", null, "dc-row-value");
    if (d.value && (d.value.verdict || best || legs.length)) {
      if (d.value.verdict) content.appendChild(el("span", "dc-verdict", d.value.verdict));
      if (best) {
        const b = flagBadge(best.flag);
        const chip = el("span", `dc-best ${b.cls}`);
        chip.appendChild(signalDot(best.flag));
        chip.appendChild(el("span", "dc-best-desc", best.desc || `${best.market || ""} ${best.outcome || ""}`.trim() || "最不亏腿"));
        if (best.ev_pct != null && !Number.isNaN(Number(best.ev_pct))) {
          chip.appendChild(el("span", `dc-best-ev ${Number(best.ev_pct) >= 0 ? "dc-pos" : "dc-neg"}`, `EV ${fmtSignedPct(best.ev_pct)}`));
        }
        content.appendChild(chip);
      }
      if (legs.length) {
        legsDetail = el("div", "dc-legs");
        legsDetail.hidden = true;
        for (const lg of legs) legsDetail.appendChild(buildLegRow(lg));
        row.classList.add("dc-expandable");
        const hint = el("span", "dc-expand-hint", "▾ 明细");
        content.appendChild(hint);
        row.addEventListener("click", () => {
          legsDetail.hidden = !legsDetail.hidden;
          card.classList.toggle("open", !legsDetail.hidden);
          hint.textContent = legsDetail.hidden ? "▾ 明细" : "▴ 收起";
        });
      }
    } else {
      content.appendChild(el("span", "dc-placeholder", "未出"));
    }
    rows.appendChild(row);
  }

  card.appendChild(rows);
  if (legsDetail) card.appendChild(legsDetail);
  card.appendChild(renderOddsPanel(d.odds));
  return card;
}

// 决策卡的最高级 flag (取 value.legs + best_leg)
function decTopFlag(d) {
  const legs = (d.value && Array.isArray(d.value.legs)) ? d.value.legs : [];
  const flags = legs.map((l) => l.flag).filter(Boolean);
  if (d.value && d.value.best_leg && d.value.best_leg.flag) flags.push(d.value.best_leg.flag);
  return flags.length ? topFlag(flags) : "skip";
}
// "M.D HH:MM" -> "HH:MM"
function koTime(ko) {
  const p = String(ko || "").trim().split(" ");
  return p.length === 2 ? p[1] : (ko || "—");
}
function buildScheduleRow(d) {
  const flag = decTopFlag(d);
  const row = el("div", `sched-row flag-${flag === "skip" ? "skip" : flag}`);
  if (d.view_status !== "upcoming") row.classList.add("done");
  row.appendChild(signalDot(flag));
  row.appendChild(el("div", "sr-time", koTime(d.ko_bj)));
  const home = `${d.home_flag ? d.home_flag + " " : ""}${d.home_cn || ""}`.trim();
  const away = `${d.away_flag ? d.away_flag + " " : ""}${d.away_cn || ""}`.trim();
  row.appendChild(el("div", "sr-match", home && away ? `${home} vs ${away}` : (d.match_key || "—")));
  if (d.v1 && d.v1.score != null) row.appendChild(el("div", "sr-v1", `v1 ${d.v1.score}`));
  const best = d.value && d.value.best_leg;
  if (best) {
    const b = flagBadge(best.flag);
    const chip = el("div", `sr-chip ${b.cls}`);
    chip.appendChild(signalDot(best.flag));
    chip.appendChild(el("span", null, best.desc || `${best.market || ""} ${best.outcome || ""}`.trim() || "最不亏"));
    const ev = best.ev_pct;
    if (ev != null && !Number.isNaN(Number(ev))) {
      chip.appendChild(el("span", Number(ev) >= 0 ? "ev-pos" : "ev-neg", fmtSignedPct(ev)));
    }
    row.appendChild(chip);
  }
  return row;
}

function renderDecisions(decisions) {
  const mount = $("#decision-list");
  if (!mount) return;
  decisions = decisions || [];
  state._lastDecisions = decisions;
  mount.innerHTML = "";

  // 筛选条
  const bar = el("div", "dec-filter");
  const FILTERS = [["upcoming", "未结束"], ["all", "全部"], ["edge", "仅绿"]];
  for (const [k, lab] of FILTERS) {
    const b = el("button", `df-btn${state.decFilter === k ? " on" : ""}`, lab);
    b.addEventListener("click", () => { state.decFilter = k; renderDecisions(state._lastDecisions); });
    bar.appendChild(b);
  }
  mount.appendChild(bar);

  if (decisions.length === 0) { mount.appendChild(el("div", "empty", "今日暂无决策卡, 在本地跑 /跑今天 生成")); return; }

  // 应用筛选
  let rows = decisions.slice();
  if (state.decFilter === "upcoming") rows = rows.filter((d) => d.view_status === "upcoming");
  if (state.decFilter === "edge") rows = rows.filter((d) => decTopFlag(d) === "green");
  if (rows.length === 0) { mount.appendChild(el("div", "empty", "暂无符合条件的场次")); return; }

  // 选中默认 = 第一行
  if (!state.decSel || !rows.some((d) => d.match_key === state.decSel)) state.decSel = rows[0].match_key;

  const mobile = window.matchMedia("(max-width:820px)").matches;
  const sel = rows.find((d) => d.match_key === state.decSel) || rows[0];
  const groups = [
    ["今日 · 未结束", rows.filter((d) => d.view_status === "upcoming")],
    ["已结束 / 进行中", rows.filter((d) => d.view_status !== "upcoming")],
  ];

  const buildList = (container) => {
    for (const [label, arr] of groups) {
      if (!arr.length) continue;
      container.appendChild(el("div", "dec-group-h", label));
      for (const d of arr) {
        const row = buildScheduleRow(d);
        if (d.match_key === state.decSel) row.classList.add("sel");
        row.addEventListener("click", () => { state.decSel = d.match_key; renderDecisions(state._lastDecisions); });
        container.appendChild(row);
        if (mobile && d.match_key === state.decSel) {
          const card = buildDecisionCard(d);
          card.classList.add("dec-inline");
          container.appendChild(card);
        }
      }
    }
  };

  if (mobile) {
    buildList(mount);
  } else {
    const split = el("div", "dec-split");
    const left = el("div", "dec-list-col");
    buildList(left);
    const panel = el("div", "dec-panel");
    panel.appendChild(buildDecisionCard(sel));
    split.appendChild(left);
    split.appendChild(panel);
    mount.appendChild(split);
  }
}

async function loadDecisions() {
  const list = $("#decision-list");
  try {
    const resp = await apiGet("/api/decisions");
    renderDecisions((resp && resp.decisions) || []);
  } catch (err) {
    if (list) { list.innerHTML = ""; list.appendChild(el("div", "empty", "决策卡加载失败: " + err.message)); }
  }
}

// 价值"重抓+刷新"
async function refreshValue(btn) {
  const original = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "重抓中…"; }
  try {
    await apiSend("/api/refresh", "POST");
    setTimeout(() => {
      refreshState();
      loadDecisions();
      if (btn) { btn.disabled = false; btn.textContent = original || "重抓 · 刷新"; }
    }, 3000);
  } catch (err) {
    if (btn) { btn.disabled = false; btn.textContent = "刷新失败, 重试"; }
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
    head.appendChild(el("span", `wc-kind kind-${w.kind || "x"}`, kindLabel[w.kind] || w.kind || "?"));
    head.appendChild(el("span", "wc-key", w.key || "—"));
    if (w.id != null) {
      const del = el("button", "wc-del", "✕");
      del.title = "取消关注";
      del.addEventListener("click", () => unpin(w.id));
      head.appendChild(del);
    }
    card.appendChild(head);

    if (w.note) card.appendChild(el("div", "wc-note", w.note));

    if (Array.isArray(w.matches) && w.matches.length) {
      const ml = el("div", "wc-matches");
      for (const m of w.matches) {
        const line = typeof m === "string" ? m : `${m.match || ""} ${m.ko_bj || ""}`.trim();
        ml.appendChild(el("div", "wc-match-line", line));
      }
      card.appendChild(ml);
    }

    if (w.lineup) {
      const lu = el("div", "wc-lineup");
      const luHead = el("div", "wc-lineup-h", "首发");
      const formation = typeof w.lineup === "object" ? w.lineup.formation : null;
      if (formation) luHead.appendChild(el("span", "wc-formation", formation));
      lu.appendChild(luHead);
      const players = typeof w.lineup === "object" && Array.isArray(w.lineup.players) ? w.lineup.players : null;
      if (players && players.length) {
        const chips = el("div", "wc-lineup-chips");
        for (const p of players) chips.appendChild(el("span", "wc-chip", String(p)));
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

    if (Array.isArray(w.news) && w.news.length) {
      const nl = el("div", "wc-news");
      for (const n of w.news) {
        const url = typeof n === "string" ? n : n.url;
        const t = typeof n === "string" ? n : n.title || n.url;
        if (!url) continue;
        const item = el("div", "wc-news-item");
        const a = el("a", "wc-news-link");
        a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
        a.textContent = "↗ " + t;
        item.appendChild(a);
        const ts = typeof n === "object" ? n.ts : "";
        if (ts) item.appendChild(el("span", "wc-news-ts", ts));
        nl.appendChild(item);
      }
      card.appendChild(nl);
    }

    if (Array.isArray(w.radar_hits) && w.radar_hits.length) {
      const rh = el("div", "wc-radar");
      rh.appendChild(el("div", "wc-radar-h", "雷达命中"));
      for (const h of w.radar_hits) {
        const b = flagBadge(h.flag);
        const hit = el("div", `wc-radar-hit ${b.cls}`);
        hit.appendChild(signalDot(h.flag));
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
  try { await apiSend(`/api/watchlist/${encodeURIComponent(id)}`, "DELETE"); }
  catch (_) { await apiSend(`/api/watchlist?id=${encodeURIComponent(id)}`, "DELETE"); }
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

function setupBetForm() {
  const form = $("#bet-form");
  const openBtn = $("#open-bet-form");
  const cancelBtn = $("#cancel-bet");
  const msg = $("#bet-msg");

  openBtn.addEventListener("click", () => { form.hidden = !form.hidden; });
  cancelBtn.addEventListener("click", () => { form.hidden = true; msg.textContent = ""; });

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
    const legs = legsText ? legsText.split(",").map((s) => ({ desc: s.trim() })).filter((l) => l.desc) : [];
    try {
      await apiSend("/api/bets", "POST", { wallet, legs, stake, odds, note });
      msg.textContent = "已记账";
      msg.className = "bet-msg ok";
      form.reset();
      await refreshState();
      setTimeout(() => { form.hidden = true; msg.textContent = ""; }, 800);
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
  if (window.markdownit) md = window.markdownit({ html: false, linkify: true, breaks: true });
  return md;
}
function reportName(r) { return typeof r === "string" ? r : r.name; }
function reportTitle(r) { return typeof r === "string" ? r : r.title || r.name; }

// 报告修改时间 (毫秒); 后端给 mtime(unix 秒), 旧/无字段则 0
function reportMtimeMs(r) {
  const m = r && typeof r === "object" ? Number(r.mtime) : NaN;
  return Number.isFinite(m) && m > 0 ? m * 1000 : 0;
}
function dayKey(d) { return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
function dayLabel(ms) {
  if (!ms) return "未知时间";
  const d = new Date(ms), now = new Date(), yest = new Date(now);
  yest.setDate(now.getDate() - 1);
  if (dayKey(d) === dayKey(now)) return "今天";
  if (dayKey(d) === dayKey(yest)) return "昨天";
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}
function hhmm(ms) {
  if (!ms) return "";
  const d = new Date(ms), p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function loadReportsList() {
  const tabsEl = $("#report-tabs");
  try {
    const list = await apiGet("/api/reports");
    tabsEl.innerHTML = "";
    if (!Array.isArray(list) || list.length === 0) { tabsEl.appendChild(el("div", "empty", "暂无报告")); return; }
    // 按修改时间倒序 (最新在前); 后端已排, 前端兜底
    const sorted = list.slice().sort((a, b) => reportMtimeMs(b) - reportMtimeMs(a));
    // 按天分组: 一天一标题, 报告一行一个 (标题左·时间右); 默认只露最新 3 行, 余者折叠
    const VISIBLE = 3;
    const groups = [];
    let curKey = null, shown = 0;
    sorted.forEach((r) => {
      const ms = reportMtimeMs(r);
      const key = ms ? dayKey(new Date(ms)) : "unknown";
      if (key !== curKey) {
        curKey = key;
        const g = el("div", "report-group");
        g.appendChild(el("div", "report-group-h", dayLabel(ms)));
        tabsEl.appendChild(g);
        groups.push({ el: g, rows: [] });
      }
      const name = reportName(r);
      const row = el("button", "report-tab");
      row.dataset.name = name;
      row.appendChild(el("span", "rt-title", reportTitle(r)));
      const t = hhmm(ms);
      if (t) row.appendChild(el("span", "rt-time", t));
      row.addEventListener("click", () => openReport(name));
      groups[groups.length - 1].el.appendChild(row);
      groups[groups.length - 1].rows.push({ el: row, extra: shown >= VISIBLE });
      shown++;
    });
    const hiddenCount = Math.max(0, sorted.length - VISIBLE);
    if (hiddenCount > 0) {
      const toggle = el("button", "report-more");
      const apply = (collapsed) => {
        groups.forEach((g) => {
          const hasVisible = g.rows.some((r) => !r.extra);
          g.el.hidden = collapsed && !hasVisible; // 整组都在折叠区 → 连日期标题一起藏
          g.rows.forEach((r) => { r.el.hidden = collapsed && r.extra; });
        });
        toggle.textContent = collapsed ? `展开全部 (还有 ${hiddenCount} 篇) ▾` : "收起 ▴";
      };
      let collapsed = true;
      toggle.addEventListener("click", () => { collapsed = !collapsed; apply(collapsed); });
      tabsEl.appendChild(toggle);
      apply(true);
    }
    openReport(state.activeReport || reportName(sorted[0]));
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

// ================= tab 切换 (支持 #hash 深链, 可收藏/分享某一页) =================
const TABS = ["decisions", "dashboard", "reports"];
function activateTab(tab, updateHash) {
  if (!TABS.includes(tab)) tab = "decisions";
  $$(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".view").forEach((v) => v.classList.remove("active"));
  const view = $(`#view-${tab}`);
  if (view) view.classList.add("active");
  if (updateHash) {
    try { history.replaceState(null, "", "#" + tab); } catch (_) {}
  }
  if (tab === "reports") loadReportsList();
  if (tab === "decisions") loadDecisions();
}
function setupTabs() {
  $$(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab, true));
  });
  window.addEventListener("hashchange", () => activateTab((location.hash || "").slice(1), false));
}
// ================= 配色切换 (localStorage 记忆) =================
const THEMES = ["newsprint", "cold-glass", "blueprint", "amber"];
function currentTheme() {
  const t = document.documentElement.dataset.theme;
  return THEMES.includes(t) ? t : "newsprint";
}
function applyTheme(theme) {
  if (!THEMES.includes(theme)) theme = "newsprint";
  // newsprint = :root 默认, 不需要属性 (移除即回默认)
  if (theme === "newsprint") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("wc-theme", theme); } catch (_) {}
  $$("#theme-switch .sw").forEach((b) =>
    b.setAttribute("aria-pressed", String(b.dataset.theme === theme))
  );
}
function setupThemeSwitch() {
  const wrap = $("#theme-switch");
  if (!wrap) return;
  $$(".sw", wrap).forEach((b) =>
    b.addEventListener("click", () => applyTheme(b.dataset.theme))
  );
  applyTheme(currentTheme()); // 同步当前选中态
}

function setupWatchAdd() {
  $("#watch-add").addEventListener("submit", async (e) => {
    e.preventDefault();
    const kind = $("#watch-kind").value;
    const key = $("#watch-key").value.trim();
    if (!key) return;
    try { await pin(kind, key, ""); $("#watch-key").value = ""; }
    catch (err) { alert("关注失败: " + err.message); }
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
    $("#updated-at").textContent = "同步 " + (s.ts ? s.ts.replace("T", " ").slice(0, 19) : new Date().toLocaleTimeString());
  } catch (err) {
    setConn(false);
    $("#updated-at").textContent = "离线: " + err.message;
  }
  loadDecisions();
}

// ================= 启动 =================
function init() {
  setupTabs();
  setupWatchAdd();
  setupBetForm();
  setupRadarRefresh();
  window.matchMedia("(max-width:820px)").addEventListener("change", () => {
    if ($("#view-decisions") && $("#view-decisions").classList.contains("active")) {
      renderDecisions(state._lastDecisions);
    }
  });
  setupThemeSwitch();
  // 初始 tab: 跟随 #hash (默认 decisions)
  const initial = (location.hash || "").slice(1);
  if (TABS.includes(initial) && initial !== "decisions") activateTab(initial, false);
  state.cdTimer = setInterval(tickCountdown, 1000);
  refreshState();
  state.pollTimer = setInterval(refreshState, POLL_INTERVAL_MS);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
