"""Pull live forecasts + observations for every resort and write web/data/<resort>.json.

Central forecast: per-resort bias-corrected, skill-weighted blend of deterministic models
(factors/weights from calibrate.py). Phase, snow level and snow-to-liquid ratio come from a
consensus pressure-level profile. Ranges (25-75% shown, 10-90% in the appendix) average the
quantiles of (a) the pooled, bias-corrected ensembles and (b) the blend times its historical
obs/forecast error quantiles for that storm size. Includes the past 7 days (model-estimated) and
a daily weather report (report.py).
"""
import json, os, re, sys, time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from config import RESORTS, FT, ELEVS, PROFILE_MODELS, LEVELS, ENSEMBLES
from catalog import FAVORITES
from sources import get_json, cache_path, obs_raw, IN
import physics as ph
import report

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "web", "data")
OM = "https://api.open-meteo.com/v1/forecast"
ENS = "https://ensemble-api.open-meteo.com/v1/ensemble"
DAYS = 16
PAST = 7
# Regions refresh as separate parallel CI jobs (own runner each), so a rate limit in one
# doesn't stall the rest.
GROUPS = {"sierra": ["California"], "west": ["Pacific Northwest", "Utah", "Wyoming · Montana · Idaho"],
          "rockies": ["Colorado", "New Mexico"], "north": ["British Columbia", "Alberta", "Alaska"]}
OTHERS_EVERY_H = 11.5  # non-favorites: twice a day keeps ~35 resorts inside the free quota
# per-resort context, set at the start of process()
CTX = dict(tz="America/Los_Angeles", local=ZoneInfo("America/Los_Angeles"), models=[])
KMH_MPH = 0.621371


def om_point(pt, **extra):
    return dict(latitude=pt["lat"], longitude=pt["lon"], elevation=round(pt["ft"] * FT),
                cell_selection="nearest", timezone=CTX["tz"], forecast_days=DAYS, **extra)


# ---------------------------------------------------------------- fetch

def fetch_det(pt):
    vars_ = ["precipitation", "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
             "wind_gusts_10m", "wind_direction_10m", "cloud_cover", "freezing_level_height"]
    return get_json(OM, om_point(pt, hourly=",".join(vars_), models=",".join(CTX["models"]), past_days=PAST))


def fetch_profiles(pt):
    vars_ = [f"{v}_{p}hPa" for p in LEVELS for v in
             ("temperature", "relative_humidity", "geopotential_height", "wind_speed")]
    return get_json(OM, om_point(pt, hourly=",".join(vars_), models=",".join(PROFILE_MODELS), past_days=PAST))


def fetch_ens(pt, model):
    try:
        return get_json(ENS, om_point(pt, hourly="precipitation", models=model))
    except RuntimeError as e:
        print("  ensemble failed", model, e, file=sys.stderr)
        return None


def fetch_alerts(pt):
    try:
        j = get_json("https://api.weather.gov/alerts/active", dict(point=f"{pt['lat']},{pt['lon']}"))
    except Exception as e:
        print("  alerts failed", e, file=sys.stderr)
        return []
    return [dict(event=p["event"], severity=p.get("severity"), headline=p.get("headline"),
                 onset=p.get("onset") or p.get("effective"), ends=p.get("ends") or p.get("expires"),
                 description=(p.get("description") or "")[:1500])
            for p in (f["properties"] for f in j.get("features", []))]


def nws_point(pt):
    path = cache_path(f"nws_point_{pt['lat']}_{pt['lon']}.json")
    if not os.path.exists(path):
        p = get_json(f"https://api.weather.gov/points/{pt['lat']},{pt['lon']}")["properties"]
        json.dump(dict(office=p["gridId"], x=p["gridX"], y=p["gridY"]), open(path, "w"))
    return json.load(open(path))


DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?)?")


def nws_hourly(values, per_hour_split):
    """Expand NWS ISO8601 interval values to {local hour string: value}."""
    out = {}
    for v in values:
        start, dur = v["validTime"].split("/")
        m = DUR.match(dur)
        hours = int(m.group(1) or 0) * 24 + int(m.group(2) or 0)
        t0 = datetime.fromisoformat(start).astimezone(CTX["local"])
        for k in range(max(hours, 1)):
            t = (t0 + timedelta(hours=k)).strftime("%Y-%m-%dT%H:00")
            out[t] = (v["value"] or 0) / hours if per_hour_split else v["value"]
    return out


