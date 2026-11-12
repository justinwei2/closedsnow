"""Hindcast calibration: per resort x model precipitation factors, blend weights, error quantiles.

Uses as-issued forecasts (Open-Meteo Previous Runs API) vs the resort's verification station,
over the seasons in config.SEASONS. Writes data/calibration.json.

72h window starting local day D = lead-1 day D + lead-2 day D+1 + lead-3 day D+2.
"""
import json, os, sys, time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from config import RESORTS, SEASONS
from sources import get_json, cache_path, obs_daily, write_json

HERE = os.path.dirname(os.path.abspath(__file__))
CAL_PATH = os.path.join(HERE, "data", "calibration.json")
FACTOR_MIN, FACTOR_MAX = 0.4, 2.0  # beyond this the model/cell is not representative; cap, let weight fall
RATIO_BINS = [(10, 25), (25, 50), (50, 1e9)]  # mm liquid per 72h
WET = 10.0  # mm/72h: only windows with a real storm feed the error distribution


def std_tz(tz):
    """Fixed-offset zone for the resort's standard time (SNOTEL days ignore DST)."""
    off = datetime(2025, 1, 15, tzinfo=ZoneInfo(tz)).utcoffset().total_seconds() / 3600
    return f"Etc/GMT{-int(off):+d}"


def hindcast(key, model, start, end):
    r = RESORTS[key]
    path = cache_path(f"hind_{key}_{model}_{start}.json")
    if not os.path.exists(path):
        lat, lon = r["obs"][4], r["obs"][5]  # verify at the gauge, not the resort point
        q = dict(latitude=lat, longitude=lon, models=model, start_date=start, end_date=end,
                 timezone=std_tz(r["tz"]), cell_selection="nearest",  # station days are local standard time
                 hourly=",".join(f"precipitation_previous_day{n}" for n in (1, 2, 3)))
        try:
            j = get_json("https://previous-runs-api.open-meteo.com/v1/forecast", q)
        except RuntimeError as e:
            if "HTTP 4" not in str(e):
                raise
            j = {}  # no history for this model/winter here: skip it, don't fail the resort
        json.dump(j, open(path, "w"))
        time.sleep(4)  # stay well inside Open-Meteo's free-tier per-minute limit
    j = json.load(open(path))
    out = defaultdict(dict)
    if "hourly" not in j:
        return out
    h = j["hourly"]
    for n in (1, 2, 3):
        vals = h.get(f"precipitation_previous_day{n}", [])
        per = defaultdict(list)
        for t, v in zip(h["time"], vals):
            per[date.fromisoformat(t[:10])].append(v)
        for d, vs in per.items():
            if len(vs) == 24 and None not in vs:
                out[n][d] = sum(vs)
    return out


def windows72(fc, obs):
    pairs = {}
    for d in fc[1]:
        a, b = d + timedelta(1), d + timedelta(2)
        o = [obs.get(d + timedelta(i)) for i in range(3)]
        if a in fc[2] and b in fc[3] and None not in o:
            pairs[d] = (fc[1][d] + fc[2][a] + fc[3][b], sum(o))
    return pairs


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = q * (len(xs) - 1)
    lo = int(i)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (i - lo)


