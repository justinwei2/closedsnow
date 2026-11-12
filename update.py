"""Live forecast for each resort -> web/data/<resort>.json, plus the index and a freshness check.

Snow: per-resort bias-corrected, skill-weighted blend of deterministic models (calibrate.py).
Phase, snow level and snow-to-liquid ratio come from a consensus pressure-level profile.
Ranges average the quantiles of (a) the pooled bias-corrected ensembles and (b) the blend times
this resort's historical obs/forecast error for that storm size.

    python3 update.py [resort ...] [--scheduled] [--group G] [--no-index]
    python3 update.py --plan              # regions with due resorts (CI matrix)
    python3 update.py --check [--url SITE]
"""
import json, os, sys, time, traceback
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import conditions as cx
import sources as src
from resorts import CATALOG, ELEVS, ENSEMBLES, FT, GROUPS, HERE, IN, KMH_MPH, PROFILE_MODELS, LEVELS, RESORTS, SNOTEL_STATES, load_calibration

OUT = os.path.join(HERE, "web", "data")
PAST_DAYS = 7
TRIGGER_MODEL = "ecmwf_ifs025"  # refresh when its new run lands: it arrives last of the 6-hourly models
SAFETY_H = 7           # ...or when data is older than this anyway
MAX_AGE_H = 14         # freshness alarm: about 2 missed model cycles
QS = (0.05, 0.25, 0.5, 0.75, 0.95)  # middle 50% and middle 90%
HOURLY_OUT = ("precip_mm", "snow_in", "rain_in", "frac", "slr", "temp_f", "wind_mph", "gust_mph", "wind_dir", "rh", "cloud")
_discussions = {}  # one NWS discussion per office per run


def attempt(what, fn, default=None):
    """Optional data source: log and fall back on failure, but never swallow quota limits."""
    try:
        return fn()
    except src.QuotaExceeded:
        raise
    except Exception as e:
        print(f"  {what} failed: {e}", file=sys.stderr)
        return default


def soundings(grid, shift_c=0.0):
    """Per hour: consensus Profile (mean of the profile models) and each model's Profile.
    shift_c: calibrated storm-day temperature correction, applied to every level."""
    cons, per_model = [], {m: [] for m in PROFILE_MODELS}
    for i in range(len(grid.times)):
        stacks = []
        for m in PROFILE_MODELS:
            cols = [[grid.at(f"{v}_{p}hPa", m, i) for p in LEVELS]
                    for v in ("geopotential_height", "temperature", "relative_humidity", "wind_speed")]
            ok = all(None not in c for c in cols)
            if ok:
                cols[1] = [t + shift_c for t in cols[1]]
            per_model[m].append(cx.Profile(*cols) if ok else None)
            if ok:
                stacks.append(cols)
        cons.append(cx.Profile(*[[sum(s[k][j] for s in stacks) / len(stacks) for j in range(len(LEVELS))]
                                 for k in range(4)]) if stacks else None)
    return cons, per_model


def blend(grid, cal, now_i):
    """Bias-corrected, skill-weighted hourly precip blend (renormalised over models present) and
    each model's corrected series. Weights depend on lead time (days 1-3 vs 4+)."""
    precip, contrib = [], {m: [] for m in grid.models}
    for i in range(len(grid.times)):
        num = den = 0.0
        for m in grid.models:
            v = grid.at("precipitation", m, i)
            c = None if v is None else v * cal.models[m].factor
            contrib[m].append(c)
            w = cal.weight(m, (i - now_i) / 24)
            if c is not None and w:
                num += w * c; den += w
        precip.append(num / den if den else None)
    return precip, contrib


