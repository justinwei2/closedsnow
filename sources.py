"""Data sources, each mapped onto a small typed record:

- Open-Meteo forecast / ensemble / previous-runs  -> ModelGrid (hourly series per model)
- NRCS SNOTEL, California CDEC, BC snow pillows (ASWS)
  and Alberta Rivers stations                      -> StationSeries of StationDay
- NWS alerts and forecast discussions            -> Alert, Discussion

HTTP goes through get_json: retries with backoff, Open-Meteo quota handling, optional replay.
"""
import csv, hashlib, io, json, math, os, random, sys, time, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from resorts import FT, IN, LEVELS, PROFILE_MODELS, SEASONS, USER_AGENT

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
WAIT_ON_QUOTA = False  # setup jobs set True: sleep through Open-Meteo hourly/daily limits
OM = "https://api.open-meteo.com/v1/forecast"


class QuotaExceeded(RuntimeError):
    pass


# ---------------------------------------------------------------- HTTP

def write_json(path, obj, **kw):
    """Atomic write: a killed job never leaves a truncated file behind."""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, **kw)
    json.load(open(tmp))
    os.replace(tmp, path)


def get_json(url, params=None, retries=6, parse=json.load):
    """GET JSON (or whatever `parse` makes of the response) with retries. SKISITE_REPLAY=dir records
    responses on first use and replays them afterwards (deterministic regression tests)."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    replay = os.environ.get("SKISITE_REPLAY")
    if not replay:
        return _fetch(url, retries, parse)
    path = os.path.join(replay, hashlib.sha1(url.encode()).hexdigest() + ".json")
    if not os.path.exists(path):
        os.makedirs(replay, exist_ok=True)
        write_json(path, _fetch(url, retries, parse))
    return json.load(open(path))


def _fetch(url, retries, parse=json.load):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    attempt = 0
    while attempt < retries:
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return parse(r)
        except urllib.error.HTTPError as e:
            try:
                reason = json.load(e).get("reason", "")
            except Exception:
                reason = ""
            if e.code == 429 and ("Hourly" in reason or "Daily" in reason):
                if not WAIT_ON_QUOTA:
                    raise QuotaExceeded(reason)
                wait = 3600 if "Daily" in reason else 600
                print(f"  quota: {reason} - sleeping {wait // 60} min", file=sys.stderr)
                time.sleep(wait)
                continue  # quota waits don't use up retries
            if 400 <= e.code < 500 and e.code not in (403, 408, 429):  # NRCS throws transient 403s
                raise RuntimeError(f"HTTP {e.code} {reason}: {url[:160]}")  # permanent: don't retry
            print(f"  retry {attempt}: {e} {reason} {url[:120]}", file=sys.stderr)
        except Exception as e:
            print(f"  retry {attempt}: {e} {url[:120]}", file=sys.stderr)
        attempt += 1
        if attempt < retries:
            time.sleep(min(60, 3 * 2 ** attempt) + random.uniform(0, 2))  # exponential backoff + jitter
    raise RuntimeError(f"failed: {url[:200]}")


def cached(name, fetch):
    """JSON cached under cache/name, for setup data that never changes (hindcasts, NWS grid ids)."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        write_json(path, fetch())
    return json.load(open(path))


# ---------------------------------------------------------------- Open-Meteo

@dataclass
class ModelGrid:
    """One Open-Meteo response: hourly series for one or more models at a grid cell."""
    times: list
    lat: float
    lon: float
    data: dict
    models: list

    @classmethod
    def from_json(cls, j, models):
        h = dict(j.get("hourly", {}))
        return cls(h.pop("time", []), j.get("latitude"), j.get("longitude"), h, list(models))

    def series(self, var, model):
        """Hourly values of var for model; Open-Meteo drops the _model suffix for a single model."""
        v = self.data.get(f"{var}_{model}")
        return v if v is not None or len(self.models) != 1 else self.data.get(var)

    def at(self, var, model, i):
        s = self.series(var, model)
        return s[i] if s else None

    def mean(self, var, i):
        vs = [v for v in (self.at(var, m, i) for m in self.models) if v is not None]
        return sum(vs) / len(vs) if vs else None

    def wind_dir(self, i):
        """Speed-weighted vector mean of the models' 10 m wind direction (degrees)."""
        sx = sy = 0.0
        n = 0
        for m in self.models:
            d, s = self.at("wind_direction_10m", m, i), self.at("wind_speed_10m", m, i)
            if d is not None and s is not None:
                sx += s * math.sin(math.radians(d)); sy += s * math.cos(math.radians(d)); n += 1
        return (math.degrees(math.atan2(sx, sy)) % 360) if n else None

    def members(self):
        """Ensemble responses: every precipitation series (control + members)."""
        return [v for k, v in self.data.items() if k.startswith("precipitation")]


