# opensnow (unofficial)

An open-source, personal take on OpenSnow-style mountain forecasts. **Not affiliated with or
endorsed by OpenSnow.** Live: https://justinwei2.github.io/opensnow/ ·
[how it works](https://justinwei2.github.io/opensnow/methodology.html) (formulas, thresholds, labels).

On-mountain base / mid / summit forecasts for ~35 western North America resorts: a
bias-corrected multi-model blend with 25–75% ranges, snow quality, snow level vs lift heights, a
daily weather report (wind holds, powder mornings, rain line), NWS alerts and discussions.
Python stdlib only; the site is static (`web/`).

## How it runs (all on GitHub)
| Workflow | When | What |
|---|---|---|
| `refresh` | every 3 h | 4 parallel region jobs refresh resorts whose data is > 7.5 h old, save JSON to the single-commit `data` branch, run `update.py --check`, trigger `pages` |
| `pages` | after refresh, or on `web/` changes | deploy `web/` + data to GitHub Pages (3 attempts) |
| `backfill` | nightly / manual | locate + calibrate new catalog resorts per region; commits `data/` and [CALIBRATION.md](CALIBRATION.md) |
| `watchdog` | daily | checks the live site is fresh |
| `autoretry` | after a failure | re-runs failed jobs once (not health-check failures) |

A failed run emails the repo owner (github.com/settings/notifications → Actions). Open-Meteo's
free tier (10k calls/day) is the budget: a refresh is ~65–80 calls per resort; calibration
~200 per US resort, once.

## Files
`catalog.py` resorts · `locate.py` snap points to terrain, pick gauge and models · `calibrate.py`
bias factors from as-issued forecasts · `update.py` live forecast → JSON, `--check` freshness alarm · `physics.py` rain/snow,
snow level, snow ratio, labels · `report.py` weather-report notes ·
`backfill.py` locate + calibrate orchestration · `sources.py` HTTP/retries and station data.

Add a resort: one line in `catalog.py`; the next backfill sets it up.
Local preview: `python3 update.py alta && python3 -m http.server -d web 8000`.

## Data
Open-Meteo (CC BY 4.0; ECMWF, NOAA, ECCC, DWD models), api.weather.gov, NRCS SNOTEL, California
CDEC, all via documented APIs. Limitations are listed on the methodology page.
