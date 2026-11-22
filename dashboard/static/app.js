// Club dashboard: reads the JSON API and renders tiles, awards, a sortable leaderboard and trend charts.
// Every name comes from Strava and is attacker-controlled, so text goes in with textContent, never innerHTML.
"use strict";

const $ = (sel) => document.querySelector(sel);
const fmt = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });
const fmt0 = new Intl.NumberFormat("en", { maximumFractionDigits: 0 });
const fmt1 = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const state = { report: null, trend: null, drawTrend: null, sortKey: "km", sortDir: -1, query: "" };

const AWARDS = [
  ["distance", "Most distance"], ["elevation", "Most climbing"], ["time", "Most time moving"],
  ["fastest", "Fastest average"], ["longest", "Longest activity"], ["climber", "Steepest climber"],
  ["flattest", "Flattest routes"], ["rest", "Most time stopped"],
];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c);
  return node;
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}

function dayLabel(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
}

// --- header ----------------------------------------------------------------------------------------------

function renderClub(club) {
  const words = club.name.split(/\s+/).filter(Boolean);
  $("#club-badge").textContent = (words.length > 1 ? words.map((w) => w[0]).join("") : club.name.slice(0, 4))
    .slice(0, 5).toUpperCase();
  $("#club-name").textContent = club.name;
  $("#title").textContent = club.name;
  document.title = `${club.name} – Dashboard`;
  $("#club-link").href = club.strava_url;
  $("#subtitle").textContent = `Since ${dayLabel(club.challenge_start)} · ${club.sports.join(", ")}`;
  const sync = club.sync || {};
  $("#updated").textContent = sync.last_success
    ? `Last sync ${new Date(sync.last_success).toLocaleString("en")}` : "Not synced yet";
  const err = $("#sync-error");
  err.hidden = !sync.last_error;
  err.textContent = sync.last_error ? `Last sync failed: ${sync.last_error}` : "";
}

// --- leaderboard view ------------------------------------------------------------------------------------

function renderTiles(r) {
  const pct = r.member_count ? Math.round((100 * r.active_count) / r.member_count) : 0;
  const tiles = [
    [fmt0.format(r.total_km), "km"], [fmt0.format(r.activity_count), "activities"],
    [fmt0.format(r.total_elev), "m climbed"], [fmt0.format(r.active_count), `active (${pct}%)`],
    [fmt0.format(r.member_count), "members"],
  ];
  $("#tiles").replaceChildren(...tiles.map(([v, l]) =>
    el("div", { class: "tile" }, el("div", { class: "val", text: v }), el("div", { class: "lbl", text: l }))));
}

function renderAwards(r) {
  const cards = AWARDS.filter(([k]) => r.awards[k]).map(([k, label]) => {
    const a = r.awards[k];
    return el("div", { class: "award" }, el("div", { class: "kind", text: label }),
      el("div", { class: "who", text: a.name }), el("div", { class: "what", text: a.value }));
  });
  $("#awards").replaceChildren(...(cards.length ? cards : [el("p", { class: "muted", text: "No awards yet." })]));
}

function renderBoard() {
  const r = state.report;
  if (!r) return;
  const q = state.query.trim().toLowerCase();
  // Rank is always by distance; sorting only reorders the rows.
  const byKm = r.leaderboard.filter((row) => row.acts > 0);
  const rank = new Map(byKm.map((row, i) => [row.athlete_id, i + 1]));
  const rows = r.leaderboard
    .filter((row) => !q || row.name.toLowerCase().includes(q))
    .slice()
    .sort((a, b) => state.sortDir * ((a[state.sortKey] ?? -1) - (b[state.sortKey] ?? -1)) ||
      (rank.get(a.athlete_id) ?? 1e9) - (rank.get(b.athlete_id) ?? 1e9));
  $("#board-count").textContent = `(${byKm.length} active of ${r.leaderboard.length})`;
  $("#board").replaceChildren(...rows.map((row) => {
    const n = rank.get(row.athlete_id);
    const tr = el("tr", row.acts ? {} : { class: "zero" },
      el("td", { class: "num" + (n && n <= 3 ? " medal" : ""), text: n ?? "–" }),
      el("td", { class: "name", text: row.name, title: row.name }),
      el("td", { class: "num", text: fmt1.format(row.km) }),
      el("td", { class: "num", text: row.acts ? (row.gap ? `–${fmt1.format(row.gap)}` : "leader") : "" }),
      el("td", { class: "num", text: row.acts }),
      el("td", { class: "num", text: row.acts ? row.time : "" }),
      el("td", { class: "num", text: row.acts ? fmt0.format(row.elev) : "" }),
      el("td", { class: "num", text: row.acts ? row.avg_speed : "" }),
      el("td", { class: "num", text: row.acts ? `${fmt.format(row.longest)} km` : "" }));
    return tr;
  }));
}

