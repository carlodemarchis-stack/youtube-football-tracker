"""Tiny ntfy.sh push-notification helper.

Reads NTFY_TOPIC from the environment. If unset, calls are no-ops so
the rest of the app keeps working in dev / when alerts aren't wired up.

Subscribe to the topic in the ntfy mobile/web app to receive alerts.
"""
from __future__ import annotations

import os
import sys

import requests


def send_ntfy(
    message: str,
    *,
    title: str | None = None,
    priority: str = "default",
    tags: str | None = None,
    topic: str | None = None,
    server: str = "https://ntfy.sh",
) -> bool:
    """Send a push notification to ntfy.

    Returns True if the message was posted (HTTP 2xx), False otherwise.
    Never raises — alerting must not break the caller.
    """
    topic = topic or os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    headers = {"Priority": priority}
    if title:
        # ntfy.sh reads the Title header but HTTP headers must be
        # latin-1-encodable. Em-dashes / smart quotes etc. would raise
        # 'latin-1 can't encode character'. Strip down to safe ASCII
        # (UTF-8 in the body is fine — that's where most of our text
        # lives anyway).
        try:
            title.encode("latin-1")
            headers["Title"] = title
        except UnicodeEncodeError:
            headers["Title"] = title.encode("ascii", "replace").decode("ascii")
    if tags:
        headers["Tags"] = tags
    try:
        r = requests.post(
            f"{server.rstrip('/')}/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=5,
        )
        return 200 <= r.status_code < 300
    except Exception as e:
        print(f"[ntfy] failed to send: {e}", file=sys.stderr)
        return False