def fetch_nws(pt):
    try:
        g = nws_point(pt)
        p = get_json(f"https://api.weather.gov/gridpoints/{g['office']}/{g['x']},{g['y']}")["properties"]
        return dict(office=g["office"], elev_m=p["elevation"]["value"],
                    snow=nws_hourly(p["snowfallAmount"]["values"], True),
                    qpf=nws_hourly(p["quantitativePrecipitation"]["values"], True),
                    snow_level=nws_hourly(p["snowLevel"]["values"], False))
    except Exception as e:
        print("  NWS failed", e, file=sys.stderr)
        return None


_afd_cache = {}


def fetch_afd(office):
    if office in _afd_cache:
        return _afd_cache[office]
    try:
        g = get_json(f"https://api.weather.gov/products/types/AFD/locations/{office}")["@graph"][0]
        p = get_json(g["@id"])
        _afd_cache[office] = dict(office=office, issued=p["issuanceTime"], text=p["productText"])
    except Exception as e:
        print("  AFD failed", e, file=sys.stderr)
        _afd_cache[office] = None
    return _afd_cache[office]


def fetch_obs(obs):
    end = date.today() + timedelta(1)
    start = end - timedelta(15)
    try:
        raw = obs_raw(obs, start.isoformat(), end.isoformat())
    except Exception as e:
        print("  obs failed", e, file=sys.stderr)
        return None
    by = defaultdict(dict)
    for el, vals in raw.items():
        for d, v in vals:
            if v is not None:
                by[d[:10]][el] = v
    days = sorted(by)
    rows = []
    for a, b in zip(days, days[1:]):
        A, B = by[a], by[b]
        row = dict(date=a, depth_in=A.get("SNWD"))
        if "SNWD" in A and "SNWD" in B:
            row["new_snow_in"] = max(0.0, B["SNWD"] - A["SNWD"])
        if "WTEQ" in A and "WTEQ" in B:
            row["swe_in"] = round(B["WTEQ"] - A["WTEQ"], 2)
        if "PREC" in A and "PREC" in B and 0 <= B["PREC"] - A["PREC"] < 10:
            row["precip_in"] = round(B["PREC"] - A["PREC"], 2)
        if "TAVG" in A:
            row["tavg_f"] = A["TAVG"]
        rows.append(row)
    latest = lambda el: next((by[d][el] for d in reversed(days) if el in by[d]), None)
    return dict(station=obs[2], elev_ft=obs[3], days=rows,
                depth_in=latest("SNWD"), swe_in=latest("WTEQ"))


# ---------------------------------------------------------------- processing

def build_profiles(pj):
    """-> per hour: consensus Profile and per-model Profiles."""
    h = pj["hourly"]
    times = h["time"]
    cons, per_model = [], defaultdict(list)
    for i in range(len(times)):
        stacks = []
        for m in PROFILE_MODELS:
            try:
                z = [h[f"geopotential_height_{p}hPa_{m}"][i] for p in LEVELS]
                t = [h[f"temperature_{p}hPa_{m}"][i] for p in LEVELS]
                rh = [h[f"relative_humidity_{p}hPa_{m}"][i] for p in LEVELS]
                w = [h[f"wind_speed_{p}hPa_{m}"][i] for p in LEVELS]
            except KeyError:
                continue
            if None in z or None in t or None in rh or None in w:
                per_model[m].append(None)
                continue
            stacks.append((z, t, rh, w))
            per_model[m].append(ph.Profile(z, t, rh, w))
        if stacks:
            n = len(stacks)
            avg = [[sum(s[k][j] for s in stacks) / n for j in range(len(LEVELS))] for k in range(4)]
            cons.append(ph.Profile(*avg))
        else:
            cons.append(None)
    return times, cons, per_model


