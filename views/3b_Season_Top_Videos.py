"""Season Top Videos — three ranked tables (views / likes / comments)
for the current global filter.

Was inlined at the bottom of every zoom on Season 25/26; split out so
the rhythm-and-cadence Season page stays focused, and the rankings get
their own URL / nav slot.
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.filters import (
    get_global_filter, get_global_channels, get_channels_for_filter,
    get_league_for_channel, get_include_league, get_all_leagues_scope,
    render_page_subtitle, render_club_header, render_league_header,
)
from src.channels import get_season_since
from src.auth import require_login
from src.season_top import render_top_season_videos

load_dotenv()
require_login()

st.title("Season Top Videos")

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

league, club = get_global_filter()
SEASON_SINCE = get_season_since(channel=club, league=league)

# Channel lookup used by the renderer for badge / club name.
ch_by_id = {c["id"]: c for c in all_channels}


# ── Z3: single club ───────────────────────────────────────────
if club is not None:
    render_club_header(club, all_channels)
    render_page_subtitle(f"Top videos this season for {club['name']}",
                         updated_raw=None)
    _ids = [club["id"]]
    _label = club["name"]

# ── Z2: one league ────────────────────────────────────────────
elif league is not None:
    league_channels = get_channels_for_filter(all_channels, league)
    render_league_header(league, channels_in_scope=league_channels)
    render_page_subtitle(f"Top videos this season across {league}",
                         updated_raw=None)
    # Respect the per-league "All Clubs + League" toggle (matches the
    # behavior on the main Season page so the two pages tell the same
    # story under the same filter).
    if get_include_league():
        scope = [c for c in league_channels
                 if c.get("entity_type") not in
                    ("Player", "Federation", "OtherClub", "WomenClub")]
    else:
        scope = [c for c in league_channels
                 if c.get("entity_type") not in
                    ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    _ids = [c["id"] for c in scope]
    _label = league

# ── Z1: all leagues (respect the All-Leagues scope toggle) ────
else:
    _scope_label = get_all_leagues_scope()
    render_page_subtitle("Top videos this season across all leagues",
                         updated_raw=None)
    # On Season the Z1 "Overall" path always includes league channels,
    # so we mirror that here for consistency.
    if _scope_label == "Leagues only":
        scope = [c for c in all_channels if c.get("entity_type") == "League"]
    elif _scope_label == "All clubs":
        scope = [c for c in all_channels
                 if c.get("entity_type") not in
                    ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    else:
        scope = [c for c in all_channels
                 if c.get("entity_type") not in
                    ("Player", "Federation", "OtherClub", "WomenClub")]
    _ids = [c["id"] for c in scope]
    _label = "All Leagues"


if not _ids:
    st.caption("No channels in scope.")
    st.stop()

# ── Render the three ranked tables ────────────────────────────
render_top_season_videos(db, _ids, ch_by_id, SEASON_SINCE,
                         limit=20, header=f"🏆 Top Season Videos — {_label}",
                         order_by="view_count")
render_top_season_videos(db, _ids, ch_by_id, SEASON_SINCE,
                         limit=5, header=f"❤️ Top Liked Videos — {_label}",
                         order_by="like_count")
render_top_season_videos(db, _ids, ch_by_id, SEASON_SINCE,
                         limit=5, header=f"💬 Top Commented Videos — {_label}",
                         order_by="comment_count")
