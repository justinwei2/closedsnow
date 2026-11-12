# snowreport

Snow forecasts for 25 ski resorts in western North America and Japan, built from free weather data.

Site: https://justinwei2.github.io/snowreport/ · Methodology: https://justinwei2.github.io/snowreport/methodology.html

Each resort has base, mid and summit points. For each point the site shows:

- snowfall for the past 7 days and next 10 days (6am–6am days, split into daytime / nighttime)
- likely ranges: middle 50% (25–75%) and middle 80% (10–90%) from ensembles plus the resort's historical forecast error
- snow quality and snow ratio, rain/snow line, freezing level
- hourly forecast, and 12 h snow / rain with forecast spread (scrollable: last 7 days and next 10)
- daily alerts: powder, wet / icy precipitation, wind holds, wind chill
- NWS alerts and forecast discussion (US)

## How it works

1. **Setup (once per resort, `calibrate.py`).** Snap the catalog coordinates to a 90 m DEM. Pick
   the nearest gauge (SNOTEL/CDEC in the US, a BC snow pillow, or an Alberta Rivers station) and the models that have data there. Compare two winters of
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
| `refresh` | hourly check | refresh a resort once a new ECMWF run has landed (~4×/day, within ~1 h) or its data is > 7 h old; one parallel job per due resort; save to the `data` branch, freshness check, trigger `pages` |
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
| `calibrate.py` | locate + calibrate + merge per-resort results |
| `update.py` | forecast pipeline, index, `--check` |
| `web/` | `index.html` (forecast), `map.html` (map), `methodology.html`; one `app.js` and `style.css` |

## Usage

```
python3 calibrate.py [--force]      # set up new resorts (--force: recalibrate all)
python3 update.py alta              # refresh one resort -> web/data/alta.json
python3 -m http.server -d web 8000  # preview
SKISITE_REPLAY=/tmp/replay python3 update.py alta   # record API responses, replay on later runs
```

## Adding a resort

1. Add a line to `CATALOG` in `resorts.py`:
   `key: (name, country, tz, (base lat, lon, ft, label), (summit lat, lon, ft, label), (mid ft, label))`.
   - Use the **published** base and top-lift elevations; rough coordinates are fine (within ~1 km).
     Setup snaps each to the terrain-map spot nearest that elevation (summit within 2 km, base 1.2 km).
   - **Mid** is optional but worth setting: name a real mid-mountain spot (main gondola top or
     mid-mountain lodge) and its published elevation, e.g. `(6069, "Roundhouse")`. Setup puts it on
     the base-to-summit line at that elevation. Without it, mid is the point 55% of the way up.
   - Separate ski areas (Whistler / Blackcomb, Palisades / Alpine) are separate entries.
2. Push. The nightly `backfill` locates and calibrates it (or run `gh workflow run backfill.yml
   -f resorts="key"`), and the next `refresh` publishes it.
3. Check the setup log line `key  base 2228/2214  mid 6069  summit 7087/7160  obs=...`: terrain-map vs
   listed elevations (a warning prints if they differ by > 600 ft), and the gauge picked.
4. Calibration only uses a gauge within 1,500 ft of mid-mountain (`GAUGE_NEAR_FT`); a valley gauge
   leaves the resort on raw models, with the reason shown. To re-place points after editing an
   entry: `python3 calibrate.py key --relocate` (keeps the calibration unless the gauge changes).

A resort outside the covered gauge networks (SNOTEL/CDEC in the US, BC snow pillows, Alberta
Rivers, JMA) also needs a gauge source: an adapter in `sources.py` mapping its data onto `StationSeries`,
plus the station search in `calibrate.py` (or a hand-picked gauge in `MANUAL`). Until then the
resort runs on the raw models with equal weights, and the page says it isn't calibrated.

## Data

Open-Meteo (CC BY 4.0; ECMWF, NOAA, ECCC, DWD and JMA models), api.weather.gov, NRCS SNOTEL, California
CDEC, BC River Forecast Centre automated snow weather stations (public data files), Alberta
Rivers (AEP) and the Japan Meteorological Agency (daily AMeDAS tables). The Alberta endpoints are the ones the rivers.alberta.ca site itself uses: not a
published API, and the data are provisional. Known limitations are listed on the methodology page.