def om_params(pt, tz, days=16, **extra):
    return dict(latitude=pt.lat, longitude=pt.lon, elevation=round(pt.ft * FT), cell_selection="nearest",
                timezone=tz, forecast_days=days, **extra)


def forecast(pt, tz, models, past_days):
    vars_ = ["precipitation", "temperature_2m", "relative_humidity_2m", "wind_speed_10m",
             "wind_gusts_10m", "wind_direction_10m", "cloud_cover"]
    j = get_json(OM, om_params(pt, tz, hourly=",".join(vars_), models=",".join(models), past_days=past_days))
    return ModelGrid.from_json(j, models)


def profiles(pt, tz, past_days):
    vars_ = [f"{v}_{p}hPa" for p in LEVELS for v in ("temperature", "relative_humidity", "geopotential_height", "wind_speed")]
    j = get_json(OM, om_params(pt, tz, hourly=",".join(vars_), models=",".join(PROFILE_MODELS), past_days=past_days))
    return ModelGrid.from_json(j, PROFILE_MODELS)


def ensemble(pt, tz, model):
    j = get_json("https://ensemble-api.open-meteo.com/v1/ensemble", om_params(pt, tz, hourly="precipitation", models=model))
    return ModelGrid.from_json(j, [model])


def previous_runs(lat, lon, elev_ft, model, start, end, tz):
    """As-issued forecasts for a past period (calibration): precip at lead days 1-7 and 2 m
    temperature at lead day 1, at the gauge's elevation. 8 variables: one call's worth of quota."""
    hourly = [f"precipitation_previous_day{n}" for n in range(1, 8)] + ["temperature_2m_previous_day1"]
    q = dict(latitude=lat, longitude=lon, elevation=round(elev_ft * FT), models=model, start_date=start, end_date=end,
             timezone=tz, cell_selection="nearest", hourly=",".join(hourly))
    return get_json("https://previous-runs-api.open-meteo.com/v1/forecast", q)


def elevations(pts):
    """DEM elevation (ft) for [(lat, lon)], paced: Open-Meteo counts every coordinate."""
    out = []
    for i in range(0, len(pts), 100):
        c = pts[i:i + 100]
        j = get_json("https://api.open-meteo.com/v1/elevation",
                     dict(latitude=",".join(f"{a:.4f}" for a, _ in c), longitude=",".join(f"{b:.4f}" for _, b in c)))
        out += [e / FT for e in j["elevation"]]
        time.sleep(len(c) / 8)  # ~480 coordinates/min, under the 600/min free limit
    return out


def has_model(lat, lon, model):
    j = get_json(OM, dict(latitude=lat, longitude=lon, hourly="precipitation", models=model, forecast_days=2))
    return any(v is not None for v in j.get("hourly", {}).get("precipitation", []))


# ---------------------------------------------------------------- stations

def std_offset_h(tz):
    """UTC offset (h) of the zone's standard time: station days ignore DST, like SNOTEL's."""
    return datetime(2025, 1, 15, tzinfo=ZoneInfo(tz)).utcoffset().total_seconds() / 3600


def _local_days(rows, tz, utc_offset_h=0.0):
    """Group (time, value) rows (time at UTC+utc_offset_h) into the zone's standard-time days."""
    shift = timedelta(hours=std_offset_h(tz) - utc_offset_h)
    by = {}
    for t, v in sorted(rows):
        by.setdefault((datetime.fromisoformat(t) + shift).date().isoformat(), []).append(v)
    return by


def _mean(vs, per_day):
    return sum(vs) / len(vs) if len(vs) >= 0.75 * per_day else None


def _cumulative(daily_mm, start, end):
    """Daily totals (mm) -> start-of-day running total (in), SNOTEL style. A day without a
    total leaves the next reading out, so no daily amount is made up across the gap."""
    out, cum, d = [], 0.0, date.fromisoformat(start)
    gap = False
    while d.isoformat() <= end:
        k = d.isoformat()
        if not gap:
            out.append((k, cum / IN))
        gap = daily_mm.get(k) is None
        cum += daily_mm.get(k) or 0.0
        d += timedelta(1)
    return out


BC_ASWS = "https://www.env.gov.bc.ca/wsd/data_searches/snow/asws/data/"


def _bc_column(sid, since):
    """Parser for a BC ASWS wide CSV (hourly UTC rows, one column per station): one station's rows."""
    def parse(fh):
        r = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        i = next((k for k, c in enumerate(next(r)) if c.split(" ")[0] == sid), None)
        return [] if i is None else [(row[0], float(row[i])) for row in r
                                     if row and row[0] >= since and i < len(row) and row[i] not in ("", "NaN")]
    return parse