def blend_precip(dj, cal):
    """Bias-corrected, weighted blend of hourly precip; renormalised over models present."""
    h = dj["hourly"]
    models = cal["models"]
    out, contrib = [], defaultdict(list)
    for i in range(len(h["time"])):
        num = den = 0.0
        for m in CTX["models"]:
            v = (h.get(f"precipitation_{m}") or [None] * (i + 1))[i]
            if v is None or m not in models:
                continue
            c = v * models[m]["factor"]
            contrib[m].append(c)
            num += models[m]["weight"] * c
            den += models[m]["weight"]
        out.append(num / den if den else None)
        for m in CTX["models"]:
            if len(contrib[m]) < i + 1:
                contrib[m].append(None)
    return out, contrib


def mean_of(h, var, i):
    vs = [h[f"{var}_{m}"][i] for m in CTX["models"] if h.get(f"{var}_{m}") and h[f"{var}_{m}"][i] is not None]
    return sum(vs) / len(vs) if vs else None


def wquantiles(vals_w, qs):
    vals_w = sorted(vals_w)
    tot = sum(w for _, w in vals_w)
    out, acc, j = {}, 0.0, 0
    for q in sorted(qs):
        while j < len(vals_w) and acc + vals_w[j][1] < q * tot:
            acc += vals_w[j][1]; j += 1
        out[q] = vals_w[min(j, len(vals_w) - 1)][0]
    return out


QS = (0.1, 0.25, 0.5, 0.75, 0.9)


def qkey(q):
    return f"p{int(round(q * 100))}"


def mean_dir(h, i):
    """Vector-mean 10 m wind direction across models (degrees)."""
    import math
    sx = sy = 0.0
    n = 0
    for m in CTX["models"]:
        d, s = (h.get(f"wind_direction_10m_{m}") or [None] * (i + 1))[i], (h.get(f"wind_speed_10m_{m}") or [None] * (i + 1))[i]
        if d is None or s is None:
            continue
        sx += s * math.sin(math.radians(d)); sy += s * math.cos(math.radians(d)); n += 1
    return (math.degrees(math.atan2(sx, sy)) % 360) if n else None


def rnd(v):
    return round(v, 3) if isinstance(v, float) else v


