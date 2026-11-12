"""Snow conditions: rain/snow phase, snow level, snow-to-liquid ratio, quality labels, and the
daily weather report (wind holds, powder mornings, rain line, visibility, cold).

Provenance (full list on the methodology page):
- Rain/snow: logistic in wet-bulb, 50% at TW50. Jennings et al. 2018 put the Sierra's 50% *air*
  temperature threshold at ~0.6-1.5 C; +0.5 C wet-bulb sits in that range for moist air.
- SLR: Kuchera ramp on the warmest temperature between the elevation and 500 hPa.
- Wind penalty, density classes and every report threshold: rules of thumb.
"""
import math
from dataclasses import dataclass
from datetime import date, timedelta

TW50, TW_SCALE = 0.5, 0.35  # snow fraction 50% at Tw=0.5 C; ~90% at -0.3 C, ~10% at 1.3 C
KUCHERA_T0 = 271.16         # K
SLR_MIN, SLR_MAX = 4.0, 25.0
WARM_NOSE_C = 2.0           # melting layer aloft for sleet / freezing rain
LIFT_HOURS = range(8, 16)   # 8am-4pm local
HOLD_LIKELY, HOLD_POSSIBLE = (45, 65), (30, 45)  # ridge (sustained, gust) mph
GUST_FACTOR = 1.4           # gust ~ 1.4x sustained over complex terrain
POWDER_MORNING_IN = 8       # summit snow 4pm prior day -> 9am
WIND_CHILL_F = -10
LOW_VIS_CLOUD = 90          # % cloud with precipitation during lift hours
DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


@dataclass
class Hour:
    """Derived forecast for one hour at one elevation."""
    t: str
    precip_mm: float | None = None
    temp_f: float | None = None
    wind_mph: float | None = None
    gust_mph: float | None = None
    wind_dir: float | None = None
    cloud: float | None = None
    frac: float | None = None   # share falling as snow
    slr: float | None = None    # snow-to-liquid ratio
    tw_c: float | None = None
    warm_nose: bool = False
    snow_in: float | None = None
    rain_in: float | None = None

    @property
    def hour(self):
        return int(self.t[11:13])


# ---------------------------------------------------------------- physics

def wet_bulb(t, rh):
    """Stull (2011) wet-bulb approximation, deg C. Valid ~5-99% RH, -20..50 C."""
    rh = min(max(rh, 5.0), 99.0)
    return (t * math.atan(0.151977 * math.sqrt(rh + 8.313659)) + math.atan(t + rh)
            - math.atan(rh - 1.676331) + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh) - 4.686035)


def snow_fraction(tw):
    return 1.0 / (1.0 + math.exp((tw - TW50) / TW_SCALE))


def interp(z, zs, vs):
    """Linear interpolation of vs at height z; zs ascending. Extrapolate flat."""
    if z <= zs[0]:
        return vs[0]
    for i in range(1, len(zs)):
        if z <= zs[i]:
            return vs[i - 1] + (z - zs[i - 1]) / (zs[i] - zs[i - 1]) * (vs[i] - vs[i - 1])
    return vs[-1]


class Profile:
    """One hour's sounding: heights (m), temp (C), rh (%), wind (km/h); sorted by height."""

    def __init__(self, z, t, rh, wind):
        order = sorted(range(len(z)), key=lambda i: z[i])
        self.z, self.t, self.rh, self.w = ([s[i] for i in order] for s in (z, t, rh, wind))
        self.tw = [wet_bulb(a, b) for a, b in zip(self.t, self.rh)]

    def at(self, z):
        return dict(t=interp(z, self.z, self.t), rh=interp(z, self.z, self.rh),
                    tw=interp(z, self.z, self.tw), wind=interp(z, self.z, self.w))

    def crossing(self, values, thresh):
        """Highest height where values cross thresh; lowest level if the whole column is below."""
        best = None
        for i in range(1, len(self.z)):
            a, b = values[i - 1], values[i]
            if (a - thresh) * (b - thresh) <= 0 and a != b:
                best = self.z[i - 1] + (thresh - a) / (b - a) * (self.z[i] - self.z[i - 1])
        return self.z[0] if best is None and values[0] < thresh else best

    def freezing_level(self):
        return self.crossing(self.t, 0.0)

    def snow_level(self):
        """Height of 50% snow (wet-bulb = TW50)."""
        return self.crossing(self.tw, TW50)

    def column_tmax(self, z):
        return max([self.at(z)["t"]] + [t for zz, t in zip(self.z, self.t) if zz > z])

    def warm_nose(self, z):
        """Classic sleet/freezing-rain sounding: snow grows aloft (< -5 C above the warm layer),
        melts in a layer warmer than WARM_NOSE_C, then falls into sub-freezing air at z."""
        if self.at(z)["tw"] > 0.0:
            return False
        above = [t for zz, t in zip(self.z, self.t) if zz > z + 100]
        for k, t in enumerate(above):
            if t > WARM_NOSE_C:
                return any(t2 < -5.0 for t2 in above[k + 1:])
        return False


