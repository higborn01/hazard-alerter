"""Plot today's earthquakes (since local midnight) and currently-elevated
volcanoes on one world map, and push it as an image attachment via ntfy.

Single-shot script meant to run once a day at 7am US Eastern via GitHub
Actions -- this isn't deduplicated against quake_alert.py/volcano_alert.py's
state, it's just a snapshot report of "today so far."
"""
from datetime import datetime, timezone
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
MAP_FILE = SCRIPT_DIR / "daily_map.png"

VOLCANO_MARKER_COLOR = {"GREEN": "green", "YELLOW": "gold", "ORANGE": "orange", "RED": "red"}


def local_midnight_utc():
    now_local = datetime.now(LOCAL_TZ)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc)


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


def build_map(quakes, volcanoes):
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
            name="Earthquakes (today)",
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
        title="Today's hazard map",
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.05),
    )
    return fig


def main():
    since_utc = local_midnight_utc()
    quakes = fetch_quakes(since_utc)
    volcanoes = fetch_volcanoes()

    fig = build_map(quakes, volcanoes)
    fig.write_image(str(MAP_FILE), width=1400, height=800, scale=2)

    induced_count = sum(1 for q in quakes if q["possibly_induced"])
    message = f"{len(quakes)} quakes (M{MIN_MAGNITUDE}+, {induced_count} possibly induced) and {len(volcanoes)} elevated volcanoes since midnight."
    notify.send_file("Daily hazard map", message, MAP_FILE)
    print(f"Saved {MAP_FILE} and sent. {len(quakes)} quakes, {len(volcanoes)} volcanoes plotted.")


if __name__ == "__main__":
    main()
