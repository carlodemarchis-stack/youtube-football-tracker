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
  - channels list:   5 min  — changes only when admin adds a channel
  - recent videos:   60 sec — refreshed by the hourly RSS cron
  - dashboard_cache: 60 sec — refreshed by daily/hourly crons
"""
from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=300, show_spinner=False)
def get_all_channels(_db) -> list[dict]:
    """Cached wrapper for db.get_all_channels()."""
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
