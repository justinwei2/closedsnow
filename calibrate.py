"""One-time setup per resort: locate (snap points to terrain, pick a gauge and the models with data
there) -> calibrate (precip bias factors, weights and error quantiles from two winters of
as-issued forecasts vs the gauge). Resumable; sleeps through Open-Meteo rate limits.

    python3 calibrate.py [resort ...] [--force] # resorts not set up yet (--force: recalibrate them)
    python3 calibrate.py alta --out o           # CI: one resort -> o/{resorts,calibration}.json
    python3 calibrate.py --plan [resort ...] [--force]  # CI: resorts still to set up (job matrix)
    python3 calibrate.py --merge DIR            # CI: merge DIR/*/ into data/, borrowed defaults, CALIBRATION.md
"""
import glob, json, math, os, sys, time
from datetime import date, datetime, timedelta
import sources as src
from resorts import (BASE_MODELS, CATALOG, COUNTRIES, DATA, ELEVS, FT, HERE, IN, MANUAL, SEASONS,
                     Calibration, Gauge, ModelCal, Point, Resort, by_country, load_calibration, load_resorts)

STEP = 0.003                        # deg, ~300 m DEM search grid
FACTOR_MIN, FACTOR_MAX = 0.4, 2.0   # beyond this the model/cell isn't representative; cap, let weight fall
RATIO_BINS = [(10, 25), (25, 50), (50, 1e9)]  # forecast storm size, mm liquid per 72 h
WET = 10.0                          # mm/72 h: only real storms feed the error distribution
STORM_MM = 2.5                      # gauge mm/day: a "storm day" for the temperature bias
QS = ("0.05", "0.25", "0.5", "0.75", "0.95")


# ---------------------------------------------------------------- locate

def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def snap(lat, lon, ft, radius_km):
    """Point within radius whose DEM elevation is closest to ft (ties -> nearest the guess)."""
    n = int(radius_km / 111 / STEP) + 1
    pts = [(lat + dy * STEP, lon + dx * STEP / math.cos(math.radians(lat))) for dy in range(-n, n + 1) for dx in range(-n, n + 1)]
    pts = [p for p in pts if km(lat, lon, *p) <= radius_km]
    el = src.elevations(pts)
    i = min(range(len(pts)), key=lambda i: (round(abs(el[i] - ft) / 50), km(lat, lon, *pts[i])))
    return pts[i][0], pts[i][1], el[i]


def mid_point(b, s):
    """Point on the base->summit line about 55% of the way up."""
    target = b[2] + 0.55 * (s[2] - b[2])
    pts = [(b[0] + (s[0] - b[0]) * k / 24, b[1] + (s[1] - b[1]) * k / 24) for k in range(4, 21)]
    el = src.elevations(pts)
    i = min(range(len(pts)), key=lambda i: abs(el[i] - target))
    return pts[i][0], pts[i][1], el[i]


_stations = {}


def snotel():
    if "snotel" not in _stations:
        _stations["snotel"] = [s for s in src.snotel_stations()
                               if (s.get("endDate") or "2100") >= date.today().isoformat()]  # active only
    return _stations["snotel"]


def nearest_snotel(lat, lon, lo_ft, hi_ft):
    """Closest active SNOTEL gauge within 25 km, preferring the lift-served elevation band."""
    cands = []
    for s in snotel():
        d = km(lat, lon, s["latitude"], s["longitude"])
        if d <= 25 and lo_ft - 1500 <= s["elevation"] <= hi_ft + 500:
            cands.append((d + (0 if lo_ft <= s["elevation"] <= hi_ft else 4), s))
    if not cands:
        return None
    s = min(cands, key=lambda c: c[0])[1]
    return Gauge("snotel", s["stationTriplet"], f"{s['name'].title()} SNOTEL", round(s["elevation"]), s["latitude"], s["longitude"])


