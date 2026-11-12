"use strict";
const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const ELEVS = ["base", "mid", "summit"];
const ELEV_NAME = { base: "Base", mid: "Mid-mountain", summit: "Summit" };
const state = { index: [], key: null, data: null, elev: localStorage.getItem("elev") || "base", tab: { hourly: "current", daily: "current", level: "current", plume: "current" } };

const MODEL_NAMES = {
  ecmwf_ifs: "ECMWF IFS 9 km", ecmwf_ifs025: "ECMWF IFS 0.25°", ecmwf_aifs025_single: "ECMWF AIFS",
  gfs_seamless: "GFS + HRRR", ncep_nbm_conus: "NBM 2.5 km", gem_seamless: "GEM (Canada)", icon_seamless: "ICON (DWD)",
  gem_hrdps_continental: "HRDPS 2.5 km",
};
const QUALITY = {  // one colour per label everywhere: orange = good snow, purple = heavy / mixed / sleet / rain
  "blower": { sw: "--q-blower", text: "Blower" },
  "light & dry": { sw: "--q-light", text: "Light & dry" },
  "medium": { sw: "--q-medium", text: "Medium" },
  "dense": { sw: "--q-dense", text: "Dense" },
  "heavy / wet (Sierra cement)": { sw: "--q-heavy", text: "Heavy / wet" },
  "mixed rain/snow": { sw: "--q-mixed", text: "Mixed" },
  "sleet / freezing rain risk": { sw: "--q-sleet", text: "Sleet / ZR" },
  "rain": { sw: "--q-rain", text: "Rain" },
};
const ALERT = { powder: "--q-light", wet: "--q-sleet", warn: "--warning", info: "--muted" };  // daily-overview alert squares
const ALERT_NAME = { powder: "Powder", wet: "Wet / icy", warn: "Warning", info: "Note" };
const NOTE_ALERT = { powder: "powder", rain: "wet", sleet: "wet", vis: "info" };  // weather-report note -> alert
// weather-report note styling: status colours always ship with an icon + text

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
const today = () => state.data.today;  // current ski day (resort-local)
function tabs(id, which) {  // Current / History toggle for a section
  const box = $(id);
  box.replaceChildren();
  for (const [k, label] of [["current", "Current"], ["history", "History"]]) {
    const b = el("button", { type: "button", class: state.tab[which] === k ? "on" : "" }, label);
    b.addEventListener("click", () => { state.tab[which] = k; render(); });
    box.append(b);
  }
}
const window17 = (d) => [...d.daily.filter((x) => x.past).slice(-7), ...d.daily.filter((x) => !x.past).slice(0, 10)];  // last 7 + next 10 days
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