def hours_at(r, e, grid, cons, precip, temp_bias_c):
    z = r.point(e).ft * FT
    out = []
    for i, t in enumerate(grid.times):
        tc, w10, g10 = (grid.mean(v, i) for v in ("temperature_2m", "wind_speed_10m", "wind_gusts_10m"))
        tc = None if tc is None else tc + temp_bias_c
        h = cx.Hour(t, precip[i], None if tc is None else tc * 9 / 5 + 32, None if w10 is None else w10 * KMH_MPH,
                    None if g10 is None else g10 * KMH_MPH, grid.wind_dir(i), grid.mean("cloud_cover", i),
                    grid.mean("relative_humidity_2m", i))
        p = cons[i]
        if p is not None:
            at = p.at(z)
            h.frac, h.slr, h.warm_nose, h.tw_c = cx.snow_fraction(at["tw"]), cx.slr_at(p, z), p.warm_nose(z), at["tw"]
            if e == "summit":  # exposed ridge: free-air wind at summit height, not smoothed 10 m wind
                h.wind_mph = at["wind"] * KMH_MPH
                h.gust_mph = max(h.gust_mph or 0, h.wind_mph * cx.GUST_FACTOR)
            if h.warm_nose:  # refrozen sleet / ice pellets, not fluffy snow
                h.slr = 2.0
            if h.precip_mm is not None:
                h.snow_in = h.precip_mm * h.frac * h.slr / IN
                h.rain_in = h.precip_mm * (1 - h.frac) / IN
        out.append(h)
    return out


def wquantiles(vals_w, qs):
    vals_w = sorted(vals_w)
    tot = sum(w for _, w in vals_w)
    out, acc, j = {}, 0.0, 0
    for q in sorted(qs):
        while j < len(vals_w) and acc + vals_w[j][1] < q * tot:
            acc += vals_w[j][1]; j += 1
        out[q] = vals_w[min(j, len(vals_w) - 1)][0]
    return out


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def rnd(v):
    return round(v, 2) if isinstance(v, float) else v


DEPTH_KM, DEPTH_FT = 15, 800  # a gauge stands in for an elevation within this distance / height


def depths(r):
    """Latest gauge snow depth for base / mid / summit: the resort's gauge or the nearest active
    SNOTEL within DEPTH_KM and DEPTH_FT of that elevation; None where no gauge qualifies."""
    import calibrate  # station search helpers
    cands = [r.gauge] if r.gauge else []
    for st in SNOTEL_STATES.get(r.region, []):
        for s in calibrate._stations.setdefault(st, src.snotel_stations(st)):
            if (s["stationTriplet"].endswith(":SNTL") and (s.get("endDate") or "2100") >= date.today().isoformat()
                    and calibrate.km(r.mid.lat, r.mid.lon, s["latitude"], s["longitude"]) <= DEPTH_KM):
                cands.append(src.Gauge("snotel", s["stationTriplet"], f"{s['name'].title()} SNOTEL", round(s["elevation"]),
                                       s["latitude"], s["longitude"]))
    out, latest = {}, {}
    for e in ELEVS:
        pt = r.point(e)
        ok = [g for g in cands if abs(g.ft - pt.ft) <= DEPTH_FT]
        g = min(ok, key=lambda g: (abs(g.ft - pt.ft), calibrate.km(pt.lat, pt.lon, g.lat, g.lon)), default=None)
        if g is None:
            out[e] = None
            continue
        if g.id not in latest:
            end = date.today() + timedelta(1)
            latest[g.id] = src.StationSeries.fetch(g, (end - timedelta(5)).isoformat(), end.isoformat()).latest("depth")
        out[e] = None if latest[g.id] is None else dict(depth_in=latest[g.id], station=g.name, ft=g.ft)
    return out