def nearest_bcasws(lat, lon, lo_ft, hi_ft):
    """Closest active BC snow pillow with a precipitation gauge, same rules as nearest_snotel."""
    if "bc" not in _stations:
        j = src.get_json("https://openmaps.gov.bc.ca/geo/pub/wfs", dict(
            service="WFS", version="2.0.0", request="GetFeature", outputFormat="json", srsName="EPSG:4326",
            typeName="WHSE_WATER_MANAGEMENT.SSL_SNOW_ASWS_STNS_SP"))
        with_pc = set(src.get_json(src.BC_ASWS + "PC.csv", parse=lambda fh: [c.split(" ")[0] for c in fh.readline().decode().split(",")]))
        _stations["bc"] = [f["properties"] for f in j["features"]
                           if f["properties"]["STATUS"] == "Active" and f["properties"]["ELEVATION"]
                           and f["properties"]["LOCATION_ID"] in with_pc]
    cands = []
    for s in _stations["bc"]:
        d, ft = km(lat, lon, s["LATITUDE"], s["LONGITUDE"]), s["ELEVATION"] / FT
        if d <= 25 and lo_ft - 1500 <= ft <= hi_ft + 500:
            cands.append((d + (0 if lo_ft <= ft <= hi_ft else 4), s, ft))
    if not cands:
        return None
    _, s, ft = min(cands, key=lambda c: c[0])
    return Gauge("bcasws", s["LOCATION_ID"], f"{s['LOCATION_NAME']} (BC snow pillow)", round(ft), s["LATITUDE"], s["LONGITUDE"])


def usable_models(lat, lon):
    ok = []
    for m in BASE_MODELS:
        try:
            if src.has_model(lat, lon, m):
                ok.append(m)
        except src.QuotaExceeded:
            raise  # never record a model as missing just because we were rate limited
        except RuntimeError as e:
            if "HTTP 4" not in str(e):  # only "no data here" (4xx) means the model is absent
                raise
        time.sleep(0.4)
    if len(ok) < 3:
        raise RuntimeError(f"only {ok} usable at {lat},{lon}; refusing to record (rerun later)")
    return ok


def locate(key):
    name, country, tz, b, s = CATALOG[key]
    man = MANUAL.get(key, {})
    if "mid" in man:  # hand-verified points
        base, summit, mid = b[:3], s[:3], man["mid"][:3]
        mlabel = man["mid"][3]
        dem = dict(zip(ELEVS, src.elevations([base[:2], mid[:2], summit[:2]])))
    else:
        sb = snap(*s[:3], 2.0)
        if abs(sb[2] - s[2]) > 600:  # rough guess too far off: search wider
            sb = snap(*s[:3], 4.0)
        bb = snap(*b[:3], 1.2)
        base, summit = (bb[0], bb[1], b[2]), (sb[0], sb[1], s[2])
        mp = mid_point(base, summit)
        mid, mlabel = (mp[0], mp[1], round(mp[2] / 100) * 100), "Mid-mountain"
        dem = dict(base=bb[2], mid=mp[2], summit=sb[2])
    pt = lambda p, label: Point(round(p[0], 4), round(p[1], 4), p[2], label)
    gauge = (Gauge(*man["gauge"]) if man.get("gauge") else
             nearest_snotel(mid[0], mid[1], b[2], s[2]) if country == "US" else nearest_bcasws(mid[0], mid[1], b[2], s[2]))
    r = Resort(key, name, country, tz, pt(base, b[3]), pt(mid, mlabel), pt(summit, s[3]), gauge,
               usable_models(summit[0], summit[1]), {k: round(v) for k, v in dem.items()}, man.get("afd_office"))
    warn = [f"{e} DEM {r.dem_ft[e]} vs {r.point(e).ft}" for e in ("base", "summit") if abs(r.dem_ft[e] - r.point(e).ft) > 600]
    print(f"{key:15s} base {r.dem_ft['base']:>6}/{b[2]:<6} mid {mid[2]:>6} summit {r.dem_ft['summit']:>6}/{s[2]:<6} "
          f"obs={gauge.name if gauge else '-'} models={len(r.models)} {'WARN ' + '; '.join(warn) if warn else ''}")
    return r


# ---------------------------------------------------------------- calibrate

def std_tz(tz):
    """Fixed-offset zone for the resort's standard time (station days ignore DST)."""
    return f"Etc/GMT{-int(src.std_offset_h(tz)):+d}"


