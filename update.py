"""Live forecast for each resort -> web/data/<resort>.json, plus the index and a freshness check.

Snow: per-resort bias-corrected, skill-weighted blend of deterministic models (calibrate.py).
Phase, snow level and snow-to-liquid ratio come from a consensus pressure-level profile.
Ranges average the quantiles of (a) the pooled bias-corrected ensembles and (b) the blend times
this resort's historical obs/forecast error for that storm size.

    python3 update.py [resort ...] [--scheduled] [--group G] [--no-index]
    python3 update.py --check [--url SITE]
"""
import json, os, sys, time, traceback
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import conditions as cx
import sources as src
from resorts import CATALOG, ELEVS, ENSEMBLES, FT, GROUPS, HERE, IN, KMH_MPH, PROFILE_MODELS, LEVELS, RESORTS, load_calibration

OUT = os.path.join(HERE, "web", "data")
PAST_DAYS = 7
REFRESH_EVERY_H = 7.5  # ~every 9 h on the 3-hourly schedule: ~7.5k of Open-Meteo's 10k calls/day
MAX_AGE_H = 20         # freshness alarm: about 2 missed refreshes
FORMAT = 2             # bump when the JSON layout changes: older files count as stale
QS = (0.1, 0.25, 0.5, 0.75, 0.9)
HOURLY_OUT = ("temp_f", "wind_mph", "frac", "slr", "snow_in", "rain_in")  # fields the charts read
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
                    None if g10 is None else g10 * KMH_MPH, grid.wind_dir(i), grid.mean("cloud_cover", i))
        p = cons[i]
        if p is not None:
            at = p.at(z)
            h.frac, h.slr, h.warm_nose, h.tw_c = cx.snow_fraction(at["tw"]), cx.slr_at(p, z), p.warm_nose(z), at["tw"]
            if e == "summit":  # exposed ridge: free-air wind at summit height, not smoothed 10 m wind
                h.wind_mph = at["wind"] * KMH_MPH
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
    return round(v, 3) if isinstance(v, float) else v


def process(r, cal):
    print("resort", r.key)
    local = ZoneInfo(r.tz)
    models = [m for m in r.models if m in cal.models]
    grids = {e: src.forecast(r.point(e), r.tz, models, PAST_DAYS) for e in ELEVS}
    times = grids["summit"].times
    cons, per_model = soundings(src.profiles(r.summit, r.tz, PAST_DAYS), cal.profile_shift_c)
    ens = {m: attempt(f"ensemble {m}", lambda m=m: src.ensemble(r.summit, r.tz, m)) for m in ENSEMBLES}
    nws = {e: attempt("NWS", lambda e=e: src.nws_grid(r.point(e), local)) if r.us else None for e in ELEVS}
    alerts = attempt("alerts", lambda: [asdict(a) for a in src.nws_alerts(r.summit)]) if r.us else []
    office = (r.afd_office or (nws["summit"].office if nws["summit"] else None)) if r.us else None
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
    nws_sl = nws["summit"].snow_level if nws["summit"] else {}
    levels = []
    for i, p in enumerate(cons):
        sl = [x for x in (pm[i].snow_level() for pm in per_model.values() if pm[i] is not None) if x is not None]
        lv, fz, n = (p.snow_level() if p else None), (p.freezing_level() if p else None), nws_sl.get(times[i])
        levels.append(dict(snow_level_ft=None if lv is None else lv / FT, freezing_level_ft=None if fz is None else fz / FT,
                           snow_level_lo_ft=min(sl) / FT if sl else None, snow_level_hi_ft=max(sl) / FT if sl else None,
                           nws_snow_level_ft=None if n is None else n / FT))

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
        for lo, hi in (("p10", "p90"), ("p25", "p75")):  # the ranges always include the forecast
            res[lo], res[hi] = min(res[lo], snow), max(res[hi], snow)
        return res

    # ---- ski days: 6am to 6am, split into morning (6-12), afternoon (12-6pm) and night (6pm-6am)
    def quality_of(hrs, min_liq=1.0):
        wet = [h for h in hrs if (h.precip_mm or 0) > 0.2 and h.frac is not None]
        liq = sum(h.precip_mm for h in wet)
        if liq <= 0:
            return {}
        snow_liq = sum(h.precip_mm * h.frac for h in wet)
        slr = sum(h.precip_mm * h.frac * h.slr for h in wet) / snow_liq if snow_liq > 0 else None
        nose = any(h.warm_nose and h.precip_mm > 0.5 for h in wet)
        return dict(slr=slr, quality=cx.quality(slr or 10, snow_liq / liq, nose) if liq > min_liq else None)

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
                                 **quality_of(part, 0.5)) for part in (hrs[:6], hrs[6:12], hrs[12:24])]
            temps = [h.temp_f for h in hrs if h.temp_f is not None]
            lift = cx.lift_hours(hrs, d)
            winds = [h.wind_mph for h in lift if h.wind_mph is not None]
            gusts = [h.gust_mph for h in lift if h.gust_mph is not None]
            dirs = [h.wind_dir for h in lift if h.wind_dir is not None]
            b.update(hi_f=max(temps, default=None), lo_f=min(temps, default=None), wind_mph=max(winds, default=None),
                     gust_mph=max(gusts, default=None), wind_dir=cx.compass(cx.circ_mean(dirs)) if dirs else None,
                     models={m: (sum(v for v in s[first:i1] if v is not None) if any(v is not None for v in s[first:i1]) else None)
                             for m, s in model_snow[e].items()})
            if nws[e] and d >= today:
                ns = [nws[e].snow.get(times[i]) for i in range(i0, i1)]
                if any(v is not None for v in ns):
                    b["nws_snow_in"] = sum(v or 0 for v in ns) / IN
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
        if d >= today:
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

    # ---- plumes: cumulative snow and rain from now, forecast line + ensemble spread (every 3 h)
    horizon = range(now_i, min(len(times), now_i + 24 * 10))
    plume = {}
    for e in ELEVS:
        hrs = hours[e]
        liq = sum(hrs[i].precip_mm or 0 for i in horizon)
        s_liq = sum(hours["summit"][i].precip_mm or 0 for i in horizon)
        scale = liq / s_liq if s_liq > 0.5 else 1.0  # ensembles are at the summit point
        out = {k: {q: [] for q in ("fc", *map(str, QS))} for k in ("snow", "rain")}
        cum = {"snow": [0.0] * len(members), "rain": [0.0] * len(members)}
        alive, fc = [True] * len(members), {"snow": 0.0, "rain": 0.0}
        for n, i in enumerate(horizon):
            h, t = hrs[i], times[i]
            fc["snow"] += h.snow_in or 0; fc["rain"] += h.rain_in or 0
            for j, (w, mm) in enumerate(members):
                if not alive[j] or t not in mm or h.frac is None:
                    alive[j] = alive[j] and t in mm  # a member past its horizon stops counting
                    continue
                cum["snow"][j] += mm[t] * scale * h.frac * h.slr / IN
                cum["rain"][j] += mm[t] * scale * (1 - h.frac) / IN
            if n % 3:
                continue
            for k in ("snow", "rain"):
                vals = [(cum[k][j], members[j][0]) for j in range(len(members)) if alive[j]]
                q = wquantiles(vals, QS) if vals else {}
                c = fc[k] / q[0.5] if q and q[0.5] > 0.01 and fc[k] > 0 else 1.0  # centre the spread on the forecast
                out[k]["fc"].append(round(fc[k], 2))
                for p in QS:
                    v = q[p] * c if q else fc[k]
                    out[k][str(p)].append(round(min(v, fc[k]) if p < 0.5 else max(v, fc[k]) if p > 0.5 else fc[k], 2))
        plume[e] = dict(t=[times[i] for n, i in enumerate(horizon) if n % 3 == 0], **out)

    hourly = [dict(t=t, **{k: rnd(v) for k, v in levels[i].items()},
                   **{e: {k: rnd(getattr(hours[e][i], k)) for k in HOURLY_OUT} for e in ELEVS})
              for i, t in enumerate(times) if now_i <= i < now_i + 24 * 10]  # charts use the next 10 days
    return dict(
        key=r.key, name=r.name, region=r.region, format=FORMAT, now=now, today=today,
        updated=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        points={e: dict(requested=asdict(r.point(e)), grid=dict(lat=grids[e].lat, lon=grids[e].lon)) for e in ELEVS},
        calibration=dict(station=cal.station, uncalibrated=cal.uncalibrated, peers=cal.peers, peers_scope=cal.peers_scope,
                         temp_bias_c=cal.temp_bias_c, profile_shift_c=cal.profile_shift_c,
                         models={m: asdict(v) for m, v in cal.models.items()}),
        daily=daily, hourly=hourly, summary=summary, plume=plume, obs=obs, afd=afd, alerts=alerts,
    )


