"""Fetch recent earthquakes from USGS + EMSC and push them to a phone
via ntfy.sh.

Single-shot script: fetch -> filter -> notify -> save-state -> exit.
No loop in here on purpose -- run it by hand to test, then schedule it
with Task Scheduler / cron.

Two sources:
  - USGS: reliable globally, but under-reports small (M1-3) quakes
    outside the US.
  - EMSC (emsc.py): fills that gap for Europe/the Mediterranean only.

Notification policy:
  - Any quake >= IMMEDIATE_ALERT_MAGNITUDE notifies right away, every run.
  - Everything else (down to MIN_MAGNITUDE) is queued and rolled up into
    ONE digest notification per calendar day, so small/frequent quakes
    don't spam the phone but still get reported.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

import emsc
import notify

MIN_MAGNITUDE = 1.0
IMMEDIATE_ALERT_MAGNITUDE = 3.5

# 1.0_day.geojson = USGS "M1.0+, past day" feed.
USGS_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_day.geojson"

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "quake_state.json"


def load_state():
    if not STATE_FILE.exists():
        return {"seen_ids": [], "digest_pending": [], "last_digest_date": None}
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
        lon, lat, *_ = feature["geometry"]["coordinates"]
        quakes.append({
            "id": feature["id"],
            "mag": mag,
            "place": props["place"],
            "time": props["time"],
            "url": props["url"],
            "lat": lat,
            "lon": lon,
        })
    return quakes


def format_time(time_ms):
    return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def notify_immediate(quake):
    when = format_time(quake["time"])
    message = f"M{quake['mag']} - {quake['place']}\n{when}\n{quake['url']}"
    notify.send(f"Earthquake M{quake['mag']}", message, priority="high")
    print(f"Immediate notify: {quake['id']} M{quake['mag']} {quake['place']}")


def send_digest(pending, today):
    pending_sorted = sorted(pending, key=lambda q: q["mag"], reverse=True)
    lines = [f"{len(pending)} quake(s) M{MIN_MAGNITUDE}-{IMMEDIATE_ALERT_MAGNITUDE} in the last day:"]
    for q in pending_sorted[:15]:
        lines.append(f"M{q['mag']} - {q['place']}")
    if len(pending_sorted) > 15:
        lines.append(f"...and {len(pending_sorted) - 15} more")
    notify.send(f"Daily quake digest ({today})", "\n".join(lines))
    print(f"Digest sent: {len(pending)} quakes")


def process_quake(quake, seen_ids, digest_pending):
    if quake["id"] in seen_ids:
        return
    if quake["mag"] < MIN_MAGNITUDE:
        return

    seen_ids.add(quake["id"])
    if quake["mag"] >= IMMEDIATE_ALERT_MAGNITUDE:
        notify_immediate(quake)
    else:
        digest_pending.append({"mag": quake["mag"], "place": quake["place"]})


def main():
    state = load_state()
    seen_ids = set(state["seen_ids"])
    digest_pending = state["digest_pending"]

    for quake in fetch_usgs_quakes():
        process_quake(quake, seen_ids, digest_pending)

    for quake in emsc.fetch_quakes(MIN_MAGNITUDE):
        process_quake(quake, seen_ids, digest_pending)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if digest_pending and state["last_digest_date"] != today:
        send_digest(digest_pending, today)
        digest_pending = []
        state["last_digest_date"] = today

    state["seen_ids"] = sorted(seen_ids)
    state["digest_pending"] = digest_pending
    save_state(state)
    print(f"Done. {len(seen_ids)} ids tracked, {len(digest_pending)} pending for next digest.")


if __name__ == "__main__":
    main()
