"""Process-wide caches for the hottest read paths.

Streamlit's `@st.cache_data` is shared across sessions inside the same
process — so caching `get_all_channels()` here means 100 anon visitors
hitting Home in a 5-minute window cost us **one** Supabase query, not 100.

Underscored args (`_db`) tell Streamlit not to hash that param. We rely on
the database object being effectively constant per-process. If two
different Database instances are passed in, they share the same cache —
which is correct here (same project, same key).

TTLs are chosen so a stale read is cheap (numbers are off by minutes, not
hours) but cron-driven updates show up reasonably fast:
  - channels list:  30 min — changes only when admin adds a channel,
                            and the admin page invalidates explicitly
  - recent videos:  60 sec — refreshed by the hourly RSS cron
  - dashboard_cache: 60 sec — refreshed by daily/hourly crons
"""
from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=1800, show_spinner=False)
def get_all_channels(_db) -> list[dict]:
    """Cached wrapper for db.get_all_channels(). 30-min TTL because the
    channels list only changes when an admin adds/edits one — the
    Channel Management page calls st.cache_data.clear() after writes."""
    return _db.get_all_channels() or []


@st.cache_data(ttl=60, show_spinner=False)
def get_recent_videos(_db, limit: int, channel_ids: tuple[str, ...] | None) -> list[dict]:
    """Cached wrapper for db.get_recent_videos().

    `channel_ids` is a tuple (not a list) so Streamlit can hash it as a
    cache key. Callers pass `tuple(ids)`.
    """
    return _db.get_recent_videos(
        limit=limit,
        channel_ids=list(channel_ids) if channel_ids is not None else None,
    ) or []


@st.cache_data(ttl=60, show_spinner=False)
def read_dashboard_cache(_db, name: str, scope_key: str) -> dict | None:
    """Cached wrapper for src.dashboard_cache.read()."""
    from src import dashboard_cache as _dc
    return _dc.read(_db, name, scope_key)


@st.cache_data(ttl=60, show_spinner=False)
def get_last_fetch_time(_db, status_prefix: str) -> str | None:
    """Cached wrapper for db.get_last_fetch_time()."""
    return _db.get_last_fetch_time(status_prefix)


@st.cache_data(ttl=3600, show_spinner=False)
def get_last_upload_by_channel(_db, channel_ids_tuple: tuple[str, ...],
                               scope_key: str | None = None) -> dict[str, str]:
    """For each channel id in the input, return YYYY-MM-DD of its most
    recent video — or "" if we don't have one ingested.

    Used by the Federations / Players / Other Clubs / Women pages for
    the "🟢 Active / 🟡 Slowing / 🟠 Quiet / 🔴 Dormant" status badge.

    Fast path: when `scope_key` (the entity_type) is given, read the map
    the nightly cron precomputed into dashboard_cache
    (name='last_upload'). That's one cheap row vs. a paginated scan of
    the entire `videos` table — the scan, only masked by this 1h cache,
    is what made these pages slow on a cold cache (e.g. after a deploy).

    Fallback: missing/empty precomputed row (e.g. before the first cron
    run after this ships) → the original full scan, so behaviour is
    unchanged and there is no regression.

    Cached at 1h TTL: the underlying data only changes once a day.
    """
    if not channel_ids_tuple:
        return {}
    # ── Fast path: precomputed by the nightly cron ──
    if scope_key:
        try:
            resp = (
                _db.client.table("dashboard_cache")
                .select("payload")
                .eq("name", "last_upload")
                .eq("scope_key", scope_key)
                .limit(1)
                .execute()
            )
            data = resp.data or []
            if data:
                m = (data[0].get("payload") or {}).get("map") or {}
                if m:
                    want = set(channel_ids_tuple)
                    return {k: v for k, v in m.items() if k in want}
        except Exception:
            pass  # fall through to the scan
    # ── Fallback: original full scan ──
    from src.database import _fetch_all
    rows = _fetch_all(
        _db.client.table("videos")
        .select("channel_id,published_at")
        .in_("channel_id", list(channel_ids_tuple))
        .order("published_at", desc=True)
    )
    out: dict[str, str] = {}
    for r in rows:
        cid = r.get("channel_id")
        if cid and cid not in out:
            out[cid] = (r.get("published_at") or "")[:10]
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_last_upload_via_youtube(yt_channel_ids_tuple: tuple[str, ...]) -> dict[str, str]:
    """For each YouTube channel ID with no videos in our DB, fetch the
    most recent upload date directly from the YouTube Data API.

    Cached at 1h TTL because:
    - This is a per-channel API call (1 quota unit each)
    - The Federations / Players / Other Clubs / Women pages previously
      did this loop inline on every render — with 50+ federations, that
      was 50× ~150ms = ~7-10s of synchronous API calls per page render.

    Empty tuple → empty result, zero work. Returns {} on any YT API
    failure so the caller can fall back to a "—" badge.
    """
    if not yt_channel_ids_tuple:
        return {}
    import os
    from src.youtube_api import YouTubeClient
    yt_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not yt_key:
        return {}
    out: dict[str, str] = {}
    try:
        yt = YouTubeClient(yt_key)
        for yt_cid in yt_channel_ids_tuple:
            if not yt_cid or not yt_cid.startswith("UC"):
                continue
            try:
                uploads = "UU" + yt_cid[2:]
                recent = yt.get_recent_video_entries(uploads, max_results=1)
                if recent:
                    out[yt_cid] = (recent[0].get("published") or "")[:10]
            except Exception:
                continue
    except Exception:
        pass
    return out