def process(key, r, cal):
    print("resort", key)
    CTX.update(tz=r["tz"], local=ZoneInfo(r["tz"]), models=[m for m in r["models"] if m in cal["models"]])
    det = {e: fetch_det(r[e]) for e in ELEVS}
    times, cons, per_model_prof = build_profiles(fetch_profiles(r["summit"]))
    ens = {m: fetch_ens(r["summit"], m) for m in ENSEMBLES}
    nws = {e: fetch_nws(r[e]) for e in ELEVS}
    alerts = fetch_alerts(r["summit"])
    obs = fetch_obs(r["obs"])
    office = r.get("afd_office") or (nws["summit"] or {}).get("office")
    afd = fetch_afd(office) if office else None
    now = datetime.now(CTX["local"]).strftime("%Y-%m-%dT%H:00")
    now_i = next((i for i, t in enumerate(times) if t >= now), len(times) - 1)

    # ---- hourly per elevation
    elev = {}
    for e in ELEVS:
        dj = det[e]; h = dj["hourly"]
        assert h["time"] == times, "time axes differ"
        precip, contrib = blend_precip(dj, cal)
        z = r[e]["ft"] * FT
        rows = []
        for i, t in enumerate(times):
            p = cons[i]
            tc, w10, g10 = (mean_of(h, v, i) for v in ("temperature_2m", "wind_speed_10m", "wind_gusts_10m"))
            row = dict(t=t, precip_mm=precip[i], temp_f=None if tc is None else tc * 9 / 5 + 32,
                       wind_mph=None if w10 is None else w10 * KMH_MPH,
                       gust_mph=None if g10 is None else g10 * KMH_MPH,
                       wind_dir=mean_dir(h, i), cloud=mean_of(h, "cloud_cover", i))
            if p is not None:
                at = p.at(z)
                frac, slr = ph.snow_fraction(at["tw"]), ph.slr_at(p, z)
                row.update(frac=frac, slr=slr, warm_nose=p.warm_nose(z), tw_c=at["tw"])
                if e == "summit":  # exposed ridge: free-air wind at summit height, not smoothed 10 m wind
                    row["wind_mph"] = at["wind"] * KMH_MPH
                if precip[i] is not None:
                    row["snow_in"] = precip[i] * frac * slr / IN
                    row["rain_in"] = precip[i] * (1 - frac) / IN
            rows.append(row)
        model_snow = {m: [None if (c[i] is None or "frac" not in rows[i])
                          else c[i] * rows[i]["frac"] * rows[i]["slr"] / IN for i in range(len(times))]
                      for m, c in contrib.items()}
        elev[e] = dict(rows=rows, model_snow=model_snow, grid=dict(lat=dj["latitude"], lon=dj["longitude"]))

    levels = []
    sl_nws = (nws["summit"] or {}).get("snow_level", {})
    for i, p in enumerate(cons):
        sl = [pm[i].snow_level() for pm in per_model_prof.values() if i < len(pm) and pm[i] is not None]
        sl = [x for x in sl if x is not None]
        lv = p.snow_level() if p else None
        fz = p.freezing_level() if p else None
        levels.append(dict(snow_level_ft=None if lv is None else lv / FT, freezing_level_ft=None if fz is None else fz / FT,
                           snow_level_lo_ft=min(sl) / FT if sl else None, snow_level_hi_ft=max(sl) / FT if sl else None,
                           nws_snow_level_ft=None if sl_nws.get(times[i]) is None else sl_nws[times[i]] / FT))

    # ---- ensembles (summit point), bias-corrected, each ensemble weighted equally
    members = []
    for m, j in ens.items():
        if not j or "hourly" not in j:
            continue
        h = j["hourly"]
        f = cal["models"].get(ENSEMBLES[m], {}).get("factor", 1.0)
        ks = [k for k in h if k.startswith("precipitation")]
        for k in ks:
            members.append((1.0 / len(ks), {t: v * f for t, v in zip(h["time"], h[k]) if v is not None}))

    def emp_q(liq_mm):
        """Historical obs/forecast ratio quantiles for a storm of this size at this resort."""
        rq = cal.get("ratio_q", {})
        for b in cal.get("ratio_bins", []):
            if b["lo_mm"] <= liq_mm < b["hi_mm"]:
                rq = b["q"]
        return {q: rq.get(str(q), 1.0) for q in QS}

    def window(i0, i1, e):
        """Totals for hours [i0, i1) at elevation e; ranges only for future windows."""
        i0, i1 = max(0, i0), min(len(times), i1)
        rows = elev[e]["rows"][i0:i1]
        snow = sum(x.get("snow_in") or 0 for x in rows)
        liq = sum(x["precip_mm"] or 0 for x in rows)
        res = dict(p50=snow, liquid_in=liq / IN, rain_in=sum(x.get("rain_in") or 0 for x in rows))
        if i0 < now_i or i1 <= i0:
            return res
        s_liq = sum(x["precip_mm"] or 0 for x in elev["summit"]["rows"][i0:i1])
        if liq > 0.5:
            ratio = snow / liq
        else:  # dry blend: typical hourly snow/liquid so ensemble wet tails still show
            rs = [x["frac"] * x["slr"] / IN for x in rows if "frac" in x]
            ratio = sum(rs) / len(rs) if rs else 0
        scale = liq / s_liq if s_liq > 0.5 else 1.0  # ensembles are at the summit point
        ts = times[i0:i1]
        vals = []
        for w, mm in members:
            got = [mm[t] for t in ts if t in mm]
            if got and len(got) >= 0.9 * len(ts):
                vals.append((sum(got) * len(ts) / len(got) * scale, w))
        eq = emp_q(s_liq)
        ensq = wquantiles(vals, QS) if vals else None
        for q in QS:
            emp = snow * eq[q]
            # quantile averaging (Vincentization) of ensemble and historical-error distributions
            res[qkey(q)] = (ensq[q] * ratio + emp) / 2 if ensq else emp
        res["p50"] = snow
        res["n_members"] = len(vals)
        return res

    # ---- daily
    day_of = [t[:10] for t in times]
    days = sorted(set(day_of))
    today = now[:10]
    obs_by_day = {x["date"]: x for x in (obs or {}).get("days", [])}
    daily = []
    for d in days:
        idx = [i for i, t in enumerate(day_of) if t == d]
        i0, i1 = idx[0], idx[-1] + 1
        if d == today:
            i0 = now_i  # rest of today
        entry = dict(date=d, past=d < today)
        for e in ELEVS:
            rows = elev[e]["rows"][idx[0]:i1]
            b = window(idx[0] if d < today else i0, i1, e)
            wet = [x for x in rows if (x["precip_mm"] or 0) > 0.2 and "frac" in x]
            liq = sum(x["precip_mm"] for x in wet)
            if liq > 0:
                snow_liq = sum(x["precip_mm"] * x["frac"] for x in wet)
                frac = snow_liq / liq
                slr = sum(x["precip_mm"] * x["frac"] * x["slr"] for x in wet) / snow_liq if snow_liq > 0 else None
                nose = any(x.get("warm_nose") and x["precip_mm"] > 0.5 for x in wet)
                b.update(snow_frac=frac, slr=slr, density_pct=100 / slr if slr else None,
                         quality=ph.quality(slr or 10, frac, nose) if liq > 1 else None)
            temps = [x["temp_f"] for x in rows if x.get("temp_f") is not None]
            lift = report.lift_hours(rows, d)
            winds = [x["wind_mph"] for x in lift if x.get("wind_mph") is not None]
            gusts = [x["gust_mph"] for x in lift if x.get("gust_mph") is not None]
            dirs = [x["wind_dir"] for x in lift if x.get("wind_dir") is not None]
            b.update(hi_f=max(temps) if temps else None, lo_f=min(temps) if temps else None,
                     wind_mph=max(winds) if winds else None, gust_mph=max(gusts) if gusts else None,
                     wind_dir=report.compass(sum(dirs) / len(dirs)) if dirs else None,
                     models={m: (sum(v for v in s[idx[0]:i1] if v is not None)
                                 if any(v is not None for v in s[idx[0]:i1]) else None)
                             for m, s in elev[e]["model_snow"].items()})
            if nws[e] and d >= today:
                ns = [nws[e]["snow"].get(times[i]) for i in range(i0, i1)]
                if any(v is not None for v in ns):
                    b["nws_snow_in"] = sum(v or 0 for v in ns) / IN
            entry[e] = b
        wet_i = [i for i in range(idx[0], i1) if (elev["summit"]["rows"][i]["precip_mm"] or 0) > 0.2]
        sl_wet = [levels[i]["snow_level_ft"] for i in wet_i if levels[i]["snow_level_ft"] is not None]
        sls = [levels[i]["snow_level_ft"] for i in range(idx[0], i1) if levels[i]["snow_level_ft"] is not None]
        fz = [levels[i]["freezing_level_ft"] for i in range(idx[0], i1) if levels[i]["freezing_level_ft"] is not None]
        entry.update(snow_level_ft=(sum(sl_wet) / len(sl_wet)) if sl_wet else (sum(sls) / len(sls) if sls else None),
                     snow_level_max_ft=max(sl_wet) if sl_wet else None,
                     freezing_level_ft=sum(fz) / len(fz) if fz else None)
        o = obs_by_day.get(d)
        if o:
            entry["obs"] = dict(new_snow_in=o.get("new_snow_in"), precip_in=o.get("precip_in"), swe_in=o.get("swe_in"))
        if d >= today:
            entry["report"], entry["wind_hold"] = report.build(
                d, entry, {e: elev[e]["rows"] for e in ELEVS}, {e: r[e]["ft"] for e in ELEVS})
        daily.append(entry)

    # ---- summary windows (hour-based, from now)
    nxt = {lbl: {e: window(now_i, now_i + h, e) for e in ELEVS}
           for lbl, h in (("Next 24 hours", 24), ("Next 3 days", 72), ("Next 5 days", 120), ("Next 10 days", 240))}
    past = {lbl: {e: window(now_i - h, now_i, e) for e in ELEVS}
            for lbl, h in (("Last 24 hours", 24), ("Last 3 days", 72), ("Last 7 days", 168))}
    if obs:
        done = [x for x in obs["days"] if x["date"] < today]
        for lbl, n in (("Last 24 hours", 1), ("Last 3 days", 3), ("Last 7 days", 7)):
            vals = [x.get("new_snow_in") for x in done[-n:]]
            past[lbl]["station_in"] = sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None

    hourly = []
    for i, t in enumerate(times):
        if not (now_i <= i < now_i + 24 * 7):  # charts only use the next 7 days hourly
            continue
        hourly.append(dict(t=t, past=i < now_i, **{k: rnd(v) for k, v in levels[i].items()},
                           **{e: {k: rnd(v) for k, v in elev[e]["rows"][i].items() if k != "t"} for e in ELEVS}))
    return dict(
        key=key, name=r["name"], sub=r["sub"], region=r["region"], pass_=r["pass_"], tz=r["tz"], now=now,
        updated=datetime.now(timezone.utc).isoformat(timespec="minutes"),
        points={e: dict(requested=r[e], grid=elev[e]["grid"],
                        nws_elev_ft=nws[e]["elev_m"] / FT if nws[e] else None) for e in ELEVS},
        calibration=cal, daily=daily, hourly=hourly, next=nxt, past=past, obs=obs, afd=afd,
        alerts=alerts, ensembles={m: bool(j and "hourly" in j) for m, j in ens.items()},
    )