def hindcast(r, model, start, end):
    """As-issued forecasts at the gauge (cached; empty if no history): ({lead: {date: mm}}, {date: C})."""
    def fetch():
        try:
            j = src.previous_runs(r.gauge.lat, r.gauge.lon, r.gauge.ft, model, start, end, std_tz(r.tz))
        except RuntimeError as e:
            if "HTTP 4" not in str(e):
                raise
            j = {}  # no history for this model/winter here: skip it, don't fail the resort
        time.sleep(4)  # stay well inside the free-tier per-minute limit
        return j
    h = src.cached(f"hind_{r.key}_{model}_{start}.json", fetch).get("hourly") or {}
    def daily(var, agg, shift_h=0):  # precip is stamped at the end of its hour: 01:00..00:00 is one day
        per = {}
        for t, v in zip(h.get("time", []), h.get(var, [])):
            per.setdefault((datetime.fromisoformat(t) - timedelta(hours=shift_h)).date(), []).append(v)
        return {d: agg(vs) for d, vs in per.items() if len(vs) == 24 and None not in vs}
    precip = {n: daily(f"precipitation_previous_day{n}", sum, 1) for n in range(1, 8)}
    return precip, daily("temperature_2m_previous_day1", lambda vs: sum(vs) / len(vs))


def windows72(fc, obs):
    """72 h windows starting day D: lead-1 day D + lead-2 day D+1 + lead-3 day D+2 -> (fc, obs)."""
    pairs = {}
    for d in fc[1]:
        a, b = d + timedelta(1), d + timedelta(2)
        o = [obs.get(d + timedelta(i)) for i in range(3)]
        if a in fc[2] and b in fc[3] and None not in o:
            pairs[d] = (fc[1][d] + fc[2][a] + fc[3][b], sum(o))
    return pairs


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = q * (len(xs) - 1)
    lo = int(i)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (i - lo)


def calibrate_one(r):
    obs, obs_t = {}, {}  # gauge precip (mm) and mean temperature (C) by day
    for s, e in SEASONS:
        st = src.StationSeries.fetch(r.gauge, s, (date.fromisoformat(e) + timedelta(3)).isoformat(), r.tz)
        print(f"    gauge {s[:4]}-{e[2:4]}: {len(st.days)} days", flush=True)
        obs.update({date.fromisoformat(k): v for k, v in st.precip_mm().items()})
        obs_t.update({date.fromisoformat(d.date): (d.temp_f - 32) / 1.8 for d in st.days if d.temp_f is not None})
    cal, pairs_by, ext, temps = {}, {}, {}, {}
    for m in r.models:
        pairs, lead_pairs = {}, []
        for s, e in SEASONS:
            fc, t = hindcast(r, m, s, e)
            pairs.update(windows72(fc, obs))
            lead_pairs += [(v, obs[d]) for n in range(4, 8) for d, v in fc[n].items() if d in obs]
            for d, v in t.items():
                temps.setdefault(d, []).append(v)
        print(f"    {m}: {len(pairs)} 72 h windows{'' if len(pairs) >= 60 else ' (too few: skipped)'}", flush=True)
        if len(pairs) < 60:
            continue
        f = sum(p[0] for p in pairs.values()); o = sum(p[1] for p in pairs.values())
        factor = min(FACTOR_MAX, max(FACTOR_MIN, o / f if f else 1.0))
        mae = lambda k: sum(abs(p[0] * k - p[1]) for p in pairs.values()) / len(pairs)
        cal[m] = ModelCal(round(factor, 3), 0.0, round(mae(factor), 2), round(mae(1.0), 2), len(pairs))
        pairs_by[m] = pairs
        if len(lead_pairs) >= 60:  # daily skill at lead days 4-7 (short-range models don't get here)
            ext[m] = sum(abs(v * factor - o) for v, o in lead_pairs) / len(lead_pairs)
    w = {m: 1 / c.mae72 ** 2 for m, c in cal.items()}  # inverse-MSE-ish weights
    w_ext = {m: 1 / max(v, 0.1) ** 2 for m, v in ext.items()}
    for m in cal:
        cal[m].weight = round(w[m] / sum(w.values()), 3)
        cal[m].weight_ext = round(w_ext[m] / sum(w_ext.values()), 3) if m in w_ext else 0.0
        cal[m].mae_ext = round(ext[m], 2) if m in ext else None
    blend = []  # renormalised over models with data that day
    for d in set().union(*pairs_by.values()):
        num = sum(w[m] * p[d][0] * cal[m].factor for m, p in pairs_by.items() if d in p)
        den = sum(w[m] for m, p in pairs_by.items() if d in p)
        blend.append((num / den, next(p[d][1] for p in pairs_by.values() if d in p)))
    ratios = [o / f for f, o in blend if f >= WET]
    bins = []  # relative error shrinks for big forecast storms, so also bin by size
    for lo, hi in RATIO_BINS:
        rb = [o / f for f, o in blend if lo <= f < hi]
        if len(rb) >= 15:
            bins.append(dict(lo_mm=lo, hi_mm=hi, n=len(rb), q={q: round(quantile(rb, float(q)), 3) for q in QS}))
    # temperature: station minus the models' mean daily temperature at the gauge (lead 1)
    diffs = [(obs_t[d] - sum(v) / len(v), obs.get(d, 0) >= STORM_MM) for d, v in temps.items() if d in obs_t]
    wet = [x for x, storm in diffs if storm]
    bias = sum(x for x, _ in diffs) / len(diffs) if diffs else None
    return Calibration(cal, station=r.gauge.name, n_windows=len(blend),
                       blend_mae72=round(sum(abs(a - b) for a, b in blend) / len(blend), 2),
                       ratio_q={q: round(quantile(ratios, float(q)), 3) for q in QS}, ratio_bins=bins, n_wet=len(ratios),
                       temp_bias_c=round(bias, 2) if bias is not None else None,
                       temp_bias_wet_c=round(sum(wet) / len(wet), 2) if len(wet) >= 15 else None, temp_n_wet=len(wet),
                       temp_mae_c=[round(sum(abs(x) for x, _ in diffs) / len(diffs), 2),
                                   round(sum(abs(x - bias) for x, _ in diffs) / len(diffs), 2)] if diffs else None)


