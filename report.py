"""Daily weather report: wind holds, powder mornings, rain on the lifts, visibility, cold.

All thresholds are rules of thumb (not calibrated against lift-status history). Wind holds vary
by lift: exposed upper chairs, gondolas and trams close first.
"""
LIFT_HOURS = range(8, 16)          # 8am-4pm local

# Ridge (free-air at summit elevation) sustained wind / estimated gust, mph
HOLD_LIKELY = dict(sustained=45, gust=65)
HOLD_POSSIBLE = dict(sustained=30, gust=45)
GUST_FACTOR = 1.4                  # gust ~ 1.4x sustained over complex terrain (heuristic)
POWDER_MORNING_IN = 8              # summit snow 4pm prior day -> 9am
WIND_CHILL_F = -10
LOW_VIS_CLOUD = 90                 # % cloud cover with precipitation during lift hours

DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def circ_mean(degs):
    import math
    x = sum(math.sin(math.radians(d)) for d in degs); y = sum(math.cos(math.radians(d)) for d in degs)
    return math.degrees(math.atan2(x, y)) % 360


def compass(deg):
    return None if deg is None else DIRS[int(((deg % 360) + 22.5) // 45) % 8]


def wind_chill(t_f, v_mph):
    if t_f is None or v_mph is None or t_f > 50 or v_mph < 3:
        return t_f
    return 35.74 + 0.6215 * t_f - 35.75 * v_mph ** 0.16 + 0.4275 * t_f * v_mph ** 0.16


def lift_hours(rows, day):
    return [x for x in rows if x["t"].startswith(day) and int(x["t"][11:13]) in LIFT_HOURS]


def wind_hold(summit_rows, day):
    hrs = lift_hours(summit_rows, day)
    sus = [x.get("wind_mph") for x in hrs if x.get("wind_mph") is not None]
    gst = [max(x.get("gust_mph") or 0, (x.get("wind_mph") or 0) * GUST_FACTOR) for x in hrs]
    if not sus:
        return None
    s, g = max(sus), max(gst)
    dirs = [x.get("wind_dir") for x in hrs if x.get("wind_dir") is not None]
    d = compass(circ_mean(dirs)) if dirs else None
    level = ("likely" if s >= HOLD_LIKELY["sustained"] or g >= HOLD_LIKELY["gust"] else
             "possible" if s >= HOLD_POSSIBLE["sustained"] or g >= HOLD_POSSIBLE["gust"] else "low")
    return dict(level=level, sustained_mph=round(s), gust_mph=round(g), dir=d)


def powder_morning(summit_rows, day):
    """Snow at the summit from 4pm the day before through 9am."""
    from datetime import date, timedelta
    prev = (date.fromisoformat(day) - timedelta(1)).isoformat()
    tot = sum(x.get("snow_in") or 0 for x in summit_rows
              if (x["t"].startswith(prev) and int(x["t"][11:13]) >= 16)
              or (x["t"].startswith(day) and int(x["t"][11:13]) < 9))
    return tot


def build(day, entry, rows_by_elev, elevs_ft):
    """List of {kind, level, text} for one day. level: info | watch | warn."""
    notes = []
    sr = rows_by_elev["summit"]
    w = wind_hold(sr, day)
    if w and w["level"] != "low":
        notes.append(dict(kind="wind", level="warn" if w["level"] == "likely" else "watch",
                          text=f"Upper-lift wind holds {w['level']}: ridge {w['dir'] or ''} "
                               f"{w['sustained_mph']} mph, gusts ~{w['gust_mph']} mph during lift hours"))
    pm = powder_morning(sr, day)
    if pm >= POWDER_MORNING_IN:
        notes.append(dict(kind="powder", level="info",
                          text=f"Powder morning: ~{round(pm)}\" at the summit overnight; upper terrain may "
                               "open late for avalanche control"))
    s = entry["summit"]
    if s.get("p50", 0) >= 1 or s.get("liquid_in", 0) >= 0.1:
        sl = entry.get("snow_level_max_ft") or entry.get("snow_level_ft")
        if sl is not None:
            if sl > elevs_ft["summit"]:
                notes.append(dict(kind="rain", level="warn", text=f"Rain to the summit (snow level ~{round(sl, -2):,.0f}′)"))
            elif sl > elevs_ft["base"]:
                notes.append(dict(kind="rain", level="watch",
                                  text=f"Rain/snow line ~{round(sl, -2):,.0f}′, between base ({elevs_ft['base']:,}′) "
                                       f"and summit: wet snow or rain on lower lifts"))
    if any(entry[e].get("quality") == "sleet / freezing rain risk" for e in rows_by_elev):
        notes.append(dict(kind="sleet", level="warn", text="Warm layer aloft: sleet / freezing rain possible (icy lifts)"))
    hrs = lift_hours(sr, day)
    wet_cloudy = [x for x in hrs if (x.get("precip_mm") or 0) > 0.3 and (x.get("cloud") or 0) >= LOW_VIS_CLOUD]
    if len(wet_cloudy) >= 4:
        notes.append(dict(kind="vis", level="info", text="Low visibility / flat light on the upper mountain"))
    chills = [wind_chill(x.get("temp_f"), x.get("wind_mph")) for x in hrs]
    chills = [c for c in chills if c is not None]
    if chills and min(chills) <= WIND_CHILL_F:
        notes.append(dict(kind="cold", level="watch", text=f"Wind chill near {round(min(chills))}°F at the summit"))
    return notes, w