function setSort(key) {
  state.sortDir = state.sortKey === key ? -state.sortDir : -1;
  state.sortKey = key;
  document.querySelectorAll("th.sortable").forEach((th) => {
    th.setAttribute("aria-sort", th.dataset.key === key ? (state.sortDir < 0 ? "descending" : "ascending") : "none");
  });
  renderBoard();
}

async function loadReport(day) {
  const r = await getJSON(day ? `/api/report?date=${day}` : "/api/report");
  state.report = r;
  $("#empty").hidden = r.leaderboard.length > 0;
  renderTiles(r);
  renderAwards(r);
  renderBoard();
}

// --- trend view ------------------------------------------------------------------------------------------

const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

// The top of the y-axis: four equal, round steps (1, 2, 2.5 or 5 x 10^n; 2.5 only where it stays whole) that fit v.
function niceMax(v) {
  if (v <= 0) return 4;
  const raw = v / 4, p = 10 ** Math.floor(Math.log10(raw));
  const step = (p >= 10 ? [1, 2, 2.5, 5, 10] : [1, 2, 5, 10]).map((m) => m * p).find((m) => m >= raw);
  return Math.max(4, step * 4);
}

// One series, so no legend: the card title names it. Crosshair + tooltip snap to the nearest day.
function lineChart(host, points, unit) {
  // Drawn at the card's real pixel width (redrawn on resize), so text and strokes are never scaled.
  const W = Math.max(240, Math.round(host.clientWidth)), H = 200, L = 44, R = 12, T = 18, B = 24;
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": host.parentElement.querySelector("figcaption").textContent });
  host.replaceChildren(s);
  if (!points.length) return;
  const max = niceMax(Math.max(...points.map((p) => p.v)));
  const x = (i) => L + (points.length === 1 ? (W - L - R) / 2 : (i * (W - L - R)) / (points.length - 1));
  const y = (v) => T + (H - T - B) * (1 - v / max);
  for (let k = 0; k <= 4; k++) {
    const v = (max * k) / 4;
    s.append(svg("line", { class: "grid", x1: L, x2: W - R, y1: y(v), y2: y(v) }));
    const t = svg("text", { class: "axis", x: L - 6, y: y(v) + 4, "text-anchor": "end" });
    t.textContent = fmt0.format(v);
    s.append(t);
  }
  const ticks = Math.min(5, points.length);
  for (let k = 0; k < ticks; k++) {
    const i = ticks === 1 ? 0 : Math.round((k * (points.length - 1)) / (ticks - 1));
    const t = svg("text", { class: "axis", x: x(i), y: H - 6, "text-anchor": k === 0 ? "start" : k === ticks - 1 ? "end" : "middle" });
    t.textContent = new Date(points[i].d + "T00:00:00").toLocaleDateString("en", { day: "numeric", month: "short" });
    s.append(t);
  }
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  s.append(svg("path", { class: "area", d: `${d}L${x(points.length - 1)},${y(0)}L${x(0)},${y(0)}Z` }));
  s.append(svg("path", { class: "line", d }));
  const last = points[points.length - 1];
  const end = svg("text", { class: "end-label", x: x(points.length - 1) - 4, y: y(last.v) - 8, "text-anchor": "end" });
  end.textContent = fmt.format(last.v) + unit;
  s.append(end);

  const cross = svg("line", { class: "cross", y1: T, y2: H - B, visibility: "hidden" });
  const dot = svg("circle", { class: "dot", r: 4, visibility: "hidden" });
  const hit = svg("rect", { x: L, y: 0, width: W - L - R, height: H, fill: "transparent", tabindex: 0 });
  s.append(cross, dot, hit);
  const tip = $("#tooltip");
  const show = (i, clientX, clientY) => {
    const p = points[i];
    for (const n of [cross, dot]) n.setAttribute("visibility", "visible");
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
    dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.v));
    tip.replaceChildren(el("strong", { text: fmt.format(p.v) + unit }), document.createTextNode(dayLabel(p.d)));
    tip.hidden = false;
    tip.style.left = `${Math.min(clientX + 12, window.innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${clientY - tip.offsetHeight - 12}px`;
  };
  const hide = () => { tip.hidden = true; for (const n of [cross, dot]) n.setAttribute("visibility", "hidden"); };
  hit.addEventListener("pointermove", (e) => {
    const box = s.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    const i = Math.max(0, Math.min(points.length - 1, Math.round(((px - L) / (W - L - R)) * (points.length - 1))));
    show(i, e.clientX, e.clientY);
  });
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => { const b = s.getBoundingClientRect(); show(points.length - 1, b.right - 40, b.top + 40); });
  hit.addEventListener("blur", hide);
}

