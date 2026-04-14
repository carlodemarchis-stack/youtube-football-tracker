from __future__ import annotations

import os

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import get_global_color_map

load_dotenv()

st.title("Compare")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = db.get_all_channels()
clubs = [ch for ch in all_channels if ch.get("entity_type") != "League"]

if not clubs:
    st.warning("No clubs loaded yet.")
    st.stop()

club_names = sorted([ch["name"] for ch in clubs])

# ── Preset buttons ────────────────────────────────────────────
st.subheader("Quick Comparisons")
col1, col2, col3 = st.columns(3)
preset = None
with col1:
    if st.button("Serie A Big 3"):
        preset = ["Juventus", "Inter", "AC Milan"]
with col2:
    if st.button("Top 5 by Subs"):
        top5 = sorted(clubs, key=lambda c: c.get("subscriber_count", 0), reverse=True)[:5]
        preset = [c["name"] for c in top5]
with col3:
    if st.button("Top 5 by Views"):
        top5 = sorted(clubs, key=lambda c: c.get("total_views", 0), reverse=True)[:5]
        preset = [c["name"] for c in top5]

if preset:
    st.session_state["_compare_clubs"] = preset

# ── Club selector ─────────────────────────────────────────────
default = st.session_state.get("_compare_clubs", [])
default = [n for n in default if n in club_names]
selected = st.multiselect("Select 2-5 clubs to compare", club_names, default=default, max_selections=5)
st.session_state["_compare_clubs"] = selected

if len(selected) < 2:
    st.info("Select at least 2 clubs to compare.")
    st.stop()

# ── Get data ──────────────────────────────────────────────────
compare_channels = [ch for ch in clubs if ch["name"] in selected]
color_map = get_global_color_map()

# ── Stats table ───────────────────────────────────────────────
st.subheader("Side by Side")
rows = []
for ch in compare_channels:
    avg = ch.get("total_views", 0) // max(ch.get("video_count", 1), 1)
    rows.append({
        "Club": ch["name"],
        "Subscribers": fmt_num(ch.get("subscriber_count", 0)),
        "Total Views": fmt_num(ch.get("total_views", 0)),
        "Videos": fmt_num(ch.get("video_count", 0)),
        "Avg Views/Video": fmt_num(avg),
    })
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── Charts ────────────────────────────────────────────────────
comp_df = pd.DataFrame(compare_channels)
comp_df = comp_df.sort_values("subscriber_count", ascending=False)

def make_bar(data, y_col, title):
    fig = px.bar(data, x="name", y=y_col, color="name", color_discrete_map=color_map, title=title)
    fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="", margin=dict(t=40, b=20))
    return fig

col1, col2 = st.columns(2)
with col1:
    st.plotly_chart(make_bar(comp_df, "subscriber_count", "Subscribers"), use_container_width=True)
with col2:
    st.plotly_chart(make_bar(comp_df, "total_views", "Total Views"), use_container_width=True)

# ── Top 10 videos from each club ──────────────────────────────
st.subheader("Top Videos Head-to-Head")
all_vids = []
for ch in compare_channels:
    vids = db.get_videos_by_channel(ch["id"])
    for v in vids[:10]:
        v["club_name"] = ch["name"]
        all_vids.append(v)

if all_vids:
    vdf = pd.DataFrame(all_vids).sort_values("view_count", ascending=False).reset_index(drop=True)
    vdf["rank"] = range(1, len(vdf) + 1)

    fig = px.bar(
        vdf, x="rank", y="view_count",
        color="club_name", color_discrete_map=color_map,
        hover_data=["title"],
        labels={"rank": "Rank", "view_count": "Views", "club_name": "Club"},
    )
    fig.update_layout(
        xaxis_title="Rank", yaxis_title="Views",
        legend_title="Club", margin=dict(t=20, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    table_df = vdf[["title", "club_name", "view_count"]].head(20).copy()
    table_df["view_count"] = table_df["view_count"].apply(fmt_num)
    table_df.index = range(1, len(table_df) + 1)
    table_df.index.name = "Rank"
    st.dataframe(
        table_df.rename(columns={"title": "Title", "club_name": "Club", "view_count": "Views"}),
        use_container_width=True,
    )