# ---------------------------------------------------------------- files

def updated_at(key, current_format=False):
    try:
        d = json.load(open(os.path.join(OUT, f"{key}.json")))
    except Exception:
        return None
    return None if current_format and d.get("format", 1) < FORMAT else d["updated"]


def age_h(key):
    """Age from the JSON's own timestamp (file mtimes are reset by git checkouts)."""
    t = updated_at(key, current_format=True)
    return None if t is None else (datetime.now(timezone.utc) - datetime.fromisoformat(t)).total_seconds() / 3600


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


def update_one(key):
    cal = load_calibration()
    if key not in RESORTS or key not in cal:
        raise KeyError(f"{key}: not set up yet (run calibrate.py)")
    data = process(RESORTS[key], cal[key])
    nxt = data["hourly"][:72]
    ok = sum(1 for h in nxt if h["summit"]["snow_in"] is not None and h["summit"]["temp_f"] is not None)
    if len(nxt) < 48 or ok < 0.8 * len(nxt):  # mostly blank: keep the old file (--check flags staleness)
        raise RuntimeError(f"{key}: forecast mostly empty ({ok}/{len(nxt)} hours); not publishing")
    src.write_json(os.path.join(OUT, f"{key}.json"), data, separators=(",", ":"))


def main(keys=None, scheduled=False, group=None, index=True):
    """Refresh resorts (default: all, or a region group). scheduled: only those older than
    REFRESH_EVERY_H, oldest first. Stops cleanly on Open-Meteo quota; failures get a second pass."""
    os.makedirs(OUT, exist_ok=True)
    todo = keys or [k for k, r in RESORTS.items() if not group or r.region in GROUPS[group]]
    if scheduled:
        age = {k: age_h(k) for k in todo}
        todo = sorted((k for k in todo if age[k] is None or age[k] > REFRESH_EVERY_H), key=lambda k: -(age[k] or 1e9))
        print(f"group={group or 'all'}: refreshing {todo or 'nothing (all fresh)'}")
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
    else:
        main([a for a in args if not a.startswith("--") and a != opt("--group")] or None,
             scheduled="--scheduled" in args, group=opt("--group"), index="--no-index" not in args)