def _updated(path):
    try:
        return json.load(open(path))["updated"]
    except Exception:
        return None


def write_index():
    idx = []
    for key, r in RESORTS.items():
        p = os.path.join(OUT, f"{key}.json")
        idx.append(dict(key=key, name=r["name"], sub=r["sub"], region=r["region"], pass_=r["pass_"],
                        favorite=key in FAVORITES, calibrated=bool(r.get("obs")),
                        updated=_updated(p)))
    json.dump(dict(resorts=idx), open(os.path.join(OUT, "index.json"), "w"))


def update_one(key):
    cal_all = json.load(open(os.path.join(HERE, "data", "calibration.json")))
    if key not in RESORTS or key not in cal_all:
        raise KeyError(f"{key}: not located/calibrated (run locate.py, calibrate.py)")
    data = process(key, RESORTS[key], cal_all[key])
    tmp = os.path.join(OUT, f".{key}.json")
    json.dump(data, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, os.path.join(OUT, f"{key}.json"))  # atomic: the page never reads half a file


def data_age_h(key):
    """Age from the JSON's own timestamp (file mtimes are reset by git checkouts)."""
    p = os.path.join(OUT, f"{key}.json")
    try:
        t = json.load(open(p))["updated"]
    except Exception:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(t)).total_seconds() / 3600


