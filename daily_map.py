"""Plot today's earthquakes (since local midnight) and currently-elevated
volcanoes on one world map, and push it as an image attachment via ntfy.

Single-shot script meant to run once a day at 7am US Eastern via GitHub
Actions -- this isn't deduplicated against quake_alert.py/volcano_alert.py's
state, it's just a snapshot report of "today so far."
"""
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import requests

import emsc
import notify

MIN_MAGNITUDE = 1.0
LOCAL_TZ = ZoneInfo("America/New_York")

# Rough bounding boxes for regions with well-documented oil/gas
# wastewater-injection-induced seismicity. This is a location heuristic,
# not real attribution (that requires well-level injection data this
# script doesn't have) -- see TexNet (catalog.texnet.beg.utexas.edu) and
# the Oklahoma Corporation Commission for the real thing.
INDUCED_HOTSPOTS = [
    # (min_lat, max_lat, min_lon, max_lon)
    (30.5, 33.5, -104.5, -101.0),  # Permian Basin, West TX / SE NM
    (33.6, 37.0, -103.0, -94.4),   # Oklahoma
    (36.9, 38.5, -99.5, -97.0),    # South-central Kansas
]


def is_possibly_induced(lat, lon):
    return any(min_lat <= lat <= max_lat and min_lon <= lon <= max_lon for min_lat, max_lat, min_lon, max_lon in INDUCED_HOTSPOTS)

QUAKE_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_day.geojson"
VOLCANO_FEED_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes"
VOLCANO_DETAIL_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getVolcano/{vnum}"

SCRIPT_DIR = Path(__file__).resolve().parent
DOCS_DIR = SCRIPT_DIR / "docs"

# MAP_OUTPUT_NAME/MAP_SINCE_HOURS let the same script serve two modes:
#   - scheduled run (no env vars): daily_map.png, since local midnight
#   - on-demand run (MAP_SINCE_HOURS=24, MAP_OUTPUT_NAME=map_24h.png):
#     a rolling 24h window, triggered manually (e.g. from an iPhone
#     Shortcut hitting this workflow's workflow_dispatch trigger)
OUTPUT_NAME = os.environ.get("MAP_OUTPUT_NAME", "daily_map.png")
MAP_FILE = DOCS_DIR / OUTPUT_NAME
# GitHub Pages, serving the docs/ folder of this repo -- gives the map
# a permanent URL instead of ntfy's upload path, which expires after 3h.
MAP_PAGES_URL = f"https://higborn01.github.io/hazard-alerter/{OUTPUT_NAME}"

VOLCANO_MARKER_COLOR = {"GREEN": "green", "YELLOW": "gold", "ORANGE": "orange", "RED": "red"}


def compute_since_utc():
    """Returns (since_utc, label). label describes the window in the
    message/title -- "since midnight" for the scheduled run, or
    "last Nh" for an on-demand rolling window."""
    since_hours = os.environ.get("MAP_SINCE_HOURS")
    if since_hours:
        hours = float(since_hours)
        return datetime.now(timezone.utc) - timedelta(hours=hours), f"last {since_hours}h"

    now_local = datetime.now(LOCAL_TZ)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc), "since midnight"


def fetch_quakes(since_utc):
    quakes = []

    resp = requests.get(QUAKE_FEED_URL, timeout=10)
    resp.raise_for_status()
    for feature in resp.json()["features"]:
        props = feature["properties"]
        mag = props["mag"]
        if mag is None:
            continue
        quake_time = datetime.fromtimestamp(props["time"] / 1000, tz=timezone.utc)
        if quake_time < since_utc:
            continue
        lon, lat, *_ = feature["geometry"]["coordinates"]
        quakes.append({"mag": mag, "place": props["place"], "lat": lat, "lon": lon, "possibly_induced": is_possibly_induced(lat, lon)})

    # EMSC fills in small Europe/Mediterranean quakes USGS misses. A
    # handful of larger events may appear in both sources and plot as
    # two overlapping markers -- an acceptable tradeoff for otherwise
    # missing that region's activity entirely.
    for q in emsc.fetch_quakes(MIN_MAGNITUDE):
        quake_time = datetime.fromtimestamp(q["time"] / 1000, tz=timezone.utc)
        if quake_time < since_utc:
            continue
        quakes.append({"mag": q["mag"], "place": q["place"], "lat": q["lat"], "lon": q["lon"], "possibly_induced": is_possibly_induced(q["lat"], q["lon"])})

    return quakes


