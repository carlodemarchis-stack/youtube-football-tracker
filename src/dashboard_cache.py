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
from zoneinfo import ZoneInfo

# Lookback window for the format-trend chart. Must match what the Daily Recap
# page expects (see views/1_Daily_Recap.py: LOOKBACK_DAYS).
FORMAT_TREND_LOOKBACK_DAYS = 14

# Bucket dates in CET — matches the Daily Recap page (date picker, KPI counts,
# everything else is CET). UTC bucketing would attribute videos published in
# the late-night CET hours (≈22:00–23:59 UTC) to the wrong day.
CET = ZoneInfo("Europe/Rome")


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
    # CET-bucketed (matches the Daily Recap page's KPI line + date picker).
    # Window: [start_cet 00:00 CET, today_cet 00:00 CET) — i.e. all of yesterday
    # and the previous (lookback_days - 1) full CET days.
    today_cet = datetime.now(CET).date()
    start_cet = today_cet - timedelta(days=lookback_days)
    start_iso = datetime.combine(start_cet, datetime.min.time(), tzinfo=CET) \
                       .astimezone(timezone.utc).isoformat()
    end_iso   = datetime.combine(today_cet, datetime.min.time(), tzinfo=CET) \
                       .astimezone(timezone.utc).isoformat()

    videos = _fetch_videos_window(db, start_iso, end_iso, channel_ids)

    buckets: dict[str, dict[str, int]] = {}
    # Pre-seed every date so days with 0 videos still render
    d = start_cet
    while d < today_cet:
        buckets[d.isoformat()] = {"long": 0, "short": 0, "live": 0}
        d += timedelta(days=1)

    for v in videos:
        pub = v.get("published_at") or ""
        try:
            # Convert UTC published_at → CET date
            dkey = datetime.fromisoformat(pub.replace("Z", "+00:00")) \
                           .astimezone(CET).date().isoformat()
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
    return {"as_of": today_cet.isoformat(), "rows": rows}


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

    # 3. daily_note: yesterday's witty AI commentary (best-effort).
    # Decoration (inline country flags before each club/league name)
    # happens here at write time, so pages just read ready-to-render HTML.
    try:
        from src import ai_note as _an
        from datetime import datetime as _dt, timedelta as _td
        target = (_dt.now(CET).date() - _td(days=1))
        log(f"[dashboard_cache] computing daily_note / {target.isoformat()}")
        payload = _an.compose_payload(db, target)
        note = _an.generate_daily_note(payload, log=log)
        if note:
            html = _an.decorate_with_badges(note, chans)
            write(db, "daily_note", target.isoformat(),
                  {"text": note,         # raw model output (kept for debug)
                   "html": html,         # decoration-ready HTML used by pages
                   "payload_summary": {
                      "total_new_videos": payload["totals"]["new_videos"],
                      "weekday": payload["weekday"],
                   }})
    except Exception as e:
        log(f"[dashboard_cache] daily_note failed (non-fatal): {e}")

    log("[dashboard_cache] rebuild done")