const ratio = (slr) => slr ? `${slr.toFixed(0)}:1` : null;
function snowRatio(quality, slr) {  // swatch in the quality colour + ratio; rain/mixed/sleet show the label
  const q = QUALITY[quality];
  if (!q) return el("span", { class: "muted" }, "–");
  const c = el("span", { class: "ratio", title: q.text });
  const sw = el("span", { class: "sw" });
  sw.style.background = `var(${q.sw})`;
  const wet = /rain|sleet|mixed/.test(quality);
  c.append(sw, document.createTextNode(wet || !slr ? q.text : ratio(slr)));
  return c;
}
const chip = snowRatio;

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
function renderAlerts(d) {
  const box = $("#alerts");
  box.replaceChildren();
  if (d.alerts === null) {  // fetch failed this refresh: unknown, not "no alerts"
    const card = el("div", { class: "alert" });
    const bar = el("span", { class: "bar" }); bar.style.background = "var(--muted)";
    const body = el("div"); body.append(el("b", {}, "NWS alerts unavailable this refresh"), el("div", { class: "when" }, "Check weather.gov for active warnings."));
    card.append(bar, body); box.append(card);
    return;
  }
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


function qualityKeys(box) {
  for (const k of Object.keys(QUALITY)) {
    const row = el("span", { class: "qkey" }); const sw = el("span", { class: "sw" });
    sw.style.background = `var(${QUALITY[k].sw})`; row.append(sw, document.createTextNode(QUALITY[k].text));
    box.append(row);
  }
}

function renderSummary(d, e) {
  const t0 = d.today, s = d.summary[e];
  const past = d.daily.filter((x) => x.past).slice(-7);
  const next = d.daily.filter((x) => !x.past);
  const groups = [
    { label: "Last 7 Days", days: past, tot: s.past7, past: true },
    { center: true },
    { label: "Next 1–5 Days", days: next.slice(0, 5), tot: s.next1_5 },
    { label: "Next 6–10 Days", days: next.slice(5, 10), tot: s.next6_10 },
  ];
  const total = (v) => v.periods.reduce((a, p) => a + (p.snow_in || 0), 0);  // 6am-6am, sum of the 3 parts
  const vmax = Math.max(2, ...groups.flatMap((g) => (g.days || []).flatMap((x) => x[e].periods.map((p) => p.snow_in || 0))));
  const box = $("#summary");
  box.replaceChildren();
  $("#summary-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · a day runs 6am to 6am (overnight snow counts toward the day it fell)`;
  for (const g of groups) {
    if (g.center) {
      const c = el("div", { class: "scenter" });
      c.append(el("div", { class: "slabel" }, "Last 24 Hours"), el("div", { class: "sbig" }, fmtIn(s.last24.p50)),
        el("div", { class: "muted" }, s.last24.station_in != null ? `station measured ${fmtIn(s.last24.station_in)}` : "model estimate"));
      const lg = el("div", { class: "legend-col" });
      qualityKeys(lg);
      c.append(lg);
      box.append(c);
      continue;
    }
    const grp = el("div", { class: "sgroup" }); grp.style.flex = g.days.length;
    grp.append(el("div", { class: "slabel" }, g.label),
      el("div", { class: "stotal" + (g.past ? " past" : "") }, fmtIn(g.tot.p50)),
      el("div", { class: "muted srange" }, g.past && g.tot.station_in != null ? `station ${fmtIn(g.tot.station_in)}` : "\u00a0"));
    const days = el("div", { class: "sdays" });
    for (const x of g.days) {
      const v = x[e], dl = dayLabel(x.date), weekend = /Sat|Sun/.test(dl.dow);
      const col = el("div", { class: "sday" + (x.past ? " past" : ""), tabindex: 0 });
      const stack = el("div", { class: "sstack" });
      v.periods.forEach((p) => {  // morning, afternoon, night left to right
        const seg = el("span", { class: "sseg" });
        seg.style.height = p.snow_in >= 0.05 ? `${p.snow_in / vmax * 100}%` : "0";
        seg.style.background = `var(${(QUALITY[p.quality] || QUALITY["medium"]).sw})`;
        stack.append(seg);
      });
      const wrap = el("div", { class: "sbar-wrap" });
      wrap.append(stack);
      const rain = v.periods.reduce((a, p) => a + (p.rain_in || 0), 0);
      const label = el("div", { class: "sday-l" + (weekend ? " wk" : "") + (x.date === t0 ? " today" : "") });
      label.append(el("span", {}, x.date === t0 ? "Today" : dl.dow.slice(0, 2)), el("span", {}, String(dl.dom)));
      const val = el("div", { class: "sval" });
      if (rain >= 0.1) val.append(el("span", { class: "rainmark", title: `${rain.toFixed(2)}" rain` }));
      val.append(document.createTextNode(fmtSnow(total(v))));
      col.append(val, wrap, label);
      days.append(col);
      hover(col, (ev) => showTip(ev, `${dl.dow} ${dl.mon} ${dl.dom} · ${ELEV_NAME[e]}`, [
        { name: x.past ? "Model estimate" : "Forecast", value: fmtIn(total(v)) },
        ...(x.obs?.new_snow_in != null ? [{ name: "Station (calendar day)", value: fmtIn(x.obs.new_snow_in) }] : []),
        ...["Morning 6a–12p", "Afternoon 12–6p", "Night 6p–6a"].map((nm, k) => {
          const p = v.periods[k];
          return { name: nm, value: `${fmtIn(p.snow_in)}${p.quality && p.snow_in >= 0.05 ? " · " + QUALITY[p.quality].text : ""}${p.rain_in >= 0.01 ? ` · rain ${p.rain_in.toFixed(2)}"` : ""}` };
        }),
        ...(rain >= 0.01 ? [{ name: "Rain", value: `${rain.toFixed(2)}"` }] : []),
        { name: "Snow level", value: fmtFt(x.snow_level_ft) },
      ]));
    }
    grp.append(days);
    box.append(grp);
  }
}

function windChill(t, v) {
  return t == null || v == null || t > 50 || v < 3 ? t : 35.74 + 0.6215 * t - 35.75 * v ** 0.16 + 0.4275 * t * v ** 0.16;
}

function conditions(h) {
  if ((h.snow_in || 0) >= 0.05) return "Snow";
  if ((h.rain_in || 0) >= 0.01) return (h.snow_in || 0) > 0.01 ? "Mixed" : "Rain";
  return h.cloud == null ? "–" : h.cloud >= 75 ? "Cloudy" : h.cloud >= 30 ? "Partly" : "Clear";
}

function renderHourly(d, e) {
  const tb = $("#hourly-table"), wrap = $("#hourly-wrap");
  tb.replaceChildren();
  tabs("#hourly-tabs", "hourly");
  const history = state.tab.hourly === "history";
  const hrs = d.hourly.filter((h) => history ? h.t < d.now : h.t >= d.now);
  const ni = history ? -1 : 0;
  $("#hourly-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · ${history ? "last 7 days (model estimate)" : "next 10 days"}`;
  const hl = (t) => { const h = +t.slice(11, 13); return `${h % 12 || 12}${h < 12 ? "a" : "p"}`; };
  const rows = [  // [label, unit, value(hour, elevation data)]
    ["Precip Chance", "%", (h) => h.precip_chance == null ? "–" : `${h.precip_chance}`],
    ["Precipitation", "in", (h, v) => v.precip_mm == null ? "–" : (v.precip_mm / 25.4).toFixed(2)],
    ["Snowfall", "in", (h, v) => v.snow_in == null ? "–" : v.snow_in.toFixed(1)],
    ["Snow Ratio", "", (h, v) => (v.snow_in || 0) >= 0.05 && v.slr ? `${v.slr.toFixed(0)}:1` : "–"],
    ["Snow Level", "ft", (h) => h.snow_level_ft == null ? "–" : (Math.round(h.snow_level_ft / 100) * 100).toLocaleString()],
    ["Conditions", "", (h, v) => conditions(v)],
    ["Temperature", "°F", (h, v) => v.temp_f == null ? "–" : Math.round(v.temp_f)],
    ["Feels Like", "°F", (h, v) => { const f = windChill(v.temp_f, v.wind_mph); return f == null ? "–" : Math.round(f); }],
    ["Rel. Humidity", "%", (h, v) => v.rh == null ? "–" : Math.round(v.rh)],
    ["Wind Speed", "mph", (h, v) => v.wind_mph == null ? "–" : v.wind_mph < 3 ? "Calm" : `${compass(v.wind_dir) || ""}${Math.round(v.wind_mph)}`],
    ["Wind Gust", "mph", (h, v) => v.gust_mph == null ? "–" : Math.round(v.gust_mph)],
    ["Cloud Cover", "%", (h, v) => v.cloud == null ? "–" : Math.round(v.cloud)],
  ];
  const head = el("tr"); head.append(el("th", { class: "rowh" }, ""));
  hrs.forEach((h, i) => {
    const th = el("th", { class: (i === ni ? "now" : ""), "data-i": i });
    th.append(el("div", {}, hl(h.t)), el("div", { class: "muted" }, dayLabel(h.t.slice(0, 10)).dow));
    head.append(th);
  });
  tb.append(head);
  for (const [label, unit, fn] of rows) {
    const tr = el("tr");
    const th = el("td", { class: "rowh" }); th.append(el("b", {}, label), document.createTextNode(unit ? ` ${unit}` : ""));
    tr.append(th);
    hrs.forEach((h, i) => tr.append(el("td", { class: (i === ni ? "now" : "") }, String(fn(h, h[e])))));
    tb.append(tr);
  }
  requestAnimationFrame(() => { wrap.scrollLeft = history ? wrap.scrollWidth : 0; });  // history: start at the latest hour
}

const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compass = (deg) => deg == null ? null : DIRS[Math.floor(((deg % 360) + 22.5) / 45) % 8];

function renderDailyTable(d, e) {
  const tb = $("#daily-table");
  tb.replaceChildren();
  tb.className = "grid days";
  tabs("#daily-tabs", "daily");
  const days = state.tab.daily === "history" ? d.daily.filter((x) => x.past).slice(-7) : d.daily.filter((x) => !x.past).slice(0, 10);
  $("#daily-sub").textContent = `${ELEV_NAME[e]} · ${d.points[e].requested.ft.toLocaleString()} ft · rows: 6am–6pm, 6pm–6am`;
  // daytime rows also carry the day's report notes (powder morning, rain line, sleet, flat light);
  // wind-hold / wind-chill notes are already row alerts
  // alerts are mountain-wide: the same on every elevation view. Union of base / mid / summit for
  // this 12 h row (labelled by elevation where they differ), deduplicated
  const rowAlerts = (h, x, k) => {
    const seen = new Map();
    for (const el_ of ELEVS) {
      for (const a of x[el_].halves[k]?.alerts || []) {
        const key = a.kind === "warn" ? a.text : `${a.kind}:${el_}`;
        if (!seen.has(key)) seen.set(key, { kind: a.kind, text: a.text, where: a.kind === "warn" ? "Mountain" : ELEV_NAME[el_].replace("-mountain", "") });
      }
    }
    const notes = k ? [] : (x.report || []).filter((n) => NOTE_ALERT[n.kind]).map((n) => ({ kind: NOTE_ALERT[n.kind], text: n.text, where: "Mountain" }));
    return [...seen.values(), ...notes];
  };
  const cols = [  // [header, cell(h, x, k), class, header group]
    ["Temp", (h) => fmtF(h.temp_f)],
    ["Alerts", (h, x, k) => {
      const al = rowAlerts(h, x, k);
      if (!al.length) return "";
      const s = el("span", { class: "alerts", "aria-label": al.map((a) => a.text).join("; ") });
      for (const kind of Object.keys(ALERT)) {  // one square per colour; hover lists that colour's alerts
        const items = al.filter((a) => a.kind === kind);
        if (!items.length) continue;
        const q = el("span", { class: "asq", tabindex: 0 });
        q.style.background = `var(${ALERT[kind]})`;
        hover(q, (ev) => showTip(ev, `${ALERT_NAME[kind]} · ${h.name === "day" ? "6am–6pm" : "6pm–6am"}`,
          items.map((a) => ({ name: a.where || "", value: a.text, color: `var(${ALERT[kind]})` }))));
        s.append(q);
      }
      return s;
    }],
    ["Snow Ratio", (h) => (h.liquid_in > 0.02 ? snowRatio(h.quality, h.slr) : "–")],
    ["12-Hr Snowfall", (h) => {  // forecast with the middle-50% range, e.g. 5" (1–7")
      const c = el("span"); c.append(el("b", {}, fmtIn(h.snow_in)));
      const r = fmtRange(h.p25, h.p75);
      if (r) c.append(el("span", { class: "muted" }, ` (${r})`));
      return c;
    }],
    ["Snow Level (ft)", (h) => fmtFt(h.snow_level_ft)],
    ["Precip Chance", (h) => h.precip_chance == null ? "–" : `${h.precip_chance}%`],
    ["Precip Amount", (h) => h.liquid_in >= 0.005 ? `${h.liquid_in.toFixed(2)}"` : "0\""],
    ["Speed", (h) => `${h.wind_dir || ""} ${fmtMph(h.wind_mph)}`, "", "Wind (mph)"],
    ["Gust", (h) => fmtMph(h.gust_mph), "", "Wind (mph)"],
  ];
  const head1 = el("tr"), head2 = el("tr");
  head1.append(el("th", { class: "rowh", rowspan: 2 }, ""));
  cols.forEach(([name, , , grp], k) => {
    if (!grp) return head1.append(el("th", { rowspan: 2 }, name));
    if (cols[k - 1]?.[3] !== grp) head1.append(el("th", { colspan: cols.filter((c) => c[3] === grp).length, class: "grp" }, grp));
    head2.append(el("th", {}, name));
  });
  tb.append(head1, head2);
  for (const x of days) {
    const dl = dayLabel(x.date);
    x[e].halves.forEach((h, k) => {
      const tr = el("tr", { class: `${k ? "night" : "dayrow"}${h.past ? " past" : ""}` });
      if (!k) {
        const td = el("td", { class: "dlabel", rowspan: x[e].halves.length });
        td.append(document.createTextNode(x.date === today() ? "Today" : dl.dow), el("span", {}, `${dl.mon} ${dl.dom}`));
        tr.append(td);
      }
      for (const [, fn, cls] of cols) {
        const td = el("td", { class: cls || "" });
        const v = fn(h, x, k);
        if (v instanceof Node) td.append(v); else td.textContent = v;
        tr.append(td);
      }
      tb.append(tr);
    });
  }
}

function timeChart(container, hours, opts) {
  if (hours.length < 2) { container.replaceChildren(el("div", { class: "muted" }, "No hourly data.")); return; }
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
    if (d) f.svg.append(svgEl("path", { d: d + back + "Z", fill: b.color, opacity: b.opacity ?? 0.12 }));
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
  const ni = hours.findIndex((h) => h.t >= state.data.now);
  if (ni > 0) {  // past (model estimate) | forecast
    f.svg.append(svgEl("line", { x1: X(ni), x2: X(ni), y1: f.m.t - 4, y2: f.m.t + f.ih, class: "divider" }));
    f.svg.append(svgText({ x: X(ni) - 4, y: f.m.t + 10, "text-anchor": "end" }, "past"), svgText({ x: X(ni) + 4, y: f.m.t + 10 }, "forecast"));
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
  tabs("#level-tabs", "level");
  const hours = d.hourly.filter((h) => state.tab.level === "history" ? h.t < d.now : h.t >= d.now);
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
      { get: (h) => h.snow_level_ft, color: "var(--series-1)" },
    ],
    tip: (h) => [
      { name: "Snow level", value: fmtFt(h.snow_level_ft), color: "var(--series-1)" },
      { name: "Model range", value: `${fmtFt(h.snow_level_lo_ft)}–${fmtFt(h.snow_level_hi_ft)}` },
      { name: "Freezing level", value: fmtFt(h.freezing_level_ft), color: "var(--series-2)" },
      { name: `Temp (${ELEV_NAME[e].toLowerCase()})`, value: fmtF(h[e].temp_f) },
    ],
  });
  legend("#level-legend", [
    { label: "Snow level (≈50% snow)", color: "var(--series-1)" },
    { label: "Model spread", color: "var(--series-1)", kind: "key-band" },
    { label: "Freezing level", color: "var(--series-2)" },
  ]);
}

function plumeChart(d, e) {  // snow + rain per 12 h period (6a-6p, 6p-6a) with spread whiskers
  tabs("#plume-tabs", "plume");
  const history = state.tab.plume === "history";
  const all = d.plume[e];
  const keep = all.t.map((t, i) => [t, i]).filter(([t, i]) => history ? (all.t[i + 1] || "9") <= d.now : (all.t[i + 1] || "9") > d.now).map(([, i]) => i);
  const pick = (o) => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, keep.map((i) => v[i])]));
  const p = { t: keep.map((i) => all.t[i]), snow: pick(all.snow), rain: pick(all.rain) };
  const kinds = [["snow", "var(--q-light)", "Snow", 1], ["rain", "var(--q-rain)", "Rain", 2]];
  const n = p.t.length;
  const c = $("#plume");
  const f = frame(c, { t: 16, r: 12, b: 34, l: 40 });
  const top = Math.max(2, ...p.snow["0.95"], ...p.rain["0.95"]);
  const { max, step } = niceMax(top);
  yAxis(f, max, step, (v) => `${v}"`);
  const band = f.iw / n, bw = Math.max(3, Math.min(10, band * 0.32));
  const Y = (v) => f.m.t + f.ih - (Math.min(v, max) / max) * f.ih;
  const ni = p.t.findIndex((t) => t >= d.now.slice(0, 13));
  p.t.forEach((t, i) => {
    const cx = f.m.l + band * i + band / 2, past = t < d.now && (p.t[i + 1] || "9") <= d.now;
    const g = svgEl("g", { class: "col" });
    kinds.forEach(([k, color], j) => {
      const v = p[k].fc[i], x = cx + (j ? 1 : -1) * (bw / 2 + 1) - bw / 2;
      if (v >= 0.05) g.append(column(x, Y(v), bw, f.m.t + f.ih - Y(v), past ? "var(--muted)" : color));
      if (!past && p[k]["0.95"][i] > p[k]["0.05"][i] + 0.05) {
        const xm = x + bw / 2;
        g.append(svgEl("line", { x1: xm, x2: xm, y1: Y(p[k]["0.05"][i]), y2: Y(p[k]["0.95"][i]), stroke: "var(--text-secondary)", "stroke-width": 1 }),
          svgEl("line", { x1: xm, x2: xm, y1: Y(p[k]["0.25"][i]), y2: Y(p[k]["0.75"][i]), stroke: "var(--text-secondary)", "stroke-width": 3, "stroke-linecap": "round" }));
      }
    });
    if (t.endsWith("T06:00")) {  // label each day under its daytime + nighttime pair
      const dl = dayLabel(t.slice(0, 10));
      f.svg.append(svgText({ x: cx + band / 2, y: f.H - 18, "text-anchor": "middle", class: t.slice(0, 10) === d.today ? "today" : "" },
        t.slice(0, 10) === d.today ? "Today" : dl.dow.slice(0, 2)), svgText({ x: cx + band / 2, y: f.H - 5, "text-anchor": "middle" }, String(dl.dom)));
      if (i) f.svg.append(svgEl("line", { x1: cx - band / 2, x2: cx - band / 2, y1: f.m.t, y2: f.m.t + f.ih, class: "gridline" }));
    }
    const hit = svgEl("rect", { class: "hit", x: cx - band / 2, y: f.m.t, width: band, height: f.ih + 30 });
    const dl = dayLabel(t.slice(0, 10)), night = t.endsWith("T18:00");
    hover(hit, (ev) => showTip(ev, `${dl.dow} ${dl.mon} ${dl.dom} · ${night ? "6pm–6am" : "6am–6pm"}`, kinds.flatMap(([k, color, name, dp]) => [
      { name, value: `${p[k].fc[i].toFixed(dp)}"`, color },
      ...(past ? [] : [{ name: "  middle 50% · 90%", value: `${p[k]["0.25"][i].toFixed(dp)}–${p[k]["0.75"][i].toFixed(dp)} · ${p[k]["0.05"][i].toFixed(dp)}–${p[k]["0.95"][i].toFixed(dp)}"` }]),
    ])));
    g.append(hit);
    f.svg.append(g);
  });
  if (ni > 0) {
    const xd = f.m.l + band * ni;
    f.svg.append(svgEl("line", { x1: xd, x2: xd, y1: f.m.t - 6, y2: f.m.t + f.ih, class: "divider" }),
      svgText({ x: xd - 6, y: f.m.t - 4, "text-anchor": "end" }, "past"), svgText({ x: xd + 6, y: f.m.t - 4 }, "forecast"));
  }
  $("#plume-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · per 12 h · ${history ? "last 7 days (model estimate)" : "next 10 days"}`;
  legend("#plume-legend", [
    { label: "Snow (in)", color: "var(--q-light)", kind: "key-rect" },
    { label: "Rain (in of water)", color: "var(--q-rain)", kind: "key-rect" },
    { label: "Past (model estimate)", color: "var(--muted)", kind: "key-rect" },
    { label: "Middle 50% (thick) · 90% (thin)", glyph: "┃" },
  ]);
}