def raw(r):
    """Not calibrated yet (or no usable gauge): the raw models, equal weights, no historical error."""
    return Calibration({m: ModelCal(1.0, round(1 / len(r.models), 3)) for m in r.models}, uncalibrated=True)


# ---------------------------------------------------------------- orchestration

def save(resorts, cal):
    src.write_json(os.path.join(DATA, "resorts.json"), {k: r.to_json() for k, r in resorts.items()}, indent=1)
    src.write_json(os.path.join(DATA, "calibration.json"), {k: c.to_json() for k, c in cal.items()}, indent=1)


def todo(k, resorts, cal, force=False):
    """Needs locating or calibrating (force: recalibrate)."""
    return force or k not in resorts or resorts[k].gauge is None or k not in cal or not cal[k].calibrated


def plan(only=None, force=False):
    """Resorts still to set up, as a GitHub Actions matrix (one job each)."""
    resorts, cal = load_resorts(), load_calibration()
    print(json.dumps([k for k in (only or CATALOG) if todo(k, resorts, cal, force)]))


def run(out=None, force=False, only=None):
    """Locate and calibrate resorts that aren't done (force: recalibrate them too); always save
    progress. only: specific resort keys. Gauge-less resorts are located again."""
    keys = only or list(CATALOG)
    resorts, cal = load_resorts(), load_calibration()
    t0 = time.time()
    log = lambda i, k, msg: print(f"[{i}/{len(keys)} {time.time() - t0:5.0f}s] {k}: {msg}", flush=True)
    try:
        for i, k in enumerate(keys, 1):
            if k not in resorts or resorts[k].gauge is None:  # gauge-less: retry (new gauge networks)
                log(i, k, "locating points, gauge and models")
                try:
                    resorts[k] = locate(k)
                except src.QuotaExceeded:
                    raise
                except Exception as e:
                    print(f"  locate failed {k}: {e}", file=sys.stderr)
                save(resorts, cal)
            r = resorts.get(k)
            if r and r.gauge and (force or k not in cal or not cal[k].calibrated):
                log(i, k, f"calibrating against {r.gauge.name} ({len(r.models)} models x {len(SEASONS)} winters)")
                try:
                    cal[k] = calibrate_one(r)
                    print(k, "blend MAE72", cal[k].blend_mae72, {m: c.factor for m, c in cal[k].models.items()})
                except src.QuotaExceeded:
                    raise
                except Exception as e:
                    print(f"  calibration failed {k}: {e}", file=sys.stderr)
                save(resorts, cal)
            else:
                log(i, k, "already calibrated" if r and r.gauge else "no gauge: raw models")
    finally:  # hand back progress even on a crash or timeout
        if out:
            os.makedirs(out, exist_ok=True)
            json.dump({k: resorts[k].to_json() for k in keys if k in resorts}, open(os.path.join(out, "resorts.json"), "w"), indent=1)
            json.dump({k: cal[k].to_json() for k in keys if k in cal and cal[k].calibrated},
                      open(os.path.join(out, "calibration.json"), "w"), indent=1)
    print(f"setup: located {sum(k in resorts for k in keys)}/{len(keys)}, "
          f"calibrated {sum(k in cal and cal[k].calibrated for k in keys)}")


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def merge(d):
    """Merge per-resort outputs into data/; uncalibrated resorts get the raw models; report."""
    resorts, cal = load_resorts(), load_calibration()
    for part in sorted(glob.glob(os.path.join(d, "*", ""))):
        for k, v in _load(os.path.join(part, "resorts.json")).items():
            r = Resort.from_json(k, v)
            if r.gauge or k not in resorts or not resorts[k].gauge:  # never trade a gauge for none
                resorts[k] = r
        for k, v in _load(os.path.join(part, "calibration.json")).items():
            cal[k] = Calibration.from_json(v)
    resorts = {k: r for k, r in resorts.items() if k in CATALOG}
    cal = {k: c for k, c in cal.items() if k in resorts and c.calibrated}
    for k, r in resorts.items():
        cal.setdefault(k, raw(r))
    save(resorts, cal)
    write_report(resorts, cal)


