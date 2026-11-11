"use strict";
const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const ELEVS = ["base", "mid", "summit"];
const ELEV_NAME = { base: "Base", mid: "Mid-mountain", summit: "Summit" };
const state = { index: [], key: null, data: null, elev: localStorage.getItem("elev") || "summit", poll: null };

const MODEL_NAMES = {
  ecmwf_ifs: "ECMWF IFS 9 km", ecmwf_ifs025: "ECMWF IFS 0.25°", ecmwf_aifs025_single: "ECMWF AIFS",
  gfs_seamless: "GFS + HRRR", ncep_nbm_conus: "NBM 2.5 km", gem_seamless: "GEM (Canada)", icon_seamless: "ICON (DWD)",
  gem_hrdps_continental: "HRDPS 2.5 km",
};
const QUALITY = {
  "blower": { sw: "--seq-600", text: "Blower" },
  "light & dry": { sw: "--seq-400", text: "Light & dry" },
  "medium": { sw: "--seq-300", text: "Medium" },
  "dense": { sw: "--seq-200", text: "Dense" },
  "heavy / wet (Sierra cement)": { sw: "--seq-100", text: "Heavy / wet" },
  "mixed rain/snow": { sw: "--warning", text: "⚠ Mixed" },
  "sleet / freezing rain risk": { sw: "--serious", text: "⚠ Sleet / ZR" },
  "rain": { sw: "--critical", text: "☂ Rain" },
};
// weather-report note styling: status colours always ship with an icon + text
const NOTE = {
  wind: { ic: "≋", byLevel: true }, powder: { ic: "❄", color: "--good" }, rain: { ic: "☂", byLevel: true },
  sleet: { ic: "!", color: "--critical" }, vis: { ic: "◌", color: "--muted" }, cold: { ic: "°", byLevel: true },
};
const LEVEL_COLOR = { warn: "--critical", watch: "--warning", info: "--muted" };

// ---------------------------------------------------------------- formatting
const fmtSnow = (v) => v == null ? "–" : v < 0.25 ? "0" : v < 1 ? "<1" : String(Math.round(v));
const fmtIn = (v) => v == null ? "–" : `${fmtSnow(v)}"`;
const fmtRange = (lo, hi) => (hi == null || hi < 0.5) ? "" : `${fmtSnow(lo)}–${fmtSnow(hi)}"`;
const fmtFt = (v) => v == null ? "–" : `${(Math.round(v / 100) * 100).toLocaleString()}′`;
const fmtF = (v) => v == null ? "–" : `${Math.round(v)}°`;
const fmtMph = (v) => v == null ? "–" : `${Math.round(v)}`;
const dayLabel = (d) => {
  const dt = new Date(d + "T12:00:00");
  return { dow: dt.toLocaleDateString(undefined, { weekday: "short" }), dom: dt.getDate(), mon: dt.toLocaleDateString(undefined, { month: "short" }) };
};
const today = () => state.data.now.slice(0, 10);  // resort-local
const el = (tag, attrs = {}, text) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
};
const svgEl = (tag, attrs = {}) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};
const svgText = (attrs, text) => { const t = svgEl("text", attrs); t.textContent = text; return t; };

function niceMax(v, steps = 4) {
  const raw = Math.max(v, 1e-6) / steps, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  return { max: step * steps, step };
}

function chip(quality) {
  const q = QUALITY[quality];
  if (!q) return el("span", { class: "muted" }, "–");
  const c = el("span", { class: "chip" });
  const sw = el("span", { class: "sw" });
  sw.style.background = `var(${q.sw})`;
  c.append(sw, document.createTextNode(q.text));
  return c;
}

// ---------------------------------------------------------------- tooltip
const tip = $("#tip");
function showTip(evt, head, rows) {
  tip.replaceChildren(el("div", { class: "t-head" }, head));
  for (const r of rows) {
    const row = el("div", { class: "t-row" });
    const nm = el("span", { class: "nm" });
    if (r.color) { const k = el("span", { class: "key-line" }); k.style.background = r.color; nm.append(k); }
    nm.append(document.createTextNode(r.name));
    row.append(nm, el("b", {}, r.value));
    tip.append(row);
  }
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > innerWidth - 8) x = evt.clientX - w - pad;
  if (y + h > innerHeight - 8) y = evt.clientY - h - pad;
  tip.style.left = `${x}px`; tip.style.top = `${y}px`;
}
const hideTip = () => { tip.hidden = true; };
function hover(target, fn) {
  const show = (e) => fn(e.clientX ? e : { clientX: target.getBoundingClientRect().right, clientY: target.getBoundingClientRect().top + 30 });
  target.addEventListener("pointermove", show); target.addEventListener("focus", show);
  target.addEventListener("pointerleave", hideTip); target.addEventListener("blur", hideTip);
}