BC_FILES = {"PC": ("PC.csv", "PC_Archive.csv"), "SD": ("SD.csv", "SD_Archive.csv"),  # (current, archive)
            "TA": ("TA.csv", "TA_Archive.csv"), "SW": ("SWDaily.csv", "SW_DailyArchive.csv")}


def bc_asws(element, sid, start):
    """(UTC time, value) rows for one BC snow-pillow station since start. The current files cover
    the water year (from Oct 1); older dates come from the archive (6-85 MB, extracted once)."""
    today = date.today()
    wy = f"{today.year - (today.month < 10)}-10-01"
    current, archive = BC_FILES[element]
    if start >= wy:
        return get_json(BC_ASWS + current, parse=_bc_column(sid, start))
    since = min(s for s, _ in SEASONS)  # cache per water year: the archive gains the last one each autumn
    rows = cached(f"bcasws_{element}_{sid}_{since}_{wy}.json", lambda: get_json(BC_ASWS + archive, parse=_bc_column(sid, since)))
    return [r for r in rows if r[0] >= start] + bc_asws(element, sid, wy)


AB_RIVERS = "https://rivers.alberta.ca/"
AB_UTC_H = -7  # Alberta Rivers timestamps are MST all year


def _wiski_csv(fh):
    """Alberta WISKI download: '#' header lines, then 'Timestamp;Value' rows."""
    return [(t, float(v)) for t, v in (line.strip().split(";") for line in io.TextIOWrapper(fh)
                                       if line.strip() and not line.startswith("#")) if v not in ("", "NaN")]


def _alberta(gauge, start, end, tz):
    """Alberta Rivers station, id 'station:precip-TSID'. Precipitation history (15-min increments)
    from the WISKI download; air temperature (hourly) only from the 5-day live table. Its snow depth
    is skipped: readings jump between -25 and 290 cm."""
    sid, tsid = gauge.id.split(":")
    pc = get_json(AB_RIVERS + "WiskiLiveDataService/Download",
                  dict(tsId=tsid, to=end, filename="pc", zip="false", json="false", **{"from": start}), parse=_wiski_csv)
    daily = {d: sum(vs) for d, vs in _local_days(pc, tz, AB_UTC_H).items() if len(vs) >= 0.75 * 96}
    raw = {"PREC": _cumulative(daily, start, end)}
    if end >= (date.today() - timedelta(4)).isoformat():
        j = get_json(AB_RIVERS + f"apps/Basins/data/figures/river/abrivers/envdata/envdata_{sid}_table.json")
        cols = [m["parameter_name"] for m in j["ts_metadata"]]
        if "TA" in cols:
            k = cols.index("TA") + 1
            rows = [(r["values"][0], r["values"][k]) for r in j["data"] if r["values"][k] is not None]
            raw["TAVG"] = [(d, m * 1.8 + 32) for d, vs in _local_days(rows, tz, AB_UTC_H).items() if (m := _mean(vs, 24)) is not None]
    return raw


def _bc(gauge, start, end, tz):
    """BC snow pillow: cumulative precip, SWE (daily, 8am PST), depth and air temp (hourly UTC)."""
    raw = {}
    for name, el, conv, agg in (("PC", "PREC", lambda mm: mm / IN, lambda vs: vs[0]),
                                ("SW", "WTEQ", lambda mm: mm / IN, lambda vs: vs[0]),
                                ("SD", "SNWD", lambda cm: cm / 2.54, lambda vs: vs[0]),
                                ("TA", "TAVG", lambda c: c * 1.8 + 32, lambda vs: _mean(vs, 24))):
        if name == "SD" and start < (date.today() - timedelta(30)).isoformat():
            continue  # depth isn't used for calibration: skip its 43 MB archive
        rows = [r for r in bc_asws(name, gauge.id, start) if r[0][:10] <= end]
        raw[el] = [(d, conv(a)) for d, vs in _local_days(rows, tz).items() if (a := agg(vs)) is not None]
    return raw


@dataclass
class StationDay:
    """Start-of-day readings (in): cumulative gauge precip, snow water equivalent, snow depth;
    plus the day's mean air temperature (F)."""
    date: str
    precip: float | None = None
    swe: float | None = None
    depth: float | None = None
    temp_f: float | None = None