def main(keys=None, scheduled=False, group=None, index=True):
    """Default: refresh favorites. scheduled=True (GitHub Actions): favorites every run, every
    other resort once its data is older than OTHERS_EVERY_H, oldest first; stops cleanly on quota."""
    from sources import QuotaExceeded
    os.makedirs(OUT, exist_ok=True)
    pool = [k for k in RESORTS if not group or RESORTS[k]["region"] in GROUPS[group]]
    if scheduled:
        others = [k for k in pool if k not in FAVORITES]
        stale = [k for k in others if (data_age_h(k) is None or data_age_h(k) > OTHERS_EVERY_H)]
        keys = [k for k in FAVORITES if k in pool] + sorted(stale, key=lambda k: -(data_age_h(k) or 1e9))
        print(f"group={group or 'all'}: refreshing {keys or 'nothing (all fresh)'}")
    todo = list(keys if scheduled else (keys or FAVORITES))
    for attempt in (1, 2):  # second pass retries resorts that failed for non-quota reasons
        failed = []
        for key in todo:
            if key not in RESORTS:
                continue
            try:
                update_one(key)
            except QuotaExceeded as e:
                print(f"  quota reached ({e}); keeping existing data for the rest", file=sys.stderr)
                failed = []
                break
            except KeyError as e:  # located but not calibrated yet: expected during backfill
                print(f"  skip {e}", file=sys.stderr)
            except Exception:
                import traceback; traceback.print_exc()
                failed.append(key)
            time.sleep(2)
        if not failed:
            break
        print(f"  retrying {failed} in 60 s", file=sys.stderr)
        time.sleep(60)
        todo = failed
    if index:
        write_index()


if __name__ == "__main__":
    args = sys.argv[1:]
    grp = args[args.index("--group") + 1] if "--group" in args else None
    pos = [a for a in args if not a.startswith("--") and a != grp]
    main(pos or None, scheduled="--scheduled" in args, group=grp, index="--no-index" not in args)