// ---------------------------------------------------------------- chart primitives
function frame(container, m) {
  container.replaceChildren();
  const W = container.clientWidth, H = container.clientHeight;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  container.append(svg);
  return { svg, W, H, m, iw: W - m.l - m.r, ih: H - m.t - m.b };
}
function yAxis(f, max, step, fmt) {
  for (let v = 0; v <= max + 1e-9; v += step) {
    const y = f.m.t + f.ih - (v / max) * f.ih;
    f.svg.append(svgEl("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: y, y2: y, class: v === 0 ? "baseline" : "gridline" }));
    f.svg.append(svgText({ x: f.m.l - 6, y: y + 4, "text-anchor": "end" }, fmt(v)));
  }
}
function column(x, y, w, h, fill) {  // 4px rounded data-end, square at the baseline
  const r = Math.min(4, h, w / 2);
  return svgEl("path", { class: "mark", fill,
    d: `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z` });
}
function legend(target, items) {
  const lg = $(target);
  lg.replaceChildren();
  for (const it of items) {
    const s = el("span");
    if (it.glyph) s.append(el("span", { class: "muted" }, it.glyph));
    else { const k = el("span", { class: it.kind || "key-line" }); k.style.background = it.color; s.append(k); }
    s.append(document.createTextNode(it.label)); lg.append(s);
  }
}

// ---------------------------------------------------------------- sections
function renderTotals(d, e) {
  const past = $("#past"), next = $("#next");
  past.replaceChildren(); next.replaceChildren();
  for (const [label, v] of Object.entries(d.past)) {
    const t = el("div", { class: "tile past" });
    t.append(el("div", { class: "label" }, label), el("div", { class: "value" }, fmtIn(v[e].p50)));
    t.append(el("div", { class: "sub" }, v.station_in != null ? `station measured ${fmtIn(v.station_in)}` : "model estimate"));
    past.append(t);
  }
  Object.entries(d.next).forEach(([label, v]) => {
    const s = v[e];
    const t = el("div", { class: "tile" + (label === "Next 5 days" ? " hero" : "") });
    t.append(el("div", { class: "label" }, label), el("div", { class: "value" }, fmtIn(s.p50)));
    const r = fmtRange(s.p25, s.p75);
    t.append(el("div", { class: "range" }, r ? `likely ${r}` : " "));
    if ((s.rain_in || 0) >= 0.1) t.append(el("div", { class: "sub" }, `+ ${s.rain_in.toFixed(2)}" rain`));
    next.append(t);
  });
}

function renderAlerts(d) {
  const box = $("#alerts");
  box.replaceChildren();
  for (const a of d.alerts || []) {
    const sev = /warning/i.test(a.event) ? "--critical" : /watch/i.test(a.event) ? "--warning" : "--serious";
    const card = el("div", { class: "alert" });
    const bar = el("span", { class: "bar" }); bar.style.background = `var(${sev})`;
    const body = el("div");
    body.append(el("b", {}, `⚠ ${a.event}`));
    const when = [a.onset, a.ends].filter(Boolean).map((t) => new Date(t).toLocaleString(undefined, { weekday: "short", hour: "numeric" })).join(" → ");
    body.append(el("div", { class: "when" }, `${when} · NWS`));
    if (a.description) {
      const det = el("details"); det.append(el("summary", {}, "Details"), el("pre", {}, a.description)); body.append(det);
    }
    card.append(bar, body);
    box.append(card);
  }
}

function renderReport(d, e) {
  const box = $("#report");
  box.replaceChildren();
  const days = d.daily.filter((x) => !x.past).slice(0, 7);
  $("#report-sub").textContent = `next 7 days · ${ELEV_NAME[e].toLowerCase()} snow · wind holds judged at the summit ridge`;
  for (const x of days) {
    const v = x[e];
    const notes = x.report || [];
    const snowy = (v.p50 || 0) >= 1 || (v.rain_in || 0) >= 0.1;
    const row = el("div", { class: "rday" });
    const dl = dayLabel(x.date);
    const dd = el("div", { class: "d" }); dd.append(document.createTextNode(x.date === today() ? "Today" : dl.dow), el("span", { class: "muted" }, ` ${dl.mon} ${dl.dom}`));
    const amt = el("div", { class: "amt" });
    if (snowy) {
      amt.append(el("b", {}, `${fmtIn(v.p50)}${fmtRange(v.p25, v.p75) ? "  (" + fmtRange(v.p25, v.p75) + ")" : ""}`));
      if (v.quality) amt.append(chip(v.quality));
    } else amt.append(el("span", { class: "quiet" }, "Dry"));
    const nn = el("div", { class: "notes" });
    for (const n of notes) {
      const spec = NOTE[n.kind] || { ic: "•", color: "--muted" };
      const color = spec.byLevel ? LEVEL_COLOR[n.level] : spec.color;
      const line = el("div", { class: "note" });
      const ic = el("span", { class: "ic" + (color === "--warning" ? " warning" : "") }, spec.ic);
      ic.style.background = `var(${color})`;
      line.append(ic, el("span", {}, n.text));
      nn.append(line);
    }
    const w = x.wind_hold;
    if (!notes.length) {
      const bits = [];
      if (w) bits.push(`ridge wind ${w.dir || ""} ${w.sustained_mph} mph`);
      if (v.hi_f != null) bits.push(`${fmtF(v.lo_f)} / ${fmtF(v.hi_f)}`);
      nn.append(el("span", { class: "quiet" }, `Nothing notable${bits.length ? " · " + bits.join(" · ") : ""}`));
    }
    row.append(dd, amt, nn);
    box.append(row);
  }
}

function snowChart(d, e) {
  const t0 = today();
  const past = d.daily.filter((x) => x.past).slice(-7);
  const fut = d.daily.filter((x) => !x.past).slice(0, 10);
  const days = [...past, ...fut];
  const c = $("#daily-chart");
  const f = frame(c, { t: 16, r: 12, b: 34, l: 36 });
  const top = Math.max(4, ...days.map((x) => Math.max(x[e].p75 || 0, x[e].p50 || 0, x[e].nws_snow_in || 0, x.obs?.new_snow_in || 0)));
  const { max, step } = niceMax(top);
  yAxis(f, max, step, (v) => `${v}"`);
  const band = f.iw / days.length, bw = Math.min(24, band * 0.55);
  const Y = (v) => f.m.t + f.ih - (Math.min(v, max) / max) * f.ih;
  const station = d.obs ? `station ${d.obs.elev_ft.toLocaleString()} ft` : null;
  days.forEach((x, i) => {
    const v = x[e], cx = f.m.l + band * i + band / 2;
    const g = svgEl("g", { class: "col" });
    const fill = x.past ? "var(--muted)" : "var(--series-1)";
    const h = f.m.t + f.ih - Y(v.p50 || 0);
    if (h > 0.5) g.append(column(cx - bw / 2, Y(v.p50), bw, h, fill));
    if (!x.past && v.p75 > 0.5) {
      const w = svgEl("g", { stroke: "var(--text-secondary)", "stroke-width": 1.5, "stroke-linecap": "round" });
      w.append(svgEl("line", { x1: cx, x2: cx, y1: Y(v.p25 || 0), y2: Y(v.p75) }),
        svgEl("line", { x1: cx - 4, x2: cx + 4, y1: Y(v.p75), y2: Y(v.p75) }),
        svgEl("line", { x1: cx - 4, x2: cx + 4, y1: Y(v.p25 || 0), y2: Y(v.p25 || 0) }));
      g.append(w);
    }
    if (!x.past && v.nws_snow_in > 0.25) g.append(svgEl("circle", { cx: cx + bw / 2 + 5, cy: Y(v.nws_snow_in), r: 4, fill: "var(--series-3)", stroke: "var(--surface-1)", "stroke-width": 2 }));
    if (x.past && x.obs?.new_snow_in > 0.25) g.append(svgEl("circle", { cx: cx + bw / 2 + 5, cy: Y(x.obs.new_snow_in), r: 4, fill: "var(--series-2)", stroke: "var(--surface-1)", "stroke-width": 2 }));
    if (v.p50 >= 1) g.append(svgText({ x: cx, y: Y(Math.max(v.p50, x.past ? 0 : v.p75 || 0)) - 5, "text-anchor": "middle", class: "today" }, fmtSnow(v.p50)));
    const dl = dayLabel(x.date);
    const cls = x.date === t0 ? "today" : "";
    g.append(svgText({ x: cx, y: f.H - 18, "text-anchor": "middle", class: cls }, x.date === t0 ? "Today" : dl.dow),
      svgText({ x: cx, y: f.H - 5, "text-anchor": "middle", class: cls }, dl.dom));
    const hit = svgEl("rect", { class: "hit", x: cx - band / 2, y: f.m.t, width: band, height: f.ih + 30, tabindex: 0 });
    hover(hit, (ev) => showTip(ev, `${dl.dow} ${dl.mon} ${dl.dom} · ${ELEV_NAME[e]}`, x.past ? [
      { name: "Model estimate", value: fmtIn(v.p50), color: "var(--muted)" },
      ...(x.obs ? [{ name: `Measured (${station})`, value: fmtIn(x.obs.new_snow_in), color: "var(--series-2)" }] : []),
      { name: "Liquid", value: `${(v.liquid_in || 0).toFixed(2)}"` },
    ] : [
      { name: "Forecast", value: fmtIn(v.p50), color: "var(--series-1)" },
      { name: "Likely (25–75%)", value: fmtRange(v.p25, v.p75) || "–" },
      ...(v.nws_snow_in != null ? [{ name: "NWS", value: fmtIn(v.nws_snow_in), color: "var(--series-3)" }] : []),
      { name: "Quality", value: QUALITY[v.quality]?.text || "–" },
      { name: "Snow:liquid", value: v.slr ? `${v.slr.toFixed(0)}:1` : "–" },
      { name: "Snow level", value: fmtFt(x.snow_level_ft) },
    ]));
    g.append(hit);
    f.svg.append(g);
  });
  if (past.length) {
    const xd = f.m.l + band * past.length;
    f.svg.append(svgEl("line", { x1: xd, x2: xd, y1: f.m.t - 6, y2: f.m.t + f.ih, class: "divider" }));
    f.svg.append(svgText({ x: xd - 6, y: f.m.t - 4, "text-anchor": "end" }, "past"), svgText({ x: xd + 6, y: f.m.t - 4 }, "forecast"));
  }
  $("#snow-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · last 7 days and next 10`;
  legend("#daily-legend", [
    { label: "Past (model estimate)", color: "var(--muted)", kind: "key-rect" },
    ...(d.obs ? [{ label: `Measured at ${station}`, color: "var(--series-2)", kind: "key-rect" }] : []),
    { label: "Forecast", color: "var(--series-1)", kind: "key-rect" },
    { label: "Likely range (25–75%)", glyph: "┿" },
    { label: "NWS forecast", color: "var(--series-3)", kind: "key-rect" },
  ]);
}

function renderDailyTable(d, e) {
  const tb = $("#daily-table");
  tb.replaceChildren();
  const days = d.daily.filter((x) => !x.past).slice(0, 10);
  $("#daily-sub").textContent = `${ELEV_NAME[e]} · ${d.points[e].requested.ft.toLocaleString()} ft`;
  const head = el("tr"); head.append(el("th", { class: "rowh" }, ""));
  days.forEach((x) => { const dl = dayLabel(x.date); head.append(el("th", { class: x.date === today() ? "today" : "" }, x.date === today() ? "Today" : `${dl.dow} ${dl.dom}`)); });
  tb.append(head);
  const row = (label, fn, cls = "") => {
    const tr = el("tr"); tr.append(el("td", { class: "rowh" }, label));
    days.forEach((x) => {
      const td = el("td", { class: (x.date === today() ? "today " : "") + cls });
      const v = fn(x, x[e]);
      if (v instanceof Node) td.append(v); else td.textContent = v;
      tr.append(td);
    });
    tb.append(tr);
  };
  const HOLD = { likely: "Likely", possible: "Possible", low: "–" };
  row("Snow", (x, v) => fmtIn(v.p50), "strong");
  row("Likely range", (x, v) => fmtRange(v.p25, v.p75) || "–");
  row("Quality", (x, v) => (v.liquid_in > 0.04 ? chip(v.quality) : "–"));
  row("Snow:liquid", (x, v) => v.slr && v.liquid_in > 0.04 ? `${v.slr.toFixed(0)}:1` : "–");
  row("Rain", (x, v) => (v.rain_in || 0) >= 0.05 ? `${v.rain_in.toFixed(2)}"` : "–");
  row("Snow level", (x) => fmtFt(x.snow_level_ft));
  row("Freezing level", (x) => fmtFt(x.freezing_level_ft));
  row("Hi / Lo", (x, v) => `${fmtF(v.hi_f)} / ${fmtF(v.lo_f)}`);
  row(e === "summit" ? "Ridge wind (mph)" : "Wind (mph)", (x, v) => v.wind_mph == null ? "–" : `${v.wind_dir || ""} ${fmtMph(v.wind_mph)}${v.gust_mph ? " g" + fmtMph(v.gust_mph) : ""}`);
  row("Wind holds", (x) => HOLD[x.wind_hold?.level] || "–");
  if (days.some((x) => x[e].nws_snow_in != null)) row("NWS snow", (x, v) => v.nws_snow_in != null ? fmtIn(v.nws_snow_in) : "–");
}

function timeChart(container, hours, opts) {
  const f = frame(container, opts.margin || { t: 10, r: 74, b: 24, l: 48 });
  const n = hours.length;
  const X = (i) => f.m.l + (i / (n - 1)) * f.iw;
  const { min = 0, max, step, fmt } = opts.y;
  const Y = (v) => f.m.t + f.ih - ((v - min) / (max - min)) * f.ih;
  const clampY = (v) => Y(Math.max(min, Math.min(max, v)));
  for (let v = min; v <= max + 1e-9; v += step) {
    f.svg.append(svgEl("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: Y(v), y2: Y(v), class: v === min ? "baseline" : "gridline" }));
    f.svg.append(svgText({ x: f.m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, fmt(v)));
  }
  hours.forEach((h, i) => {
    if (h.t.endsWith("T00:00")) {
      const dl = dayLabel(h.t.slice(0, 10));
      f.svg.append(svgEl("line", { x1: X(i), x2: X(i), y1: f.m.t, y2: f.m.t + f.ih, class: "gridline" }));
      f.svg.append(svgText({ x: X(i) + 4, y: f.H - 6 }, `${dl.dow} ${dl.dom}`));
    }
  });
  for (const b of opts.bands || []) {
    let d = "", back = "";
    hours.forEach((h, i) => {
      const lo = b.lo(h), hi = b.hi(h);
      if (lo == null || hi == null) return;
      d += `${d ? "L" : "M"}${X(i)},${clampY(hi)}`; back = `L${X(i)},${clampY(lo)}` + back;
    });
    if (d) f.svg.append(svgEl("path", { d: d + back + "Z", fill: b.color, opacity: 0.12 }));
  }
  for (const b of opts.bars || []) {
    const bw = Math.max(1, f.iw / n - 1);
    hours.forEach((h, i) => {
      let base = 0;
      for (const s of b) {
        const v = s.get(h) || 0;
        if (v <= 0) continue;
        const y0 = Y(Math.min(max, base)), y1 = Y(Math.min(max, base + v));
        f.svg.append(svgEl("rect", { x: X(i) - bw / 2, y: y1, width: bw, height: Math.max(0, y0 - y1), fill: s.color }));
        base += v;
      }
    });
  }
  for (const r of opts.refs || []) {
    if (r.v < min || r.v > max) continue;
    f.svg.append(svgEl("line", { x1: f.m.l, x2: f.m.l + f.iw, y1: Y(r.v), y2: Y(r.v), class: "ref" }));
    f.svg.append(svgText({ x: f.m.l + f.iw + 6, y: Y(r.v) + 4, class: "reflabel" + (r.strong ? " today" : "") }, r.label));
  }
  for (const s of opts.lines || []) {
    let d = "", pen = false;
    hours.forEach((h, i) => {
      const v = s.get(h);
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${clampY(v).toFixed(1)}`; pen = true;
    });
    f.svg.append(svgEl("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  const xh = svgEl("line", { class: "xhair", y1: f.m.t, y2: f.m.t + f.ih, visibility: "hidden" });
  const hit = svgEl("rect", { class: "hit", x: f.m.l, y: f.m.t, width: f.iw, height: f.ih });
  f.svg.append(xh, hit);
  hit.addEventListener("pointermove", (ev) => {
    const r = f.svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) * (f.W / r.width);
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - f.m.l) / f.iw) * (n - 1))));
    xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
    showTip(ev, new Date(hours[i].t).toLocaleString(undefined, { weekday: "short", hour: "numeric", month: "short", day: "numeric" }), opts.tip(hours[i]));
  });
  hit.addEventListener("pointerleave", () => { xh.setAttribute("visibility", "hidden"); hideTip(); });
}

function levelCharts(d, e) {
  const start = d.hourly.findIndex((h) => !h.past);
  const hours = d.hourly.slice(Math.max(0, start), Math.max(0, start) + 24 * 7);
  const p = d.points;
  const vals = hours.flatMap((h) => [h.snow_level_ft, h.freezing_level_ft, h.snow_level_hi_ft, h.snow_level_lo_ft]).filter((v) => v != null);
  const lo = Math.max(0, Math.floor((Math.min(p.base.requested.ft, ...vals) - 500) / 2000) * 2000);
  const hi = Math.min(18000, Math.ceil((Math.max(p.summit.requested.ft, ...vals) + 500) / 2000) * 2000);
  timeChart($("#level-chart"), hours, {
    y: { min: lo, max: hi, step: 2000, fmt: (v) => `${v / 1000}k′` },
    bands: [{ lo: (h) => h.snow_level_lo_ft, hi: (h) => h.snow_level_hi_ft, color: "var(--series-1)" }],
    refs: ELEVS.map((x) => ({ v: p[x].requested.ft, label: `${ELEV_NAME[x].split("-")[0]} ${(p[x].requested.ft / 1000).toFixed(1)}k`, strong: x === e })),
    lines: [
      { get: (h) => h.freezing_level_ft, color: "var(--series-2)" },
      { get: (h) => h.nws_snow_level_ft, color: "var(--series-3)" },
      { get: (h) => h.snow_level_ft, color: "var(--series-1)" },
    ],
    tip: (h) => [
      { name: "Snow level", value: fmtFt(h.snow_level_ft), color: "var(--series-1)" },
      { name: "Model range", value: `${fmtFt(h.snow_level_lo_ft)}–${fmtFt(h.snow_level_hi_ft)}` },
      ...(h.nws_snow_level_ft != null ? [{ name: "NWS snow level", value: fmtFt(h.nws_snow_level_ft), color: "var(--series-3)" }] : []),
      { name: "Freezing level", value: fmtFt(h.freezing_level_ft), color: "var(--series-2)" },
      { name: `Temp (${ELEV_NAME[e].toLowerCase()})`, value: fmtF(h[e].temp_f) },
    ],
  });
  legend("#level-legend", [
    { label: "Snow level (≈50% snow)", color: "var(--series-1)" },
    { label: "Model spread", color: "var(--series-1)", kind: "key-band" },
    ...(hours.some((h) => h.nws_snow_level_ft != null) ? [{ label: "NWS snow level", color: "var(--series-3)" }] : []),
    { label: "Freezing level", color: "var(--series-2)" },
  ]);
  const rate = Math.max(0.2, ...hours.map((h) => (h[e].snow_in || 0) + (h[e].rain_in || 0)));
  const { max, step } = niceMax(rate, 2);
  timeChart($("#precip-chart"), hours, {
    y: { min: 0, max, step, fmt: (v) => `${v}"` },
    bars: [[{ get: (h) => h[e].snow_in, color: "var(--series-1)" }, { get: (h) => h[e].rain_in, color: "var(--series-2)" }]],
    tip: (h) => [
      { name: "Snow / hr", value: `${(h[e].snow_in || 0).toFixed(2)}"`, color: "var(--series-1)" },
      { name: "Rain / hr", value: `${(h[e].rain_in || 0).toFixed(2)}"`, color: "var(--series-2)" },
      { name: "Snow fraction", value: h[e].frac != null ? `${Math.round(h[e].frac * 100)}%` : "–" },
      { name: "Snow:liquid", value: h[e].slr ? `${h[e].slr.toFixed(0)}:1` : "–" },
      { name: "Wind", value: `${fmtMph(h[e].wind_mph)} mph` },
    ],
  });
  legend("#precip-legend", [
    { label: `Snow per hour (${ELEV_NAME[e].toLowerCase()})`, color: "var(--series-1)", kind: "key-rect" },
    { label: "Rain per hour", color: "var(--series-2)", kind: "key-rect" },
  ]);
}

// ---------------------------------------------------------------- appendix
function renderModels(d, e) {
  const tb = $("#models-table");
  tb.replaceChildren();
  const days = d.daily.filter((x) => !x.past).slice(0, 10);
  const head = el("tr"); head.append(el("th", { class: "rowh" }, `Model (${ELEV_NAME[e].toLowerCase()} snow, in)`));
  days.forEach((x) => { const dl = dayLabel(x.date); head.append(el("th", {}, `${dl.dow} ${dl.dom}`)); });
  head.append(el("th", {}, "Total"), el("th", {}, "Weight"));
  tb.append(head);
  const cal = d.calibration.models;
  const models = Object.keys(MODEL_NAMES).filter((m) => cal[m] && days.some((x) => x[e].models[m] != null));
  const rows = [
    ...models.map((m) => ({ name: MODEL_NAMES[m], vals: days.map((x) => x[e].models[m]), w: cal[m].weight })),
    { name: "Blend", vals: days.map((x) => x[e].p50), strong: true },
  ];
  if (days.some((x) => x[e].nws_snow_in != null)) rows.push({ name: "NWS (forecaster)", vals: days.map((x) => x[e].nws_snow_in ?? null) });
  const vmax = Math.max(6, ...rows.flatMap((r) => r.vals.filter((v) => v != null)));
  for (const r of rows) {
    const tr = el("tr"); tr.append(el("td", { class: "rowh" + (r.strong ? " strong" : "") }, r.name));
    let tot = 0, any = false;
    r.vals.forEach((v) => {
      const td = el("td", { class: "cell" + (r.strong ? " strong" : "") }, v == null ? "–" : fmtSnow(v));
      if (v != null) { tot += v; any = true; td.style.background = `color-mix(in srgb, var(--seq-400) ${Math.round(Math.min(1, v / vmax) * 45)}%, transparent)`; }
      tr.append(td);
    });
    tr.append(el("td", { class: "strong" }, any ? fmtIn(tot) : "–"), el("td", { class: "muted" }, r.w != null ? `${Math.round(r.w * 100)}%` : ""));
    tb.append(tr);
  }
  const c = d.calibration;
  $("#cal-note").textContent = c.uncalibrated
    ? `No nearby automated gauge, so this resort is not individually calibrated: model factors are the median of ${c.peers} calibrated resorts${c.peers ? " in the region" : ""} and models are weighted equally.`
    : `Each model's precipitation is multiplied by a factor learned at ${c.station} (2024–26 as-issued forecasts) and weighted ∝ 1/error². Models drop out past their range (HRDPS 2 d, NBM 11 d, ICON 7 d, GEM 10 d).`;
}

function renderObs(d) {
  const o = d.obs;
  const tiles = $("#obs-tiles");
  tiles.replaceChildren();
  $("#obs-chart").replaceChildren();
  if (!o) { $("#obs-station").textContent = ""; tiles.append(el("div", { class: "muted" }, "No automated station data for this resort.")); return; }
  $("#obs-station").textContent = `· ${o.station}, ${o.elev_ft.toLocaleString()} ft`;
  const days = o.days.slice(-14);
  const sum = (n, k) => days.slice(-n).reduce((a, x) => a + (x[k] || 0), 0);
  const tile = (label, value, sub) => {
    const t = el("div", { class: "tile" });
    t.append(el("div", { class: "label" }, label), el("div", { class: "value" }, value));
    if (sub) t.append(el("div", { class: "sub" }, sub));
    tiles.append(t);
  };
  tile("Snow depth", o.depth_in != null ? `${Math.round(o.depth_in)}"` : "–", o.swe_in != null ? `${o.swe_in.toFixed(1)}" water content` : "");
  tile("Last 7 days", `${Math.round(sum(7, "new_snow_in"))}"`, `${sum(7, "precip_in").toFixed(2)}" precip`);
  tile("Last 14 days", `${Math.round(sum(14, "new_snow_in"))}"`, `${sum(14, "precip_in").toFixed(2)}" precip`);
  const f = frame($("#obs-chart"), { t: 10, r: 8, b: 22, l: 30 });
  const { max, step } = niceMax(Math.max(4, ...days.map((x) => x.new_snow_in || 0)), 2);
  yAxis(f, max, step, (v) => `${v}"`);
  const band = f.iw / days.length, bw = Math.min(20, band * 0.6);
  days.forEach((x, i) => {
    const cx = f.m.l + band * (i + 0.5), hh = ((x.new_snow_in || 0) / max) * f.ih;
    if (hh > 0.5) f.svg.append(column(cx - bw / 2, f.m.t + f.ih - hh, bw, hh, "var(--series-2)"));
    if (i % 2 === days.length % 2) f.svg.append(svgText({ x: cx, y: f.H - 6, "text-anchor": "middle" }, dayLabel(x.date).dom));
    const hit = svgEl("rect", { class: "hit", x: cx - band / 2, y: f.m.t, width: band, height: f.ih });
    hover(hit, (ev) => showTip(ev, x.date, [
      { name: "Depth gain", value: fmtIn(x.new_snow_in), color: "var(--series-2)" },
      { name: "Precip", value: x.precip_in != null ? `${x.precip_in.toFixed(2)}"` : "–" },
      { name: "Depth", value: x.depth_in != null ? `${Math.round(x.depth_in)}"` : "–" },
    ]));
    f.svg.append(hit);
  });
  legend("#obs-legend", [{ label: "Daily snow depth gain (settles, so it under-reads new snow)", color: "var(--series-2)", kind: "key-rect" }]);
}

function renderAfd(d) {
  const box = $("#afd");
  box.replaceChildren();
  if (!d.afd) { $("#afd-meta").textContent = ""; box.textContent = "No NWS discussion for this location (outside the US)."; return; }
  $("#afd-meta").textContent = `· NWS ${d.afd.office} · ${new Date(d.afd.issued).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}`;
  let text = d.afd.text;
  const s = text.search(/Area Forecast Discussion/i);
  if (s > 0) text = text.slice(s);
  const m = text.match(/\.(KEY MESSAGES|SYNOPSIS)[\s\S]*?(?=\n&&|\n\.[A-Z])/);
  if (m) { box.append(el("div", { class: "km" }, m[0].trim()), document.createTextNode("\n\n")); }
  box.append(document.createTextNode(text.replace(m ? m[0] : "", "").trim()));
}

function renderWide(d, e) {
  const tb = $("#wide-table");
  tb.replaceChildren();
  const days = d.daily.filter((x) => !x.past).slice(0, 10);
  const head = el("tr"); head.append(el("th", { class: "rowh" }, ELEV_NAME[e]));
  days.forEach((x) => { const dl = dayLabel(x.date); head.append(el("th", {}, `${dl.dow} ${dl.dom}`)); });
  tb.append(head);
  for (const [lbl, k] of [["90%", "p90"], ["75%", "p75"], ["Forecast", "p50"], ["25%", "p25"], ["10%", "p10"]]) {
    const tr = el("tr"); tr.append(el("td", { class: "rowh" }, lbl));
    days.forEach((x) => tr.append(el("td", { class: k === "p50" ? "strong" : "" }, fmtIn(x[e][k]))));
    tb.append(tr);
  }
  const tr = el("tr"); tr.append(el("td", { class: "rowh" }, "Next 3 / 5 / 10 days"));
  const td = el("td", { colspan: days.length });
  td.style.textAlign = "left";
  td.textContent = Object.entries(d.next).filter(([l]) => l !== "Next 24 hours").map(([l, v]) => `${l.replace("Next ", "")}: ${fmtSnow(v[e].p10)}–${fmtSnow(v[e].p90)}"`).join("  ·  ");
  tr.append(td); tb.append(tr);
}

function haversine(a, b, c, d) {
  const r = Math.PI / 180;
  const x = Math.sin(((c - a) * r) / 2) ** 2 + Math.cos(a * r) * Math.cos(c * r) * Math.sin(((d - b) * r) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}

function renderPoints(d) {
  const tb = $("#points-table");
  tb.replaceChildren();
  const head = el("tr");
  ["Elevation", "Point", "Lat, lon", "Elevation used", "Model grid cell"].forEach((h) => head.append(el("th", { class: h === "Elevation" ? "rowh" : "" }, h)));
  tb.append(head);
  for (const e of [...ELEVS].reverse()) {
    const r = d.points[e].requested, g = d.points[e].grid;
    const tr = el("tr");
    [ELEV_NAME[e], r.label, `${r.lat.toFixed(4)}, ${r.lon.toFixed(4)}`, `${r.ft.toLocaleString()} ft`,
      `${haversine(r.lat, r.lon, g.lat, g.lon).toFixed(1)} km away`].forEach((v, i) => tr.append(el("td", { class: i ? "" : "rowh" }, v)));
    tb.append(tr);
  }
  const ct = $("#cal-table");
  ct.replaceChildren();
  const c = d.calibration;
  if (c.uncalibrated) return;
  const h2 = el("tr");
  ["Model", "Bias factor", "Weight", "72 h error raw → corrected (in)", "Windows"].forEach((h, i) => h2.append(el("th", { class: i ? "" : "rowh" }, h)));
  ct.append(h2);
  for (const [m, v] of Object.entries(c.models)) {
    const tr = el("tr");
    [MODEL_NAMES[m] || m, `×${v.factor.toFixed(2)}`, `${Math.round(v.weight * 100)}%`,
      v.raw_mae72 != null ? `${(v.raw_mae72 / 25.4).toFixed(2)} → ${(v.mae72 / 25.4).toFixed(2)}` : "–", v.n].forEach((x, i) => tr.append(el("td", { class: i ? "" : "rowh" }, String(x))));
    ct.append(tr);
  }
}

function renderMethod(d) {
  const m = $("#method");
  m.replaceChildren();
  const sec = (h, p) => { m.append(el("h3", {}, h), el("p", {}, p)); };
  const big = (d.calibration.ratio_bins || []).at(-1);
  sec("Location", "Every number is for named points on the mountain (base, mid-mountain, summit), never the nearest town. Points are snapped to a 90 m terrain model, requests use the nearest model grid cell, and the published lift elevation is used. That way temperature, snow level and the rain/snow split are computed at real lift heights. Precipitation still comes from the model grid cell: no free model resolves individual ridges.");
  sec("Snow amount", "Up to eight models (ECMWF IFS 9 km and 0.25°, ECMWF AIFS, GFS/HRRR, NBM, GEM, ICON, plus HRDPS in Canada) are bias-corrected against a nearby SNOTEL/CDEC gauge using two winters of as-issued forecasts. They are then averaged with weights based on each model's error. Liquid becomes snow through the hourly rain/snow fraction and snow-to-liquid ratio at each elevation. Past days use the same models' short-range forecasts (an estimate, not a measurement).");
  sec("Likely range", "The middle 50% of outcomes (25th–75th percentile). It averages two distributions percentile by percentile: the five bias-corrected ensembles (≈190 members: ECMWF ENS, AIFS ENS, GEFS, GEM, ICON) and the blend times this resort's historical forecast error for a storm of that size." +
    (big ? ` For big storms (>2" of liquid in 72 h forecast) the gauge measured ${big.q["0.25"]}×–${big.q["0.75"]}× the forecast half the time.` : ""));
  sec("Rain vs snow", "Wet-bulb temperature at each elevation from a consensus ECMWF/GFS/GEM/ICON profile; 50% snow at +0.5 °C wet-bulb (Jennings et al. 2018 found ≈0.6–1.5 °C air temperature for the Sierra). Sleet/freezing rain is flagged only for the classic sounding: snow forming aloft, a melting layer above +2 °C, sub-freezing air at the elevation.");
  sec("Snow quality", "Snow-to-liquid ratio by the Kuchera method (warmest temperature between the elevation and ~18,000 ft), reduced up to 30% in strong wind. Density = 1/ratio: <6% blower, 6–8% light & dry, 8–11% medium, 11–15% dense, >15% heavy/wet. These cutoffs and the wind penalty are rules of thumb, not yet calibrated against snow-density observations.");
  sec("Weather report", "Wind holds use the free-air wind at summit height during lift hours (8am–4pm): likely at ≥45 mph sustained or ≈65 mph gusts, possible at ≥30 / ≈45 mph. Lifts differ (gondolas and exposed upper chairs close first), and these thresholds are rules of thumb, not fit to lift-status history. A powder morning is ≥8\" at the summit between 4pm and 9am. Alerts are the NWS's own.");
  sec("Data", "Open-Meteo (CC-BY 4.0; ECMWF, NOAA, ECCC, DWD models), NWS gridpoint forecasts, alerts and Area Forecast Discussions, NRCS SNOTEL and California CDEC observations.");
}

function renderCalCoverage() {
  const tb = $("#calcov-table");
  tb.replaceChildren();
  const head = el("tr");
  ["Resort", "Region", "Status", "Gauge", "Error (in / 72 h)", "Data updated"].forEach((h, i) => head.append(el("th", { class: i ? "" : "rowh" }, h)));
  tb.append(head);
  const LABEL = { calibrated: "✓ Calibrated", pending: "◐ Pending", "regional default": "Regional default", "not set up yet": "○ Setting up" };
  for (const r of state.index) {
    const tr = el("tr");
    const name = el("td", { class: "rowh" });
    name.append(r.located === false ? document.createTextNode(r.name) : el("a", { href: `#${r.key}` }, r.name));
    tr.append(name);
    [r.region, LABEL[r.calibration] || r.calibration || "–",
      r.station ? `${r.station}${r.station_ft ? ` (${r.station_ft.toLocaleString()} ft)` : ""}` : "–",
      r.blend_mae72_in != null ? `${r.blend_mae72_in.toFixed(2)}"` : "–",
      r.updated ? new Date(r.updated).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }) : "not yet"]
      .forEach((v) => tr.append(el("td", {}, v)));
    tb.append(tr);
  }
}

// ---------------------------------------------------------------- shell
function renderElevToggle(d) {
  const box = $("#elev-toggle");
  box.replaceChildren();
  for (const e of [...ELEVS].reverse()) {
    const b = el("button", { type: "button", "data-elev": e, role: "tab", "aria-selected": String(e === state.elev), class: e === state.elev ? "on" : "" });
    b.append(document.createTextNode(ELEV_NAME[e]), el("span", { class: "ft" }, `${d.points[e].requested.ft.toLocaleString()} ft`));
    b.addEventListener("click", () => { state.elev = e; localStorage.setItem("elev", e); render(); });
    box.append(b);
  }
}

function render() {
  const d = state.data;
  if (!d) return;
  const e = state.elev;
  $("#r-name").textContent = d.name;
  $("#r-pass").textContent = d.pass_ || "";
  $("#r-sub").textContent = [d.sub, d.region].filter(Boolean).join(" · ");
  $("#updated").textContent = `updated ${new Date(d.updated).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}`;
  renderElevToggle(d);
  renderAlerts(d);
  renderTotals(d, e);
  renderReport(d, e);
  snowChart(d, e);
  renderDailyTable(d, e);
  levelCharts(d, e);
  renderModels(d, e);
  renderObs(d);
  renderAfd(d);
  renderWide(d, e);
  renderPoints(d);
  renderMethod(d);
  renderCalCoverage();
}

async function fetchData(key) {
  const r = await fetch(`data/${key}.json?ts=${Date.now()}`);
  return r.ok ? r.json() : null;
}

async function load(key) {
  state.key = key;
  clearInterval(state.poll);
  const info = state.index.find((r) => r.key === key);
  document.querySelectorAll("#favs a").forEach((a) => a.classList.toggle("on", a.dataset.key === key));
  $("#q").value = "";
  let d = await fetchData(key);
  if (d && !d.next) d = null;  // pre-v2 file: treat as missing so it refreshes
  if (key !== state.key) return;
  if (d) { state.data = d; $("#loading").hidden = true; $("#content").hidden = false; render(); }
  else {
    state.data = null; $("#content").hidden = true; $("#loading").hidden = false;
    $("#r-name").textContent = info?.name || key; $("#r-pass").textContent = info?.pass_ || ""; $("#r-sub").textContent = info?.region || "";
    $("#elev-toggle").replaceChildren();
  }
  // ask the local server to refresh if missing/stale; poll until the file changes
  let st;
  try { st = await (await fetch(`api/refresh?key=${key}`)).json(); }
  catch {  // static hosting (GitHub Pages): no refresh endpoint, data arrives on the schedule
    if (!d) $("#loading").textContent = "No forecast for this resort yet. It refreshes automatically every 12 hours; check back soon.";
    return;
  }
  if (st.status !== "refreshing") return;
  $("#updated").textContent = "refreshing…";
  const before = d?.updated;
  state.poll = setInterval(async () => {
    const s = await (await fetch(`api/refresh?key=${key}`)).json().catch(() => ({}));
    if (s.status?.startsWith("error")) { clearInterval(state.poll); $("#updated").textContent = "refresh failed"; $("#loading").textContent = `Could not fetch this resort: ${s.status}`; return; }
    if (s.status !== "fresh") return;
    clearInterval(state.poll);
    const nd = await fetchData(key);
    if (nd && key === state.key && nd.updated !== before) { state.data = nd; $("#loading").hidden = true; $("#content").hidden = false; render(); }
  }, 4000);
}

// ---------------------------------------------------------------- search combobox
function setupSearch() {
  const q = $("#q"), list = $("#results");
  let active = -1, opts = [];
  const close = () => { list.hidden = true; q.setAttribute("aria-expanded", "false"); active = -1; };
  const draw = () => {
    const term = q.value.trim().toLowerCase();
    const hits = state.index.filter((r) => r.located !== false).filter((r) => !term || [r.name, r.sub, r.region, r.pass_].join(" ").toLowerCase().includes(term));
    list.replaceChildren(); opts = [];
    let grp = null;
    for (const r of hits) {
      if (r.region !== grp) { grp = r.region; list.append(el("li", { class: "grp", role: "presentation" }, grp)); }
      const li = el("li", { class: "opt", role: "option", id: `opt-${r.key}` });
      li.append(el("span", { class: "nm" }, r.name), el("span", { class: "ds" }, [r.sub, r.pass_].filter(Boolean).join(" · ")));
      li.addEventListener("mousedown", (ev) => { ev.preventDefault(); location.hash = r.key; close(); q.blur(); });
      list.append(li); opts.push({ li, r });
    }
    if (!hits.length) list.append(el("li", { class: "grp" }, "No matching resort"));
    list.hidden = false; q.setAttribute("aria-expanded", "true");
  };
  const mark = () => opts.forEach((o, i) => { o.li.classList.toggle("act", i === active); if (i === active) { o.li.scrollIntoView({ block: "nearest" }); q.setAttribute("aria-activedescendant", o.li.id); } });
  q.addEventListener("input", () => { active = 0; draw(); mark(); });
  q.addEventListener("focus", () => { draw(); });
  q.addEventListener("blur", () => setTimeout(close, 100));
  q.addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { active = Math.min(opts.length - 1, active + 1); mark(); ev.preventDefault(); }
    else if (ev.key === "ArrowUp") { active = Math.max(0, active - 1); mark(); ev.preventDefault(); }
    else if (ev.key === "Enter" && opts[Math.max(0, active)]) { location.hash = opts[Math.max(0, active)].r.key; close(); q.blur(); }
    else if (ev.key === "Escape") { close(); q.blur(); }
  });
  addEventListener("keydown", (ev) => { if (ev.key === "/" && document.activeElement !== q) { ev.preventDefault(); q.focus(); } });
}

async function boot() {
  state.index = (await (await fetch(`data/index.json?ts=${Date.now()}`)).json()).resorts;
  const favs = $("#favs");
  for (const r of state.index.filter((x) => x.favorite)) favs.append(el("a", { href: `#${r.key}`, "data-key": r.key }, r.name));
  setupSearch();
  const pick = () => load(location.hash.slice(1) || state.index.find((r) => r.favorite)?.key || state.index[0].key);
  addEventListener("hashchange", pick);
  let resizeT;
  addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(render, 150); });
  $("#theme").addEventListener("click", () => {
    const root = document.documentElement;
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    localStorage.setItem("theme", root.dataset.theme);
    render();
  });
  if (localStorage.getItem("theme")) document.documentElement.dataset.theme = localStorage.getItem("theme");
  pick();
}
boot().catch((err) => { $("#app").prepend(el("p", { class: "err" }, `Failed to load: ${err.message}. Run update.py first.`)); });