def process(r, cal):
    print("resort", r.key)
    run = attempt("model run metadata", latest_run)
    local = ZoneInfo(r.tz)
    models = [m for m in r.models if m in cal.models]
    grids = {e: src.forecast(r.point(e), r.tz, models, PAST_DAYS) for e in ELEVS}
    times = grids["summit"].times
    cons, per_model = soundings(src.profiles(r.summit, r.tz, PAST_DAYS), cal.profile_shift_c)
    ens = {m: attempt(f"ensemble {m}", lambda m=m: src.ensemble(r.summit, r.tz, m)) for m in ENSEMBLES}
    alerts = attempt("alerts", lambda: [asdict(a) for a in src.nws_alerts(r.summit)]) if r.us else []
    office = (r.afd_office or attempt("NWS office", lambda: src.nws_office(r.summit))) if r.us else None
    if office and office not in _discussions:
        _discussions[office] = attempt("discussion", lambda: asdict(src.nws_discussion(office)))
    afd = _discussions.get(office) if office else None
    obs = None
    if r.gauge:
        end = date.today() + timedelta(1)
        st = attempt("obs", lambda: src.StationSeries.fetch(r.gauge, (end - timedelta(15)).isoformat(), end.isoformat()))
        if st:
            obs = dict(station=r.gauge.name, elev_ft=r.gauge.ft, days=st.daily(), depth_in=st.latest("depth"),
                       swe_in=st.latest("swe"))
    depth = attempt("snow depths", lambda: depths(r))
    now = datetime.now(local).strftime("%Y-%m-%dT%H:00")
    now_i = next((i for i, t in enumerate(times) if t >= now), len(times) - 1)

    # ---- hourly per elevation
    hours, model_snow = {}, {}
    for e in ELEVS:
        assert grids[e].times == times, "time axes differ"
        precip, contrib = blend(grids[e], cal, now_i)
        hrs = hours[e] = hours_at(r, e, grids[e], cons, precip, cal.temp_bias_c or 0.0)
        model_snow[e] = {m: [None if c[i] is None or hrs[i].frac is None else c[i] * hrs[i].frac * hrs[i].slr / IN
                             for i in range(len(times))] for m, c in contrib.items()}
    levels = []
    for i, p in enumerate(cons):
        sl = [x for x in (pm[i].snow_level() for pm in per_model.values() if pm[i] is not None) if x is not None]
        lv, fz = (p.snow_level() if p else None), (p.freezing_level() if p else None)
        levels.append(dict(snow_level_ft=None if lv is None else lv / FT, freezing_level_ft=None if fz is None else fz / FT,
                           snow_level_lo_ft=min(sl) / FT if sl else None, snow_level_hi_ft=max(sl) / FT if sl else None))

    # ---- ensembles (summit point), bias-corrected, each ensemble weighted equally
    members = []
    for m, g in ens.items():
        series = g.members() if g else []
        f = cal.models[ENSEMBLES[m]].factor if ENSEMBLES[m] in cal.models else 1.0
        members += [(1.0 / len(series), {t: v * f for t, v in zip(g.times, s) if v is not None}) for s in series]

    def window(i0, i1, e):
        """Totals for hours [i0, i1) at elevation e; ranges only for future windows."""
        i0, i1 = max(0, i0), min(len(times), i1)
        hrs = hours[e][i0:i1]
        snow = sum(h.snow_in or 0 for h in hrs)
        liq = sum(h.precip_mm or 0 for h in hrs)
        res = dict(p50=snow, liquid_in=liq / IN, rain_in=sum(h.rain_in or 0 for h in hrs))
        if i0 < now_i or i1 <= i0:
            return res
        s_liq = sum(h.precip_mm or 0 for h in hours["summit"][i0:i1])
        # snow per mm of liquid; in a dry blend use the typical hourly ratio so ensemble wet tails show
        ratio = snow / liq if liq > 0.5 else (mean([h.frac * h.slr / IN for h in hrs if h.frac is not None]) or 0)
        scale = liq / s_liq if s_liq > 0.5 else 1.0  # ensembles are at the summit point
        ts = times[i0:i1]
        vals = []
        for w, mm in members:
            got = [mm[t] for t in ts if t in mm]
            if got and len(got) >= 0.9 * len(ts):
                vals.append((sum(got) * len(ts) / len(got) * scale, w))
        eq = cal.error_quantiles(s_liq * 72 / max(1, i1 - i0), QS)  # bins were calibrated on 72 h totals
        ensq = wquantiles(vals, QS) if vals else None
        for q in QS:
            emp = snow * eq[q]
            if ensq and ensq[0.5] > 0 and snow > 0:  # ensemble spread *shape*, centred on the blend
                ens = snow * ensq[q] / ensq[0.5]
            else:  # blend or ensemble median dry: absolute ensemble amounts
                ens = ensq[q] * ratio if ensq else emp
            res[f"p{round(q * 100)}"] = (ens + emp) / 2  # quantile averaging of the two distributions
        res["p50"] = snow
        for lo, hi in (("p5", "p95"), ("p25", "p75")):  # the ranges always include the forecast
            res[lo], res[hi] = min(res[lo], snow), max(res[hi], snow)
        return res

    # ---- ski days: 6am to 6am, split into morning (6-12), afternoon (12-6pm) and night (6pm-6am)
    def quality_of(hrs, min_liq=1.0, wet_mm=0.2):
        wet = [h for h in hrs if (h.precip_mm or 0) > wet_mm and h.frac is not None]
        liq = sum(h.precip_mm for h in wet)
        if liq <= 0:
            return {}
        snow_liq = sum(h.precip_mm * h.frac for h in wet)
        slr = sum(h.precip_mm * h.frac * h.slr for h in wet) / snow_liq if snow_liq > 0 else None
        nose = any(h.warm_nose and h.precip_mm > 0.5 for h in wet)
        q = cx.quality(slr or 10, snow_liq / liq, nose) if liq > min_liq else None
        return dict(slr=slr, quality=q) | ({"sleet": True} if q == "mixed" and nose else {})

    def precip_chance(span):
        """Share of ensemble members (weighted) with >= 0.01" liquid in the span, at the summit."""
        ts = [times[i] for i in span]
        hit = tot = 0.0
        for w, mm in members:
            if all(t in mm for t in ts):
                tot += w
                hit += w if sum(mm[t] for t in ts) >= 0.254 else 0
        return round(100 * hit / tot) if tot else None

    def half(name, span, e, d):
        """One 12-hour row of the daily overview (day 6am-6pm / night 6pm-6am)."""
        hrs = [hours[e][i] for i in span]
        w = window(max(span.start, now_i) if span.stop > now_i else span.start, span.stop, e)
        temps = [h.temp_f for h in hrs if h.temp_f is not None]
        feels = [cx.wind_chill(h.temp_f, h.wind_mph) for h in hrs if h.temp_f is not None]
        winds = [h.wind_mph for h in hrs if h.wind_mph is not None]
        gusts = [h.gust_mph for h in hrs if h.gust_mph is not None]
        dirs = [h.wind_dir for h in hrs if h.wind_dir is not None]
        wet = [levels[i]["snow_level_ft"] for i in span if (hours["summit"][i].precip_mm or 0) > 0.2 and levels[i]["snow_level_ft"] is not None]
        sls = [levels[i]["snow_level_ft"] for i in span if levels[i]["snow_level_ft"] is not None]
        fz = [levels[i]["freezing_level_ft"] for i in span if levels[i]["freezing_level_ft"] is not None]
        out = dict(name=name, past=span.stop <= now_i, snow_in=w["p50"], p25=w.get("p25"), p75=w.get("p75"),
                   liquid_in=w["liquid_in"], rain_in=w["rain_in"], precip_chance=precip_chance(span),
                   temp_f=(max if name == "day" else min)(temps) if temps else None,
                   feels_f=mean(feels), rh=mean([h.rh for h in hrs if h.rh is not None]),
                   wind_mph=max(winds, default=None), gust_mph=max(gusts, default=None),
                   wind_dir=cx.compass(cx.circ_mean(dirs)) if dirs else None,
                   cloud=mean([h.cloud for h in hrs if h.cloud is not None]),
                   snow_level_ft=mean(wet) or mean(sls), freezing_level_ft=mean(fz),
                   **quality_of(hrs, 0.0, 0.0))
        out["alerts"] = cx.half_alerts(out, hrs, d, name, [hours["summit"][i] for i in span])
        return out

    today = (datetime.fromisoformat(now) - timedelta(hours=6)).strftime("%Y-%m-%d")  # current ski day
    starts = {t[:10]: i for i, t in enumerate(times) if t.endswith("T06:00")}
    obs_by_day = {x["date"]: x for x in (obs or {}).get("days", [])}
    daily = []
    for d, first in sorted(starts.items()):
        i1 = min(first + 24, len(times))
        i0 = max(first, now_i) if d == today else first  # today: the rest of the ski day
        entry = dict(date=d, past=d < today)
        for e in ELEVS:
            hrs = hours[e][first:i1]
            b = window(first if d < today else i0, i1, e)
            b.update(quality_of(hrs))
            b["periods"] = [dict(snow_in=sum(h.snow_in or 0 for h in part), rain_in=sum(h.rain_in or 0 for h in part),
                                 **quality_of(part, 0.0, 0.0)) for part in (hrs[:6], hrs[6:12], hrs[12:24])]
            b["halves"] = [half(name, range(a, min(z, i1)), e, d) for name, a, z in
                           (("day", first, first + 12), ("night", first + 12, first + 24)) if a < i1]
            temps = [h.temp_f for h in hrs if h.temp_f is not None]
            lift = cx.lift_hours(hrs, d)
            winds = [h.wind_mph for h in lift if h.wind_mph is not None]
            gusts = [h.gust_mph for h in lift if h.gust_mph is not None]
            dirs = [h.wind_dir for h in lift if h.wind_dir is not None]
            b.update(hi_f=max(temps, default=None), lo_f=min(temps, default=None), wind_mph=max(winds, default=None),
                     gust_mph=max(gusts, default=None), wind_dir=cx.compass(cx.circ_mean(dirs)) if dirs else None,
                     models={m: (sum(v for v in s[first:i1] if v is not None) if any(v is not None for v in s[first:i1]) else None)
                             for m, s in model_snow[e].items()})
            entry[e] = b
        wet_i = [i for i in range(first, i1) if (hours["summit"][i].precip_mm or 0) > 0.2]
        sl_wet = [levels[i]["snow_level_ft"] for i in wet_i if levels[i]["snow_level_ft"] is not None]
        sls = [levels[i]["snow_level_ft"] for i in range(first, i1) if levels[i]["snow_level_ft"] is not None]
        fz = [levels[i]["freezing_level_ft"] for i in range(first, i1) if levels[i]["freezing_level_ft"] is not None]
        entry.update(snow_level_ft=mean(sl_wet) if sl_wet else mean(sls), snow_level_max_ft=max(sl_wet, default=None),
                     freezing_level_ft=mean(fz))
        if d in obs_by_day:  # station days are calendar days
            o = obs_by_day[d]
            entry["obs"] = dict(new_snow_in=o.get("new_snow_in"), precip_in=o.get("precip_in"), swe_in=o.get("swe_in"))
        entry["report"], entry["wind_hold"] = cx.report(d, entry, hours, {e: r.point(e).ft for e in ELEVS})
        daily.append(entry)

    # ---- summary strip: last 7 days | last 24 hours | next 1-5 | next 6-10 (ski days)
    day_start = lambda offset: starts.get((datetime.fromisoformat(today) + timedelta(offset)).strftime("%Y-%m-%d"))
    span = lambda a, b: (day_start(a) or 0, day_start(b) or len(times))
    summary = {}
    for e in ELEVS:
        last24 = window(now_i - 24, now_i, e)
        summary[e] = dict(past7=window(*span(-7, 0), e), last24=last24,
                          next1_5=window(now_i, span(5, 5)[0], e), next6_10=window(*span(5, 10), e))
    if obs:
        done = [x for x in obs["days"] if x["date"] < now[:10]]
        for key, n in (("last24", 1), ("past7", 7)):
            vals = [x["new_snow_in"] for x in done[-n:] if x.get("new_snow_in") is not None]
            for e in ELEVS:
                summary[e][key]["station_in"] = sum(vals) if vals else None

    # ---- snow and rain per 12 h (daytime 6a-6p / nighttime 6p-6a), last 7 + next 10 days: forecast +
    # ensemble spread (future only; each member's precip split
    # with the forecast's hourly phase and snow ratio); bands widened to include the forecast
    today_minus = lambda n: (datetime.fromisoformat(today) - timedelta(n)).strftime("%Y-%m-%d")
    plume = {}
    for e in ELEVS:
        hrs = hours[e]
        out = {k: {q: [] for q in ("fc", *map(str, QS))} for k in ("snow", "rain")}
        span = [d for d in sorted(starts) if today_minus(7) <= d < today_minus(-10)]
        bins = [range(a, min(a + 12, len(times))) for d in span for a in (starts[d], starts[d] + 12) if a < len(times)]
        for b in bins:
            s_liq = sum(hours["summit"][i].precip_mm or 0 for i in b)
            liq = sum(hrs[i].precip_mm or 0 for i in b)
            scale = liq / s_liq if s_liq > 0.1 else 1.0  # ensembles are at the summit point
            fc = dict(snow=sum(hrs[i].snow_in or 0 for i in b), rain=sum(hrs[i].rain_in or 0 for i in b))
            vals = dict(snow=[], rain=[])
            for w, mm in members:
                if not all(times[i] in mm for i in b):
                    continue  # member past its horizon
                ok = [i for i in b if hrs[i].frac is not None]
                vals["snow"].append((sum(mm[times[i]] * scale * hrs[i].frac * hrs[i].slr / IN for i in ok), w))
                vals["rain"].append((sum(mm[times[i]] * scale * (1 - hrs[i].frac) / IN for i in ok), w))
            for k in ("snow", "rain"):
                q = wquantiles(vals[k], QS) if vals[k] else {p: fc[k] for p in QS}
                out[k]["fc"].append(round(fc[k], 2))
                for p in QS:
                    out[k][str(p)].append(round(min(q[p], fc[k]) if p < 0.5 else max(q[p], fc[k]) if p > 0.5 else q[p], 2))
        plume[e] = dict(t=[times[b[0]] for b in bins], **out)

    hourly = [dict(t=t, precip_chance=precip_chance(range(i, i + 1)) if i >= now_i else None, **{k: rnd(v) for k, v in levels[i].items()},
                   **{e: {k: rnd(getattr(hours[e][i], k)) for k in HOURLY_OUT} for e in ELEVS})
              for i, t in enumerate(times) if now_i - 24 * 7 <= i < now_i + 24 * 10]  # charts: last 7 + next 10 days
    return dict(
        key=r.key, name=r.name, region=r.region, model_run=run, now=now, today=today,
        updated=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        points={e: dict(requested=asdict(r.point(e)), grid=dict(lat=grids[e].lat, lon=grids[e].lon)) for e in ELEVS},
        calibration=dict(station=cal.station, uncalibrated=cal.uncalibrated, peers=cal.peers, peers_scope=cal.peers_scope,
                         temp_bias_c=cal.temp_bias_c, profile_shift_c=cal.profile_shift_c,
                         models={m: asdict(v) for m, v in cal.models.items()}),
        daily=daily, hourly=hourly, summary=summary, plume=plume, obs=obs, depth=depth, afd=afd, alerts=alerts,
    )