class StationSeries:
    """Daily readings from a gauge (SNOTEL, CDEC, BC ASWS, Alberta Rivers), plus the daily amounts derived from them."""

    def __init__(self, days):
        self.days = sorted(days, key=lambda d: d.date)

    @classmethod
    def fetch(cls, gauge, start, end, tz="UTC"):
        """Readings from start to end; tz: the resort's zone, for gauges that report hourly in UTC."""
        if gauge.kind == "bcasws":
            raw = _bc(gauge, start, end, tz)
        elif gauge.kind == "abrivers":
            raw = _alberta(gauge, start, end, tz)
        elif gauge.kind == "snotel":
            j = get_json("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data",
                         dict(stationTriplets=gauge.id, elements="PREC,WTEQ,SNWD,TAVG", duration="DAILY",
                              beginDate=start, endDate=end))
            raw = {e["stationElement"]["elementCode"]: [(v["date"][:10], v.get("value")) for v in e["values"]]
                   for e in (j[0]["data"] if j else [])}  # [] = no data for the range
        else:  # CDEC sensors: 2 precip, 3 SWE, 18 depth, 30 temp
            raw = {}
            for r in get_json("https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet",
                              dict(Stations=gauge.id, SensorNums="2,3,18,30", dur_code="D", Start=start, End=end)):
                v = r["value"]
                raw.setdefault({2: "PREC", 3: "WTEQ", 18: "SNWD", 30: "TAVG"}[r["SENSOR_NUM"]], []).append(
                    (datetime.strptime(r["date"], "%Y-%m-%d %H:%M").strftime("%Y-%m-%d"), None if v is None or v <= -9000 else v))
        by = {}
        for el, attr in (("PREC", "precip"), ("WTEQ", "swe"), ("SNWD", "depth"), ("TAVG", "temp_f")):
            for d, v in raw.get(el, []):
                if v is not None:
                    setattr(by.setdefault(d, StationDay(d)), attr, v)
        return cls(by.values())

    def latest(self, attr):
        return next((getattr(d, attr) for d in reversed(self.days) if getattr(d, attr) is not None), None)

    def daily(self):
        """Per-day amounts (in) from readings on consecutive days: depth gain, SWE change, gauge precip.
        A day before a missing reading gets no amounts (they'd span two days)."""
        out = []
        for a, b in zip(self.days, self.days[1:]):
            row = dict(date=a.date, depth_in=a.depth)
            if (date.fromisoformat(b.date) - date.fromisoformat(a.date)).days != 1:
                out.append(row)
                continue
            if a.depth is not None and b.depth is not None:
                row["new_snow_in"] = max(0.0, b.depth - a.depth)
            if a.swe is not None and b.swe is not None:
                row["swe_in"] = round(b.swe - a.swe, 2)
            if a.precip is not None and b.precip is not None and 0 <= b.precip - a.precip < 10:
                row["precip_in"] = round(b.precip - a.precip, 2)
            out.append(row)
        return out

    def precip_mm(self):
        """Calibration truth, {date: mm}: the gauge, unless it clearly missed a storm the snowpack
        recorded (storage gauges cap/freeze; Mammoth Pass caught 12" vs 31" SWE gain in 2024-25).
        Taking max() everywhere would bias high from SWE-pillow noise."""
        by = {d.date: d for d in self.days}

        def inc(attr):
            out = {}
            for k, d in by.items():
                nxt = by.get((datetime.fromisoformat(k) + timedelta(1)).strftime("%Y-%m-%d"))
                a, b = getattr(d, attr), getattr(nxt, attr) if nxt else None
                if a is not None and b is not None and -10 < b - a < 10:  # drop gauge resets / spikes
                    out[k] = max(0.0, b - a) * IN
            return out
        prec, swe = inc("precip"), inc("swe")
        out = {}
        for d in set(prec) | set(swe):
            p, w = prec.get(d), swe.get(d, 0.0)
            out[d] = w if (p is None or (w > 7.6 and p < 0.5 * w)) else p
        return out


def snotel_stations(state):
    return get_json("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/stations", dict(stateCds=state, networkCds="SNTL"))


# ---------------------------------------------------------------- NWS (US only)

@dataclass
class Alert:
    event: str
    severity: str | None
    headline: str | None
    onset: str | None
    ends: str | None
    description: str


@dataclass
class Discussion:
    office: str
    issued: str
    text: str


def nws_office(pt):
    """NWS forecast office covering a point (cached: it never changes)."""
    return cached(f"nws_office_{pt.lat}_{pt.lon}.json",
                  lambda: get_json(f"https://api.weather.gov/points/{pt.lat},{pt.lon}")["properties"]["gridId"])


def nws_alerts(pt):
    j = get_json("https://api.weather.gov/alerts/active", dict(point=f"{pt.lat},{pt.lon}"))
    return [Alert(p["event"], p.get("severity"), p.get("headline"), p.get("onset") or p.get("effective"),
                  p.get("ends") or p.get("expires"), (p.get("description") or "")[:1500])
            for p in (f["properties"] for f in j.get("features", []))]


def nws_discussion(office):
    g = get_json(f"https://api.weather.gov/products/types/AFD/locations/{office}")["@graph"][0]
    p = get_json(g["@id"])
    return Discussion(office, p["issuanceTime"], p["productText"])
