"""Plot the day's earthquakes and currently-elevated volcanoes on one
world map, and push it as an image attachment via ntfy.

Single-shot script meant to run once a day (its own Task Scheduler
entry, separate from the more frequent quake_alert.py / volcano_alert.py
checks) -- this isn't deduplicated against those scripts' state, it's
just a snapshot report.
"""
from pathlib import Path

import plotly.graph_objects as go
import requests

import emsc
import notify

MIN_MAGNITUDE = 1.0
QUAKE_FEED_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/1.0_day.geojson"
VOLCANO_FEED_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getElevatedVolcanoes"
VOLCANO_DETAIL_URL = "https://volcanoes.usgs.gov/hans-public/api/volcano/getVolcano/{vnum}"

SCRIPT_DIR = Path(__file__).resolve().parent
MAP_FILE = SCRIPT_DIR / "daily_map.png"

VOLCANO_MARKER_COLOR = {"GREEN": "green", "YELLOW": "gold", "ORANGE": "orange", "RED": "red"}


def fetch_quakes():
    resp = requests.get(QUAKE_FEED_URL, timeout=10)
    resp.raise_for_status()
    quakes = []
    for feature in resp.json()["features"]:
        mag = feature["properties"]["mag"]
        if mag is None:
            continue
        lon, lat, *_ = feature["geometry"]["coordinates"]
        quakes.append({"mag": mag, "place": feature["properties"]["place"], "lat": lat, "lon": lon})

    # EMSC fills in small Europe/Mediterranean quakes USGS misses. A
    # handful of larger events may appear in both sources and plot as
    # two overlapping markers -- an acceptable tradeoff for otherwise
    # missing that region's activity entirely.
    for q in emsc.fetch_quakes(MIN_MAGNITUDE):
        quakes.append({"mag": q["mag"], "place": q["place"], "lat": q["lat"], "lon": q["lon"]})

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

    if quakes:
        fig.add_trace(go.Scattergeo(
            lat=[q["lat"] for q in quakes],
            lon=[q["lon"] for q in quakes],
            text=[f"M{q['mag']} - {q['place']}" for q in quakes],
            hoverinfo="text",
            mode="markers",
            marker=dict(
                size=[max(4, q["mag"] * 3) for q in quakes],
                color=[q["mag"] for q in quakes],
                colorscale="YlOrRd",
                showscale=True,
                colorbar=dict(title="Magnitude", x=1.0),
                line=dict(width=0.5, color="black"),
            ),
            name="Earthquakes (M1.0+, past day)",
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
        title="Daily hazard map",
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.05),
    )
    return fig


def main():
    quakes = fetch_quakes()
    volcanoes = fetch_volcanoes()

    fig = build_map(quakes, volcanoes)
    fig.write_image(str(MAP_FILE), width=1400, height=800, scale=2)

    message = f"{len(quakes)} quakes (M1.0+) and {len(volcanoes)} elevated volcanoes in the last day."
    notify.send_file("Daily hazard map", message, MAP_FILE)
    print(f"Saved {MAP_FILE} and sent. {len(quakes)} quakes, {len(volcanoes)} volcanoes plotted.")


if __name__ == "__main__":
    main()
