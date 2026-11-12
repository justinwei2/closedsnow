"""HTTP + data-source helpers (Open-Meteo, NRCS SNOTEL, CDEC, NWS)."""
import json, os, random, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta
from config import USER_AGENT

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)
IN = 25.4
WAIT_ON_QUOTA = False  # backfill.py sets True: sleep through Open-Meteo hourly/daily limits


class QuotaExceeded(RuntimeError):
    pass


def write_json(path, obj, **kw):
    """Atomic write: a killed job never leaves a truncated file behind."""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, **kw)
    json.load(open(tmp))  # validate
    os.replace(tmp, path)


def cache_path(name):
    return os.path.join(CACHE, name)


def get_json(url, params=None, retries=6):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    attempt = 0
    while attempt < retries:
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            reason = ""
            try:
                reason = json.load(e).get("reason", "")
            except Exception:
                pass
            if e.code == 429 and WAIT_ON_QUOTA and ("Hourly" in reason or "Daily" in reason):
                wait = 3600 if "Daily" in reason else 600
                print(f"  quota: {reason} - sleeping {wait // 60} min", file=sys.stderr)
                time.sleep(wait)
                continue  # quota waits don't use up retries
            if e.code == 429 and ("Hourly" in reason or "Daily" in reason):
                raise QuotaExceeded(reason)
            if 400 <= e.code < 500 and e.code not in (408, 429):
                raise RuntimeError(f"HTTP {e.code} {reason}: {url[:160]}")  # permanent: don't retry
            print(f"  retry {attempt}: {e} {reason} {url[:120]}", file=sys.stderr)
        except Exception as e:
            print(f"  retry {attempt}: {e} {url[:120]}", file=sys.stderr)
        attempt += 1
        time.sleep(min(60, 3 * 2 ** attempt) + random.uniform(0, 2))  # exponential backoff + jitter
    raise RuntimeError(f"failed: {url[:200]}")


def _snotel(triplet, start, end):
    j = get_json("https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1/data",
                 dict(stationTriplets=triplet, elements="PREC,WTEQ,SNWD,TAVG", duration="DAILY",
                      beginDate=start, endDate=end))
    return {e["stationElement"]["elementCode"]: [(v["date"], v.get("value")) for v in e["values"]]
            for e in (j[0]["data"] if j else [])}  # [] = station has no data for the range


def _cdec(station, start, end):
    # daily sensors: 2 = accumulated precip (in), 3 = SWE (in), 18 = snow depth (in), 30 = avg air temp (F)
    rows = get_json("https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet",
                    dict(Stations=station, SensorNums="2,3,18,30", dur_code="D", Start=start, End=end))
    name = {2: "PREC", 3: "WTEQ", 18: "SNWD", 30: "TAVG"}
    out = {}
    for r in rows:
        v = r["value"]
        out.setdefault(name[r["SENSOR_NUM"]], []).append(
            (datetime.strptime(r["date"], "%Y-%m-%d %H:%M").strftime("%Y-%m-%d"), None if v is None or v <= -9000 else v))
    return out


def obs_raw(obs, start, end):
    """Daily station series {element: [(date, value)]} from SNOTEL or CDEC."""
    kind, sid = obs[0], obs[1]
    if kind == "snotel":
        return _snotel(sid, start, end)
    return _cdec(sid, start, end)


def obs_daily(obs, start, end):
    """-> {date: precip mm on that local day} from cumulative start-of-day PREC."""
    path = cache_path(f"obs_{obs[1].replace(':', '_')}_{start}_{end}.json")
    if not os.path.exists(path):
        json.dump(obs_raw(obs, start, end), open(path, "w"))
    raw = json.load(open(path))
    def increments(el):
        vals = {date.fromisoformat(d[:10]): v for d, v in raw.get(el, []) if v is not None}
        inc = {}
        for d, v in vals.items():
            n = vals.get(d + timedelta(1))
            if n is not None and -10 < n - v < 10:  # drop gauge resets / spikes
                inc[d] = max(0.0, n - v) * IN
        return inc
    # Gauge catch, unless the gauge clearly missed a storm the snowpack recorded (storage gauges
    # cap/freeze; Mammoth Pass caught 12" vs 31" SWE gain in 2024-25). Taking max() everywhere
    # would bias obs high from SWE pillow noise, so only substitute on a clear miss.
    prec, swe = increments("PREC"), increments("WTEQ")
    out = {}
    for d in set(prec) | set(swe):
        p, w = prec.get(d), swe.get(d, 0.0)
        out[d] = w if (p is None or (w > 7.6 and p < 0.5 * w)) else p
    return out
