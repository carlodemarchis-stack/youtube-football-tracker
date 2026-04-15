"""Snapshot-driven growth helpers.

All functions work off the `channel_snapshots` table. Rows are expected to be
ordered oldest → newest (captured_date ascending).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def _parse_date(d) -> date:
    if isinstance(d, date) and not isinstance(d, datetime):
        return d
    if isinstance(d, datetime):
        return d.date()
    return datetime.fromisoformat(str(d)[:10]).date()


def latest_before(snaps: list[dict], cutoff: date) -> dict | None:
    """Return the most recent snapshot with captured_date <= cutoff, else None."""
    out = None
    for s in snaps:
        if _parse_date(s["captured_date"]) <= cutoff:
            out = s
        else:
            break
    return out


def delta(snaps: list[dict], metric: str, days: int) -> int | None:
    """metric value now − metric value `days` ago.
    Returns None if we don't yet have a snapshot old enough."""
    if not snaps:
        return None
    latest = snaps[-1]
    cutoff = _parse_date(latest["captured_date"]) - timedelta(days=days)
    past = latest_before(snaps, cutoff)
    if not past or past is latest:
        return None
    return int(latest.get(metric, 0) or 0) - int(past.get(metric, 0) or 0)


def delta_since(snaps: list[dict], metric: str, since_iso: str) -> int | None:
    """metric value now − metric value at/on or nearest after `since_iso`."""
    if not snaps:
        return None
    since = _parse_date(since_iso)
    latest = snaps[-1]
    # Find first snapshot on or after the cutoff (start of period)
    base = None
    for s in snaps:
        if _parse_date(s["captured_date"]) >= since:
            base = s
            break
    if not base or base is latest:
        return None
    return int(latest.get(metric, 0) or 0) - int(base.get(metric, 0) or 0)


def group_by_channel(snaps: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in snaps:
        out.setdefault(s["channel_id"], []).append(s)
    # Ensure each list is sorted by date (RLS returns ordered, but be defensive)
    for v in out.values():
        v.sort(key=lambda r: _parse_date(r["captured_date"]))
    return out


def days_covered(snaps: list[dict]) -> int:
    if not snaps:
        return 0
    first = _parse_date(snaps[0]["captured_date"])
    last = _parse_date(snaps[-1]["captured_date"])
    return (last - first).days + 1
