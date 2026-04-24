from __future__ import annotations

import os

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import get_global_filter, get_global_channels, render_page_subtitle
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

render_page_subtitle(
    f"AI-generated channel analysis · {existing_insights.get('model', 'unknown')}",
    updated_raw=ai_date,
)

if is_stale:
    st.warning("Data has been updated since this analysis was generated. Consider re-running AI insights from the **Data** page.")

# ── Club KPI banner (same as Channels page) ─────────────────
from src.filters import get_league_for_channel as _get_lg
_clubs = [c for c in all_channels if c.get("entity_type") not in ("League", "Player")]
_ch_league = _get_lg(channel)
_peers = [c for c in _clubs if _get_lg(c) == _ch_league]

_subs = channel.get("subscriber_count", 0) or 0
_views = channel.get("total_views", 0) or 0
_vids = channel.get("video_count", 0) or 0
_vps = _views // max(_subs, 1)
_vpv = _views // max(_vids, 1)

_metric_getters = {
    "subs": lambda c: c.get("subscriber_count", 0) or 0,
    "views": lambda c: c.get("total_views", 0) or 0,
    "vps": lambda c: (c.get("total_views", 0) or 0) // max(c.get("subscriber_count", 0) or 0, 1),
    "videos": lambda c: c.get("video_count", 0) or 0,
    "vpv": lambda c: (c.get("total_views", 0) or 0) // max(c.get("video_count", 0) or 0, 1),
}

def _rank(metric, scope):
    pool = _peers if scope == "league" else _clubs
    sorted_pool = sorted(pool, key=_metric_getters[metric], reverse=True)
    try:
        return next(i + 1 for i, c in enumerate(sorted_pool) if c["id"] == channel["id"]), len(sorted_pool)
    except StopIteration:
        return None, len(sorted_pool)

def _rank_html(metric):
    lr, lt = _rank(metric, "league")
    orr, ot = _rank(metric, "overall")
    parts = []
    if lr:
        parts.append(f"#{lr}/{lt} in {_ch_league}")
    if orr:
        parts.append(f"#{orr}/{ot} overall")
    return f"<div style='color:#888;font-size:0.8rem;margin-top:-6px'>{' · '.join(parts)}</div>" if parts else ""

_cols = st.columns(5)
with _cols[0]:
    st.metric("Total Subscribers", fmt_num(_subs))
    st.markdown(_rank_html("subs"), unsafe_allow_html=True)
with _cols[1]:
    st.metric("Total Views", fmt_num(_views))
    st.markdown(_rank_html("views"), unsafe_allow_html=True)
with _cols[2]:
    st.metric("Views/Sub", fmt_num(_vps))
    st.markdown(_rank_html("vps"), unsafe_allow_html=True)
with _cols[3]:
    st.metric("Total Videos", fmt_num(_vids))
    st.markdown(_rank_html("videos"), unsafe_allow_html=True)
with _cols[4]:
    st.metric("Avg Views/Video", fmt_num(_vpv))
    st.markdown(_rank_html("vpv"), unsafe_allow_html=True)

# Summary
if data.get("summary"):
    st.markdown(f"**{data['summary']}**")

# ── Top 100 All-Time ─────────────────────────────────────────
st.markdown("---")
st.subheader("Top 100 All-Time")

# Channel-Specific Themes - derive from video_themes map
video_themes_map = data.get("video_themes", {})
videos = db.get_videos_by_channel(channel["id"])

if video_themes_map and videos:
    st.markdown("**Channel-Specific Themes**")

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
    st.markdown("**Channel-Specific Themes**")
    ct_df = pd.DataFrame(data["custom_themes"])
    if not ct_df.empty and "theme" in ct_df.columns:
        fig_ct = px.pie(ct_df, names="theme", values="count", hole=0.4)
        fig_ct.update_layout(legend_title="Theme")
        st.plotly_chart(fig_ct, use_container_width=True)

# Content Themes
if data.get("themes"):
    st.markdown("**Content Themes**")
    for t in data["themes"]:
        st.markdown(f"**{t['theme']}** - {t.get('count', 0)} videos, avg {fmt_num(t.get('avg_views', 0))} views")
        if t.get("examples"):
            st.caption(", ".join(t["examples"][:3]))

# Key Figures
if data.get("key_figures"):
    st.markdown("**Key Figures / Players**")
    for fig in data["key_figures"]:
        col1, col2, col3 = st.columns([3, 2, 2])
        col1.markdown(f"**{fig['name']}**")
        col2.markdown(f"{fig.get('video_count', 0)} videos")
        col3.markdown(f"{fmt_num(fig.get('total_views', 0))} views (avg {fmt_num(fig.get('avg_views', 0))})")
        if fig.get("notable_videos"):
            st.caption(", ".join(fig["notable_videos"][:3]))

# Surprise Hits
if data.get("surprise_hits"):
    st.markdown("**Surprise Hits**")
    for s in data["surprise_hits"]:
        st.markdown(f"**{s['title']}** ({fmt_num(s.get('views', 0))} views)")
        st.caption(s.get("why_surprising", ""))