# ---------------------------------------------------------------- files

def latest_run():
    """Init time of the trigger model's newest available run (Open-Meteo metadata, not an API call)."""
    j = src.get_json(f"https://api.open-meteo.com/data/{TRIGGER_MODEL}/static/meta.json")
    return datetime.fromtimestamp(j["last_run_initialisation_time"], timezone.utc).isoformat(timespec="minutes")


def saved(key):
    try:
        return json.load(open(os.path.join(OUT, f"{key}.json")))
    except Exception:
        return None


def updated_at(key):
    d = saved(key)
    return d["updated"] if d else None


def due(key, run):
    """Needs a refresh: no data, a newer model run, older than SAFETY_H, or SKISITE_FORCE set."""
    d = saved(key)
    if not d or os.environ.get("SKISITE_FORCE"):
        return True
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["updated"])).total_seconds() / 3600
    return age > SAFETY_H or (run is not None and d.get("model_run") != run)


def plan():
    """Region groups with due resorts, as a GitHub Actions matrix (stagger each job's start)."""
    run = attempt("model run metadata", latest_run)
    groups = [g for g in GROUPS if any(due(k, run) for k, r in RESORTS.items() if r.region in GROUPS[g])]
    print(json.dumps([dict(group=g, delay=90 * i) for i, g in enumerate(groups)]))


