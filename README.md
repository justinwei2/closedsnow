# opensnow (unofficial)

An open-source, personal take on OpenSnow-style mountain forecasts. **Not affiliated with or
endorsed by OpenSnow.** Live: https://justinwei2.github.io/opensnow/

## Snow Report: personal resort forecast

On-mountain (base / mid / summit) snow forecasts for ~35 expert-worthy Ikon/Epic resorts in
western North America. Shows a bias-corrected multi-model blend with 25–75% ranges, a weather
report (wind holds, powder mornings, rain line), snow level vs.
lift elevations, snow quality (blower → Sierra cement, mixed, sleet, rain), a per-model comparison,
recent station observations, and the NWS Area Forecast Discussion.

    python3 backfill.py    # one-time: locate new catalog resorts + calibrate (sleeps through rate limits)
    python3 update.py      # refresh favorites -> web/data/*.json   (--scheduled: tiered, as in CI)
    python3 serve.py       # local: http://localhost:8765, favorites every 3 h, others on demand

## Hosted (GitHub Pages + Actions)
`.github/workflows/refresh.yml` runs every 3 h on GitHub as four parallel, staggered region jobs
(sierra / west / rockies / north; own runner each, so a rate limit in one region doesn't stall the
others), then one publish job. Favorites refresh every run, other resorts every ~12 h (~7.5k of
Open-Meteo's free 10k calls/day). Requests retry with exponential backoff; on a hard quota limit a
region stops and keeps its previous data. Calibration is one-time (~200 calls per US resort); a
refresh costs ~65-80 calls per resort. Forecast JSON lives on a single-commit
`data` branch (force-pushed each run, so the repo doesn't grow); each run restores it, refreshes,
saves it and deploys `web/` to Pages. `backfill.yml` (manual) runs
locate + calibrate in CI. Optional repo secret `SKISITE_CONTACT` (email) goes in the NWS User-Agent.
Repo must be public for free Pages. The static site has no on-demand refresh.

No dependencies beyond Python 3.11+ stdlib. Open-Meteo free tier (non-commercial, 10k calls/day);
one full refresh is ~40 requests.

## Adding a resort
Add a line to `catalog.py` (approximate base/summit coordinates + published elevations + pass),
then run `python3 backfill.py`: it snaps the points to the DEM, picks the nearest SNOTEL gauge,
probes which models have data there, and calibrates. Canadian resorts have no SNOTEL, so they use
regional default factors (the page says so).

## Files
- `config.py`: resorts, models, levels
- `calibrate.py`: per-resort/model precip factors + weights + error quantiles from as-issued forecasts
- `physics.py`: wet-bulb, snow fraction, snow level, Kuchera SLR, warm-nose sleet flag, quality labels
- `update.py`: live fetch + blend + ranges → JSON
- `sources.py`: HTTP, SNOTEL, CDEC helpers
- `web/`: static site (no build step)

## Failures, retries & alerts
- HTTP: exponential backoff with jitter; Open-Meteo hourly/daily quota stops a region cleanly
  (previous data kept). Resorts that error get a second pass after 60 s. Data-branch pushes and
  the Pages deploy retry.
- `health.py` runs at the end of every refresh and fails the run when a favorite is > 9 h old
  or another calibrated resort > 30 h old (one missed refresh doesn't trip it). A failed run
  makes GitHub email you.
- `watchdog.yml` checks the live site daily from the outside (catches a schedule that silently
  stopped). `backfill.yml` runs nightly and is a no-op once every resort is calibrated.
- Emails go to the account that last changed the workflow schedules (justinwei2). Check
  github.com/settings/notifications → System → Actions → Email, "only failed workflows".

## Known limitations
- Snow level / phase come from pressure-level profiles (1000-500 hPa). At high resorts (Colorado,
  Utah, Taos) the lowest levels sit below the model terrain and are extrapolated by the model; the
  reported snow level can then read "below base". Interpolation at lift heights uses levels above.
- Calibration factors and the error shown are fit and scored on the same two winters (in-sample,
  so slightly optimistic); ECMWF IFS 9 km and AIFS have one winter of history.
- Canadian resorts have no SNOTEL gauge: factors are borrowed medians from US resorts.
- Wind-hold, snow-density and powder-morning thresholds are rules of thumb, not fit to history.

## Data & attribution
Open-Meteo free API (non-commercial use; data CC BY 4.0, attribution in the site footer),
api.weather.gov (US public domain; contact in User-Agent), NRCS AWDB / SNOTEL and California CDEC
(public). All via documented APIs: no HTML scraping.
