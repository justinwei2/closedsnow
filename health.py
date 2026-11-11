"""Data freshness check. Exits 1 (so the GitHub Actions run fails and GitHub emails you)
when calibrated resorts have missing or stale forecasts. A single missed refresh (rate limit,
API hiccup) doesn't trip it; only a persistent problem does.

    python3 health.py                     # check web/data (CI, after a refresh)
    python3 health.py --url https://...   # check the live site (daily watchdog)
"""
import json, os, sys, urllib.request
from datetime import datetime, timezone
from catalog import FAVORITES

HERE = os.path.dirname(os.path.abspath(__file__))
FAV_MAX_H = 9      # favorites refresh every 3 h: ~2 missed runs
OTHER_MAX_H = 30   # others every ~12 h: ~2 missed refreshes


def load(url):
    if url:
        with urllib.request.urlopen(url.rstrip("/") + "/data/index.json", timeout=30) as r:
            return json.load(r)["resorts"]
    return json.load(open(os.path.join(HERE, "web", "data", "index.json")))["resorts"]


def main():
    url = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else None
    # live check trusts the published index (local data/ may be ahead of the last refresh)
    cal = None if url else json.load(open(os.path.join(HERE, "data", "calibration.json")))
    now = datetime.now(timezone.utc)
    rows, bad = [], []
    for r in load(url):
        not_ready = (r.get("calibration") in (None, "not set up yet")) if cal is None else (r["key"] not in cal)
        if r.get("located") is False or not_ready:
            rows.append((r["name"], "awaiting calibration (backfill)", "")); continue
        limit = FAV_MAX_H if r["key"] in FAVORITES else OTHER_MAX_H
        if not r.get("updated"):
            age, status = None, "MISSING"
        else:
            age = (now - datetime.fromisoformat(r["updated"])).total_seconds() / 3600
            status = "ok" if age <= limit else f"STALE (> {limit} h)"
        rows.append((r["name"], status, f"{age:.1f} h" if age is not None else "–"))
        if status != "ok":
            bad.append((r["name"], r["key"] in FAVORITES or status == "MISSING"))
    table = "| Resort | Status | Data age |\n|---|---|---|\n" + "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    # Email (fail the run) for a stale favorite, a missing forecast, or 3+ stale others; a single
    # straggler is only a warning so one flaky resort doesn't send an email every 3 h.
    alarm = [n for n, serious in bad if serious] or ([n for n, _ in bad] if len(bad) >= 3 else [])
    head = f"### Forecast health: {'❌ ' if alarm else '⚠️ ' if bad else '✅ '}{len(bad)} problem(s)\n\n"
    print(head + table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(head + table + "\n")
    if bad and not alarm:
        print(f"::warning::stale: {', '.join(n for n, _ in bad)}")
    if alarm:
        print(f"::error::stale or missing forecasts: {', '.join(alarm)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
