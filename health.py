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
        if r.get("located") is False or (cal is not None and r["key"] not in cal):
            rows.append((r["name"], "awaiting calibration (backfill)", "")); continue
        limit = FAV_MAX_H if r["key"] in FAVORITES else OTHER_MAX_H
        if not r.get("updated"):
            age, status = None, "MISSING"
        else:
            age = (now - datetime.fromisoformat(r["updated"])).total_seconds() / 3600
            status = "ok" if age <= limit else f"STALE (> {limit} h)"
        rows.append((r["name"], status, f"{age:.1f} h" if age is not None else "–"))
        if status != "ok":
            bad.append(r["name"])
    table = "| Resort | Status | Data age |\n|---|---|---|\n" + "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    head = f"### Forecast health: {'❌ ' + str(len(bad)) + ' problem(s)' if bad else '✅ all fresh'}\n\n"
    print(head + table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(head + table + "\n")
    if bad:
        print(f"::error::stale or missing forecasts: {', '.join(bad)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
