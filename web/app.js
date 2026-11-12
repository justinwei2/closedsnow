"use strict";
const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const ELEVS = ["base", "mid", "summit"];
const ELEV_NAME = { base: "Base", mid: "Mid-mountain", summit: "Summit" };
const state = { index: [], key: null, data: null, elev: localStorage.getItem("elev") || "base" };

const MODEL_NAMES = {
  ecmwf_ifs: "ECMWF IFS 9 km", ecmwf_ifs025: "ECMWF IFS 0.25°", ecmwf_aifs025_single: "ECMWF AIFS",
  gfs_seamless: "GFS + HRRR", ncep_nbm_conus: "NBM 2.5 km", gem_seamless: "GEM (Canada)", icon_seamless: "ICON (DWD)",
  gem_hrdps_continental: "HRDPS 2.5 km",
};
const QUALITY = {  // one colour per label everywhere: orange = snow, purple = mixed / sleet, blue = rain
  "snow": { sw: "--q-snow", text: "Snow" },
  "mixed": { sw: "--q-mixed", text: "Mixed / sleet" },
  "rain": { sw: "--q-rain", text: "Rain" },
};
const qual = (q) => QUALITY[q] || { sw: "--muted", text: q || "–" };  // unknown labels (older data) stay grey
const ALERT = { powder: "--q-snow", wet: "--q-mixed", warn: "--warning", info: "--muted" };  // daily-overview alert squares
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
function snowRatio(quality, slr) {  // swatch in the quality colour + ratio; mixed / rain show the label
  if (!quality) return el("span", { class: "muted" }, "–");
  const q = qual(quality);
  const c = el("span", { class: "ratio", title: q.text });
  const sw = el("span", { class: "sw" });
  sw.style.background = `var(${q.sw})`;
  const wet = quality === "mixed" || quality === "rain";
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

const amount = (snow, rain) =>  // e.g. 1" + 0.13" rain; snow or rain alone when the other is ~0
  [snow >= 0.25 || !(rain >= 0.01) ? fmtIn(snow) : null, rain >= 0.01 ? `${rain.toFixed(2)}" rain` : null].filter(Boolean).join(" + ");

function windowType(t) {  // overall type of a multi-day total, same snow-fraction cutoffs as §7
  if (!t.liquid_in || t.liquid_in < 0.05) return null;
  const frac = 1 - (t.rain_in || 0) / t.liquid_in;
  return frac < 0.2 ? "rain" : frac < 0.8 ? "mixed" : "snow";
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
  const sum = (v, k) => v.halves.reduce((a, h) => a + (h[k] || 0), 0);  // 6am-6am: daytime + nighttime
  const vmax = Math.max(2, ...groups.flatMap((g) => (g.days || []).flatMap((x) => x[e].halves.map((h) => h.snow_in || 0))));
  const box = $("#summary");
  box.replaceChildren();
  $("#summary-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · days run 6am to 6am; night is 6pm to 6am the next morning`;
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
    const tot = el("div", { class: "stotal" + (g.past ? " past" : "") }, fmtIn(g.tot.p50));
    const sub = el("div", { class: "muted srange" }, g.past && g.tot.station_in != null ? `station ${fmtIn(g.tot.station_in)}` : "\u00a0");
    const type = !g.past && windowType(g.tot);
    if (type) {  // forecast totals take the window's precipitation-type colour, named underneath
      tot.style.color = `var(${QUALITY[type].sw})`;
      sub.replaceChildren(chip(type));
    }
    grp.append(el("div", { class: "slabel" }, g.label), tot, sub);
    const days = el("div", { class: "sdays" });
    for (const x of g.days) {
      const v = x[e], dl = dayLabel(x.date), weekend = /Sat|Sun/.test(dl.dow);
      const col = el("div", { class: "sday" + (x.past ? " past" : ""), tabindex: 0 });
      const stack = el("div", { class: "sstack" });
      v.halves.forEach((p) => {  // daytime, nighttime left to right
        const seg = el("span", { class: "sseg" });
        seg.style.height = p.snow_in >= 0.05 ? `${p.snow_in / vmax * 100}%` : "0";
        seg.style.background = `var(${qual(p.quality).sw})`;
        stack.append(seg);
      });
      const wrap = el("div", { class: "sbar-wrap" });
      wrap.append(stack);
      const rain = sum(v, "rain_in");
      const label = el("div", { class: "sday-l" + (weekend ? " wk" : "") + (x.date === t0 ? " today" : "") });
      label.append(el("span", {}, x.date === t0 ? "Today" : dl.dow.slice(0, 2)), el("span", {}, String(dl.dom)));
      col.append(el("div", { class: "sval" }, fmtSnow(sum(v, "snow_in"))), wrap, label);
      days.append(col);
      hover(col, (ev) => showTip(ev, `${dl.dow} ${dl.mon} ${dl.dom} · ${ELEV_NAME[e]}`, [
        { name: x.past ? "Model estimate" : "Forecast", value: amount(sum(v, "snow_in"), rain) },
        ...(x.obs?.new_snow_in != null ? [{ name: "Station (calendar day)", value: fmtIn(x.obs.new_snow_in) }] : []),
        ...["Daytime 6a–6p", "Nighttime 6p–6a"].map((nm, k) => {
          const p = v.halves[k] || {};
          return { name: nm, value: amount(p.snow_in, p.rain_in) + (p.quality === "mixed" ? ` · ${qual("mixed").text}` : "") };
        }),
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
  const hrs = d.hourly, ni = hrs.findIndex((h) => h.t >= d.now);
  const cls = (i) => i === ni ? "now" : i < ni ? "past" : "";
  $("#hourly-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · last 7 days (model estimate) and next 10`;
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
    const th = el("th", { class: cls(i) });
    th.append(el("div", {}, hl(h.t)), el("div", { class: "muted" }, dayLabel(h.t.slice(0, 10)).dow));
    head.append(th);
  });
  tb.append(head);
  for (const [label, unit, fn] of rows) {
    const tr = el("tr");
    const th = el("td", { class: "rowh" }); th.append(el("b", {}, label), document.createTextNode(unit ? ` ${unit}` : ""));
    tr.append(th);
    hrs.forEach((h, i) => tr.append(el("td", { class: cls(i) }, String(fn(h, h[e])))));
    tb.append(tr);
  }
  const now = head.children[ni + 1];  // open at the current hour; the past is to the left
  if (now) wrap.scrollLeft = now.offsetLeft - head.children[0].offsetWidth;
}

const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const compass = (deg) => deg == null ? null : DIRS[Math.floor(((deg % 360) + 22.5) / 45) % 8];

function renderDailyTable(d, e) {
  const tb = $("#daily-table");
  tb.replaceChildren();
  tb.className = "grid days";
  const days = window17(d);
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
  const thead = el("thead"); thead.append(head1, head2);
  tb.append(thead);
  for (const x of days) {
    const dl = dayLabel(x.date);
    x[e].halves.forEach((h, k) => {
      const tr = el("tr", { class: `${k ? "night" : "dayrow"}${h.past ? " past" : ""}`, "data-date": x.date });
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
  const t0 = tb.querySelector(`tr.dayrow[data-date="${today()}"]`);  // open at today; the past is above
  if (t0) $("#daily-wrap").scrollTop = t0.offsetTop - thead.offsetHeight;
  legend("#alert-legend", Object.keys(ALERT).map((k) => ({ label: ALERT_NAME[k], color: `var(${ALERT[k]})`, kind: "key-rect" })));
}

const VISIBLE_DAYS = 7;  // scrolling time charts show about a week; scroll for the rest
function timeChart(container, pts, opts) {  // pts: [{t, ...}] at a fixed step (perDay per day)
  if (pts.length < 2) { container.replaceChildren(el("div", { class: "muted" }, "No data.")); return; }
  const m = { t: 14, b: 24, l: 48, r: 74, ...opts.margin }, H = opts.height || 240, ih = H - m.t - m.b;
  container.replaceChildren(); container.classList.add("scroll");
  const vis = Math.max(200, container.clientWidth - m.l - m.r), n = pts.length;
  const dx = vis / (VISIBLE_DAYS * (opts.perDay || 24)), pad = 2, W = Math.max(vis, dx * (n - 1) + 2 * pad);
  const left = svgEl("svg", { width: m.l, height: H }), right = svgEl("svg", { width: m.r, height: H });
  const svg = svgEl("svg", { width: W, height: H, role: "img" });
  const sc = el("div", { class: "xscroll" }); sc.append(svg);
  container.append(left, sc, right);
  const X = (i) => pad + i * dx;
  const { min = 0, max, step, fmt } = opts.y;
  const Y = (v) => m.t + ih - ((Math.max(min, Math.min(max, v)) - min) / (max - min)) * ih;
  for (let v = min; v <= max + 1e-9; v += step) {
    svg.append(svgEl("line", { x1: 0, x2: W, y1: Y(v), y2: Y(v), class: v === min ? "baseline" : "gridline" }));
    left.append(svgText({ x: m.l - 6, y: Y(v) + 4, "text-anchor": "end" }, fmt(v)));
  }
  const tick = opts.tick || ((p) => p.t.endsWith("T00:00"));
  pts.forEach((p, i) => {
    if (!tick(p)) return;
    const day = p.t.slice(0, 10), dl = dayLabel(day);
    svg.append(svgEl("line", { x1: X(i), x2: X(i), y1: m.t, y2: m.t + ih, class: "gridline" }),
      svgText({ x: X(i) + 4, y: H - 6, class: day === state.data.today ? "today" : "" }, day === state.data.today ? `Today ${dl.dom}` : `${dl.dow} ${dl.dom}`));
  });
  const path = (get) => {  // polyline through non-null values
    let d = "", pen = false;
    pts.forEach((p, i) => {
      const v = get(p);
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`; pen = true;
    });
    return d;
  };
  for (const b of opts.bands || []) {
    let d = "", back = "";
    pts.forEach((p, i) => {
      const lo = b.lo(p), hi = b.hi(p);
      if (lo == null || hi == null) return;
      d += `${d ? "L" : "M"}${X(i)},${Y(hi)}`; back = `L${X(i)},${Y(lo)}` + back;
    });
    if (d) svg.append(svgEl("path", { d: d + back + "Z", fill: b.color, opacity: b.opacity ?? 0.12 }));
  }
  for (const r of opts.refs || []) {
    if (r.v < min || r.v > max) continue;
    svg.append(svgEl("line", { x1: 0, x2: W, y1: Y(r.v), y2: Y(r.v), class: "ref" }));
    right.append(svgText({ x: 6, y: Y(r.v) + 4, class: "reflabel" + (r.strong ? " today" : "") }, r.label));
  }
  for (const s of opts.lines || [])
    svg.append(svgEl("path", { d: path(s.get), fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  const hrs = (t) => (new Date(t) - new Date(pts[0].t)) / 36e5;  // hours since the first point
  const xNow = pad + hrs(state.data.now) * dx * (opts.perDay || 24) / 24;
  if (xNow > pad && xNow < W - pad) {  // past (model estimate) | forecast, placed by time
    svg.append(svgEl("line", { x1: xNow, x2: xNow, y1: m.t - 4, y2: m.t + ih, class: "divider" }),
      svgText({ x: xNow - 4, y: m.t + 6, "text-anchor": "end" }, "past"), svgText({ x: xNow + 4, y: m.t + 6 }, "forecast"));
  }
  const xh = svgEl("line", { class: "xhair", y1: m.t, y2: m.t + ih, visibility: "hidden" });
  const hit = svgEl("rect", { class: "hit", x: 0, y: m.t, width: W, height: ih });
  svg.append(xh, hit);
  hit.addEventListener("pointermove", (ev) => {
    const i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - svg.getBoundingClientRect().left - pad) / dx)));
    xh.setAttribute("x1", X(i)); xh.setAttribute("x2", X(i)); xh.setAttribute("visibility", "visible");
    showTip(ev, opts.tipHead ? opts.tipHead(pts[i]) :
      new Date(pts[i].t).toLocaleString(undefined, { weekday: "short", hour: "numeric", month: "short", day: "numeric" }), opts.tip(pts[i]));
  });
  hit.addEventListener("pointerleave", () => { xh.setAttribute("visibility", "hidden"); hideTip(); });
  const s0 = pts.findIndex((p) => p.t >= `${state.data.today}T06:00`);  // open at the start of today
  sc.scrollLeft = s0 > 0 ? X(s0) - pad : 0;
}

function levelCharts(d, e) {
  const hours = d.hourly;
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

function plumeChart(d, e) {  // snow and rain per 12 h period (6a-6p, 6p-6a): forecast lines with spread bands
  const p = d.plume[e];
  const pts = p.t.map((t, i) => ({ t, i }));
  const kinds = [["snow", "var(--q-snow)", "Snow", 1], ["rain", "var(--q-rain)", "Rain", 2]];
  const { max, step } = niceMax(Math.max(2, ...p.snow["0.95"], ...p.rain["0.95"]));
  const q = (k, s) => (x) => p[k][s][x.i];
  timeChart($("#plume"), pts, {
    perDay: 2, tick: (x) => x.t.endsWith("T06:00"), margin: { r: 12 },
    y: { max, step, fmt: (v) => `${v}"` },
    bands: kinds.flatMap(([k, color]) => [{ lo: q(k, "0.05"), hi: q(k, "0.95"), color, opacity: 0.1 },
                                         { lo: q(k, "0.25"), hi: q(k, "0.75"), color, opacity: 0.2 }]),
    lines: kinds.map(([k, color]) => ({ get: q(k, "fc"), color })),
    tipHead: (x) => { const dl = dayLabel(x.t.slice(0, 10)); return `${dl.dow} ${dl.mon} ${dl.dom} · ${x.t.endsWith("T18:00") ? "6pm–6am" : "6am–6pm"}`; },
    tip: (x) => kinds.flatMap(([k, color, name, dp]) => [
      { name, value: `${p[k].fc[x.i].toFixed(dp)}"`, color },
      ...(p[k]["0.95"][x.i] > p[k]["0.05"][x.i] ? [{ name: "middle 50% · 90%",
        value: `${p[k]["0.25"][x.i].toFixed(dp)}–${p[k]["0.75"][x.i].toFixed(dp)} · ${p[k]["0.05"][x.i].toFixed(dp)}–${p[k]["0.95"][x.i].toFixed(dp)}"` }] : []),
    ]),
  });
  $("#plume-sub").textContent = `${ELEV_NAME[e].toLowerCase()} · per 12 h (6am–6pm, 6pm–6am)`;
  legend("#plume-legend", [
    { label: "Snow (in)", color: "var(--q-snow)" },
    { label: "Rain (in of water)", color: "var(--q-rain)" },
    { label: "Middle 50%", color: "var(--text-secondary)", kind: "key-band" },
    { label: "Middle 90%", color: "var(--text-secondary)", kind: "key-band light" },
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