def write_index():
    cal = load_calibration()
    idx = []
    for key, (name, region, *_) in CATALOG.items():  # catalog order = grouped by region for search
        r, c = RESORTS.get(key), cal.get(key)
        if not r:  # not set up yet: listed in the coverage appendix, hidden from search
            idx.append(dict(key=key, name=name, region=region, calibration="not set up yet", located=False, updated=None))
            continue
        status = "calibrated" if c and c.calibrated else "pending" if r.gauge else "regional default"
        idx.append(dict(key=key, name=r.name, region=r.region, calibration=status,
                        station=r.gauge.name if r.gauge else None, station_ft=r.gauge.ft if r.gauge else None,
                        blend_mae72_in=round(c.blend_mae72 / IN, 2) if c and c.blend_mae72 else None,
                        windows=c.n_windows if c else None, updated=updated_at(key), located=True))
    json.dump(dict(resorts=idx), open(os.path.join(OUT, "index.json"), "w"))
    for f in os.listdir(OUT):  # resorts removed from the catalog
        if f.endswith(".json") and f != "index.json" and f[:-5] not in CATALOG:
            os.remove(os.path.join(OUT, f))


def update_one(key):
    cal = load_calibration()
    if key not in RESORTS or key not in cal:
        raise KeyError(f"{key}: not set up yet (run calibrate.py)")
    data = process(RESORTS[key], cal[key])
    nxt = [h for h in data["hourly"] if h["t"] >= data["now"]][:72]
    ok = sum(1 for h in nxt if h["summit"]["snow_in"] is not None and h["summit"]["temp_f"] is not None)
    if len(nxt) < 48 or ok < 0.8 * len(nxt):  # mostly blank: keep the old file (--check flags staleness)
        raise RuntimeError(f"{key}: forecast mostly empty ({ok}/{len(nxt)} hours); not publishing")
    src.write_json(os.path.join(OUT, f"{key}.json"), data, separators=(",", ":"))


