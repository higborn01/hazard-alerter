"""One-off script: send a single test push via ntfy.sh.

Confirms the phone/topic/network path works before any earthquake
logic gets added. Run it, check your phone, then we move on.
"""
import json

import requests

with open("config.json") as f:
    config = json.load(f)

topic = config["ntfy_topic"]
url = f"https://ntfy.sh/{topic}"

# ntfy reads the notification body straight from the POST body (plain
# text, UTF-8). Optional headers like Title/Priority/Tags let you set
# metadata without stuffing it into the message text.
response = requests.post(
    url,
    data="Test notification — earthquake alerter setup is working.".encode("utf-8"),
    headers={"Title": "ntfy setup test"},
)
response.raise_for_status()

print(f"POST to {url} -> {response.status_code}")
