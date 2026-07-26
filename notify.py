"""Shared ntfy.sh helper used by every alert script in this project."""
import json
import os
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"


def load_topic():
    # GitHub Actions runs set NTFY_TOPIC from a repo secret (config.json
    # isn't committed, since the topic is the whole "secret" in ntfy's
    # security model). Local/Task Scheduler runs fall back to the file.
    env_topic = os.environ.get("NTFY_TOPIC")
    if env_topic:
        return env_topic
    with open(CONFIG_FILE) as f:
        return json.load(f)["ntfy_topic"]


def send(title, message, priority="default"):
    topic = load_topic()
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": priority},
        timeout=10,
    )
    resp.raise_for_status()


def send_file(title, message, file_path):
    """POST a local file's bytes as the body; ntfy's Filename header
    switches it from a text message to a file attachment.

    Note: ntfy.sh deletes uploaded attachments after 3 hours. Fine for
    something you'll open right away, but not for anything that might
    sit unread -- use send_url_attachment for those instead."""
    topic = load_topic()
    with open(file_path, "rb") as f:
        data = f.read()
    # HTTP headers can't contain literal newlines. ntfy's documented
    # workaround: send "\n" as two literal characters and it unescapes
    # them into real newlines for display.
    escaped_message = message.replace("\n", "\\n")
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=data,
        headers={"Title": title, "Message": escaped_message, "Filename": Path(file_path).name},
        timeout=30,
    )
    resp.raise_for_status()


def send_url_attachment(title, message, attach_url, filename):
    """Point ntfy at an externally-hosted file instead of uploading
    bytes. External attachments aren't subject to ntfy's 3-hour expiry
    or 15MB size limit -- the URL just needs to stay reachable. Unlike
    send_file, the body isn't occupied by file bytes here, so the
    message goes in the body as plain text, same as send()."""
    topic = load_topic()
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Attach": attach_url, "Filename": filename},
        timeout=10,
    )
    resp.raise_for_status()
