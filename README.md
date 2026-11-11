# Snow Report: personal resort forecast

On-mountain (base / mid / summit) snow forecasts for ~35 expert-worthy Ikon/Epic resorts in
western North America. Shows a bias-corrected multi-model blend with 25–75% ranges, a weather
report (wind holds, powder mornings, rain line), snow level vs.
lift elevations, snow quality (blower → Sierra cement, mixed, sleet, rain), a per-model comparison,
recent station observations, and the NWS Area Forecast Discussion.

    python3 backfill.py    # one-time: locate new catalog resorts + calibrate (sleeps through rate limits)
    python3 update.py      # refresh favorites -> web/data/*.json   (--scheduled: tiered, as in CI)
    python3 serve.py       # local: http://localhost:8765, favorites every 3 h, others on demand

## Hosted (GitHub Pages + Actions)
`.github/workflows/refresh.yml` runs every 3 h on GitHub: favorites refresh every run, other
resorts every ~12 h (inside Open-Meteo's free 10k/day). Each run commits `web/data` (forecast
history; also keeps the schedule alive) and deploys `web/` to Pages. `backfill.yml` (manual) runs
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
