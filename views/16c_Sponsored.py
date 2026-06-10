"""💰 Sponsored — videos carrying YouTube's "Includes paid promotion"
disclosure (paidProductPlacementDetails.hasPaidProductPlacement),
scoped by the global Top-5 league/club filter.

Two simple lists:
  - Latest: most recently published sponsored videos.
  - Top views: most-watched sponsored videos all-time.

The flag is fetched on every video read (src.youtube_api) and stored
on videos.has_paid_promotion; rare in football content (~1.3% of the
catalogue) so this page is a focused view of brand-deal content.
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.filters import (
    get_global_filter, get_global_channels, get_channels_for_filter,
    render_page_subtitle,
)
from src.season_top import render_top_season_videos_table
from src.auth import require_login

load_dotenv()
require_login()

st.title("💰 Sponsored")
render_page_subtitle(
    "Videos that disclose paid promotion (brand deals / product "
    "placement), within the current league/club filter.",
    caveat=("Source: YouTube's 'Includes paid promotion' flag. Rare "
            "in football content, so this is a focused slice — not "
            "every commercial tie-in formally discloses."),
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or _cached_channels(db)
_ch_by_id = {c["id"]: c for c in all_channels}

# Scope channels by the global filter (league + Overall/Clubs/Leagues
# scope), then narrow to a single club if one is picked.
league, club = get_global_filter()
scoped = get_channels_for_filter(all_channels, league)
if club:
    scoped = [c for c in scoped if c.get("id") == club.get("id")]
scope_ids = [c["id"] for c in scoped if c.get("id")]

if not scope_ids:
    st.info("No channels in the current filter scope.")
    st.stop()


@st.cache_data(ttl=300, show_spinner=False)
def _sponsored(channel_ids: tuple[str, ...], order_col: str,
               limit: int = 50) -> list[dict]:
    """Sponsored videos in scope, ordered by `order_col` desc."""
    if not channel_ids:
        return []
    return (db.client.table("videos")
            .select("id,youtube_video_id,title,channel_id,thumbnail_url,"
                    "duration_seconds,format,published_at,view_count,"
                    "like_count,comment_count,category")
            .in_("channel_id", list(channel_ids))
            .eq("has_paid_promotion", True)
            .order(order_col, desc=True)
            .limit(limit)
            .execute().data) or []


_ids_t = tuple(scope_ids)
_latest = _sponsored(_ids_t, "published_at")
_top = _sponsored(_ids_t, "view_count")

if not _latest and not _top:
    st.info("No sponsored videos found in the current filter scope.")
    st.stop()

# ── Top views ─────────────────────────────────────────────────────
render_top_season_videos_table(
    _top, _ch_by_id,
    header="👁️ Top sponsored videos — by views",
    order_by="views",
    max_height=1200,
)

st.markdown("---")

# ── Latest ────────────────────────────────────────────────────────
render_top_season_videos_table(
    _latest, _ch_by_id,
    header="🆕 Latest sponsored videos",
    subtitle="Most recently published, newest first.",
    order_by="views",
    max_height=1200,
)
