"""Push one daily weather forecast notification via ntfy.sh, using the
National Weather Service's free public API (no key required).

Single-shot script meant to run once a day at 7am via Task Scheduler.
No dedup/state needed -- it's just "today's forecast," sent once.
"""
import os
import textwrap
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

import notify

# NWS requires a descriptive User-Agent identifying the app and a way
# to contact the operator -- not an API key, just good-citizen practice.
HEADERS = {"User-Agent": "earthquake-weather-alerter (jsalerno13579@gmail.com)"}

# The scheduled 7am run has no phone to ask, so it always uses the fixed
# NJ zip below. A manually-triggered run (the iPhone Shortcut) can pass
# PHONE_ZIP -- when present, it replaces the NJ slot with wherever the
# phone actually is (iOS reverse-geocodes the phone's GPS to a zip code
# on-device before sending it). Sarasota is always fixed.
PHONE_ZIP = os.environ.get("PHONE_ZIP", "").strip()


def geocode_zip(zip_code):
    """zip -> (lat, lon, label), via zippopotam.us (free, no key)."""
    resp = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=10)
    resp.raise_for_status()
    place = resp.json()["places"][0]
    lat, lon = float(place["latitude"]), float(place["longitude"])
    label = f"{place['place name']}, {zip_code}"
    return lat, lon, label


if PHONE_ZIP:
    lat, lon, first_label = geocode_zip(PHONE_ZIP)
    first_coords = (lat, lon)
else:
    first_label, first_coords = "Matawan/07747, NJ", (40.4109, -74.238)

LOCATIONS = {
    first_label: first_coords,
    "Sarasota/SRQ, FL": (27.3954, -82.5544),
}

SCRIPT_DIR = Path(__file__).resolve().parent
GRAPHIC_FILE = SCRIPT_DIR / "daily_forecast.png"

ICON_SIZE = 150
CELL_WIDTH = 260
CELL_HEIGHT = 280


def fetch_forecast(lat, lon):
    points = requests.get(f"https://api.weather.gov/points/{lat},{lon}", headers=HEADERS, timeout=15)
    points.raise_for_status()
    forecast_url = points.json()["properties"]["forecast"]

    forecast = requests.get(forecast_url, headers=HEADERS, timeout=15)
    forecast.raise_for_status()
    return forecast.json()["properties"]["periods"]


def summarize(name, periods):
    lines = [name]
    for period in periods[:2]:
        lines.append(f"{period['name']}: {period['temperature']}{period['temperatureUnit']}, {period['shortForecast']}")
    return "\n".join(lines)


def load_font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def fetch_icon(url):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA").resize((ICON_SIZE, ICON_SIZE))


def build_graphic(entries):
    """entries: list of (location_name, today_period) tuples."""
    canvas = Image.new("RGB", (CELL_WIDTH * len(entries), CELL_HEIGHT), "white")
    draw = ImageDraw.Draw(canvas)
    name_font = load_font(18)
    detail_font = load_font(16)

    for i, (name, period) in enumerate(entries):
        x0 = i * CELL_WIDTH
        icon = fetch_icon(period["icon"])
        canvas.paste(icon, (x0 + (CELL_WIDTH - ICON_SIZE) // 2, 10), icon)

        draw.text((x0 + CELL_WIDTH // 2, ICON_SIZE + 20), name, font=name_font, fill="black", anchor="ma")
        temp_line = f"{period['temperature']}{period['temperatureUnit']} - {period['shortForecast']}"
        wrapped = textwrap.fill(temp_line, width=22)
        draw.multiline_text(
            (x0 + CELL_WIDTH // 2, ICON_SIZE + 50), wrapped,
            font=detail_font, fill="black", anchor="ma", align="center", spacing=6,
        )

    canvas.save(GRAPHIC_FILE)


def main():
    sections = []
    graphic_entries = []
    for name, (lat, lon) in LOCATIONS.items():
        periods = fetch_forecast(lat, lon)
        sections.append(summarize(name, periods))
        graphic_entries.append((name, periods[0]))
        print(f"Fetched forecast for {name}")

    build_graphic(graphic_entries)

    message = "\n\n".join(sections)
    notify.send_file("Daily forecast", message, GRAPHIC_FILE)
    print("Sent daily forecast notification with graphic.")


if __name__ == "__main__":
    main()
