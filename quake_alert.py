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

# Non-earthquake events USGS tags in its catalog. The all-magnitude feed
# is used because blasts are often below M1. Quarry blasts and mining
# explosions are left out on purpose: they happen dozens of times a day.
USGS_ALL_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
BLAST_TYPES = {"explosion", "chemical explosion", "nuclear explosion", "experimental explosion", "sonic boom"}

US_STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR",
}
US_PLACE_ENDINGS = set(US_STATES) | set(US_STATES.values()) | {"USA", "U.S. Virgin Islands"}

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


def is_us_place(place):
    if not place:
        return False
    ending = place.rsplit(",", 1)[-1].strip()
    return ending in US_PLACE_ENDINGS


def fetch_usgs_blasts():
    """New US explosions/sonic booms from USGS, any magnitude."""
    response = requests.get(USGS_ALL_FEED_URL, timeout=15)
    response.raise_for_status()
    blasts = []
    for feature in response.json()["features"]:
        props = feature["properties"]
        if props.get("type") in BLAST_TYPES and is_us_place(props.get("place")):
            blasts.append({"id": feature["id"], "mag": props.get("mag"), "place": props["place"], "type": props["type"]})
    return blasts


def send_blast_notification(blasts):
    lines = [f"{len(blasts)} explosion-type event(s) in the US since the last check:"]
    for b in blasts:
        mag = f"M{b['mag']} " if b["mag"] is not None else ""
        lines.append(f"{b['type'].capitalize()} {mag}- {b['place']}")
    notify.send(f"Explosion alert ({len(blasts)})", "\n".join(lines), priority="high")
    print(f"Notified: {len(blasts)} blasts")


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

    # Separate check so a feed problem here never blocks quake alerts.
    try:
        new_blasts = [b for b in fetch_usgs_blasts() if b["id"] not in seen_ids]
    except requests.exceptions.RequestException as exc:
        print(f"Blast check skipped: {exc}")
        new_blasts = []
    if new_blasts:
        seen_ids.update(b["id"] for b in new_blasts)
        send_blast_notification(new_blasts)

    state["seen_ids"] = sorted(seen_ids)
    save_state(state)
    print(f"Done. {len(seen_ids)} ids tracked, {len(matched)} new this run.")


if __name__ == "__main__":
    main()
