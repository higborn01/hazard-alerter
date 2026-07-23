"""EMSC (European-Mediterranean Seismological Centre) quake feed.

USGS's own catalog is reliable for globally significant quakes but
under-reports small (M1-3) events in Europe/the Mediterranean, since
those are mostly catalogued by regional networks. This fills that gap
for that specific region only -- it is deliberately NOT used as a
second global source, to avoid duplicate notifications for events
USGS already covers well everywhere else.
"""
import re
from datetime import datetime, timedelta, timezone

import requests

FEED_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"

# Rough Europe + Mediterranean bounding box.
BBOX = {"minlatitude": 30, "maxlatitude": 72, "minlongitude": -25, "maxlongitude": 60}


def _to_epoch_ms(iso_time):
    # Python 3.9's fromisoformat only accepts 3 or 6 fractional digits;
    # EMSC sends a single digit (e.g. "...23.0Z"), so pad it first.
    iso_time = iso_time.replace("Z", "+00:00")
    iso_time = re.sub(r"\.(\d+)", lambda m: "." + m.group(1).ljust(6, "0"), iso_time)
    dt = datetime.fromisoformat(iso_time)
    return int(dt.timestamp() * 1000)


def fetch_quakes(min_magnitude, hours=24):
    start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    params = {"format": "json", "minmagnitude": min_magnitude, "start": start, "limit": 200, **BBOX}
    resp = requests.get(FEED_URL, params=params, timeout=15)
    resp.raise_for_status()

    quakes = []
    for feature in resp.json()["features"]:
        props = feature["properties"]
        mag = props["mag"]
        if mag is None:
            continue
        lon, lat, *_ = feature["geometry"]["coordinates"]
        quakes.append({
            # Prefixed so it can never collide with a USGS id.
            "id": f"emsc-{feature['id']}",
            "mag": mag,
            "place": props["flynn_region"].title(),
            "time": _to_epoch_ms(props["time"]),
            "url": f"https://www.seismicportal.eu/eventdetails.html?unid={feature['id']}",
            "lat": lat,
            "lon": lon,
        })
    return quakes