function renderTrend(trend) {
  const series = {
    km: [trend.map((t) => ({ d: t.date, v: t.km })), " km"],
    activities: [trend.map((t) => ({ d: t.date, v: t.activities })), ""],
    active: [trend.map((t) => ({ d: t.date, v: t.active })), ""],
    participation: [trend.map((t) => ({ d: t.date, v: t.members ? (100 * t.active) / t.members : 0 })), "%"],
  };
  state.drawTrend = () => document.querySelectorAll(".chart").forEach((host) => lineChart(host, ...series[host.dataset.key]));
  state.drawTrend();
  const sundays = trend.filter((t) => new Date(t.date + "T00:00:00").getDay() === 0 || t === trend[trend.length - 1]);
  $("#weekly").replaceChildren(...sundays.reverse().map((t) => el("tr", {},
    el("td", { class: "name", text: dayLabel(t.date) }), el("td", { class: "num", text: fmt0.format(t.km) }),
    el("td", { class: "num", text: fmt0.format(t.activities) }), el("td", { class: "num", text: t.active }),
    el("td", { class: "num", text: t.members }),
    el("td", { class: "num", text: t.members ? `${Math.round((100 * t.active) / t.members)}%` : "–" }),
    el("td", { class: "num", text: fmt0.format(t.elev) }))));
}

async function showView(view) {
  document.querySelectorAll(".tab").forEach((b) => {
    const on = b.dataset.view === view;
    b.classList.toggle("active", on);
    b.setAttribute("aria-selected", on);
  });
  $("#view-board").hidden = view !== "board";
  $("#view-trend").hidden = view !== "trend";
  if (view === "trend" && !state.trend) {
    state.trend = await getJSON("/api/trend");
    renderTrend(state.trend);
  } else if (view === "trend") {
    state.drawTrend();   // the width may have changed while hidden
  }
}

// --- start -----------------------------------------------------------------------------------------------

async function main() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  document.querySelectorAll("th.sortable").forEach((th) => {
    th.tabIndex = 0;
    th.addEventListener("click", () => setSort(th.dataset.key));
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSort(th.dataset.key); } });
  });
  let pending;
  window.addEventListener("resize", () => {
    clearTimeout(pending);
    pending = setTimeout(() => { if (state.drawTrend && !$("#view-trend").hidden) state.drawTrend(); }, 150);
  });
  $("#search").addEventListener("input", (e) => { state.query = e.target.value; renderBoard(); });

  const [club, dates] = await Promise.all([getJSON("/api/club"), getJSON("/api/dates")]);
  renderClub(club);
  const select = $("#date-select");
  select.replaceChildren(...dates.slice().reverse().map((d, i) =>
    el("option", { value: d, text: i === 0 ? `Today (${dayLabel(d)})` : dayLabel(d) })));
  select.addEventListener("change", () => loadReport(select.value));
  await loadReport();
}

main().catch((e) => {
  const err = $("#sync-error");
  err.hidden = false;
  err.textContent = `Could not load the dashboard: ${e.message}`;
});
