# opensnow

Snow forecasts for 21 western North America ski resorts, built from free weather data.
Unofficial; not affiliated with OpenSnow.

Site: https://justinwei2.github.io/opensnow/ · Methodology: https://justinwei2.github.io/opensnow/methodology.html

Each resort has base, mid and summit points. For each point the site shows:

- snowfall for the past 7 days and next 10 days (6am–6am days, split into morning / afternoon / night)
- likely ranges: middle 50% (25–75%) and middle 90% (5–95%) from ensembles plus the resort's historical forecast error
- snow quality and snow ratio, rain/snow line, freezing level
- hourly forecast, and 12 h snow / rain bars with forecast spread (current and history views)
- daily alerts: powder, wet / icy precipitation, wind holds, wind chill
- NWS alerts and forecast discussion (US)

## How it works

1. **Setup (once per resort, `calibrate.py`).** Snap the catalog coordinates to a 90 m DEM. Pick
   the nearest SNOTEL/CDEC gauge and the models that have data there. Compare two winters of
   as-issued forecasts (lead days 1–7) against the gauge to get, per model: a precipitation bias
   factor, blend weights for lead days 1–3 and 4+, and the resort's temperature bias on storm days.
2. **Refresh (`update.py`).** Pull 7–8 deterministic models, pressure-level profiles, 5 ensembles
   (~190 members), NWS alerts and station data. Blend precipitation with the calibrated
   weights. Derive rain/snow, snow ratio and snow level from the bias-corrected profile.
3. **Publish.** Write one JSON file per resort; the static page in `web/` renders it.

Stdlib Python only. No server: everything runs in GitHub Actions and is served by GitHub Pages.

## Workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `refresh` | hourly check | refresh a resort once a new ECMWF run has landed (~4×/day, within ~1 h) or its data is > 7 h old; parallel region jobs; save to the `data` branch, freshness check, trigger `pages` |
| `pages` | after refresh / on `web/` changes | deploy the site |
| `backfill` | nightly | set up new catalog resorts (no-op when all are set up); run manually with `force` to recalibrate every resort |
| `watchdog` | daily | check the live site is fresh |
| `autoretry` | on failure | re-run failed jobs once |

Failed runs email the repo owner. Open-Meteo's free tier (10k calls/day) is the budget: a
refresh costs ~65–85 calls per resort (~7k/day for 21 resorts); calibration ~200 per resort.

## Files

| File | Contents |
|---|---|
| `resorts.py` | catalog, constants, `Resort` / `Point` / `Gauge` / `Calibration` records |
| `sources.py` | HTTP (retries, quota handling, record/replay) and adapters: `ModelGrid`, `StationSeries`, `Alert` |
| `conditions.py` | wet-bulb, snow fraction, snow level, Kuchera snow ratio, quality labels, weather report |
| `calibrate.py` | locate + calibrate + merge region results |
| `update.py` | forecast pipeline, index, `--check` |

## Usage

```
python3 calibrate.py [--force]      # set up new resorts (--force: recalibrate all)
python3 update.py alta              # refresh one resort -> web/data/alta.json
python3 -m http.server -d web 8000  # preview
SKISITE_REPLAY=/tmp/replay python3 update.py alta   # record API responses, replay on later runs
```

To add a resort, add a line to `CATALOG` in `resorts.py`; the next `backfill` run sets it up.

A resort in a **new geographic region** also needs calibration support for that region: a gauge
source (an adapter in `sources.py` mapping its data onto `StationSeries`, like SNOTEL/CDEC in the
US or BC's snow-pillow network) and the station search in `calibrate.py`, plus the region in
`GROUPS` (and `NON_US` / `SNOTEL_STATES` as appropriate). Until then the resort runs on regional
default factors borrowed from calibrated resorts, and the page says it isn't calibrated.

## Data

Open-Meteo (CC BY 4.0; ECMWF, NOAA, ECCC and DWD models), api.weather.gov, NRCS SNOTEL, California
CDEC. Documented APIs only. Known limitations are listed on the methodology page.
