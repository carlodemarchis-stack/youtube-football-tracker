from __future__ import annotations

import os

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import get_global_filter, get_global_channels
from src.auth import require_premium

load_dotenv()
require_premium()

st.title("AI Analysis")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

if not all_channels:
    st.warning("No channel data yet.")
    st.stop()

league, club = get_global_filter()

if not club:
    st.info("Select a specific club using the filter above to see AI insights.")
    st.stop()

channel = club

# ── Display insights ──────────────────────────────────────────
existing_insights = db.get_insights(channel["id"])

if not existing_insights:
    st.info(f"No AI insights for {channel['name']} yet. Go to **Refresh Data** and click 'Generate AI Insights'.")
    st.stop()

data = existing_insights["insights_json"]

# Check if AI analysis is stale (data fetched after AI was generated)
ai_date = existing_insights.get("generated_at", "")
data_date = channel.get("last_fetched", "")
is_stale = bool(data_date and ai_date and data_date > ai_date)

from src.analytics import fmt_date
caption = f"Generated: {fmt_date(ai_date)} | Model: {existing_insights.get('model', 'unknown')}"
if data_date:
    caption += f" | Data: {fmt_date(data_date)}"
st.caption(caption)

if is_stale:
    st.warning("Data has been updated since this analysis was generated. Consider re-running AI insights from the **Data** page.")

# Summary
if data.get("summary"):
    st.markdown(f"**{data['summary']}**")

# Channel-Specific Themes - derive from video_themes map
video_themes_map = data.get("video_themes", {})
videos = db.get_videos_by_channel(channel["id"])

if video_themes_map and videos:
    st.subheader("Channel-Specific Themes")

    vdf = pd.DataFrame(videos).head(100)
    vdf = vdf.sort_values("view_count", ascending=False).reset_index(drop=True)
    vdf["rank"] = range(1, len(vdf) + 1)
    vdf["custom_theme"] = vdf["rank"].apply(lambda r: video_themes_map.get(str(r), "Other"))

    ct_stats = vdf.groupby("custom_theme").agg(
        count=("custom_theme", "size"),
        avg_views=("view_count", "mean"),
    ).reset_index().sort_values("count", ascending=False)
    ct_stats["avg_views"] = ct_stats["avg_views"].astype(int)

    fig_ct = px.pie(ct_stats, names="custom_theme", values="count", hole=0.4)
    fig_ct.update_layout(legend_title="Theme")
    st.plotly_chart(fig_ct, use_container_width=True)

    ct_display = ct_stats.copy()
    ct_display["avg_views"] = ct_display["avg_views"].apply(fmt_num)
    ct_display.columns = ["Theme", "Videos", "Avg Views"]
    st.dataframe(ct_display, use_container_width=True, hide_index=True)

    # Bar chart: rank vs views colored by custom theme
    fig_theme_rank = px.bar(
        vdf, x="rank", y="view_count",
        color="custom_theme",
        hover_data=["title"],
        labels={"rank": "Rank", "view_count": "Views", "custom_theme": "Theme"},
    )
    fig_theme_rank.update_layout(
        xaxis=dict(tickvals=[1] + list(range(5, 96, 5)) + [100], title="Rank"),
        yaxis_title="Views",
        legend_title="Theme",
        margin=dict(t=20, b=40),
        bargap=0.1,
    )
    st.plotly_chart(fig_theme_rank, use_container_width=True)

elif data.get("custom_themes"):
    st.subheader("Channel-Specific Themes")
    ct_df = pd.DataFrame(data["custom_themes"])
    if not ct_df.empty and "theme" in ct_df.columns:
        fig_ct = px.pie(ct_df, names="theme", values="count", hole=0.4)
        fig_ct.update_layout(legend_title="Theme")
        st.plotly_chart(fig_ct, use_container_width=True)

# Key Figures
if data.get("key_figures"):
    st.subheader("Key Figures / Players")
    for fig in data["key_figures"]:
        col1, col2, col3 = st.columns([3, 2, 2])
        col1.markdown(f"**{fig['name']}**")
        col2.markdown(f"{fig.get('video_count', 0)} videos")
        col3.markdown(f"{fmt_num(fig.get('total_views', 0))} views (avg {fmt_num(fig.get('avg_views', 0))})")
        if fig.get("notable_videos"):
            st.caption(", ".join(fig["notable_videos"][:3]))

# Content Themes
if data.get("themes"):
    st.subheader("Content Themes")
    for t in data["themes"]:
        st.markdown(f"**{t['theme']}** - {t.get('count', 0)} videos, avg {fmt_num(t.get('avg_views', 0))} views")
        if t.get("examples"):
            st.caption(", ".join(t["examples"][:3]))

# Surprise Hits
if data.get("surprise_hits"):
    st.subheader("Surprise Hits")
    for s in data["surprise_hits"]:
        st.markdown(f"**{s['title']}** ({fmt_num(s.get('views', 0))} views)")
        st.caption(s.get("why_surprising", ""))

# Era Analysis
if data.get("era_analysis"):
    st.subheader("Era Analysis")
    era_df = pd.DataFrame(data["era_analysis"])
    if not era_df.empty and "avg_views" in era_df.columns:
        fig_era = px.bar(era_df, x="period", y="avg_views",
                         text="video_count",
                         labels={"period": "Period", "avg_views": "Avg Views", "video_count": "Videos"})
        fig_era.update_layout(margin=dict(t=20, b=40))
        fig_era.update_traces(texttemplate="%{text} videos", textposition="outside")
        st.plotly_chart(fig_era, use_container_width=True)

    for era in data["era_analysis"]:
        st.caption(f"**{era['period']}**: {era.get('trend', '')}")

# Format Insights
if data.get("format_insights"):
    st.subheader("Format Insights")
    fi = data["format_insights"]
    col1, col2 = st.columns(2)
    col1.metric("Shorts (<60s)", f"{fi.get('shorts_count', 0)} videos")
    col1.caption(f"Avg views: {fmt_num(fi.get('shorts_avg_views', 0))}")
    col2.metric("Long-form", f"{fi.get('long_count', 0)} videos")
    col2.caption(f"Avg views: {fmt_num(fi.get('long_avg_views', 0))}")
    if fi.get("optimal_duration"):
        st.markdown(f"**Optimal duration:** {fi['optimal_duration']}")
    if fi.get("observation"):
        st.caption(fi["observation"])