def main(keys=None, scheduled=False, group=None, index=True):
    """Refresh resorts (default: all, or a region group). scheduled: only due ones (new model run,
    old format, or stale), oldest first. Stops cleanly on Open-Meteo quota; failures get a second pass."""
    os.makedirs(OUT, exist_ok=True)
    todo = keys or [k for k, r in RESORTS.items() if not group or r.region in GROUPS[group]]
    if scheduled:
        run = attempt("model run metadata", latest_run)
        todo = sorted((k for k in todo if due(k, run)), key=lambda k: updated_at(k) or "")
        print(f"group={group or 'all'} run={run}: refreshing {todo or 'nothing'}")
    for _ in (1, 2):
        failed = []
        for key in todo:
            try:
                update_one(key)
            except src.QuotaExceeded as e:
                print(f"  quota reached ({e}); keeping existing data for the rest", file=sys.stderr)
                failed = []
                break
            except KeyError as e:  # located but not calibrated yet: expected while setting up
                print(f"  skip {e}", file=sys.stderr)
            except Exception:
                traceback.print_exc()
                failed.append(key)
            time.sleep(2)
        if not failed:
            break
        print(f"  retrying {failed} in 60 s", file=sys.stderr)
        time.sleep(60)
        todo = failed
    if index:
        write_index()


