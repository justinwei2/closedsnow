"""Slow, resumable setup for new resorts: locate -> calibrate -> index. Sleeps through
Open-Meteo free-tier hourly/daily limits instead of failing. Safe to rerun; skips done work.

    python3 backfill.py
"""
import sources
sources.WAIT_ON_QUOTA = True
import locate, calibrate, importlib, config, update

locate.main([])                       # resorts not yet in data/resorts.json
importlib.reload(config)
calibrate.RESORTS = config.RESORTS
calibrate.main([])                    # resorts without calibration (gauge-less ones get regional defaults)
update.RESORTS = config.RESORTS
update.write_index()
print("backfill done")
