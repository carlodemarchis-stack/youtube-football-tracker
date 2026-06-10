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
from collections import Counter

import streamlit as st
import pandas as pd
import plotly.express as px
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

# ── Latest ────────────────────────────────────────────────────────
render_top_season_videos_table(
    _latest, _ch_by_id,
    header="🆕 Latest sponsored videos",
    subtitle="Most recently published, newest first.",
    order_by="views",
    max_height=1200,
)

st.markdown("---")

# ── Top views ─────────────────────────────────────────────────────
render_top_season_videos_table(
    _top, _ch_by_id,
    header="👁️ Top sponsored videos — by views",
    order_by="views",
    max_height=1200,
)

st.markdown("---")


# ── Sponsored videos per channel (top 25) ─────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _sponsored_counts(channel_ids: tuple[str, ...]) -> dict[str, int]:
    """Count sponsored videos per channel across the scope. Paginated
    so a scope with >1000 sponsored videos still totals correctly.
    Fast thanks to the partial index on has_paid_promotion."""
    if not channel_ids:
        return {}
    out: list[str] = []
    off = 0
    while True:
        rows = (db.client.table("videos").select("channel_id")
                .in_("channel_id", list(channel_ids))
                .eq("has_paid_promotion", True)
                .range(off, off + 999).execute().data) or []
        out.extend(r["channel_id"] for r in rows if r.get("channel_id"))
        if len(rows) < 1000:
            break
        off += 1000
    return dict(Counter(out))


_counts = _sponsored_counts(_ids_t)
if _counts:
    _TOP_N = 25
    _ranked = sorted(_counts.items(), key=lambda kv: kv[1], reverse=True)
    _shown = _ranked[:_TOP_N]
    _df = pd.DataFrame(
        [{"Channel": (_ch_by_id.get(cid) or {}).get("name", "?"),
          "Sponsored videos": n}
         for cid, n in _shown]
    )
    st.subheader("📊 Sponsored videos per channel")
    _cap = (f"Channels in scope with the most disclosed paid-promotion "
            f"videos. {len(_counts)} channel(s) have ≥1; ")
    _cap += (f"showing the top {_TOP_N}." if len(_counts) > _TOP_N
             else "showing all.")
    st.caption(_cap)
    # Horizontal bars, highest at top.
    fig = px.bar(
        _df.iloc[::-1], x="Sponsored videos", y="Channel",
        orientation="h",
        text="Sponsored videos",
    )
    fig.update_traces(marker_color="#E0A800", textposition="outside",
                      cliponaxis=False)
    fig.update_layout(
        height=max(320, 26 * len(_shown) + 80),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, l=0, r=30, b=20),
        xaxis=dict(title="", showgrid=True,
                   gridcolor="rgba(255,255,255,0.08)"),
        yaxis=dict(title=""),
    )
    st.plotly_chart(fig, width="stretch")
