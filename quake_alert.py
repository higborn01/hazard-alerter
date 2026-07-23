"""Fetch recent earthquakes from USGS + EMSC and push a single
consolidated notification via ntfy.sh for anything new at or above
MAGNITUDE_THRESHOLD.

Single-shot script: fetch -> filter -> notify -> save-state -> exit.
Runs every 3 hours via GitHub Actions. Dedup is id-based (quake_state.json,
committed back to the repo by the workflow), so "new" means "not seen in
any previous run" -- effectively "since the last notification."

Two sources:
  - USGS: reliable globally, but under-reports small quakes outside the US.
  - EMSC (emsc.py): fills that gap for Europe/the Mediterranean only.
"""
import json
from pathlib import Path

import requests

import emsc
import notify

MAGNITUDE_THRESHOLD = 3.0

# 1.0_day.geojson = USGS "M1.0+, past day" feed (comfortably covers M3+).
USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_day.geojson"

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "quake_state.json"


def load_state():
    if not STATE_FILE.exists():
        return {"seen_ids": []}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_usgs_quakes():
    response = requests.get(USGS_FEED_URL, timeout=10)
    response.raise_for_status()
    quakes = []
    for feature in response.json()["features"]:
        props = feature["properties"]
        mag = props["mag"]
        if mag is None:
            continue
        quakes.append({"id": feature["id"], "mag": mag, "place": props["place"]})
    return quakes


def process_quake(quake, seen_ids, matched):
    if quake["id"] in seen_ids:
        return
    if quake["mag"] < MAGNITUDE_THRESHOLD:
        return
    seen_ids.add(quake["id"])
    matched.append(quake)


def send_notification(matched):
    matched_sorted = sorted(matched, key=lambda q: q["mag"], reverse=True)
    lines = [f"{len(matched)} quake(s) M{MAGNITUDE_THRESHOLD}+ since the last check:"]
    for q in matched_sorted:
        lines.append(f"M{q['mag']} - {q['place']}")
    notify.send(f"Earthquake update ({len(matched)})", "\n".join(lines), priority="high")
    print(f"Notified: {len(matched)} quakes")


def main():
    state = load_state()
    seen_ids = set(state["seen_ids"])
    matched = []

    for quake in fetch_usgs_quakes():
        process_quake(quake, seen_ids, matched)

    for quake in emsc.fetch_quakes(MAGNITUDE_THRESHOLD):
        process_quake(quake, seen_ids, matched)

    if matched:
        send_notification(matched)

    state["seen_ids"] = sorted(seen_ids)
    save_state(state)
    print(f"Done. {len(seen_ids)} ids tracked, {len(matched)} new this run.")


if __name__ == "__main__":
    main()
