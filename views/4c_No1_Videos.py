"""No. 1 Videos — each core channel's all-time #1 video, ranked by views.

One row per channel (clubs + league channels; Players / Federations /
Other Clubs / Women excluded — they live on their own pages). Same
table layout as Season Top / All-Time Top so the rendering is shared.

Read path is single dashboard_cache row (`top_no1_videos`/scope_all),
populated by refresh_top_no1_videos in the daily/hourly rebuild.
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src.filters import (
    get_global_channels, render_page_subtitle,
)
from src.auth import require_login
from src.season_top import render_top_season_videos_table

load_dotenv()
require_login()

st.title("No. 1 Videos")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or _cached_channels(db)
if not all_channels:
    st.warning("No channel data yet.")
    st.stop()

ch_by_id = {c["id"]: c for c in all_channels}

# ── Read precomputed list from dashboard_cache (refreshed by rebuild_all) ─
from src import dashboard_cache as _dc
_cached = _cached_dc_read(db, "top_no1_videos", _dc.scope_all())
_payload = (_cached or {}).get("payload") or {}
rows = list(_payload.get("rows") or [])

# Cache-miss fallback: compute inline. First deploy, or new core channels
# added since the last rebuild. Reuses the same global-probe + dedupe
# pattern as the cache builder, with a smaller probe size since this is
# a "best effort while the cron catches up" path.
if not rows:
    core_ids = {
        c["id"] for c in all_channels
        if c.get("entity_type") not in
           ("Player", "Federation", "OtherClub", "WomenClub")
        and c.get("id")
    }
    try:
        _live = (db.client.table("videos")
                 .select("id,youtube_video_id,channel_id,title,published_at,"
                         "duration_seconds,format,category,view_count,"
                         "like_count,comment_count,thumbnail_url,"
                         "channels(name,handle)")
                 .order("view_count", desc=True)
                 .limit(2000)
                 .execute().data) or []
    except Exception as _e:
        st.error(f"Couldn't load videos: {_e}")
        st.stop()
    seen: dict[str, dict] = {}
    for v in _live:
        cid = v.get("channel_id")
        if cid in core_ids and cid not in seen:
            seen[cid] = v
    rows = sorted(seen.values(),
                  key=lambda v: int(v.get("view_count") or 0),
                  reverse=True)

if not rows:
    st.caption("No videos yet.")
    st.stop()

render_page_subtitle(
    f"All-time #1 video of each channel · {len(rows)} channels · "
    f"ranked by views",
    updated_raw=(_cached or {}).get("computed_at"),
)

# ── Render ────────────────────────────────────────────────────
# Cap iframe height with inner scroll so the page stays manageable
# even with ~100 entries.
render_top_season_videos_table(
    rows, ch_by_id,
    header="🥇 No. 1 Video by Channel",
    max_height=1100,
)
