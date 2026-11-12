"""Snap catalog points to the 90 m DEM, pick a SNOTEL gauge and usable models -> data/resorts.json.

    python3 locate.py            # all missing resorts
    python3 locate.py alta taos  # (re)locate specific ones
"""
import json, math, os, sys, time
from datetime import date
from catalog import CATALOG, MANUAL
from config import FT, BASE_MODELS, EXTRA_MODELS
from sources import get_json, QuotaExceeded, write_json

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "data", "resorts.json")
STEP = 0.003  # deg, ~300 m (Open-Meteo counts each coordinate against the rate limit)


def km(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))


def dem(pts):
    out = []
    for i in range(0, len(pts), 100):
        c = pts[i:i + 100]
        j = get_json("https://api.open-meteo.com/v1/elevation",
                     dict(latitude=",".join(f"{a:.4f}" for a, _ in c), longitude=",".join(f"{b:.4f}" for _, b in c)))
        out += [e / FT for e in j["elevation"]]
        time.sleep(len(c) / 8)  # ~480 coordinates/min, under the 600/min free limit
    return out


def snap(lat, lon, ft, radius_km):
    """Point within radius whose DEM elevation is closest to ft (ties -> nearest the guess)."""
    n = int(radius_km / 111 / STEP) + 1
    pts = [(lat + dy * STEP, lon + dx * STEP / math.cos(math.radians(lat)))
           for dy in range(-n, n + 1) for dx in range(-n, n + 1)]
    pts = [p for p in pts if km(lat, lon, *p) <= radius_km]
    el = dem(pts)
    best = min(range(len(pts)), key=lambda i: (round(abs(el[i] - ft) / 50), km(lat, lon, *pts[i])))
    return pts[best][0], pts[best][1], el[best]


def mid_point(b, s):
    target = b[2] + 0.55 * (s[2] - b[2])
    pts = [(b[0] + (s[0] - b[0]) * k / 24, b[1] + (s[1] - b[1]) * k / 24) for k in range(4, 21)]
    el = dem(pts)
    i = min(range(len(pts)), key=lambda i: abs(el[i] - target))
    return pts[i][0], pts[i][1], el[i]


_stations = {}


def snotel_near(lat, lon, lo_ft, hi_ft, state_codes):
    for st in state_codes:
        if st not in _stations:
            _stations[st] = get_json("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations",
                                     dict(stateCds=st, networkCds="SNTL"))
    cands = []
    for st in state_codes:
        for s in _stations[st]:
            d = km(lat, lon, s["latitude"], s["longitude"])
            active = (s.get("endDate") or "2100") >= date.today().isoformat()  # skip discontinued gauges
            if not s["stationTriplet"].endswith(":SNTL"):  # the API also returns snow courses, reservoirs...
                continue
            if d <= 25 and lo_ft - 1500 <= s["elevation"] <= hi_ft + 500 and active:
                # prefer close and within the lift-served elevation band
                pen = d + (0 if lo_ft <= s["elevation"] <= hi_ft else 4)
                cands.append((pen, s))
    if not cands:
        return None
    _, s = min(cands, key=lambda c: c[0])
    return ("snotel", s["stationTriplet"], f"{s['name'].title()} SNOTEL", round(s["elevation"]), s["latitude"], s["longitude"])


STATES = {"California": ["CA", "NV"], "Pacific Northwest": ["WA", "OR"], "Utah": ["UT"],
          "Wyoming · Montana · Idaho": ["WY", "MT", "ID"], "Colorado": ["CO"], "New Mexico": ["NM"], "Alaska": ["AK"]}


def usable_models(lat, lon):
    ok = []
    for m in BASE_MODELS + EXTRA_MODELS:
        try:
            j = get_json("https://api.open-meteo.com/v1/forecast",
                         dict(latitude=lat, longitude=lon, hourly="precipitation", models=m, forecast_days=2))
            if any(v is not None for v in j.get("hourly", {}).get("precipitation", [])):
                ok.append(m)
        except QuotaExceeded:
            raise  # never record a model as missing just because we were rate limited
        except RuntimeError as e:
            if "HTTP 4" not in str(e):  # only "no data here" (4xx) means the model is absent
                raise
        time.sleep(0.4)
    if len([m for m in ok if m in BASE_MODELS]) < 3:
        raise RuntimeError(f"only {ok} usable at {lat},{lon}; refusing to record (rerun later)")
    return ok


def locate(key):
    name, region, tz, b, s = CATALOG[key]
    man = MANUAL.get(key, {})
    if man:
        base = (b[0], b[1], b[2])
        summit = (s[0], s[1], s[2])
        mid = man["mid"][:3]
        mlabel = man["mid"][3]
        dem_ft = dict(zip(("base", "mid", "summit"), dem([base[:2], mid[:2], summit[:2]])))
    else:
        sb = snap(s[0], s[1], s[2], 2.0)
        if abs(sb[2] - s[2]) > 600:  # rough guess too far off: search wider
            sb = snap(s[0], s[1], s[2], 4.0)
        bb = snap(b[0], b[1], b[2], 1.2)
        base, summit = (bb[0], bb[1], b[2]), (sb[0], sb[1], s[2])
        mp = mid_point((bb[0], bb[1], b[2]), (sb[0], sb[1], s[2]))
        mid = (mp[0], mp[1], round(mp[2] / 100) * 100)
        mlabel = "Mid-mountain"
        dem_ft = dict(base=bb[2], mid=mp[2], summit=sb[2])
    obs = man.get("obs") or (snotel_near(mid[0], mid[1], b[2], s[2], STATES[region]) if region in STATES else None)
    r = dict(name=name, region=region, tz=tz,
             base=dict(lat=round(base[0], 4), lon=round(base[1], 4), ft=base[2], label=b[3]),
             mid=dict(lat=round(mid[0], 4), lon=round(mid[1], 4), ft=mid[2], label=mlabel),
             summit=dict(lat=round(summit[0], 4), lon=round(summit[1], 4), ft=summit[2], label=s[3]),
             dem_ft={k: round(v) for k, v in dem_ft.items()}, obs=obs,
             models=usable_models(summit[0], summit[1]))
    if man.get("afd_office"):
        r["afd_office"] = man["afd_office"]
    warn = [f"{e} DEM {r['dem_ft'][e]} vs {r[e]['ft']}" for e in ("base", "summit")
            if abs(r["dem_ft"][e] - r[e]["ft"]) > 600]
    print(f"{key:15s} base {r['dem_ft']['base']:>6}/{b[2]:<6} mid {mid[2]:>6} summit {r['dem_ft']['summit']:>6}/{s[2]:<6} "
          f"obs={obs[2] if obs else '-'} models={len(r['models'])} {'WARN ' + '; '.join(warn) if warn else ''}")
    return r


def main(keys):
    data = json.load(open(PATH)) if os.path.exists(PATH) else {}
    for k in keys or [k for k in CATALOG if k not in data]:
        try:
            data[k] = locate(k)
        except Exception as e:
            print("  failed", k, e, file=sys.stderr)
        write_json(PATH, data, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
