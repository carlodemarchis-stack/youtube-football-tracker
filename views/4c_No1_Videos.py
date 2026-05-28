"""No. 1 Videos — each core channel's #1 video, ranked by views.

Two tables:
  1. All-time #1 per channel
  2. Season #1 per channel (videos published since SEASON start)

Both have one row per channel (clubs + league channels; Players /
Federations / Other Clubs / Women excluded). Same table layout as
Season Top / All-Time Top so the rendering is shared.

Read path is two single dashboard_cache reads
(`top_no1_videos`/scope_all and `season_top_no1_videos`/scope_all),
populated by the daily/hourly rebuild. No global filter on this
page — the entire core roster is always shown.
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
    get_global_channels, render_page_subtitle, get_league_for_channel,
)
from src.auth import require_login
from src.season_top import render_top_season_videos_table
import pandas as pd
import plotly.express as px
from src.channels import LEAGUE_COLOR
from src import theme as _T

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

from src import dashboard_cache as _dc
from src.channels import get_season_since


def _live_top1_per_channel(since: str | None) -> list[dict]:
    """Cache-miss fallback path. Smaller probe than the rebuild's 5000;
    if a long-tail channel's #1 isn't in the top 2000 globally it just
    won't appear until the next cron tick."""
    core_ids = {
        c["id"] for c in all_channels
        if c.get("entity_type") not in
           ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub", "NFL")
        and c.get("id")
    }
    q = (db.client.table("videos")
         .select("id,youtube_video_id,channel_id,title,published_at,"
                 "duration_seconds,format,category,view_count,"
                 "like_count,comment_count,thumbnail_url,"
                 "channels(name,handle)")
         .order("view_count", desc=True)
         .limit(2000))
    if since:
        q = q.gte("published_at", since)
    rows = q.execute().data or []
    seen: dict[str, dict] = {}
    for v in rows:
        cid = v.get("channel_id")
        if cid in core_ids and cid not in seen:
            seen[cid] = v
    return sorted(seen.values(),
                  key=lambda v: int(v.get("view_count") or 0),
                  reverse=True)


def _load(cache_name: str, since: str | None) -> tuple[list[dict], str | None]:
    """Read from dashboard_cache, fall back to live probe on miss.
    Returns (rows, computed_at)."""
    cached = _cached_dc_read(db, cache_name, _dc.scope_all())
    payload = (cached or {}).get("payload") or {}
    rows = list(payload.get("rows") or [])
    if rows:
        return rows, (cached or {}).get("computed_at")
    try:
        return _live_top1_per_channel(since), None
    except Exception as e:
        st.error(f"Couldn't load videos: {e}")
        return [], None


# ── Page subtitle (uses the all-time cache's computed_at) ─────
allt_rows, allt_when = _load("top_no1_videos", None)
SEASON_SINCE = get_season_since()
season_rows, season_when = _load("season_top_no1_videos", SEASON_SINCE)

render_page_subtitle(
    f"#1 video of each channel · {len(allt_rows)} channels · "
    f"all-time and current-season views",
    updated_raw=allt_when,
)

def _views_by_rank(rows: list[dict], label: str) -> None:
    """Each channel's #1 video by rank (x) vs views (y), coloured by
    league — same formatting as the Top Videos pages' 'Views by Rank'.
    Tooltip carries the full per-video detail. All rows, no cap."""
    if not rows:
        return
    recs = []
    for i, v in enumerate(rows, 1):
        ch = ch_by_id.get(v.get("channel_id")) or {}
        recs.append({
            "Rank": i,
            "Views": int(v.get("view_count") or 0),
            "League": get_league_for_channel(ch) or "Other",
            "Channel": ch.get("name") or v.get("channel_name") or "—",
            "Title": (v.get("title") or "")[:90],
            "Likes": int(v.get("like_count") or 0),
            "Comments": int(v.get("comment_count") or 0),
        })
    df = pd.DataFrame(recs)
    st.subheader(f"👁️ Views by rank — {label}")
    fig = px.bar(
        df, x="Rank", y="Views", color="League",
        color_discrete_map=LEAGUE_COLOR,
        custom_data=["Channel", "Title", "League", "Views",
                     "Likes", "Comments"],
    )
    fig.update_traces(hovertemplate=(
        "<b>%{customdata[0]}</b> · #%{x}<br>"
        "%{customdata[1]}<br>"
        "<i>%{customdata[2]}</i><br>"
        "👁️ %{customdata[3]:,} · ❤️ %{customdata[4]:,} · "
        "💬 %{customdata[5]:,}<extra></extra>"
    ))
    fig.update_layout(
        xaxis=dict(title="Rank"),
        yaxis_title="Views",
        legend_title_text="League",
        margin=dict(t=20, b=40), bargap=0.1,
        plot_bgcolor=_T.BG, paper_bgcolor=_T.BG,
        font=dict(color=_T.TEXT),
    )
    st.plotly_chart(fig, width="stretch")


# ── Render — two tables, all-time first, season below ────────
if allt_rows:
    render_top_season_videos_table(
        allt_rows, ch_by_id,
        header="🥇 All-Time No. 1 Video by Channel",
        max_height=1100,
    )
    _views_by_rank(allt_rows, "All-Time No. 1s")
else:
    st.caption("All-time #1 list not available yet.")

st.markdown("---")

if season_rows:
    render_top_season_videos_table(
        season_rows, ch_by_id,
        header="🌟 Season No. 1 Video by Channel",
        max_height=1100,
    )
    _views_by_rank(season_rows, "Season No. 1s")
else:
    st.caption("Season #1 list not available yet.")