def calibrate_one(key, r):
        obs = {}
        for s, e in SEASONS:
            obs.update(obs_daily(r["obs"], s, (date.fromisoformat(e) + timedelta(3)).isoformat()))
        per_model, pairs_by_model = {}, {}
        for m in r["models"]:
            pairs = {}
            for s, e in SEASONS:
                pairs.update(windows72(hindcast(key, m, s, e), obs))
            if len(pairs) < 60:
                continue
            f = sum(p[0] for p in pairs.values()); o = sum(p[1] for p in pairs.values())
            factor = min(FACTOR_MAX, max(FACTOR_MIN, o / f if f else 1.0))
            mae = sum(abs(p[0] * factor - p[1]) for p in pairs.values()) / len(pairs)
            raw_mae = sum(abs(p[0] - p[1]) for p in pairs.values()) / len(pairs)
            per_model[m] = dict(factor=round(factor, 3), mae72=round(mae, 2),
                                raw_mae72=round(raw_mae, 2), n=len(pairs))
            pairs_by_model[m] = pairs
        # inverse-MSE-ish weights from corrected MAE
        w = {m: 1 / v["mae72"] ** 2 for m, v in per_model.items()}
        tot = sum(w.values())
        for m in per_model:
            per_model[m]["weight"] = round(w[m] / tot, 3)
        # blend hindcast on days where every model with weight has data (renormalising otherwise)
        days = set.union(*[set(p) for p in pairs_by_model.values()])
        blend_pairs = []
        for d in days:
            num = den = 0.0
            ob = None
            for m, p in pairs_by_model.items():
                if d in p:
                    num += w[m] * p[d][0] * per_model[m]["factor"]; den += w[m]; ob = p[d][1]
            if den:
                blend_pairs.append((num / den, ob))
        bmae = sum(abs(a - b) for a, b in blend_pairs) / len(blend_pairs)
        # error distribution: obs/forecast ratio when the blend called for a real storm
        ratios = [o / f for f, o in blend_pairs if f >= WET]
        qs = (0.1, 0.25, 0.5, 0.75, 0.9)
        # relative error shrinks for big forecast storms, so also bin by forecast size
        bins = []
        for lo, hi in RATIO_BINS:
            rb = [o / f for f, o in blend_pairs if lo <= f < hi]
            if len(rb) >= 15:
                bins.append(dict(lo_mm=lo, hi_mm=hi, n=len(rb), q={str(q): round(quantile(rb, q), 3) for q in qs}))
        return dict(
            station=r["obs"][2], n_windows=len(blend_pairs), models=per_model,
            blend_mae72=round(bmae, 2),
            ratio_q={str(q): round(quantile(ratios, q), 3) for q in qs},  # str keys, as after a JSON round-trip
            ratio_bins=bins, n_wet=len(ratios),
        )


def regional_default(cal, r):
    """For resorts without a usable gauge: median factors / error quantiles of calibrated resorts
    in the same region (else all), equal model weights. Marked uncalibrated."""
    peers = [c for k, c in cal.items() if not c.get("uncalibrated") and RESORTS.get(k, {}).get("region") == r["region"]]
    scope = "region"
    if not peers:
        peers, scope = [c for c in cal.values() if not c.get("uncalibrated")], "all"
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 1.0
    models = {m: dict(factor=round(med([p["models"][m]["factor"] for p in peers if m in p["models"]]), 3),
                      weight=round(1 / len(r["models"]), 3), mae72=None, raw_mae72=None, n=0)
              for m in r["models"]}
    qs = ("0.1", "0.25", "0.5", "0.75", "0.9")
    return dict(station=None, uncalibrated=True, models=models, blend_mae72=None, n_wet=0,
                ratio_q={q: round(med([p["ratio_q"][q] for p in peers]), 3) for q in qs},
                ratio_bins=[], peers=len(peers), peers_scope=scope)


def main(keys):
    cal = json.load(open(CAL_PATH)) if os.path.exists(CAL_PATH) else {}
    todo = keys or [k for k in RESORTS if k not in cal or cal[k].get("uncalibrated")]
    for key in todo:
        r = RESORTS[key]
        if not r.get("obs"):
            continue
        try:
            cal[key] = calibrate_one(key, r)
            print(key, "blend MAE72", cal[key]["blend_mae72"], {m: v["factor"] for m, v in cal[key]["models"].items()})
        except Exception as e:
            print("  calibration failed", key, e, file=sys.stderr)
        write_json(CAL_PATH, cal, indent=1)
    for key, r in RESORTS.items():
        if key not in cal or cal[key].get("uncalibrated"):
            cal[key] = regional_default(cal, r)
    write_json(CAL_PATH, cal, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
