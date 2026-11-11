"""Resorts, models and constants.

Resorts live in data/resorts.json, built by locate.py from catalog.py: each has explicit
on-mountain base / mid / summit points (snapped to Open-Meteo's 90 m DEM; the published elevation
is passed explicitly because the DEM smooths peaks), a time zone, a verification gauge, and the
models that actually have data there. Open-Meteo is queried with cell_selection=nearest (the
default "land" mode can jump several km to a cell of similar height).
"""
import json, os

FT = 0.3048
ELEVS = ["base", "mid", "summit"]

# Deterministic models blended for the central forecast (Open-Meteo ids); per-resort subset
# is whatever locate.py found has data there.
BASE_MODELS = ["ecmwf_ifs", "ecmwf_ifs025", "ecmwf_aifs025_single", "gfs_seamless",
               "ncep_nbm_conus", "gem_seamless", "icon_seamless"]
EXTRA_MODELS = ["gem_hrdps_continental"]  # 2.5 km Canadian; covers BC/Alberta + northern US
DET_MODELS = BASE_MODELS + EXTRA_MODELS
# Models with pressure-level profiles, used for snow level / snow quality.
PROFILE_MODELS = ["ecmwf_ifs025", "gfs_seamless", "gem_seamless", "icon_seamless"]
LEVELS = [1000, 925, 850, 800, 750, 700, 650, 600, 550, 500]
# Ensembles for spread. Value = deterministic sibling whose calibration factor applies.
ENSEMBLES = {"ecmwf_ifs025": "ecmwf_ifs025", "ecmwf_aifs025": "ecmwf_aifs025_single",
             "gfs05": "gfs_seamless", "gem_global": "gem_seamless", "icon_seamless": "icon_seamless"}

SEASONS = [("2024-11-01", "2025-04-30"), ("2025-11-01", "2026-04-30")]
# NWS asks for a contact in the User-Agent; set SKISITE_CONTACT (e.g. an email) when hosted.
USER_AGENT = f"skisite-personal ({os.environ.get('SKISITE_CONTACT', 'local use')})"

_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "resorts.json")
RESORTS = json.load(open(_path)) if os.path.exists(_path) else {}