def check(url=None):
    """Freshness alarm: exit 1 (failing the GitHub run, which emails the owner) on a missing
    forecast or 3+ stale resorts; a single straggler is only a warning. url: check the live site."""
    if url:
        resorts = src.get_json(url.rstrip("/") + "/data/index.json")["resorts"]
    else:
        resorts = json.load(open(os.path.join(OUT, "index.json")))["resorts"]
    now = datetime.now(timezone.utc)
    rows, stale, missing = [], [], []
    for r in resorts:
        if r.get("calibration") in (None, "not set up yet"):
            rows.append(f"| {r['name']} | setting up | |")
        elif not r.get("updated"):
            missing.append(r["name"]); rows.append(f"| {r['name']} | MISSING | |")
        else:
            age = (now - datetime.fromisoformat(r["updated"])).total_seconds() / 3600
            if age > MAX_AGE_H:
                stale.append(r["name"])
            rows.append(f"| {r['name']} | {'ok' if age <= MAX_AGE_H else 'STALE'} | {age:.1f} h |")
    alarm = missing + (stale if len(stale) >= 3 else [])
    md = (f"### Forecast health: {'❌' if alarm else '⚠️' if stale else '✅'} {len(missing)} missing, "
          f"{len(stale)} stale\n\n| Resort | Status | Age |\n|---|---|---|\n" + "\n".join(rows))
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(md + "\n")
    if alarm:
        print(f"::error::missing or stale forecasts: {', '.join(alarm)}")
        sys.exit(1)


if __name__ == "__main__":
    args = sys.argv[1:]
    opt = lambda name: args[args.index(name) + 1] if name in args else None
    if "--check" in args:
        check(opt("--url"))
    elif "--plan" in args:
        plan()
    else:
        main([a for a in args if not a.startswith("--") and a != opt("--group")] or None,
             scheduled="--scheduled" in args, group=opt("--group"), index="--no-index" not in args)