def fetch_volcanoes():
    resp = requests.get(VOLCANO_FEED_URL, timeout=10)
    resp.raise_for_status()
    volcanoes = []
    # getElevatedVolcanoes doesn't include coordinates, so look each one
    # up individually. Only a handful are elevated at once, so this is
    # cheap.
    for entry in resp.json():
        detail = requests.get(VOLCANO_DETAIL_URL.format(vnum=entry["vnum"]), timeout=10)
        detail.raise_for_status()
        d = detail.json()
        volcanoes.append({
            "name": entry["volcano_name"],
            "color": entry["color_code"],
            "level": entry["alert_level"],
            "lat": d["latitude"],
            "lon": d["longitude"],
        })
    return volcanoes


def build_map(quakes, volcanoes, label):
    fig = go.Figure()

    natural = [q for q in quakes if not q["possibly_induced"]]
    induced = [q for q in quakes if q["possibly_induced"]]

    if natural:
        fig.add_trace(go.Scattergeo(
            lat=[q["lat"] for q in natural],
            lon=[q["lon"] for q in natural],
            text=[f"M{q['mag']} - {q['place']}" for q in natural],
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=[max(4, q["mag"] * 3) for q in natural],
                color=[q["mag"] for q in natural],
                colorscale="YlOrRd",
                showscale=True,
                colorbar=dict(title="Magnitude", x=1.0),
                line=dict(width=0.5, color="black"),
            ),
            name=f"Earthquakes ({label})",
        ))

    if induced:
        fig.add_trace(go.Scattergeo(
            lat=[q["lat"] for q in induced],
            lon=[q["lon"] for q in induced],
            text=[f"M{q['mag']} - {q['place']} (possibly induced -- TX/OK/KS hotspot)" for q in induced],
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=[max(4, q["mag"] * 3) for q in induced],
                color="black",
                line=dict(width=0.5, color="black"),
            ),
            name="Possibly induced (TX/OK/KS)",
        ))

    if volcanoes:
        fig.add_trace(go.Scattergeo(
            lat=[v["lat"] for v in volcanoes],
            lon=[v["lon"] for v in volcanoes],
            text=[f"{v['name']} - {v['level']}/{v['color']}" for v in volcanoes],
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=16,
                symbol="triangle-up",
                color=[VOLCANO_MARKER_COLOR.get(v["color"], "gray") for v in volcanoes],
                line=dict(width=1, color="black"),
            ),
            name="Elevated volcanoes",
        ))

    fig.update_geos(showcountries=True, showcoastlines=True, showland=True, landcolor="rgb(235,235,235)")
    fig.update_layout(
        title=f"Hazard map ({label})",
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.05),
    )
    return fig


def run_git(*args):
    subprocess.run(["git", *args], cwd=SCRIPT_DIR, check=True)


def commit_and_push_map():
    """Push docs/daily_map.png so GitHub Pages actually has it before we
    tell ntfy where to find it -- otherwise ntfy tries to prefetch the
    attachment immediately and shows "download failed" on a 404."""
    run_git("add", str(MAP_FILE))
    staged = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=SCRIPT_DIR)
    if staged.returncode == 0:
        print("No change to the map image, nothing to push.")
        return
    # GitHub Actions checkouts have no git identity configured. Local
    # runs already have one set on this repo (left alone here).
    if os.environ.get("GITHUB_ACTIONS") == "true":
        run_git("config", "user.name", "github-actions[bot]")
        run_git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
    run_git("commit", "-m", "Update daily map image")
    run_git("pull", "--rebase", "origin", "main")
    run_git("push")


def wait_until_live(url, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.head(url, timeout=10).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(5)
    return False


def main():
    since_utc, label = compute_since_utc()
    quakes = fetch_quakes(since_utc)
    volcanoes = fetch_volcanoes()

    fig = build_map(quakes, volcanoes, label)
    DOCS_DIR.mkdir(exist_ok=True)
    fig.write_image(str(MAP_FILE), width=1400, height=800, scale=2)

    commit_and_push_map()

    # Cache-bust the query string so phones/ntfy don't show a stale
    # cached copy of the previous image at this same URL.
    cache_busted_url = f"{MAP_PAGES_URL}?t={int(datetime.now(timezone.utc).timestamp())}"
    if not wait_until_live(MAP_PAGES_URL):
        print("Warning: Pages didn't confirm the new image within the timeout; notifying anyway.")
    else:
        print("Confirmed the map is live on GitHub Pages.")

    induced_count = sum(1 for q in quakes if q["possibly_induced"])
    message = f"{len(quakes)} quakes (M{MIN_MAGNITUDE}+, {induced_count} possibly induced) and {len(volcanoes)} elevated volcanoes {label}."
    title = "Daily hazard map" if label == "since midnight" else f"Earthquake map ({label})"
    notify.send_url_attachment(title, message, cache_busted_url, OUTPUT_NAME)
    print(f"Sent (via {cache_busted_url}). {len(quakes)} quakes, {len(volcanoes)} volcanoes plotted.")


if __name__ == "__main__":
    main()
