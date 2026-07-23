"""Check USGS volcano alert levels and push status CHANGES to a phone.

Single-shot script: fetch -> diff against last-known status -> notify
-> save-state -> exit. Schedule it with Task Scheduler / cron like the
other alert scripts.

The USGS endpoint only lists volcanoes currently at an elevated status
(yellow/orange/red, or advisory/watch/warning) -- normal (green) ones
aren't listed at all. So "new information" here means either:
  - a volcano appears that wasn't elevated before, or its color/alert
    level changed since last run, or
  - a volcano that WAS elevated has dropped out of the feed entirely,
    meaning it's back to normal.
Notifying only on those changes (instead of every run) avoids repeating
"Kilauea is still yellow" every 5 minutes.
"""
import json
from pathlib import Path

import requests

import notify

FEED_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes"

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "volcano_state.json"


def load_state():
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_elevated():
    response = requests.get(FEED_URL, timeout=10)
    response.raise_for_status()
    return response.json()


def main():
    old_state = load_state()
    new_state = {}
    changes = 0

    for entry in fetch_elevated():
        vnum = entry["vnum"]
        name = entry["volcano_name"]
        color = entry["color_code"]
        level = entry["alert_level"]
        new_state[vnum] = {"name": name, "color": color, "level": level}

        previous = old_state.get(vnum)
        if previous is None:
            notify.send(
                f"Volcano alert: {name}",
                f"{name} ({entry['obs_fullname']}) is now {level} / {color}\n{entry['notice_url']}",
                priority="high",
            )
            print(f"New elevated status: {name} {level}/{color}")
            changes += 1
        elif previous["color"] != color or previous["level"] != level:
            notify.send(
                f"Volcano status change: {name}",
                f"{name} changed from {previous['level']}/{previous['color']} to {level}/{color}\n{entry['notice_url']}",
                priority="high",
            )
            print(f"Status change: {name} {previous['level']}/{previous['color']} -> {level}/{color}")
            changes += 1

    for vnum, prev in old_state.items():
        if vnum not in new_state:
            notify.send(
                f"Volcano back to normal: {prev['name']}",
                f"{prev['name']} has dropped out of the elevated-status list (was {prev['level']}/{prev['color']}).",
            )
            print(f"Back to normal: {prev['name']}")
            changes += 1

    save_state(new_state)
    print(f"Done. {changes} change(s) notified, {len(new_state)} volcanoes currently elevated.")


if __name__ == "__main__":
    main()
