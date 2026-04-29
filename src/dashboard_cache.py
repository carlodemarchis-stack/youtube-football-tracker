"""Persisted pre-computed dashboard data.

Heavy aggregations (especially the format-trend chart on Daily Recap, which
otherwise scans ~5K video rows per first-of-the-hour visitor) get computed
once after data changes and stored in the `dashboard_cache` table. Streamlit
pages then read a single row (≤1KB) instead of paginating through the videos
table.

Refresh hooks:
    - scripts/daily_refresh.py  → calls rebuild_all() at end (full refresh)
    - scripts/hourly_rss.py     → calls rebuild_all() if new videos found

Read path:
    - views/1_Daily_Recap.py    → reads `format_trend` for current scope
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

# Lookback window for the format-trend chart. Must match what the Daily Recap
# page expects (see views/1_Daily_Recap.py: LOOKBACK_DAYS).
FORMAT_TREND_LOOKBACK_DAYS = 14


# ── scope_key helpers ────────────────────────────────────────────────────
def scope_all() -> str:
    return "all"


def scope_league(league_name: str) -> str:
    return f"league:{league_name}"


def scope_club(channel_id: str) -> str:
    return f"club:{channel_id}"


# ── read / write ─────────────────────────────────────────────────────────
def read(db, name: str, scope_key: str) -> dict | None:
    """Return the cached payload, or None if not yet computed."""
    try:
        rows = (
            db.client.table("dashboard_cache")
            .select("payload,computed_at")
            .eq("name", name)
            .eq("scope_key", scope_key)
            .limit(1)
            .execute()
            .data or []
        )
    except Exception:
        return None
    if not rows:
        return None
    payload = rows[0].get("payload")
    if isinstance(payload, str):
        # supabase-py sometimes returns jsonb as a string
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    return {"payload": payload, "computed_at": rows[0].get("computed_at")}


def write(db, name: str, scope_key: str, payload: object) -> None:
    """Upsert a cache row. Silently ignores errors — caller logs if needed."""
    try:
        db.client.table("dashboard_cache").upsert(
            {
                "name": name,
                "scope_key": scope_key,
                "payload": payload,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="name,scope_key",
        ).execute()
    except Exception as e:
        print(f"[dashboard_cache] write failed for {name}/{scope_key}: {e}",
              flush=True)


# ── compute: format trend (per-day Long / Shorts / Live counts) ─────────
def _fetch_videos_window(db, start_iso: str, end_iso: str,
                         channel_ids: list[str] | None) -> list[dict]:
    """Paginated scan of videos.published_at + format + channel_id.

    IMPORTANT: must include .order(...) — without an explicit sort key,
    PostgREST may return rows in different orders across pages, causing
    pagination to drop or duplicate rows.
    """
    page = 1000
    offset = 0
    out: list[dict] = []
    while True:
        q = (
            db.client.table("videos")
            .select("published_at,format,duration_seconds,channel_id")
            .gte("published_at", start_iso)
            .lt("published_at", end_iso)
            .order("published_at")
            .range(offset, offset + page - 1)
        )
        if channel_ids:
            q = q.in_("channel_id", list(channel_ids))
        rows = q.execute().data or []
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


def compute_format_trend(db, channel_ids: list[str] | None,
                         lookback_days: int = FORMAT_TREND_LOOKBACK_DAYS) -> dict:
    """Compute per-day Long/Shorts/Live counts for the given filter.

    Returns:
        {
            "as_of": "2026-04-29",
            "rows": [
                {"date": "2026-04-15", "long": 7, "short": 12, "live": 2},
                ...
            ]
        }
    """
    # Use UTC dates for storage — page renders in CET on read, but the bucket
    # math is identical because we bucket on the published_at *date*, not time.
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=lookback_days)
    start_iso = start.isoformat() + "T00:00:00+00:00"
    end_iso = today.isoformat() + "T00:00:00+00:00"

    videos = _fetch_videos_window(db, start_iso, end_iso, channel_ids)

    buckets: dict[str, dict[str, int]] = {}
    # Pre-seed every date so days with 0 videos still render
    d = start
    while d < today:
        buckets[d.isoformat()] = {"long": 0, "short": 0, "live": 0}
        d += timedelta(days=1)

    for v in videos:
        pub = v.get("published_at") or ""
        try:
            dkey = datetime.fromisoformat(pub.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            continue
        if dkey not in buckets:
            continue
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        buckets[dkey][fmt] += 1

    rows = [
        {"date": d, "long": b["long"], "short": b["short"], "live": b["live"]}
        for d, b in sorted(buckets.items())
    ]
    return {"as_of": today.isoformat(), "rows": rows}


# ── rebuild orchestrator ─────────────────────────────────────────────────
def rebuild_all(db, log=print) -> None:
    """Recompute and persist every cached aggregation across every scope.

    Called by daily_refresh and hourly_rss when underlying data has changed.
    Cheap: ~6 queries × ≤5K rows each, ~5–10 seconds total.
    """
    chans = db.get_all_channels()
    clubs = [c for c in chans
             if c.get("entity_type") == "Club"]
    leagues_to_clubs: dict[str, list[str]] = {}
    for c in clubs:
        from src.channels import COUNTRY_TO_LEAGUE
        lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
        if lg:
            leagues_to_clubs.setdefault(lg, []).append(c["id"])

    all_club_ids = [c["id"] for c in clubs]

    # 1. format_trend: ALL clubs
    log("[dashboard_cache] computing format_trend / all")
    payload = compute_format_trend(db, all_club_ids)
    write(db, "format_trend", scope_all(), payload)

    # 2. format_trend: per-league
    for lg, ids in leagues_to_clubs.items():
        log(f"[dashboard_cache] computing format_trend / league:{lg} ({len(ids)} clubs)")
        payload = compute_format_trend(db, ids)
        write(db, "format_trend", scope_league(lg), payload)

    log("[dashboard_cache] rebuild done")