# Era Analysis
if data.get("era_analysis"):
    st.markdown("**Era Analysis**")
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
    st.markdown("**Format Insights**")
    fi = data["format_insights"]
    col1, col2, col3 = st.columns(3)
    col1.metric("Shorts (<60s)", f"{fi.get('shorts_count', 0)} videos")
    col1.caption(f"Avg views: {fmt_num(fi.get('shorts_avg_views', 0))}")
    col2.metric("Long-form", f"{fi.get('long_count', 0)} videos")
    col2.caption(f"Avg views: {fmt_num(fi.get('long_avg_views', 0))}")
    col3.metric("Live", f"{fi.get('live_count', 0)} videos")
    col3.caption(f"Avg views: {fmt_num(fi.get('live_avg_views', 0))}")
    if fi.get("optimal_duration"):
        st.markdown(f"**Optimal duration:** {fi['optimal_duration']}")
    if fi.get("observation"):
        st.caption(fi["observation"])

# ── Season 25/26 Analysis ────────────────────────────────────
_has_season = any(data.get(k) for k in ("season_overview", "season_analysis", "season_themes",
                                         "season_key_figures", "season_best_performers",
                                         "season_format_insights", "season_monthly"))
if _has_season:
    st.markdown("---")
    st.subheader("Season 25/26")

# Season Overview
sa = data.get("season_overview") or data.get("season_analysis")  # backward compat
if sa:
    sa_cols = st.columns(4)
    sa_cols[0].metric("Videos", sa.get("total_videos", 0))
    sa_cols[1].metric("Views", fmt_num(sa.get("total_views", 0)))
    sa_cols[2].metric("Avg Views/Video", fmt_num(sa.get("avg_views_per_video", 0)))
    momentum = sa.get("momentum", "stable")
    m_icon = {"rising": "📈", "declining": "📉", "stable": "➡️"}.get(momentum, "➡️")
    sa_cols[3].metric("Momentum", f"{m_icon} {momentum.title()}")

    if sa.get("comparison_to_alltime"):
        st.markdown(f"**vs All-Time Top 100:** {sa['comparison_to_alltime']}")
    if sa.get("season_insight"):
        st.info(sa["season_insight"])

# Season Themes
if data.get("season_themes"):
    st.markdown("**Content Themes**")
    for t in data["season_themes"]:
        st.markdown(f"**{t['theme']}** - {t.get('count', 0)} videos, avg {fmt_num(t.get('avg_views', 0))} views")
        if t.get("examples"):
            st.caption(", ".join(t["examples"][:3]))

# Season Key Figures
if data.get("season_key_figures"):
    st.markdown("**Key Figures / Players**")
    for fig in data["season_key_figures"]:
        col1, col2, col3 = st.columns([3, 2, 2])
        col1.markdown(f"**{fig['name']}**")
        col2.markdown(f"{fig.get('video_count', 0)} videos")
        col3.markdown(f"{fmt_num(fig.get('total_views', 0))} views (avg {fmt_num(fig.get('avg_views', 0))})")

# Season Best Performers
if data.get("season_best_performers"):
    st.markdown("**Best Performers**")
    for v in data["season_best_performers"]:
        fmt_label = f" · {v['format']}" if v.get("format") else ""
        st.markdown(f"**{v.get('title', '')}** ({fmt_num(v.get('views', 0))} views{fmt_label})")
        if v.get("why_it_worked"):
            st.caption(v["why_it_worked"])

# Season Format Insights
if data.get("season_format_insights"):
    st.markdown("**Format Insights**")
    sfi = data["season_format_insights"]
    sf_cols = st.columns(3)
    sf_cols[0].metric("Shorts", f"{sfi.get('shorts_count', 0)} videos")
    sf_cols[0].caption(f"Avg views: {fmt_num(sfi.get('shorts_avg_views', 0))}")
    sf_cols[1].metric("Long-form", f"{sfi.get('long_count', 0)} videos")
    sf_cols[1].caption(f"Avg views: {fmt_num(sfi.get('long_avg_views', 0))}")
    sf_cols[2].metric("Live", f"{sfi.get('live_count', 0)} videos")
    sf_cols[2].caption(f"Avg views: {fmt_num(sfi.get('live_avg_views', 0))}")
    if sfi.get("format_shift"):
        st.caption(f"Format shift: {sfi['format_shift']}")

# Season Monthly Breakdown
if data.get("season_monthly"):
    st.markdown("**Monthly Breakdown**")
    monthly_df = pd.DataFrame(data["season_monthly"])
    if not monthly_df.empty and "avg_views" in monthly_df.columns:
        fig_monthly = px.bar(monthly_df, x="month", y="total_views",
                             text="video_count",
                             labels={"month": "Month", "total_views": "Total Views", "video_count": "Videos"})
        fig_monthly.update_layout(margin=dict(t=20, b=40))
        fig_monthly.update_traces(texttemplate="%{text} videos", textposition="outside")
        st.plotly_chart(fig_monthly, use_container_width=True)

    for m in data["season_monthly"]:
        if m.get("note"):
            st.caption(f"**{m['month']}**: {m['note']}")

# ── Main Issues ──────────────────────────────────────────────
if data.get("main_issues"):
    st.markdown("---")
    st.subheader("Main Issues")
    for issue in data["main_issues"]:
        sev = issue.get("severity", "medium").lower()
        sev_icon = {"high": "🔴", "medium": "🟡", "low": "🔵"}.get(sev, "⚪")
        st.markdown(f"{sev_icon} **{issue.get('issue', '')}**")
        st.markdown(f"{issue.get('detail', '')}")
        st.caption(f"💡 {issue.get('recommendation', '')}")