def slr_at(profile, z):
    """Kuchera snow-to-liquid ratio, minus up to 30% for wind (fragmentation packs snow)."""
    tk = profile.column_tmax(z) + 273.15
    slr = 12 + 2 * (KUCHERA_T0 - tk) if tk > KUCHERA_T0 else 12 + (KUCHERA_T0 - tk)
    slr *= max(0.7, 1.0 - 0.0025 * max(0.0, profile.at(z)["wind"] - 30.0))
    return min(SLR_MAX, max(SLR_MIN, slr))


def quality(slr, frac, warm_nose=False):
    """Skier-facing label for precipitation at an elevation (first match wins)."""
    if frac < 0.2:
        return "rain"
    if warm_nose:
        return "sleet / freezing rain risk"
    if frac < 0.8:
        return "mixed rain/snow"
    dens = 100.0 / slr
    for cut, label in ((6, "blower"), (8, "light & dry"), (11, "medium"), (15, "dense")):
        if dens < cut:
            return label
    return "heavy / wet (Sierra cement)"


# ---------------------------------------------------------------- weather report

def circ_mean(degs):
    x = sum(math.sin(math.radians(d)) for d in degs); y = sum(math.cos(math.radians(d)) for d in degs)
    return math.degrees(math.atan2(x, y)) % 360


def compass(deg):
    return None if deg is None else DIRS[int(((deg % 360) + 22.5) // 45) % 8]


def wind_chill(t_f, v_mph):
    if t_f is None or v_mph is None or t_f > 50 or v_mph < 3:
        return t_f
    return 35.74 + 0.6215 * t_f - 35.75 * v_mph ** 0.16 + 0.4275 * t_f * v_mph ** 0.16


def lift_hours(hours, day):
    return [h for h in hours if h.t.startswith(day) and h.hour in LIFT_HOURS]


def wind_hold(summit, day):
    hrs = lift_hours(summit, day)
    sus = [h.wind_mph for h in hrs if h.wind_mph is not None]
    if not sus:
        return None
    s = max(sus)
    g = max(max(h.gust_mph or 0, (h.wind_mph or 0) * GUST_FACTOR) for h in hrs)
    dirs = [h.wind_dir for h in hrs if h.wind_dir is not None]
    level = ("likely" if s >= HOLD_LIKELY[0] or g >= HOLD_LIKELY[1] else
             "possible" if s >= HOLD_POSSIBLE[0] or g >= HOLD_POSSIBLE[1] else "low")
    return dict(level=level, sustained_mph=round(s), gust_mph=round(g), dir=compass(circ_mean(dirs)) if dirs else None)


def powder_morning(summit, day):
    """Summit snow from 4pm the day before through 9am."""
    prev = (date.fromisoformat(day) - timedelta(1)).isoformat()
    return sum(h.snow_in or 0 for h in summit
               if (h.t.startswith(prev) and h.hour >= 16) or (h.t.startswith(day) and h.hour < 9))


def report(day, entry, hours_by_elev, elevs_ft):
    """Notes [{kind, level: info|watch|warn, text}] and the wind-hold verdict for one day."""
    notes = []
    note = lambda kind, level, text: notes.append(dict(kind=kind, level=level, text=text))
    summit = hours_by_elev["summit"]
    w = wind_hold(summit, day)
    if w and w["level"] != "low":
        note("wind", "warn" if w["level"] == "likely" else "watch",
             f"Upper-lift wind holds {w['level']}: ridge {w['dir'] or ''} {w['sustained_mph']} mph, "
             f"gusts ~{w['gust_mph']} mph during lift hours")
    pm = powder_morning(summit, day)
    if pm >= POWDER_MORNING_IN:
        note("powder", "info", f"Powder morning: ~{round(pm)}\" at the summit overnight; upper terrain may "
                               "open late for avalanche control")
    s = entry["summit"]
    sl = entry.get("snow_level_max_ft") or entry.get("snow_level_ft")
    if (s.get("p50", 0) >= 1 or s.get("liquid_in", 0) >= 0.1) and sl is not None:
        if sl > elevs_ft["summit"]:
            note("rain", "warn", f"Rain to the summit (snow level ~{round(sl, -2):,.0f}′)")
        elif sl > elevs_ft["base"]:
            note("rain", "watch", f"Rain/snow line ~{round(sl, -2):,.0f}′, between base ({elevs_ft['base']:,}′) "
                                  f"and summit: wet snow or rain on lower lifts")
    if any(entry[e].get("quality") == "sleet / freezing rain risk" for e in hours_by_elev):
        note("sleet", "warn", "Warm layer aloft: sleet / freezing rain possible (icy lifts)")
    hrs = lift_hours(summit, day)
    if sum(1 for h in hrs if (h.precip_mm or 0) > 0.3 and (h.cloud or 0) >= LOW_VIS_CLOUD) >= 4:
        note("vis", "info", "Low visibility / flat light on the upper mountain")
    chills = [c for c in (wind_chill(h.temp_f, h.wind_mph) for h in hrs) if c is not None]
    if chills and min(chills) <= WIND_CHILL_F:
        note("cold", "watch", f"Wind chill near {round(min(chills))}°F at the summit")
    return notes, w
