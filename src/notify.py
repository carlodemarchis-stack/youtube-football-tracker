"""ntfy.sh push-notification helper.

Sends a short alert to a private ntfy topic after each Railway cron run.
Topic is read from `NTFY_TOPIC` env var. If unset, every call no-ops, so
this module is safe to import even when notifications aren't configured.

ntfy URL format: https://ntfy.sh/<topic>
The topic is unguessable-by-design (treat it like an API key). Subscribe
on phone via the ntfy app or the web at https://ntfy.sh/<topic>.
"""
from __future__ import annotations

import os
import re
import urllib.request
from typing import Iterable

NTFY_BASE = os.environ.get("NTFY_BASE_URL", "https://ntfy.sh").rstrip("/")
_TOPIC_ENV = "NTFY_TOPIC"
# Default topic for this project. Subscribe at https://ntfy.sh/ytft (web)
# or in the ntfy mobile app. NTFY_TOPIC env var still wins when set.
_DEFAULT_TOPIC = "ytft"


def _topic() -> str | None:
    t = (os.environ.get(_TOPIC_ENV) or _DEFAULT_TOPIC).strip()
    return t or None


def send_ntfy(
    title: str,
    message: str,
    *,
    priority: str = "default",   # min | low | default | high | urgent
    tags: Iterable[str] | None = None,
    timeout: float = 5.0,
) -> bool:
    """POST a notification to ntfy. Returns True on 2xx, False otherwise.
    Silent no-op if NTFY_TOPIC isn't set."""
    topic = _topic()
    if not topic:
        return False
    try:
        url = f"{NTFY_BASE}/{topic}"
        body = (message or "").encode("utf-8")
        # Header values must be ASCII-safe; ntfy parses these into the
        # notification fields. Strip any non-ASCII from title/tags so
        # the request doesn't get rejected by urllib.
        def _ascii(s: str) -> str:
            return (s or "").encode("ascii", "replace").decode("ascii")
        headers = {
            "Title": _ascii(title)[:120],
            "Priority": priority,
        }
        if tags:
            headers["Tags"] = ",".join(_ascii(t) for t in tags)
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        # Notifications must never break the run — swallow everything.
        return False


def html_to_text(html: str) -> str:
    """Cheap HTML → plain-text. Good enough for the vibe-note paragraph
    we get from latest_vibe (a few <p>/<br> tags + plain prose)."""
    if not html:
        return ""
    s = re.sub(r"<\s*br\s*/?>", "\n", html, flags=re.I)
    s = re.sub(r"</\s*p\s*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    # Collapse whitespace, decode the few entities we care about.
    s = (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
           .replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"'))
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def read_latest_vibe_text(db) -> str:
    """Pull the most recent latest_vibe HTML from dashboard_cache and
    convert it to plain text. Empty string if not available."""
    try:
        from src.dashboard_cache import read as _dc_read, scope_all as _dc_scope_all
        row = _dc_read(db, "latest_vibe", _dc_scope_all())
        html = ((row or {}).get("payload") or {}).get("html") or ""
        return html_to_text(html)
    except Exception:
        return ""


def send_run_alert(
    task: str,
    *,
    ok: bool = True,
    summary: str = "",
    error: str = "",
    vibe_text: str = "",
    priority: str | None = None,
) -> bool:
    """Standard end-of-run alert format used by every cron script."""
    emoji = "OK" if ok else "FAIL"
    title = f"[{emoji}] {task}"
    parts = []
    if summary:
        parts.append(summary.strip())
    if error:
        parts.append(f"error: {error.strip()}")
    if vibe_text:
        parts.append("")  # blank line
        parts.append("AI note:")
        # Cap at ~600 chars to keep the push manageable.
        parts.append(vibe_text[:600] + ("…" if len(vibe_text) > 600 else ""))
    message = "\n".join(parts) or "(no details)"
    pri = priority or ("default" if ok else "high")
    tags = ["white_check_mark"] if ok else ["rotating_light"]
    return send_ntfy(title, message, priority=pri, tags=tags)
