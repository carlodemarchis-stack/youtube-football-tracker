"""Release-notes / version helper.

Single source of truth = RELEASES.json at the repo root. The page and the
sidebar badge both read it. "Auto-published" = committing a new entry and
deploying (Railway redeploys on push) makes it live, no separate step.

Versioning: `major` is set by hand (the one manual lever). `minor` is
bumped automatically when a user-facing change-set ships — a new release
block is prepended with version = "{major}.{prev_minor + 1}". The current
version shown everywhere is simply the newest release's version.
"""
from __future__ import annotations

import json
import os

_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "RELEASES.json"
)


def load() -> dict:
    """Return {'major': int, 'releases': [ {version, date, notes[]} ]}.
    Newest release first. Never raises — returns an empty shell on error."""
    try:
        with open(_PATH, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("major", 1)
        d.setdefault("releases", [])
        return d
    except Exception:
        return {"major": 1, "releases": []}


def current_version() -> str:
    """The newest release's version string, e.g. '1.7'. Falls back to
    '{major}.0' when there are no releases yet."""
    d = load()
    rel = d.get("releases") or []
    if rel and rel[0].get("version"):
        return str(rel[0]["version"])
    return f"{d.get('major', 1)}.0"