def write_report(resorts, cal):
    """CALIBRATION.md: what's calibrated, against which gauge, and how well."""
    lines = ["# Calibration status", "",
             "Generated by `calibrate.py`. Each resort's model precipitation is bias-corrected against a",
             "nearby gauge using two winters (2024-26) of as-issued forecasts. Error = mean absolute error of",
             "72 h liquid precipitation for the blend, after correction. Resorts not calibrated yet run on",
             "the raw models with equal weights."]
    for country, keys in by_country(CATALOG):
        lines += ["", f"## {COUNTRIES[country]}", "",
                  "| Resort | Status | Gauge | Gauge elev | 72 h windows | Blend error (in liquid / 72 h) |",
                  "|---|---|---|---|---|---|"]
        for k in keys:
            r, c = resorts.get(k), cal.get(k)
            if not r:
                lines.append(f"| {CATALOG[k][0]} | not located yet | | | | |")
                continue
            g = r.gauge
            status = "calibrated" if c and c.calibrated else "pending (raw models for now)" if g else "raw models (no gauge)"
            err = f"{c.blend_mae72 / IN:.2f}" if c and c.calibrated else ""
            lines.append(f"| {r.name} | {status} | {g.name if g else '–'} | {f'{g.ft:,} ft' if g else '–'} | "
                         f"{c.n_windows if c and c.calibrated else ''} | {err} |")
    open(os.path.join(HERE, "CALIBRATION.md"), "w").write("\n".join(lines) + "\n")


if __name__ == "__main__":
    src.WAIT_ON_QUOTA = True
    a = sys.argv[1:]
    opt = lambda name: a[a.index(name) + 1] if name in a else None
    if "--merge" in a:
        merge(opt("--merge"))
    elif "--plan" in a:
        plan([k for k in a if k in CATALOG], force="--force" in a)
    else:
        only = [k for k in a if k in CATALOG]
        run(opt("--out"), force="--force" in a, only=only)
        if not opt("--out"):
            merge(os.path.join(HERE, "__none__"))
