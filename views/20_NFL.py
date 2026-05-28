"""🏈 NFL — All Channels (hidden v0).

First NFL surface. Channel-level data only — no videos, no per-video
snapshots, no trends. 33 channels (32 franchises + @NFL HQ) shown as
one flat sortable table.

Hidden from the sidebar nav (see app.py — registered but the link is
CSS-hidden). Reached only by URL `/nfl`.

Forward-compatibility: see docs/NFL_V0.md. The page exists alone today
but the file name `20_NFL.py` reserves the `20*` numbering for the
WC2026-style future expansion (`20a_NFL_Daily_Recap.py`, etc.).
"""
from __future__ import annotations

import os

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num
from src.auth import require_login

load_dotenv()
require_login()

st.title("🏈 NFL — All Channels")
st.markdown(
    '<div style="background:#1a1c24;border-left:3px solid #FFA15A;'
    'padding:10px 14px;margin:6px 0 14px 0;border-radius:4px;'
    'font-size:13px;line-height:1.55;color:#FAFAFA">'
    '<span style="color:#FFA15A;font-weight:600">🚧 NFL preview · pre-alpha</span>'
    ' &nbsp;—&nbsp; channel-level snapshot only. No videos / no trends '
    'yet. Hidden from the sidebar; reachable by direct URL.'
    '</div>', unsafe_allow_html=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
db = Database(SUPABASE_URL, SUPABASE_KEY)
nfl = [c for c in _cached_channels(db)
       if c.get("entity_type") == "NFL"]
if not nfl:
    st.info("No NFL channels in the DB yet — run "
            "`python3 scripts/import_nfl.py` to seed the roster.")
    st.stop()

# ── KPIs ──────────────────────────────────────────────────────────────
total_subs = sum(int(c.get("subscriber_count") or 0) for c in nfl)
total_views = sum(int(c.get("total_views") or 0) for c in nfl)
top_ch = max(nfl, key=lambda c: int(c.get("subscriber_count") or 0))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Channels", len(nfl))
c2.metric("Total subscribers", fmt_num(total_subs))
c3.metric("Total lifetime views", fmt_num(total_views))
c4.metric("Most subscribed", (top_ch.get("name") or "?")[:18],
          f"{fmt_num(int(top_ch.get('subscriber_count') or 0))} subs")

st.markdown("---")

# ── Sortable table ────────────────────────────────────────────────────
ordered = sorted(nfl, key=lambda c: -int(c.get("subscriber_count") or 0))
rows = []
for i, c in enumerate(ordered, 1):
    w = (c.get("competitions") or {}).get("nfl") or {}
    url = (f"https://www.youtube.com/@{c.get('handle')}"
           if c.get("handle") else
           f"https://www.youtube.com/channel/{c.get('youtube_channel_id', '')}")
    rows.append({
        "#": i,
        "Team": c.get("name") or "?",
        "Conference": w.get("conference") or "—",
        "Division": w.get("division") or "—",
        "Subscribers": int(c.get("subscriber_count") or 0),
        "Lifetime views": int(c.get("total_views") or 0),
        "Videos": int(c.get("video_count") or 0),
        "Last fetched": (c.get("last_fetched") or "")[:10] or "—",
        "Channel": url,
    })

df = pd.DataFrame(rows)
st.dataframe(
    df, width="stretch", hide_index=True,
    column_config={
        "Subscribers":    st.column_config.NumberColumn(format="%d"),
        "Lifetime views": st.column_config.NumberColumn(format="%d"),
        "Videos":         st.column_config.NumberColumn(format="%d"),
        "Channel":        st.column_config.LinkColumn(
                              "Channel", display_text="↗"),
    },
)

st.caption(
    "Conference + Division are stored in the database (for future "
    "grouping) but presented flat for v0. Click any column header to "
    "sort. The ↗ link opens the channel on YouTube."
)