// ---------------------------------------------------------------- appendix
function renderModels(d, e) {
  const tb = $("#models-table");
  tb.replaceChildren();
  const days = window17(d);
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
    ? `Not individually calibrated (${d.obs ? "calibration pending" : "no nearby automated gauge"}): model factors are the median of ${c.peers} calibrated ${c.peers_scope === "region" ? "resorts in the region" : "US resorts"} and models are weighted equally.${/BC|Canadian Rockies/.test(d.region) ? " Canadian snow climates differ from the US resorts these factors come from, so treat amounts with extra caution." : ""}`
    : `Each model's precipitation is multiplied by a factor learned at ${c.station} from up to two winters (2024–26) of as-issued forecasts (ECMWF IFS 9 km and AIFS have one winter of history), and weighted ∝ 1/error². Factors are fit and scored on the same winters, so the error shown is a bit optimistic. Models drop out past their range (HRDPS 2 d, NBM 11 d, ICON 7 d, GEM 10 d).`;
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
  tile("Last 7 Days", `${Math.round(sum(7, "new_snow_in"))}"`, `${sum(7, "precip_in").toFixed(2)}" precip`);
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
  if (!d.afd) {
    $("#afd-meta").textContent = "";
    box.textContent = /BC|Canadian Rockies/.test(d.region) ? "NWS discussions only cover the US." : "NWS discussion unavailable this refresh.";
    return;
  }
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
  const days = window17(d);
  const head = el("tr"); head.append(el("th", { class: "rowh" }, ELEV_NAME[e]));
  days.forEach((x) => { const dl = dayLabel(x.date); head.append(el("th", {}, `${dl.dow} ${dl.dom}`)); });
  tb.append(head);
  for (const [lbl, k] of [["95th %ile", "p95"], ["75th", "p75"], ["Forecast", "p50"], ["25th", "p25"], ["5th", "p5"]]) {
    const tr = el("tr"); tr.append(el("td", { class: "rowh" }, lbl));
    days.forEach((x) => tr.append(el("td", { class: k === "p50" ? "strong" : "" }, fmtIn(x[e][k]))));
    tb.append(tr);
  }
  const s = d.summary[e];
  const tr = el("tr"); tr.append(el("td", { class: "rowh" }, "Next 1–5 / 6–10 days"));
  const td = el("td", { colspan: days.length });
  td.style.textAlign = "left";
  td.textContent = `${fmtSnow(s.next1_5.p5)}–${fmtSnow(s.next1_5.p95)}"  ·  ${fmtSnow(s.next6_10.p5)}–${fmtSnow(s.next6_10.p95)}"`;
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
  for (const e of ELEVS) {
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
  for (const e of ELEVS) {  // base -> mid -> summit
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
  const dep = $("#r-depth");  // gauge depths: measured at stations, not the resort's report
  dep.replaceChildren();
  const ds = d.depth || {};
  if (ELEVS.some((x) => ds[x])) {
    dep.append(document.createTextNode("Snow depth "));
    for (const x of ELEVS) {
      const v = ds[x];
      const s = el("span", { class: "dep", title: v ? `${v.station}, ${v.ft.toLocaleString()} ft` : "no gauge near this elevation" });
      s.append(document.createTextNode(`${ELEV_NAME[x].replace("-mountain", "")} `), el("b", {}, v ? `${Math.round(v.depth_in)}"` : "–"));
      dep.append(s);
    }
    dep.append(el("span", { class: "muted" }, " · gauge measurements"));
  }
  $("#updated").textContent = `updated ${new Date(d.updated).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}`;
  renderElevToggle(d);
  renderAlerts(d);
  renderSummary(d, e);
  renderHourly(d, e);
  renderDailyTable(d, e);
  levelCharts(d, e);
  plumeChart(d, e);
  renderModels(d, e);
  renderObs(d);
  renderAfd(d);
  renderWide(d, e);
  renderPoints(d);
  renderCalCoverage();
}

async function fetchData(key) {
  const r = await fetch(`data/${key}.json?ts=${Date.now()}`);
  return r.ok ? r.json() : null;
}

async function load(key) {
  state.key = key;
  localStorage.setItem("resort", key);
  $("#q").value = "";
  const d = await fetchData(key);
  if (key !== state.key) return;  // user switched resorts while this loaded
  state.data = d;
  $("#content").hidden = !state.data;
  $("#loading").hidden = !!state.data;
  if (state.data) return render();
  $("#r-name").textContent = state.index.find((r) => r.key === key)?.name || key;
  $("#elev-toggle").replaceChildren();
}

// ---------------------------------------------------------------- search combobox
function setupSearch() {
  const q = $("#q"), list = $("#results");
  let active = -1, opts = [];
  const close = () => { list.hidden = true; q.setAttribute("aria-expanded", "false"); active = -1; };
  const draw = () => {
    const term = q.value.trim().toLowerCase();
    const hits = state.index.filter((r) => r.located !== false && (!term || `${r.name} ${r.region}`.toLowerCase().includes(term)));
    list.replaceChildren(); opts = [];
    let grp = null;
    for (const r of hits) {
      if (r.region !== grp) { grp = r.region; list.append(el("li", { class: "grp", role: "presentation" }, grp)); }
      const li = el("li", { class: "opt", role: "option", id: `opt-${r.key}` }, r.name);
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
  setupSearch();
  const ready = state.index.filter((r) => r.located !== false);
  const last = localStorage.getItem("resort");
  const pick = () => load(location.hash.slice(1) || (ready.some((r) => r.key === last) ? last : ready[0].key));
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
