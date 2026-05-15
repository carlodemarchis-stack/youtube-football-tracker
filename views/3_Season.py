from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import (
    get_all_channels as _cached_channels,
    get_last_fetch_time as _cached_last_fetch,
    get_recent_videos as _cached_recent,
    read_dashboard_cache as _cached_dc_read,
)
from src.analytics import fmt_num, yt_popup_js, CATEGORY_COLORS, kpi_row
try:
    from src.analytics import video_table_height
except ImportError:
    # Fallback if a prior version of src.analytics is still cached.
    def video_table_height(n_rows: int, header_buffer: int = 50) -> int:
        return max(0, int(n_rows)) * 75 + header_buffer
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_include_league, get_global_color_map, get_global_color_map_dual, get_all_leagues_scope, get_league_for_channel, render_page_subtitle
from src.auth import require_login
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG, LEAGUE_COLOR, LEAGUE_COLOR_CHART, get_season_since, LEAGUE_SEASON_START
from src.dot import dual_dot, channel_badge
from src.charts import chart_title

load_dotenv()
require_login()

from src.channels import current_season_label_safe as _csl
st.title(f"Season ({_csl()})")

# Heatmap colorscale shared by all three publishing-heatmap renders
# (Z1, Z2, Z3). Dark for empty/low slots, bright red at the peak so the
# "hottest" hour reads as such regardless of scale.
_HEATMAP_SCALE = [
    [0.0,  "#0E1117"],   # background (empty cells)
    [0.001, "#2a1c1c"],  # very dim red-tint just above zero
    [0.30, "#7a2c2c"],
    [0.55, "#FF6B35"],   # orange
    [0.80, "#FF1744"],   # red
    [1.0,  "#FFEB3B"],   # peak yellow-white pop
]


def _render_publishing_heatmap_grid(grid_counts, grid_avg_views,
                                    title: str, scope_label: str):
    """Render a 7×24 publishing-rhythm heatmap from precomputed grids.

    grid_counts / grid_avg_views: 7-row × 24-col 2D iterables (rows in
    Mon→Sun order, cols 00→23 hours, CET-bucketed).
    """
    import plotly.graph_objects as _go_h
    _hour_labels = [f"{h:02d}" for h in range(24)]
    _dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    # Find peak slot for the caption
    _max_n = 0
    _peak_dow = _peak_h = 0
    for r in range(7):
        for c in range(24):
            v = int(grid_counts[r][c] or 0)
            if v > _max_n:
                _max_n, _peak_dow, _peak_h = v, r, c
    if _max_n > 0:
        _peak = (f"Peak slot: **{_dow_labels[_peak_dow]} "
                 f"{_peak_h:02d}:00 CET** with {_max_n} posts.")
    else:
        _peak = ""

    st.subheader(title)
    st.caption(f"Day-of-week × hour (CET) for the season{scope_label}. "
               f"Brighter / redder = more videos. Hover for count and "
               f"average views per slot. {_peak}")

    _avg_disp = [[fmt_num(int(round(grid_avg_views[r][c] or 0)))
                  for c in range(24)] for r in range(7)]
    _counts_z = [[int(grid_counts[r][c] or 0) for c in range(24)] for r in range(7)]
    fig_hm = _go_h.Figure(_go_h.Heatmap(
        z=_counts_z,
        x=_hour_labels,
        y=_dow_labels,
        customdata=_avg_disp,
        colorscale=_HEATMAP_SCALE,
        colorbar=dict(title="Posts", thickness=10, x=1.02, len=0.9),
        hovertemplate=("<b>%{y} %{x}:00</b><br>"
                       "%{z} post(s)<br>"
                       "Avg views/post: %{customdata}<extra></extra>"),
        xgap=1, ygap=1,
    ))
    fig_hm.update_layout(
        height=300,
        xaxis=dict(title="Hour (CET)", tickfont=dict(size=10), side="bottom", tickmode="array", tickvals=[f"{h:02d}" for h in range(0, 24, 3)], ticktext=[f"{h:02d}" for h in range(0, 24, 3)]),
        yaxis=dict(title="", autorange="reversed",
                   tickfont=dict(size=11)),
        margin=dict(t=10, b=40, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"),
    )
    st.plotly_chart(fig_hm, use_container_width=True)


SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or _cached_channels(db)

if not all_channels:
    st.warning("No channel data yet. Go to **Refresh Data** to fetch data first.")
    st.stop()

league, club = get_global_filter()
now = datetime.now(timezone.utc)
SEASON_SINCE = get_season_since(channel=club, league=league)
_daily_updated = _cached_last_fetch(db, "daily")
_rss_updated = _cached_last_fetch(db, "hourly_rss")
_season_updated = max(filter(None, [_daily_updated, _rss_updated]), default=None)
render_page_subtitle(
    f"Season performance since {SEASON_SINCE}",
    updated_raw=_season_updated,
    caveat=f"Stats cover videos published on/after {SEASON_SINCE}. Views on older videos that happen during the season are not included.",
)


# ──────────────────────────────────────────────────────────────
# Shared helpers — used by zoom 1 (All Leagues, Overall) AND
# zoom 2 (All Leagues, scope-toggle modes) so per-league charts
# stay consistent across both.
# ──────────────────────────────────────────────────────────────
def _compute_league_stats(channels):
    """Aggregate season stats per league from precomputed channel
    columns. Returns {league_name: {views,videos,long_*,short_*,
    live_*, likes, comments, *_dur_total}}. Excludes Players /
    Federations / OtherClubs / WomenClubs (they have own pages)."""
    stats: dict[str, dict] = {}
    for ch in channels:
        if ch.get("entity_type") in ("Player", "Federation", "GoverningBody",
                                      "OtherClub", "WomenClub"):
            continue
        lg = get_league_for_channel(ch)
        if not lg:
            continue
        s = stats.setdefault(lg, {
            "videos": 0, "views": 0,
            "long_v": 0, "short_v": 0, "live_v": 0,
            "long_views": 0, "short_views": 0, "live_views": 0,
            "likes": 0, "comments": 0,
            "long_likes": 0, "short_likes": 0, "live_likes": 0,
            "long_comments": 0, "short_comments": 0, "live_comments": 0,
            "long_dur_total": 0, "short_dur_total": 0,
        })
        lv = int(ch.get("season_long_views") or 0)
        sv = int(ch.get("season_short_views") or 0)
        vv = int(ch.get("season_live_views") or 0)
        ln = int(ch.get("season_long_videos") or 0)
        sn = int(ch.get("season_short_videos") or 0)
        vn = int(ch.get("season_live_videos") or 0)
        s["views"] += lv + sv + vv
        s["videos"] += ln + sn + vn
        s["long_views"] += lv;  s["short_views"] += sv;  s["live_views"] += vv
        s["long_v"] += ln;      s["short_v"] += sn;      s["live_v"] += vn
        s["likes"] += int(ch.get("season_likes") or 0)
        s["comments"] += int(ch.get("season_comments") or 0)
        s["long_likes"] += int(ch.get("season_long_likes") or 0)
        s["short_likes"] += int(ch.get("season_short_likes") or 0)
        s["live_likes"] += int(ch.get("season_live_likes") or 0)
        s["long_comments"] += int(ch.get("season_long_comments") or 0)
        s["short_comments"] += int(ch.get("season_short_comments") or 0)
        s["live_comments"] += int(ch.get("season_live_comments") or 0)
        s["long_dur_total"]  += int(ch.get("season_long_dur_avg")  or 0) * ln
        s["short_dur_total"] += int(ch.get("season_short_dur_avg") or 0) * sn
    return stats


def _render_per_league_charts(sorted_leagues):
    """5 bar charts comparing leagues: Views, Videos (stacked
    Long/Shorts/Live), Engagement Rate, Avg Duration Long, Avg
    Duration Shorts. Renders directly with st.plotly_chart."""
    import plotly.graph_objects as go
    lg_names = [lg for lg, _ in sorted_leagues]

    fig_v = go.Figure()
    fig_v.add_trace(go.Bar(name="Long",   x=lg_names, y=[s["long_views"]  for _, s in sorted_leagues], marker_color="#636EFA"))
    fig_v.add_trace(go.Bar(name="Shorts", x=lg_names, y=[s["short_views"] for _, s in sorted_leagues], marker_color="#00CC96"))
    fig_v.add_trace(go.Bar(name="Live",   x=lg_names, y=[s["live_views"]  for _, s in sorted_leagues], marker_color="#FFA15A"))
    fig_v.update_layout(title="Season Views by League (Long / Shorts / Live)", barmode="stack",
                        xaxis_title="", yaxis_title="",
                        margin=dict(t=50, b=70, l=10, r=10),
                        legend=dict(orientation="h", yanchor="top", y=-0.15,
                                    xanchor="center", x=0.5),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#FAFAFA"))

    fig_n = go.Figure()
    fig_n.add_trace(go.Bar(name="Long",   x=lg_names, y=[s["long_v"]   for _, s in sorted_leagues], marker_color="#636EFA"))
    fig_n.add_trace(go.Bar(name="Shorts", x=lg_names, y=[s["short_v"]  for _, s in sorted_leagues], marker_color="#00CC96"))
    fig_n.add_trace(go.Bar(name="Live",   x=lg_names, y=[s["live_v"]   for _, s in sorted_leagues], marker_color="#FFA15A"))
    fig_n.update_layout(title="Season Videos by League (Long / Shorts / Live)", barmode="stack",
                        xaxis_title="", yaxis_title="",
                        margin=dict(t=50, b=70, l=10, r=10),
                        legend=dict(orientation="h", yanchor="top", y=-0.15,
                                    xanchor="center", x=0.5),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#FAFAFA"))

    # Third chart on this row: Views per Video (stacked Long/Shorts/Live).
    # Useful as a per-format efficiency comparison alongside the absolute
    # views and video counts.
    def _vpv(views, vids):
        return (views / vids) if vids else 0.0
    fig_vpv = go.Figure()
    fig_vpv.add_trace(go.Bar(name="Long",   x=lg_names,
                             y=[_vpv(s["long_views"],  s["long_v"])  for _, s in sorted_leagues],
                             marker_color="#636EFA"))
    fig_vpv.add_trace(go.Bar(name="Shorts", x=lg_names,
                             y=[_vpv(s["short_views"], s["short_v"]) for _, s in sorted_leagues],
                             marker_color="#00CC96"))
    fig_vpv.add_trace(go.Bar(name="Live",   x=lg_names,
                             y=[_vpv(s["live_views"],  s["live_v"])  for _, s in sorted_leagues],
                             marker_color="#FFA15A"))
    fig_vpv.update_layout(title="Season Views/Video by League (Long / Shorts / Live)", barmode="group",
                          xaxis_title="", yaxis_title="",
                          margin=dict(t=50, b=70, l=10, r=10),
                          legend=dict(orientation="h", yanchor="top", y=-0.15,
                                      xanchor="center", x=0.5),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          font=dict(color="#FAFAFA"))

    c1, c2, c3 = st.columns(3)
    c1.plotly_chart(fig_v, use_container_width=True)
    c2.plotly_chart(fig_n, use_container_width=True)
    c3.plotly_chart(fig_vpv, use_container_width=True)

    # Three secondary charts in one row: Engagement Rate, Avg Duration
    # (Long), Avg Duration (Shorts). Same set, all derived from the
    # league_stats aggregate — no fancy data wrangling.
    er_data = sorted(
        [(lg, ((s["likes"] + s["comments"]) / s["views"] * 100) if s["views"] else 0.0)
         for lg, s in sorted_leagues],
        key=lambda kv: -kv[1],
    )
    long_dur_data = sorted(
        [(lg, s["long_dur_total"] // max(s["long_v"], 1)) for lg, s in sorted_leagues],
        key=lambda kv: -kv[1],
    )
    short_dur_data = sorted(
        [(lg, s["short_dur_total"] // max(s["short_v"], 1)) for lg, s in sorted_leagues],
        key=lambda kv: -kv[1],
    )

    # Compact layout when the row is squeezed: shorter title, smaller
    # margins, no outside-bar labels (those wrap awkwardly in narrow
    # columns and force the plot height to grow).
    def _compact(fig, title, ylabel):
        fig.update_layout(
            title=dict(text=title, font=dict(size=14)),
            xaxis_title="", yaxis_title=ylabel,
            margin=dict(t=40, b=20, l=10, r=10),
            showlegend=False, height=320,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        fig.update_xaxes(tickangle=-25)
        return fig

    # League-brand color per bar (single value per league, so each bar
    # gets its league's own color rather than one shared format color).
    _GREY = "#888"  # fallback for unknown leagues
    def _colors_for(rows):
        return [LEAGUE_COLOR_CHART.get(lg, _GREY) for lg, _ in rows]

    er_fig = ldur_fig = sdur_fig = None
    if any(v > 0 for _, v in er_data):
        er_fig = go.Figure()
        er_fig.add_trace(go.Bar(
            x=[lg for lg, _ in er_data], y=[v for _, v in er_data],
            marker_color=_colors_for(er_data),
            hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
        ))
        _compact(er_fig, "Engagement Rate by League", "%")
    if any(v > 0 for _, v in long_dur_data):
        ldur_fig = go.Figure()
        ldur_fig.add_trace(go.Bar(
            x=[lg for lg, _ in long_dur_data],
            y=[v for _, v in long_dur_data],
            marker_color=_colors_for(long_dur_data),
            customdata=[f"{int(v)//60}:{int(v)%60:02d}" for _, v in long_dur_data],
            hovertemplate="%{x}: %{customdata}<extra></extra>",
        ))
        _compact(ldur_fig, "Avg Duration by League — Long-form", "Seconds")
    if any(v > 0 for _, v in short_dur_data):
        sdur_fig = go.Figure()
        sdur_fig.add_trace(go.Bar(
            x=[lg for lg, _ in short_dur_data],
            y=[v for _, v in short_dur_data],
            marker_color=_colors_for(short_dur_data),
            hovertemplate="%{x}: %{y}s<extra></extra>",
        ))
        _compact(sdur_fig, "Avg Duration by League — Shorts", "Seconds")

    if any([er_fig, ldur_fig, sdur_fig]):
        c1, c2, c3 = st.columns(3)
        with c1:
            if er_fig:
                st.plotly_chart(er_fig, use_container_width=True)
        with c2:
            if ldur_fig:
                st.plotly_chart(ldur_fig, use_container_width=True)
        with c3:
            if sdur_fig:
                st.plotly_chart(sdur_fig, use_container_width=True)


def _render_top_season_videos(channel_ids, channels_by_id, since,
                              limit=20, header="Top Season Videos",
                              order_by="view_count"):
    """Top-N season videos table, fed by get_top_season_videos. Same
    look as the per-club Top Season Videos table at zoom 3 — channel
    name on the meta line (with the standard dual-dot for clubs / flag
    for league channels) so the league/all-leagues version is
    self-explanatory.

    order_by: 'view_count' (default), 'like_count', or 'comment_count'.
    """
    vids = db.get_top_season_videos(channel_ids=channel_ids, since=since,
                                    limit=limit, order_by=order_by)
    if not vids:
        return
    st.subheader(header)

    # Inline the per-video helpers — this function runs at all zoom
    # levels, so it can't depend on the per-club helpers defined later
    # inside the zoom-3 branch.
    def _fmt_of_local(v):
        f = (v.get("format") or "").lower()
        if f in ("long", "short", "live"):
            return f
        return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
    _COLOR = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}
    _LABEL = {"long": "Long", "short": "Shorts", "live": "Live"}

    # Channel marker (dual-dot for clubs, country flag for league
    # channels) — same convention as every other table on the site.
    color_map = get_global_color_map() or {}
    dual_map = get_global_color_map_dual() or {}

    def _dur_local(secs):
        s = int(secs or 0)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    rows = ""
    for i, v in enumerate(vids, 1):
        ch = channels_by_id.get(v.get("channel_id")) or v.get("channels") or {}
        ch_name = ch.get("name", "?")
        ch_dot = channel_badge(ch, color_map, dual_map, 12)
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        _f = _fmt_of_local(v)
        fmt_label = _LABEL[_f]
        fmt_color = _COLOR[_f]
        pub = (v.get("published_at") or "")[:10]
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        _cat = (v.get("category") or "").replace("<", "&lt;")
        _cat_color = CATEGORY_COLORS.get(_cat, "#888")
        _cat_span = (f' · <span style="color:{_cat_color}">{_cat}</span>'
                     if _cat and _cat != "Other" else "")
        # Same meta order as zoom-3: format · duration · date · category.
        # Channel badge prefixes the line so the league/all-leagues table
        # still tells you which club the video belongs to.
        _meta = (
            f'<span style="display:inline-flex;align-items:center;gap:6px">'
            f'{ch_dot}<span style="color:#FAFAFA">{ch_name}</span></span>'
            f' · <span style="color:{fmt_color}">{fmt_label}</span>'
            f' · {_dur_local(v.get("duration_seconds", 0))}'
            f' · <span style="color:#888">{pub}</span>'
            f'{_cat_span}'
        )
        rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px;vertical-align:top"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px;display:block"></td>
            <td style="padding:6px 12px;vertical-align:top">
              <div style="display:flex;flex-direction:column;justify-content:space-between;height:62px">
                <a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none;font-weight:700"><br>{title}</a>
                <div style="font-size:12px">{_meta}</div>
              </div>
            </td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('view_count') or 0))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('like_count') or 0))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('comment_count') or 0))}</td>
        </tr>"""
    components.html(f"""
    <style>
        .top-vids {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                     font-family:"Source Sans Pro",sans-serif; }}
        .top-vids th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
        .top-vids td {{ border-bottom:1px solid #262730; }}
        .top-vids tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="top-vids">
    <thead><tr>
        <th style="text-align:right">#</th>
        <th></th>
        <th>Video</th>
        <th style="text-align:right">Views</th>
        <th style="text-align:right">Likes</th>
        <th style="text-align:right">Comments</th>
    </tr></thead>
    <tbody>{rows}</tbody>
    </table>
    """, height=video_table_height(len(vids)), scrolling=False)


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 1: ALL LEAGUES — branch by scope
# ══════════════════════════════════════════════════════════════
_scope = get_all_leagues_scope() if league is None else "Overall"

if league is None and _scope == "Overall":
    # Aggregate season stats per league — using precomputed channel columns (zero video queries)
    import plotly.graph_objects as go

    league_stats = _compute_league_stats(all_channels)

    if not league_stats:
        st.info("No season data yet.")
        st.stop()

    total_views = sum(s["views"] for s in league_stats.values())
    total_videos = sum(s["videos"] for s in league_stats.values())
    total_likes = sum(s["likes"] for s in league_stats.values())
    total_comments = sum(s["comments"] for s in league_stats.values())
    avg_vpv = total_views // max(total_videos, 1)
    eng_rate = ((total_likes + total_comments) / total_views * 100) if total_views else 0.0
    st.markdown(kpi_row([
        ("Total Season Views",  fmt_num(total_views)),
        ("Total Season Videos", fmt_num(total_videos)),
        ("Avg Views/Video",     fmt_num(avg_vpv)),
        ("Total Likes",         fmt_num(total_likes)),
        ("Total Comments",      fmt_num(total_comments)),
        ("Engagement Rate",     f"{eng_rate:.2f}%"),
    ]), unsafe_allow_html=True)

    # Pie charts: long/short/live split
    import plotly.graph_objects as go
    total_long_views = sum(s["long_views"] for s in league_stats.values())
    total_short_views = sum(s["short_views"] for s in league_stats.values())
    total_live_views = sum(s["live_views"] for s in league_stats.values())
    total_longs = sum(s["long_v"] for s in league_stats.values())
    total_shorts = sum(s["short_v"] for s in league_stats.values())
    total_lives = sum(s["live_v"] for s in league_stats.values())
    total_long_likes = sum(s["long_likes"] for s in league_stats.values())
    total_short_likes = sum(s["short_likes"] for s in league_stats.values())
    total_live_likes = sum(s["live_likes"] for s in league_stats.values())
    total_long_comments = sum(s["long_comments"] for s in league_stats.values())
    total_short_comments = sum(s["short_comments"] for s in league_stats.values())
    total_live_comments = sum(s["live_comments"] for s in league_stats.values())

    _has_live = total_lives > 0
    if _has_live:
        _pie_colors = ["#636EFA", "#00CC96", "#FFA15A"]
        _pie_labels = ["Long", "Shorts", "Live"]
    else:
        _pie_colors = ["#636EFA", "#00CC96"]
        _pie_labels = ["Long", "Shorts"]

    def _zv(l, s, lv):
        return [l, s, lv] if _has_live else [l, s]

    long_vpv = total_long_views // max(total_longs, 1)
    short_vpv = total_short_views // max(total_shorts, 1)
    live_vpv = total_live_views // max(total_lives, 1) if total_lives else 0

    def _make_pie(values, labels, colors, hover_suffix):
        fig = go.Figure(go.Pie(
            labels=labels, values=values, marker=dict(colors=colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
        ))
        # No in-chart title — it rendered off-centre relative to the
        # donut and the tall top margin left a big gap. The title is a
        # centred caption above the chart instead (see _pie_title).
        fig.update_layout(
            showlegend=False, height=260,
            margin=dict(t=10, b=10, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        return fig

    # Centred caption above each donut — shared so every KPI→donut
    # surface stays visually identical (src.charts).
    from src.charts import chart_title as _pie_title

    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
    with col_p1:
        _pie_title("Views")
        st.plotly_chart(_make_pie(_zv(total_long_views, total_short_views, total_live_views), _pie_labels, _pie_colors, "views"), use_container_width=True)
    with col_p2:
        _pie_title("Videos")
        st.plotly_chart(_make_pie(_zv(total_longs, total_shorts, total_lives), _pie_labels, _pie_colors, "videos"), use_container_width=True)
    with col_p3:
        _pie_title("Views/Video")
        st.plotly_chart(_make_pie(_zv(long_vpv, short_vpv, live_vpv), _pie_labels, _pie_colors, "views/video"), use_container_width=True)
    with col_p4:
        _pie_title("Likes")
        st.plotly_chart(_make_pie(_zv(total_long_likes, total_short_likes, total_live_likes), _pie_labels, _pie_colors, "likes"), use_container_width=True)
    with col_p5:
        _pie_title("Comments")
        st.plotly_chart(_make_pie(_zv(total_long_comments, total_short_comments, total_live_comments), _pie_labels, _pie_colors, "comments"), use_container_width=True)

    st.subheader("Leagues — Season")
    sorted_leagues = sorted(league_stats.items(), key=lambda kv: kv[1]["views"], reverse=True)

    def _v(v):
        v = int(v)
        return fmt_num(v) if v else "-"

    def _dur(secs):
        s = int(secs)
        if not s:
            return "-"
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    rows_html = ""
    for lg, s in sorted_leagues:
        vpv       = s["views"]       // max(s["videos"], 1)
        long_vpv  = s["long_views"]  // max(s["long_v"], 1)
        short_vpv = s["short_views"] // max(s["short_v"], 1)
        live_vpv  = s["live_views"]  // max(s["live_v"], 1) if s.get("live_v") else 0
        long_dur  = s["long_dur_total"]  // max(s["long_v"], 1)
        short_dur = s["short_dur_total"] // max(s["short_v"], 1)
        eng_rate  = ((s["likes"] + s["comments"]) / s["views"] * 100) if s["views"] else 0.0
        flag = LEAGUE_FLAG.get(lg, "")
        rows_html += f"""<tr>
            <td style="padding:6px 12px;text-align:center;font-size:16px">{flag}</td>
            <td style="padding:6px 12px;font-weight:600">{lg}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['views'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['long_views'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['short_views'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['live_views'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['videos'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['long_v'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['short_v'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['live_v'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{_v(long_vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{_v(short_vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{_v(live_vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{_dur(long_dur)}</td>
            <td style="padding:6px 12px;text-align:right">{_dur(short_dur)}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['likes'])}</td>
            <td style="padding:6px 12px;text-align:right">{_v(s['comments'])}</td>
            <td style="padding:6px 12px;text-align:right">{eng_rate:.2f}%</td>
        </tr>"""

    # Header layout mirrors the per-channel "ch-season" table below: same
    # column groups, same group colors (Avg Duration purple, Engagement
    # red — no Rate column, matches channels). Wrapped in overflow-x for
    # the same horizontal-scroll behavior on narrow viewports.
    components.html(f"""
    <style>
      .lg-wrap {{ overflow-x:auto; width:100%; }}
      table.lg {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                  font-family:"Source Sans Pro",sans-serif; min-width:1200px; }}
      table.lg th {{ padding:6px 12px; user-select:none; white-space:nowrap; }}
      table.lg td {{ border-bottom:1px solid #262730; white-space:nowrap; }}
      table.lg tr:hover td {{ background:#1a1c24; }}
    </style>
    <div class="lg-wrap">
    <table class="lg">
      <thead>
        <tr>
          <th colspan="2"></th>
          <th colspan="4" style="text-align:center;border-bottom:2px solid #58A6FF;color:#58A6FF">Views</th>
          <th colspan="4" style="text-align:center;border-bottom:2px solid #FFCA3A;color:#FFCA3A">Videos</th>
          <th colspan="4" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Views/Video</th>
          <th colspan="2" style="text-align:center;border-bottom:2px solid #FF6B9D;color:#FF6B9D">Avg Duration</th>
          <th colspan="3" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">Engagement</th>
        </tr>
        <tr style="border-bottom:2px solid #444">
          <th style="width:30px"></th>
          <th style="text-align:left">League</th>
          <th style="text-align:right">All</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">Live</th>
          <th style="text-align:right">All</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">Live</th>
          <th style="text-align:right">All</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">Live</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">Likes</th>
          <th style="text-align:right">Comments</th>
          <th style="text-align:right">Rate</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>
    """, height=len(sorted_leagues) * 37 + 130, scrolling=False)

    # 5 per-league bar charts (Views, Videos, Engagement Rate,
    # Avg Duration Long, Avg Duration Shorts) via shared helper —
    # same set is reused in zoom 2 when league is None.
    _render_per_league_charts(sorted_leagues)

    # ── Shorts duration distribution — All Leagues / Overall scope only ──
    # Where do views, supply, and per-video efficiency concentrate within
    # the 0-60s shorts band for SEASON videos? Bucket every season short
    # into 6-second bins and show three side-by-side charts:
    #   ⚡ cumulative views, 🎬 # shorts, 📈 avg views per short.
    #
    # Cron-cached (dashboard_cache.duration_buckets/shorts) — rebuild_all
    # writes a tiny JSON payload nightly. Page reads it instantly. Cold
    # fallback runs the live paginated query (only fires before the first
    # nightly run; should never run in production).
    @st.cache_data(ttl=3600, show_spinner=False)
    def _load_season_shorts_buckets(since_iso: str) -> list[dict]:
        from src import dashboard_cache as _dc
        try:
            row = _cached_dc_read(db, "duration_buckets", "shorts")
            cached = (row or {}).get("payload", {}).get("buckets")
            if cached:
                return cached
        except Exception:
            pass
        # Live fallback — paginated query
        from src.database import _fetch_all
        BUCKETS = [(0, 6), (7, 12), (13, 18), (19, 24), (25, 30),
                   (31, 36), (37, 42), (43, 48), (49, 54), (55, 60)]
        agg = {f"{lo}-{hi}s": {"label": f"{lo}-{hi}s", "lo": lo,
                               "views": 0, "videos": 0}
               for lo, hi in BUCKETS}
        rows = _fetch_all(
            db.client.table("videos")
            .select("duration_seconds,view_count")
            .eq("format", "short")
            .gte("published_at", since_iso)
        )
        for r in rows:
            d = int(r.get("duration_seconds") or 0)
            v = int(r.get("view_count") or 0)
            if not (0 <= d <= 60):
                continue
            for lo, hi in BUCKETS:
                if lo <= d <= hi:
                    key = f"{lo}-{hi}s"
                    agg[key]["views"] += v
                    agg[key]["videos"] += 1
                    break
        out = []
        for lo, hi in BUCKETS:
            b = agg[f"{lo}-{hi}s"]
            b["avg_views"] = (b["views"] // b["videos"]) if b["videos"] else 0
            out.append(b)
        return out

    _season_buckets = _load_season_shorts_buckets(SEASON_SINCE)
    if any(b["videos"] > 0 for b in _season_buckets):
        import altair as alt
        bucket_df = pd.DataFrame(_season_buckets)
        st.subheader("⚡ Season Shorts — duration distribution")
        st.caption(f"Every season short ({SEASON_SINCE} →) bucketed in 6-second bins. "
                   "Hover for exact totals.")

        _bucket_sort = list(bucket_df["label"])
        _hover = [
            alt.Tooltip("label:N", title="Duration"),
            alt.Tooltip("views:Q", format=",", title="Total views"),
            alt.Tooltip("videos:Q", format=",", title="# videos"),
            alt.Tooltip("avg_views:Q", format=",", title="Avg views/video"),
        ]
        bcol1, bcol2, bcol3 = st.columns(3)
        with bcol1:
            st.markdown(
                '<div style="font-size:14px;color:rgba(250,250,250,0.6);'
                'line-height:1.6">⚡ Cumulative views</div>',
                unsafe_allow_html=True,
            )
            cv = (alt.Chart(bucket_df).mark_bar(color="#58A6FF")  # Views = sky blue (metric palette)
                  .encode(
                      x=alt.X("label:N", sort=_bucket_sort, title=None,
                              axis=alt.Axis(labelAngle=-30, labelOverlap=False, labelLimit=200, labelPadding=4)),
                      y=alt.Y("views:Q", title=None,
                              axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")", minExtent=50)),
                      tooltip=_hover,
                  ).properties(height=240))
            st.altair_chart(cv, use_container_width=True)
        with bcol2:
            st.markdown(
                '<div style="font-size:14px;color:rgba(250,250,250,0.6);'
                'line-height:1.6">🎬 Number of shorts</div>',
                unsafe_allow_html=True,
            )
            cn = (alt.Chart(bucket_df).mark_bar(color="#FFCA3A")  # Videos = yellow (metric palette)
                  .encode(
                      x=alt.X("label:N", sort=_bucket_sort, title=None,
                              axis=alt.Axis(labelAngle=-30, labelOverlap=False, labelLimit=200, labelPadding=4)),
                      y=alt.Y("videos:Q", title=None,
                              axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")", minExtent=50)),
                      tooltip=_hover,
                  ).properties(height=240))
            st.altair_chart(cn, use_container_width=True)
        with bcol3:
            st.markdown(
                '<div style="font-size:14px;color:rgba(250,250,250,0.6);'
                'line-height:1.6">📈 Avg views / video</div>',
                unsafe_allow_html=True,
            )
            ca = (alt.Chart(bucket_df).mark_bar(color="#AB63FA")  # Views/Video = purple (metric palette)
                  .encode(
                      x=alt.X("label:N", sort=_bucket_sort, title=None,
                              axis=alt.Axis(labelAngle=-30, labelOverlap=False, labelLimit=200, labelPadding=4)),
                      y=alt.Y("avg_views:Q", title=None,
                              axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")", minExtent=50)),
                      tooltip=_hover,
                  ).properties(height=240))
            st.altair_chart(ca, use_container_width=True)

    # ── Long videos duration distribution — same idea, named buckets ──
    # Long videos span seconds to hours; use content-aware buckets that
    # match common YouTube formats (highlights, press conferences, full
    # matches) rather than uniform width.
    # Cron-cached (dashboard_cache.duration_buckets/long) — same pattern
    # as the shorts buckets. Live fallback only fires before the first
    # nightly run.
    @st.cache_data(ttl=3600, show_spinner=False)
    def _load_season_long_buckets(since_iso: str) -> list[dict]:
        from src import dashboard_cache as _dc
        try:
            row = _cached_dc_read(db, "duration_buckets", "long")
            cached = (row or {}).get("payload", {}).get("buckets")
            if cached:
                return cached
        except Exception:
            pass
        # Live fallback — paginated query
        from src.database import _fetch_all
        BUCKETS = [
            (60,    180,    "1-3m"),
            (181,   300,    "3-5m"),
            (301,   600,    "5-10m"),
            (601,   900,    "10-15m"),
            (901,   1800,   "15-30m"),
            (1801,  3600,   "30-60m"),
            (3601,  5400,   "60-90m"),
            (5401,  86400,  "90m+"),
        ]
        agg = {label: {"label": label, "lo": lo, "views": 0, "videos": 0}
               for lo, _, label in BUCKETS}
        rows = _fetch_all(
            db.client.table("videos")
            .select("duration_seconds,view_count")
            .eq("format", "long")
            .gte("published_at", since_iso)
        )
        for r in rows:
            d = int(r.get("duration_seconds") or 0)
            v = int(r.get("view_count") or 0)
            if d < 60:
                continue
            for lo, hi, label in BUCKETS:
                if lo <= d <= hi:
                    agg[label]["views"] += v
                    agg[label]["videos"] += 1
                    break
        out = []
        for _, _, label in BUCKETS:
            b = agg[label]
            b["avg_views"] = (b["views"] // b["videos"]) if b["videos"] else 0
            out.append(b)
        return out

    _long_buckets = _load_season_long_buckets(SEASON_SINCE)
    if any(b["videos"] > 0 for b in _long_buckets):
        long_df = pd.DataFrame(_long_buckets)
        st.subheader("🎞️ Season Long videos — duration distribution")
        st.caption(f"Every season long ({SEASON_SINCE} →) bucketed by content-shape "
                   "(1-3m clips up to 90m+ full matches). Hover for exact totals.")

        _long_sort = list(long_df["label"])
        _long_hover = [
            alt.Tooltip("label:N", title="Duration"),
            alt.Tooltip("views:Q", format=",", title="Total views"),
            alt.Tooltip("videos:Q", format=",", title="# videos"),
            alt.Tooltip("avg_views:Q", format=",", title="Avg views/video"),
        ]
        lcol1, lcol2, lcol3 = st.columns(3)
        with lcol1:
            st.markdown(
                '<div style="font-size:14px;color:rgba(250,250,250,0.6);'
                'line-height:1.6">⚡ Cumulative views</div>',
                unsafe_allow_html=True,
            )
            lv = (alt.Chart(long_df).mark_bar(color="#58A6FF")  # Views = sky blue (metric palette)
                  .encode(
                      x=alt.X("label:N", sort=_long_sort, title=None,
                              axis=alt.Axis(labelAngle=-30, labelOverlap=False, labelLimit=200, labelPadding=4)),
                      y=alt.Y("views:Q", title=None,
                              axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")", minExtent=50)),
                      tooltip=_long_hover,
                  ).properties(height=240))
            st.altair_chart(lv, use_container_width=True)
        with lcol2:
            st.markdown(
                '<div style="font-size:14px;color:rgba(250,250,250,0.6);'
                'line-height:1.6">🎬 Number of long videos</div>',
                unsafe_allow_html=True,
            )
            ln = (alt.Chart(long_df).mark_bar(color="#FFCA3A")  # Videos = yellow (metric palette)
                  .encode(
                      x=alt.X("label:N", sort=_long_sort, title=None,
                              axis=alt.Axis(labelAngle=-30, labelOverlap=False, labelLimit=200, labelPadding=4)),
                      y=alt.Y("videos:Q", title=None,
                              axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")", minExtent=50)),
                      tooltip=_long_hover,
                  ).properties(height=240))
            st.altair_chart(ln, use_container_width=True)
        with lcol3:
            st.markdown(
                '<div style="font-size:14px;color:rgba(250,250,250,0.6);'
                'line-height:1.6">📈 Avg views / video</div>',
                unsafe_allow_html=True,
            )
            la = (alt.Chart(long_df).mark_bar(color="#AB63FA")  # Views/Video = purple (metric palette)
                  .encode(
                      x=alt.X("label:N", sort=_long_sort, title=None,
                              axis=alt.Axis(labelAngle=-30, labelOverlap=False, labelLimit=200, labelPadding=4)),
                      y=alt.Y("avg_views:Q", title=None,
                              axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")", minExtent=50)),
                      tooltip=_long_hover,
                  ).properties(height=240))
            st.altair_chart(la, use_container_width=True)

    # ── Publish cadence — videos per week, stacked by league ──────────
    # Quick "is anyone slowing down?" view. One bar per ISO week from
    # SEASON_SINCE → today, segments coloured by league. Live-paginated
    # query — small payload (channel_id + published_at only).
    # Read from dashboard_cache (refreshed nightly + on hourly_rss new
    # videos via dashboard_cache.refresh_publish_cadence). Live fallback
    # only runs if the cache row is missing — cron path is the norm.
    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_publish_cadence(since_iso: str, _cache_v: int = 3) -> pd.DataFrame:
        from src import dashboard_cache as _dc
        try:
            row = _cached_dc_read(db, "publish_cadence", "all")
            cached_rows = (row or {}).get("payload", {}).get("rows")
            if cached_rows:
                df = pd.DataFrame(cached_rows)
                # month is stored as 'YYYY-MM' string — convert to date
                df["month"] = pd.to_datetime(df["month"] + "-01").dt.date
                return df[["month", "league", "videos"]]
        except Exception:
            pass

        # ── Live fallback (cold cache / first deploy) ──
        from src.database import _fetch_all
        scoped_ids = {
            c["id"] for c in all_channels
            if c.get("entity_type") in ("Club", "League")
            and get_league_for_channel(c)
        }
        if not scoped_ids:
            return pd.DataFrame()
        rows = _fetch_all(
            db.client.table("videos")
            .select("channel_id,published_at")
            .gte("published_at", since_iso)
        )
        if not rows:
            return pd.DataFrame()
        rows = [r for r in rows if r.get("channel_id") in scoped_ids]
        ch_lg = {c["id"]: get_league_for_channel(c) for c in all_channels
                 if c["id"] in scoped_ids}
        recs = []
        for r in rows:
            lg = ch_lg.get(r.get("channel_id"))
            pa = r.get("published_at") or ""
            if not lg or not pa:
                continue
            try:
                d = datetime.fromisoformat(pa.replace("Z", "+00:00")).date()
            except Exception:
                continue
            recs.append({"month": d.replace(day=1), "league": lg})
        if not recs:
            return pd.DataFrame()
        df = pd.DataFrame(recs)
        return (df.groupby(["month", "league"])
                  .size().reset_index(name="videos"))

    cadence_df = _load_publish_cadence(SEASON_SINCE)
    if not cadence_df.empty:
        import altair as alt
        import calendar
        # Project the current (incomplete) month from elapsed days. Scales
        # each league's partial count up by days_in_month / days_elapsed
        # so the trajectory doesn't visually fall off a cliff just because
        # the month isn't over yet.
        plot_df = cadence_df.copy()
        today = datetime.now().date()
        cur_month_start = today.replace(day=1)
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        days_elapsed = today.day  # incl. today; OK as upper bound
        projection_note = ""
        if days_elapsed < days_in_month:
            mask = plot_df["month"] == cur_month_start
            if mask.any():
                factor = days_in_month / days_elapsed
                plot_df.loc[mask, "videos"] = (
                    plot_df.loc[mask, "videos"].astype(float) * factor
                ).round().astype(int)
                projection_note = (
                    f" {today.strftime('%b %Y')} is projected from "
                    f"{days_elapsed} of {days_in_month} days."
                )

        st.subheader("📅 Publish cadence — videos per month")
        st.caption(f"Videos published per month since {SEASON_SINCE}, by league.{projection_note}")
        league_order = [lg for lg, _ in sorted_leagues]
        league_palette = [LEAGUE_COLOR_CHART.get(lg, "#888") for lg in league_order]
        domain = league_order
        rng = league_palette
        base = alt.Chart(plot_df).encode(
            x=alt.X("yearmonth(month):T", title=None,
                    axis=alt.Axis(format="%b %Y", labelAngle=-30)),
            y=alt.Y("videos:Q", title=None, axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")")),
            color=alt.Color("league:N",
                            scale=alt.Scale(domain=domain, range=rng)),
            tooltip=[
                alt.Tooltip("yearmonth(month):T", title="Month"),
                alt.Tooltip("league:N", title="League"),
                alt.Tooltip("videos:Q", format=",", title="Videos"),
            ],
        )
        # If the current month is projected, render the final segment dashed
        # so it visually reads as estimated rather than measured. Done by
        # layering two line marks: solid up to the previous month, dashed
        # spanning the previous→projected segment (overlap on the previous
        # month so colors and points line up at the seam).
        is_projected = bool(projection_note) and (
            plot_df["month"].max() == cur_month_start
        )
        if is_projected:
            months_sorted = sorted(plot_df["month"].unique())
            if len(months_sorted) >= 2:
                last_month = months_sorted[-1]
                prev_month = months_sorted[-2]
                solid_df = plot_df[plot_df["month"] <= prev_month]
                dashed_df = plot_df[plot_df["month"] >= prev_month]
                solid_layer = (
                    alt.Chart(solid_df).encode(
                        x=alt.X("yearmonth(month):T", title=None,
                                axis=alt.Axis(format="%b %Y", labelAngle=-30)),
                        y=alt.Y("videos:Q", title=None,
                                axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")")),
                        color=alt.Color("league:N",
                                        scale=alt.Scale(domain=domain, range=rng)),
                        tooltip=[
                            alt.Tooltip("yearmonth(month):T", title="Month"),
                            alt.Tooltip("league:N", title="League"),
                            alt.Tooltip("videos:Q", format=",", title="Videos"),
                        ],
                    ).mark_line(point=True, strokeWidth=2)
                )
                dashed_layer = (
                    alt.Chart(dashed_df).encode(
                        x=alt.X("yearmonth(month):T", title=None,
                                axis=alt.Axis(format="%b %Y", labelAngle=-30)),
                        y=alt.Y("videos:Q", title=None,
                                axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")")),
                        color=alt.Color("league:N",
                                        scale=alt.Scale(domain=domain, range=rng)),
                        tooltip=[
                            alt.Tooltip("yearmonth(month):T", title="Month"),
                            alt.Tooltip("league:N", title="League"),
                            alt.Tooltip("videos:Q", format=",", title="Videos (projected)"),
                        ],
                    ).mark_line(point=True, strokeWidth=2, strokeDash=[5, 4])
                )
                cadence_chart = (solid_layer + dashed_layer).properties(height=300)
            else:
                cadence_chart = (
                    base.mark_line(point=True, strokeWidth=2)
                    .properties(height=300)
                )
        else:
            cadence_chart = (
                base.mark_line(point=True, strokeWidth=2)
                .properties(height=300)
            )
        st.altair_chart(cadence_chart, use_container_width=True)

    # ── Videos per day across the whole ecosystem ─────────────────────
    # Reads precomputed payload from dashboard_cache (rebuilt by
    # daily_refresh / hourly_rss). Falls back to inline aggregation if
    # the cache row is empty (first deploy before the next cron run).
    try:
        from src import dashboard_cache as _dc_vpd
        _vpd_payload = _cached_dc_read(db, "videos_per_day", _dc_vpd.scope_all())
        _vpd_payload = (_vpd_payload or {}).get("payload") or {}
        _dates = [pd.to_datetime(d).date()
                  for d in (_vpd_payload.get("days") or [])]
        _vals = list(_vpd_payload.get("counts") or [])
        if not _vals:
            # Cache miss — compute inline (paginated query, ~one-shot).
            from src.database import _fetch_all as _fa_vpd
            from collections import Counter as _Counter
            _vpd_clubs = [c for c in all_channels
                          if c.get("entity_type") not in
                             ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub")]
            _vpd_ids = [c["id"] for c in _vpd_clubs if c.get("id")]
            _vpd_rows = _fa_vpd(
                db.client.table("videos")
                  .select("published_at")
                  .gte("published_at", SEASON_SINCE)
                  .in_("channel_id", _vpd_ids)
            ) if _vpd_ids else []
            _vpd_counts = _Counter()
            for _r in _vpd_rows:
                _pa = _r.get("published_at") or ""
                if not _pa:
                    continue
                try:
                    _d = pd.to_datetime(_pa, utc=True).date()
                except Exception:
                    continue
                _vpd_counts[_d] += 1
            _dates = sorted(_vpd_counts.keys())
            _vals = [_vpd_counts[d] for d in _dates]
        if _vals:
            import plotly.graph_objects as _go_vpd
                # Highlight weekends with a slightly warmer color so the
                # match-day rhythm reads at a glance.
            _bar_colors_vpd = [
                "#FFA15A" if d.weekday() >= 5 else "#636EFA"
                for d in _dates
            ]
            _avg = sum(_vals) / len(_vals) if _vals else 0
            st.subheader("📈 Videos per day — All Leagues")
            st.caption(
                f"{sum(_vals):,} videos across {len(_dates)} days "
                f"(avg {_avg:.0f}/day). Weekends in orange — useful "
                f"to spot match-day spikes vs midweek baseline."
            )
            fig_vpd = _go_vpd.Figure()
            fig_vpd.add_trace(_go_vpd.Bar(
                x=_dates, y=_vals,
                marker_color=_bar_colors_vpd,
                hovertemplate="<b>%{x|%a %b %d, %Y}</b><br>"
                              "%{y} video(s)<extra></extra>",
            ))
            fig_vpd.add_hline(y=_avg, line_dash="dash",
                              line_color="rgba(255,255,255,0.35)",
                              annotation_text=f"avg {_avg:.0f}",
                              annotation_position="top right",
                              annotation_font_color="rgba(255,255,255,0.55)")
            fig_vpd.update_layout(
                height=320,
                xaxis=dict(title="", showgrid=False),
                yaxis=dict(title="Videos published", showgrid=True,
                           gridcolor="rgba(255,255,255,0.08)"),
                margin=dict(t=30, b=40, l=60, r=20),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                showlegend=False, bargap=0.15,
            )
            st.plotly_chart(fig_vpd, use_container_width=True)
    except Exception as _e:
        st.caption(f"(videos-per-day chart unavailable: {_e})")

    # ── Publishing heatmap — when does the whole ecosystem post? ──
    try:
        from src import dashboard_cache as _dc_hm_all
        _hm = _dc_hm_all.read(db, "publishing_heatmap", _dc_hm_all.scope_all())
        _hm_payload = (_hm or {}).get("payload") or {}
        _hm_counts = _hm_payload.get("counts")
        _hm_sums = _hm_payload.get("sum_views")
        if _hm_counts and _hm_sums:
            _hm_avg = [[(_hm_sums[r][c] // _hm_counts[r][c])
                        if _hm_counts[r][c] else 0
                        for c in range(24)] for r in range(7)]
            _render_publishing_heatmap_grid(
                _hm_counts, _hm_avg,
                "📅 Publishing heatmap — All Leagues",
                " (all Top-5 league channels combined)")
    except Exception as _e:
        st.caption(f"(publishing heatmap unavailable: {_e})")

    # ── Views concentration — one bar per league ───────────────────────
    try:
        from src import dashboard_cache as _dc_conc_all
        _conc_all = _dc_conc_all.read(db, "concentration", _dc_conc_all.scope_all())
        _conc_all_rows = (_conc_all or {}).get("payload", {}).get("rows", []) if _conc_all else []
        if _conc_all_rows:
            import plotly.graph_objects as _go_ca
            st.subheader("📊 Views concentration — by league")
            st.caption("How concentrated are views in each league's catalog (all clubs combined)? "
                       "Bar = % of videos that account for 80% of total views. "
                       "Lower = a few hits drive the league. Higher = views spread evenly. "
                       "Hover for top-1 / top-10 share and median vs avg.")
            _conc_all_rows = sorted(_conc_all_rows, key=lambda r: r["pct_to_80"])
            _lg_names = [LEAGUE_FLAG.get(r["league"], "") + " " + r["league"]
                         for r in _conc_all_rows]
            _pcts = [r["pct_to_80"] for r in _conc_all_rows]
            _customdata = [
                [r["n_to_80"], r["n_videos"], r["top1_pct"], r["top10_pct"],
                 fmt_num(r["median_views"]), fmt_num(r["avg_views"]),
                 fmt_num(r["total_views"])]
                for r in _conc_all_rows
            ]
            _bar_colors_ca = [LEAGUE_COLOR_CHART.get(r["league"], "#888")
                              for r in _conc_all_rows]
            fig_ca = _go_ca.Figure()
            fig_ca.add_trace(_go_ca.Bar(
                x=_pcts, y=_lg_names, orientation="h",
                marker_color=_bar_colors_ca,
                customdata=_customdata,
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Top %{customdata[0]} of %{customdata[1]} videos = 80% of views<br>"
                    "Top 1 video = %{customdata[2]:.0f}% · "
                    "Top 10 = %{customdata[3]:.0f}%<br>"
                    "Median: %{customdata[4]} · Avg: %{customdata[5]}<br>"
                    "Total season views: %{customdata[6]}<extra></extra>"
                ),
            ))
            fig_ca.add_vline(x=20, line_dash="dash", line_color="#888",
                             annotation_text="20%", annotation_position="top",
                             annotation_font_color="#888")
            fig_ca.update_layout(
                height=max(260, 36 * len(_lg_names) + 80),
                xaxis=dict(title="% of videos to reach 80% of season views",
                           range=[0, max(40, max(_pcts) * 1.2)],
                           ticksuffix="%",
                           showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
                yaxis=dict(title="", autorange="reversed"),
                margin=dict(t=30, b=50, l=140),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                showlegend=False,
            )
            st.plotly_chart(fig_ca, use_container_width=True)
    except Exception as _e:
        st.caption(f"(concentration chart unavailable: {_e})")

    # ── Z1: worst-25 zero-video-days, all leagues ──────────────────────
    # Reads precomputed `zero_day_counts` from dashboard_cache and trims
    # client-side to the worst 25. Falls back to inline aggregation on
    # cache miss (first deploy before the next cron run).
    try:
        from src import dashboard_cache as _dc_z1
        from datetime import date as _date_z1
        _z1_payload = _cached_dc_read(db, "zero_day_counts", _dc_z1.scope_all())
        _z1_payload = (_z1_payload or {}).get("payload") or {}
        _z1_zd_rows = list(_z1_payload.get("rows") or [])
        _total_days_z1 = int(_z1_payload.get("total_days") or 0)
        _start_z1 = pd.to_datetime(_z1_payload.get("since") or SEASON_SINCE).date()
        if not _z1_zd_rows:
            from src.database import _fetch_all as _fa_z1
            _z1_clubs = [c for c in all_channels
                         if c.get("entity_type") not in
                            ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub")]
            _z1_ids = [c["id"] for c in _z1_clubs if c.get("id")]
            _z1_rows = _fa_z1(
                db.client.table("videos")
                  .select("channel_id,published_at")
                  .gte("published_at", SEASON_SINCE)
                  .in_("channel_id", _z1_ids)
            ) if _z1_ids else []
            _today_z1 = _date_z1.today()
            _start_z1 = pd.to_datetime(SEASON_SINCE).date()
            _total_days_z1 = (_today_z1 - _start_z1).days + 1
            _days_by_ch_z1: dict[str, set] = {}
            for _r in _z1_rows:
                _cid = _r.get("channel_id")
                _pa = _r.get("published_at") or ""
                if not _cid or not _pa:
                    continue
                try:
                    _d = pd.to_datetime(_pa, utc=True).date()
                except Exception:
                    continue
                _days_by_ch_z1.setdefault(_cid, set()).add(_d)
            _ch_by_id_z1 = {c["id"]: c for c in _z1_clubs}
            for _cid in _z1_ids:
                _ch = _ch_by_id_z1.get(_cid) or {}
                _published_days = len(_days_by_ch_z1.get(_cid, set()))
                _z1_zd_rows.append({
                    "name": _ch.get("name") or _cid,
                    "zero_days": max(0, _total_days_z1 - _published_days),
                    "published_days": _published_days,
                })
        # Trim to worst-25 desc.
        _z1_zd_rows = sorted(_z1_zd_rows,
                             key=lambda r: r.get("zero_days", 0),
                             reverse=True)[:25]
        # Re-attach total_days to every row for the hover tooltip below.
        for _r in _z1_zd_rows:
            _r.setdefault("total_days", _total_days_z1)

        if _z1_zd_rows:
            import plotly.graph_objects as _go_z1
            st.subheader("📅 Days with zero videos — worst 25 channels")
            st.caption(
                f"Out of {_total_days_z1} season days "
                f"(since {_start_z1.strftime('%b %d, %Y')}), the 25 "
                f"channels with the most days without any new video. "
                f"Higher bar = quieter publisher."
            )
            _color_map_z1 = get_global_color_map() or {}
            _z1_names = [r["name"] for r in _z1_zd_rows]
            _z1_vals = [r["zero_days"] for r in _z1_zd_rows]
            _z1_colors = [_color_map_z1.get(n, "#888") for n in _z1_names]
            _z1_custom = [
                [r["published_days"], r["total_days"],
                 (r["published_days"] / r["total_days"] * 100) if r["total_days"] else 0]
                for r in _z1_zd_rows
            ]
            fig_z1zd = _go_z1.Figure()
            fig_z1zd.add_trace(_go_z1.Bar(
                x=_z1_names, y=_z1_vals,
                marker_color=_z1_colors,
                customdata=_z1_custom,
                hovertemplate=(
                    "<b>%{x}</b><br>"
                    "Zero-video days: %{y}<br>"
                    "Days with at least 1 video: %{customdata[0]} of %{customdata[1]} "
                    "(%{customdata[2]:.0f}%)<extra></extra>"
                ),
            ))
            fig_z1zd.update_layout(
                height=440,
                xaxis=dict(title="", tickangle=-35, showgrid=False),
                yaxis=dict(title="Days without any new video this season",
                           showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
                margin=dict(t=30, b=140, l=60),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                showlegend=False,
            )
            st.plotly_chart(fig_z1zd, use_container_width=True)
    except Exception as _e:
        st.caption(f"(zero-day chart unavailable: {_e})")

    # ── All channels table — precomputed columns (zero video queries) ───
    st.subheader("All Channels — Season")
    color_map = get_global_color_map()
    dual_colors = get_global_color_map_dual()
    # On the All-Leagues "Overall" scope we always include league
    # channels — Overall means everything visible on the page, and
    # the leagues summary above already aggregates league-channel
    # output into the league totals. Forcing the per-league toggle
    # to True here also makes the Top Season Videos block (which
    # passes include_league downstream) include league-channel
    # videos consistently.
    include_league = True

    ch_rows = []
    for ch in all_channels:
        if ch.get("entity_type") in ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub"):
            continue  # isolated entity types live on their own pages
        lv = int(ch.get("season_long_views") or 0)
        sv = int(ch.get("season_short_views") or 0)
        vv = int(ch.get("season_live_views") or 0)
        ln = int(ch.get("season_long_videos") or 0)
        sn = int(ch.get("season_short_videos") or 0)
        vn = int(ch.get("season_live_videos") or 0)
        av = lv + sv + vv
        an = ln + sn + vn
        if an == 0:
            continue
        ch_rows.append({
            "name": ch["name"], "handle": ch.get("handle", ""),
            "entity_type": ch.get("entity_type"), "country": ch.get("country"),
            "all_views": av, "all_videos": an,
            "long_views": lv, "long_videos": ln,
            "short_views": sv, "short_videos": sn,
            "live_views": vv, "live_videos": vn,
            "long_dur_total": int(ch.get("season_long_dur_avg") or 0) * max(ln, 1),
            "short_dur_total": int(ch.get("season_short_dur_avg") or 0) * max(sn, 1),
            "live_dur_total": int(ch.get("season_live_dur_avg") or 0) * max(vn, 1),
            "likes": int(ch.get("season_likes") or 0),
            "comments": int(ch.get("season_comments") or 0),
        })
    # Default sort key matches the visible "All ▼" header in column 2.
    ch_rows.sort(key=lambda r: r["all_views"], reverse=True)

    def _v(val):
        v = int(val)
        return fmt_num(v) if v else '-'

    def _dur(secs):
        s = int(secs)
        if not s:
            return '-'
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    ch_rows_html = ""
    for row in ch_rows:
        dot = channel_badge(row, color_map, dual_colors, 14)
        handle = row.get("handle", "")
        _row_click = f'onclick="window.open(\'https://www.youtube.com/{handle}\',\'_blank\',\'noopener\')" style="cursor:pointer"' if handle else ''
        all_vpv = row["all_views"] // max(row["all_videos"], 1)
        long_vpv = row["long_views"] // max(row["long_videos"], 1)
        short_vpv = row["short_views"] // max(row["short_videos"], 1)
        live_vpv = row["live_views"] // max(row["live_videos"], 1)
        long_dur = row["long_dur_total"] // max(row["long_videos"], 1)
        short_dur = row["short_dur_total"] // max(row["short_videos"], 1)
        live_dur = row["live_dur_total"] // max(row["live_videos"], 1)
        eng_rate = ((row["likes"] + row["comments"]) / row["all_views"] * 100) if row["all_views"] else 0.0

        ch_rows_html += f"""<tr {_row_click}>
            <td style="padding:6px 12px">{dot}</td>
            <td style="padding:6px 12px" data-val="{row['name']}">{row['name']}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_views']}">{_v(row['all_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_views']}">{_v(row['long_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_views']}">{_v(row['short_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['live_views']}">{_v(row['live_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_videos']}">{_v(row['all_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_videos']}">{_v(row['long_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_videos']}">{_v(row['short_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['live_videos']}">{_v(row['live_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{all_vpv}">{_v(all_vpv)}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{long_vpv}">{_v(long_vpv)}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{short_vpv}">{_v(short_vpv)}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{live_vpv}">{_v(live_vpv)}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{long_dur}">{_dur(long_dur)}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{short_dur}">{_dur(short_dur)}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['likes']}">{_v(row['likes'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['comments']}">{_v(row['comments'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{eng_rate}">{eng_rate:.2f}%</td>
        </tr>"""

    # Measured: body rows ~30px, 2-row sticky header ~62px, iframe chrome ~12px.
    _ch_table_height = len(ch_rows) * 30 + 74
    components.html(f"""
    <style>
        .ch-wrap {{ overflow-x:auto; width:100%; }}
        .ch-season {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                      font-family:"Source Sans Pro",sans-serif; background:transparent;
                      min-width:1200px; }}
        .ch-season th {{ padding:6px 12px; user-select:none; white-space:nowrap; }}
        .ch-season th[data-col] {{ cursor:pointer; }}
        .ch-season th[data-col]:hover {{ color:#636EFA; }}
        .ch-season td {{ padding:6px 12px; border-bottom:1px solid #262730; white-space:nowrap; }}
        .ch-season tr:hover td {{ background:#1a1c24; }}
        .ch-season a {{ color:inherit; text-decoration:none; }}
        .ch-season .active {{ color:#636EFA; }}
    </style>
    <div class="ch-wrap">
    <table class="ch-season">
    <thead>
    <tr>
        <th colspan="2"></th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #58A6FF;color:#58A6FF">Views</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #FFCA3A;color:#FFCA3A">Videos</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Views/Video</th>
        <th colspan="2" style="text-align:center;border-bottom:2px solid #FF6B9D;color:#FF6B9D">Avg Duration</th>
        <th colspan="3" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">Engagement</th>
    </tr>
    <tr style="border-bottom:2px solid #444">
        <th style="width:30px"></th>
        <th data-col="1" data-type="str" style="text-align:left">Channel</th>
        <th data-col="2" data-type="num" style="text-align:right" class="active">All ▼</th>
        <th data-col="3" data-type="num" style="text-align:right">Long</th>
        <th data-col="4" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="5" data-type="num" style="text-align:right">Live</th>
        <th data-col="6" data-type="num" style="text-align:right">All</th>
        <th data-col="7" data-type="num" style="text-align:right">Long</th>
        <th data-col="8" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="9" data-type="num" style="text-align:right">Live</th>
        <th data-col="10" data-type="num" style="text-align:right">All</th>
        <th data-col="11" data-type="num" style="text-align:right">Long</th>
        <th data-col="12" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="13" data-type="num" style="text-align:right">Live</th>
        <th data-col="14" data-type="num" style="text-align:right">Long</th>
        <th data-col="15" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="16" data-type="num" style="text-align:right">Likes</th>
        <th data-col="17" data-type="num" style="text-align:right">Comments</th>
        <th data-col="18" data-type="num" style="text-align:right">Rate</th>
    </tr>
    </thead>
    <tbody>{ch_rows_html}</tbody>
    </table>
    </div>
    <script>
    (function() {{
        const table = document.querySelector('.ch-season');
        const tbody = table.querySelector('tbody');
        const headers = table.querySelectorAll('th[data-col]');
        let currentCol = 2;
        let currentAsc = false;

        function sortTable(colIdx, type) {{
            const rows = Array.from(tbody.rows);
            const isStr = type === 'str';
            if (colIdx === currentCol) {{
                currentAsc = !currentAsc;
            }} else {{
                currentCol = colIdx;
                currentAsc = isStr;
            }}
            rows.sort((a, b) => {{
                const va = a.cells[colIdx].dataset.val || '';
                const vb = b.cells[colIdx].dataset.val || '';
                let cmp;
                if (isStr) {{
                    cmp = va.localeCompare(vb, undefined, {{sensitivity:'base'}});
                }} else {{
                    cmp = (parseFloat(va) || 0) - (parseFloat(vb) || 0);
                }}
                return currentAsc ? cmp : -cmp;
            }});
            rows.forEach(r => tbody.appendChild(r));

            headers.forEach(h => {{
                h.classList.remove('active');
                const base = h.textContent.replace(/ [▲▼]/g, '');
                h.textContent = base;
            }});
            const activeHdr = table.querySelector('th[data-col="' + colIdx + '"]');
            activeHdr.classList.add('active');
            activeHdr.textContent += currentAsc ? ' ▲' : ' ▼';
        }}

        headers.forEach(h => {{
            h.addEventListener('click', function() {{
                sortTable(parseInt(this.dataset.col), this.dataset.type || 'num');
            }});
        }});
    }})();
    </script>
    """, height=_ch_table_height, scrolling=False)

    # ── Top season videos across the whole ecosystem ───────────
    # Top Season Videos lists moved to their own page (Season Top Videos)
    # to keep this page focused on rhythm + cadence + format mix.
    st.stop()


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 2: ONE LEAGUE (or All Leagues + Leagues only / All clubs)
# ══════════════════════════════════════════════════════════════
if club is None:
    if league is None:
        # All Leagues + (Leagues only | All clubs) — scope-filtered channels
        clubs_only = get_channels_for_filter(all_channels, None)
    else:
        league_channels = get_channels_for_filter(all_channels, league)
        include_league = get_include_league()
        if include_league:
            clubs_only = league_channels
        else:
            clubs_only = [ch for ch in league_channels if ch.get("entity_type") not in ("League", "Player", "Federation", "GoverningBody", "OtherClub", "WomenClub")]

    if not clubs_only:
        st.info("No clubs in this league yet.")
        st.stop()

    # League header — same visual signature as render_club_header at Z3.
    # When league is None (All-Leagues + scope toggle) we don't render a
    # header; the page already has the global filter bar at the top.
    if league:
        from src.filters import render_league_header
        render_league_header(league, channels_in_scope=clubs_only)

    color_map = get_global_color_map()
    dual_colors = get_global_color_map_dual()

    # Read precomputed season breakdown from channels table (zero video queries)
    season_rows = []
    for ch in clubs_only:
        lv = int(ch.get("season_long_views") or 0)
        sv = int(ch.get("season_short_views") or 0)
        vv = int(ch.get("season_live_views") or 0)
        ln = int(ch.get("season_long_videos") or 0)
        sn = int(ch.get("season_short_videos") or 0)
        vn = int(ch.get("season_live_videos") or 0)
        av = lv + sv + vv
        an = ln + sn + vn
        lk = int(ch.get("season_likes") or 0)
        cm = int(ch.get("season_comments") or 0)
        season_rows.append({
            "name": ch["name"], "handle": ch.get("handle", ""),
            "entity_type": ch.get("entity_type"), "country": ch.get("country"),
            "all_views": av, "all_videos": an,
            "all_vpv": av // max(an, 1),
            "long_views": lv, "long_videos": ln,
            "long_vpv": lv // max(ln, 1),
            "short_views": sv, "short_videos": sn,
            "short_vpv": sv // max(sn, 1),
            "live_views": vv, "live_videos": vn,
            "live_vpv": vv // max(vn, 1),
            "long_dur": int(ch.get("season_long_dur_avg") or 0),
            "short_dur": int(ch.get("season_short_dur_avg") or 0),
            "likes": lk, "comments": cm,
            "eng_rate": ((lk + cm) / av * 100) if av else 0.0,
            "long_likes": int(ch.get("season_long_likes") or 0),
            "short_likes": int(ch.get("season_short_likes") or 0),
            "live_likes": int(ch.get("season_live_likes") or 0),
            "long_comments": int(ch.get("season_long_comments") or 0),
            "short_comments": int(ch.get("season_short_comments") or 0),
            "live_comments": int(ch.get("season_live_comments") or 0),
        })

    df = pd.DataFrame(season_rows) if season_rows else pd.DataFrame()

    # Totals banner
    total_views = int(df["all_views"].sum())
    total_videos = int(df["all_videos"].sum())
    total_longs = int(df["long_videos"].sum())
    total_shorts = int(df["short_videos"].sum())
    avg_vpv = total_views // max(total_videos, 1)
    total_long_views = int(df["long_views"].sum())
    total_short_views = int(df["short_views"].sum())
    total_likes = int(df["likes"].sum())
    total_comments = int(df["comments"].sum())
    total_eng_rate = ((total_likes + total_comments) / total_views * 100) if total_views else 0.0
    total_lives = int(df["live_videos"].sum())
    total_live_views = int(df["live_views"].sum())
    _has_live = total_lives > 0

    # KPI banner row (likes/comments + engagement rate alongside pie totals)
    st.markdown(kpi_row([
        ("Total Views",     fmt_num(total_views)),
        ("Total Videos",    fmt_num(total_videos)),
        ("Avg Views/Video", fmt_num(total_views // max(total_videos, 1))),
        ("Total Likes",     fmt_num(total_likes)),
        ("Total Comments",  fmt_num(total_comments)),
        ("Engagement Rate", f"{total_eng_rate:.2f}%"),
    ]), unsafe_allow_html=True)

    # Pie charts: long/short split
    import plotly.graph_objects as go

    if _has_live:
        pie_colors = ["#636EFA", "#00CC96", "#FFA15A"]
        pie_labels = ["Long", "Shorts", "Live"]
    else:
        pie_colors = ["#636EFA", "#00CC96"]
        pie_labels = ["Long", "Shorts"]

    long_vpv = total_long_views // max(total_longs, 1)
    short_vpv = total_short_views // max(total_shorts, 1)
    live_vpv = total_live_views // max(total_lives, 1) if total_lives else 0
    def _zvals(l, s, lv):
        return [l, s, lv] if _has_live else [l, s]

    total_long_likes = int(df["long_likes"].sum())
    total_short_likes = int(df["short_likes"].sum())
    total_live_likes = int(df["live_likes"].sum())
    total_long_comments = int(df["long_comments"].sum())
    total_short_comments = int(df["short_comments"].sum())
    total_live_comments = int(df["live_comments"].sum())

    def _make_pie_lg(values, labels, colors, hover_suffix, title):
        chart_title(title)
        fig = go.Figure(go.Pie(
            labels=labels, values=values, marker=dict(colors=colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
        ))
        fig.update_layout(
            showlegend=False, height=260,
            margin=dict(t=10, b=10, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        return fig

    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
    with col_p1:
        st.plotly_chart(_make_pie_lg(_zvals(total_long_views, total_short_views, total_live_views), pie_labels, pie_colors, "views", "Views"), use_container_width=True)
    with col_p2:
        st.plotly_chart(_make_pie_lg(_zvals(total_longs, total_shorts, total_lives), pie_labels, pie_colors, "videos", "Videos"), use_container_width=True)
    with col_p3:
        st.plotly_chart(_make_pie_lg(_zvals(long_vpv, short_vpv, live_vpv), pie_labels, pie_colors, "views/video", "Views/Video"), use_container_width=True)
    with col_p4:
        st.plotly_chart(_make_pie_lg(_zvals(total_long_likes, total_short_likes, total_live_likes), pie_labels, pie_colors, "likes", "Likes"), use_container_width=True)
    with col_p5:
        st.plotly_chart(_make_pie_lg(_zvals(total_long_comments, total_short_comments, total_live_comments), pie_labels, pie_colors, "comments", "Comments"), use_container_width=True)

    # Default sort: all views descending
    df = df.sort_values("all_views", ascending=False).reset_index(drop=True)

    # ── Build table rows ────────────────────────────────────────
    rows_html = ""
    for _, row in df.iterrows():
        dot = channel_badge(row.to_dict(), color_map, dual_colors, 14)
        handle = row.get("handle", "")
        _row_click = f'onclick="window.open(\'https://www.youtube.com/{handle}\',\'_blank\',\'noopener\')" style="cursor:pointer"' if handle else ''
        def _v(val):
            v = int(val)
            return fmt_num(v) if v else '-'

        def _dur(secs):
            s = int(secs)
            if not s:
                return '-'
            m, sec = divmod(s, 60)
            return f"{m}:{sec:02d}"

        rows_html += f"""<tr {_row_click}>
            <td style="padding:6px 12px">{dot}</td>
            <td style="padding:6px 12px" data-val="{row['name']}">{row['name']}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_views']}">{_v(row['all_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_views']}">{_v(row['long_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_views']}">{_v(row['short_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['live_views']}">{_v(row['live_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_videos']}">{_v(row['all_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_videos']}">{_v(row['long_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_videos']}">{_v(row['short_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['live_videos']}">{_v(row['live_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_vpv']}">{_v(row['all_vpv'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_vpv']}">{_v(row['long_vpv'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_vpv']}">{_v(row['short_vpv'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['live_vpv']}">{_v(row['live_vpv'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_dur']}">{_dur(row['long_dur'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_dur']}">{_dur(row['short_dur'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['likes']}">{_v(row['likes'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['comments']}">{_v(row['comments'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['eng_rate']}">{row['eng_rate']:.2f}%</td>
        </tr>"""

    # ── Sortable table ──────────────────────────────────────────
    # Tightened: single-line rows ~30px, two-row sticky header ~60px.
    _table_height = len(df) * 32 + 70
    components.html(f"""
    <style>
        .st-wrap {{ overflow-x:auto; width:100%; }}
        .st-table {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                     font-family:"Source Sans Pro",sans-serif; background:transparent;
                     min-width:1200px; }}
        .st-table th {{ padding:6px 12px; user-select:none; white-space:nowrap; }}
        .st-table th[data-col] {{ cursor:pointer; }}
        .st-table th[data-col]:hover {{ color:#636EFA; }}
        .st-table td {{ padding:6px 12px; border-bottom:1px solid #262730; white-space:nowrap; }}
        .st-table tr:hover td {{ background:#1a1c24; }}
        .st-table a {{ color:inherit; text-decoration:none; }}
        .st-table .active {{ color:#636EFA; }}
    </style>
    <div class="st-wrap">
    <table class="st-table">
    <thead>
    <tr>
        <th colspan="2"></th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #58A6FF;color:#58A6FF">Views</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #FFCA3A;color:#FFCA3A">Videos</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Views/Video</th>
        <th colspan="2" style="text-align:center;border-bottom:2px solid #FF6B9D;color:#FF6B9D">Avg Duration</th>
        <th colspan="3" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">Engagement</th>
    </tr>
    <tr style="border-bottom:2px solid #444">
        <th style="width:30px"></th>
        <th data-col="1" data-type="str" style="text-align:left">Channel</th>
        <th data-col="2" data-type="num" style="text-align:right" class="active">All ▼</th>
        <th data-col="3" data-type="num" style="text-align:right">Long</th>
        <th data-col="4" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="5" data-type="num" style="text-align:right">Live</th>
        <th data-col="6" data-type="num" style="text-align:right">All</th>
        <th data-col="7" data-type="num" style="text-align:right">Long</th>
        <th data-col="8" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="9" data-type="num" style="text-align:right">Live</th>
        <th data-col="10" data-type="num" style="text-align:right">All</th>
        <th data-col="11" data-type="num" style="text-align:right">Long</th>
        <th data-col="12" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="13" data-type="num" style="text-align:right">Live</th>
        <th data-col="14" data-type="num" style="text-align:right">Long</th>
        <th data-col="15" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="16" data-type="num" style="text-align:right">Likes</th>
        <th data-col="17" data-type="num" style="text-align:right">Comments</th>
        <th data-col="18" data-type="num" style="text-align:right">Rate</th>
    </tr>
    </thead>
    <tbody>{rows_html}</tbody>
    </table>
    </div>
    <script>
    (function() {{
        const table = document.querySelector('.st-table');
        const tbody = table.querySelector('tbody');
        const headers = table.querySelectorAll('th[data-col]');
        let currentCol = 2;
        let currentAsc = false;

        function sortTable(colIdx, type) {{
            const rows = Array.from(tbody.rows);
            const isStr = type === 'str';
            if (colIdx === currentCol) {{
                currentAsc = !currentAsc;
            }} else {{
                currentCol = colIdx;
                currentAsc = isStr;
            }}
            rows.sort((a, b) => {{
                const va = a.cells[colIdx].dataset.val || '';
                const vb = b.cells[colIdx].dataset.val || '';
                let cmp;
                if (isStr) {{
                    cmp = va.localeCompare(vb, undefined, {{sensitivity:'base'}});
                }} else {{
                    cmp = (parseFloat(va) || 0) - (parseFloat(vb) || 0);
                }}
                return currentAsc ? cmp : -cmp;
            }});
            rows.forEach(r => tbody.appendChild(r));

            headers.forEach(h => {{
                h.classList.remove('active');
                const base = h.textContent.replace(/ [▲▼]/g, '');
                h.textContent = base;
            }});
            const activeHdr = table.querySelector('th[data-col="' + colIdx + '"]');
            activeHdr.classList.add('active');
            activeHdr.textContent += currentAsc ? ' ▲' : ' ▼';
        }}

        headers.forEach(h => {{
            h.addEventListener('click', function() {{
                sortTable(parseInt(this.dataset.col), this.dataset.type || 'num');
            }});
        }});
    }})();
    </script>
    """, height=_table_height, scrolling=False)

    # ── Charts ─────────────────────────────────────────────────
    import plotly.graph_objects as go

    sorted_df = df.sort_values("all_views", ascending=False)
    chart_names = sorted_df["name"].tolist()

    # Stacked bar: Season Views (Long vs Shorts)
    fig_views = go.Figure()
    fig_views.add_trace(go.Bar(name="Long", x=chart_names, y=sorted_df["long_views"], marker_color="#636EFA"))
    fig_views.add_trace(go.Bar(name="Shorts", x=chart_names, y=sorted_df["short_views"], marker_color="#00CC96"))
    if sorted_df["live_views"].sum() > 0:
        fig_views.add_trace(go.Bar(name="Live", x=chart_names, y=sorted_df["live_views"], marker_color="#FFA15A"))
    fig_views.update_layout(
        title="Season Views (Long vs Shorts)", barmode="stack",
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=70),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
    )
    st.plotly_chart(fig_views, use_container_width=True)

    # Stacked bar: Season Videos (Long vs Shorts)
    sorted_df_v = df.sort_values("all_videos", ascending=False)
    chart_names_v = sorted_df_v["name"].tolist()
    fig_vids = go.Figure()
    fig_vids.add_trace(go.Bar(name="Long", x=chart_names_v, y=sorted_df_v["long_videos"], marker_color="#636EFA"))
    fig_vids.add_trace(go.Bar(name="Shorts", x=chart_names_v, y=sorted_df_v["short_videos"], marker_color="#00CC96"))
    if sorted_df_v["live_videos"].sum() > 0:
        fig_vids.add_trace(go.Bar(name="Live", x=chart_names_v, y=sorted_df_v["live_videos"], marker_color="#FFA15A"))
    fig_vids.update_layout(
        title="Season Videos (Long vs Shorts)", barmode="stack",
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=70),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
    )
    st.plotly_chart(fig_vids, use_container_width=True)

    # Grouped bar: Avg Views/Video (Long vs Shorts vs Live)
    sorted_df_vpv = df.sort_values("all_vpv", ascending=False)
    chart_names_vpv = sorted_df_vpv["name"].tolist()
    fig_vpv_bar = go.Figure()
    fig_vpv_bar.add_trace(go.Bar(name="Long", x=chart_names_vpv, y=sorted_df_vpv["long_vpv"], marker_color="#636EFA"))
    fig_vpv_bar.add_trace(go.Bar(name="Shorts", x=chart_names_vpv, y=sorted_df_vpv["short_vpv"], marker_color="#00CC96"))
    if sorted_df_vpv.get("live_vpv") is not None and sorted_df_vpv["live_vpv"].sum() > 0:
        fig_vpv_bar.add_trace(go.Bar(name="Live", x=chart_names_vpv, y=sorted_df_vpv["live_vpv"], marker_color="#FFA15A"))
    fig_vpv_bar.update_layout(
        title="Avg Views/Video (Long vs Shorts vs Live)", barmode="group",
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=70),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
    )
    st.plotly_chart(fig_vpv_bar, use_container_width=True)

    # Bar: Engagement Rate by club
    sorted_df_er = df.sort_values("eng_rate", ascending=False)
    if not sorted_df_er.empty:
        fig_er = go.Figure()
        fig_er.add_trace(go.Bar(
            x=sorted_df_er["name"], y=sorted_df_er["eng_rate"],
            marker_color="#EF553B",
            text=[f"{x:.2f}%" for x in sorted_df_er["eng_rate"]],
            textposition="outside",
        ))
        fig_er.update_layout(
            title="Engagement Rate (Likes + Comments / Views)",
            xaxis_title="", yaxis_title="%", margin=dict(t=40, b=20),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_er, use_container_width=True)

    # Bar: Avg Duration long-form only
    sorted_df_dur = df[df["long_dur"] > 0].sort_values("long_dur", ascending=False)
    if not sorted_df_dur.empty:
        chart_names_dur = sorted_df_dur["name"].tolist()
        fig_dur = go.Figure()
        fig_dur.add_trace(go.Bar(
            name="Long", x=chart_names_dur, y=sorted_df_dur["long_dur"],
            marker_color="#636EFA",
            text=[f"{int(s)//60}:{int(s)%60:02d}" for s in sorted_df_dur["long_dur"]],
            textposition="outside",
        ))
        fig_dur.update_layout(
            title="Avg Duration — Long-form", barmode="group",
            xaxis_title="", yaxis_title="Seconds", margin=dict(t=40, b=20),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_dur, use_container_width=True)

    # Bar: Avg Duration shorts only
    sorted_df_sdur = df[df["short_dur"] > 0].sort_values("short_dur", ascending=False)
    if not sorted_df_sdur.empty:
        chart_names_sdur = sorted_df_sdur["name"].tolist()
        fig_sdur = go.Figure()
        fig_sdur.add_trace(go.Bar(
            name="Shorts", x=chart_names_sdur, y=sorted_df_sdur["short_dur"],
            marker_color="#00CC96",
            text=[f"{int(s)}s" for s in sorted_df_sdur["short_dur"]],
            textposition="outside",
        ))
        fig_sdur.update_layout(
            title="Avg Duration — Shorts", barmode="group",
            xaxis_title="", yaxis_title="Seconds", margin=dict(t=40, b=20),
            showlegend=False,
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_sdur, use_container_width=True)

    # ── When in All-Leagues sub-modes, also surface per-league
    # comparison charts (same set as zoom 1's Overall view).
    if league is None:
        _ls = _compute_league_stats(clubs_only)
        _sl = sorted(_ls.items(), key=lambda kv: kv[1]["views"], reverse=True)
        if _sl:
            _render_per_league_charts(_sl)

    # ── League-wide publish cadence — stacked area by format ───────────
    # Single league view: aggregate the league's monthly output and split
    # it by Long / Shorts / Live as a stacked area, so the total + format
    # mix is readable at a glance.
    if league is not None:
        try:
            import altair as alt
            import calendar
            from src.database import _fetch_all as _fa
            scoped_ids = [c["id"] for c in clubs_only]

            @st.cache_data(ttl=1800, show_spinner=False)
            def _load_league_format_cadence(since_iso: str, league_name: str,
                                            ids_key: tuple, _cache_v: int = 2):
                from src import dashboard_cache as _dc
                # Read from dashboard_cache first
                try:
                    row = _cached_dc_read(db, "publish_cadence", _dc.scope_league(league_name))
                    cached_rows = (row or {}).get("payload", {}).get("rows")
                    if cached_rows:
                        df = pd.DataFrame(cached_rows)
                        df["month"] = pd.to_datetime(df["month"] + "-01").dt.date
                        return df[["month", "format", "videos"]]
                except Exception:
                    pass

                # ── Live fallback ──
                rows = _fa(
                    db.client.table("videos")
                    .select("channel_id,published_at,format,duration_seconds")
                    .gte("published_at", since_iso)
                )
                ids_set = set(ids_key)
                rows = [r for r in rows if r.get("channel_id") in ids_set]
                if not rows:
                    return pd.DataFrame()
                recs = []
                for r in rows:
                    pa = r.get("published_at") or ""
                    if not pa:
                        continue
                    try:
                        d = datetime.fromisoformat(pa.replace("Z", "+00:00")).date()
                    except Exception:
                        continue
                    fmt = (r.get("format") or "").lower()
                    if fmt not in ("long", "short", "live"):
                        fmt = "long" if (r.get("duration_seconds") or 0) >= 60 else "short"
                    label = {"long": "Long", "short": "Shorts", "live": "Live"}[fmt]
                    recs.append({"month": d.replace(day=1), "format": label})
                if not recs:
                    return pd.DataFrame()
                df = pd.DataFrame(recs)
                return (df.groupby(["month", "format"])
                          .size().reset_index(name="videos"))

            fmt_df = _load_league_format_cadence(SEASON_SINCE, league,
                                                 tuple(sorted(scoped_ids)))
            if not fmt_df.empty:
                # Project current incomplete month
                today_d = datetime.now().date()
                cur_start = today_d.replace(day=1)
                dim = calendar.monthrange(today_d.year, today_d.month)[1]
                de = today_d.day
                projection_note = ""
                plot_df = fmt_df.copy()
                if de < dim:
                    mask = plot_df["month"] == cur_start
                    if mask.any():
                        f = dim / de
                        plot_df.loc[mask, "videos"] = (
                            plot_df.loc[mask, "videos"].astype(float) * f
                        ).round().astype(int)
                        projection_note = (f" {today_d.strftime('%b %Y')} "
                                           f"projected from {de} of {dim} days.")

                st.subheader(f"📅 Publish cadence — {league}")
                st.caption(f"Videos per month, stacked by format, "
                           f"since {SEASON_SINCE}.{projection_note}")
                fmt_order = ["Long", "Shorts", "Live"]
                fmt_palette = ["#636EFA", "#00CC96", "#FFA15A"]

                def _area_layer(df_in, opacity, tooltip_views_label):
                    return (
                        alt.Chart(df_in).mark_area(opacity=opacity).encode(
                            x=alt.X("yearmonth(month):T", title=None,
                                    axis=alt.Axis(format="%b %Y", labelAngle=-30)),
                            y=alt.Y("videos:Q", title=None, stack="zero",
                                    axis=alt.Axis(labelExpr="replace(format(datum.value, \"~s\"), \"G\", \"B\")")),
                            color=alt.Color("format:N",
                                            scale=alt.Scale(domain=fmt_order,
                                                            range=fmt_palette)),
                            order=alt.Order("format:N"),
                            tooltip=[
                                alt.Tooltip("yearmonth(month):T", title="Month"),
                                alt.Tooltip("format:N", title="Format"),
                                alt.Tooltip("videos:Q", format=",",
                                            title=tooltip_views_label),
                            ],
                        )
                    )

                # If the current month is projected, fade its segment by
                # rendering two area layers: solid up to the previous month,
                # semi-transparent overlay for the prev→current segment.
                # The shared seam at prev_month keeps the stack continuous.
                is_projected_z2 = bool(projection_note) and (
                    plot_df["month"].max() == cur_start
                )
                months_sorted_z2 = sorted(plot_df["month"].unique()) if is_projected_z2 else []
                if is_projected_z2 and len(months_sorted_z2) >= 2:
                    last_m = months_sorted_z2[-1]
                    prev_m = months_sorted_z2[-2]
                    solid_df = plot_df[plot_df["month"] <= prev_m]
                    proj_df = plot_df[plot_df["month"] >= prev_m]
                    area_chart = (
                        _area_layer(solid_df, 0.85, "Videos")
                        + _area_layer(proj_df, 0.35, "Videos (projected)")
                    ).properties(height=300)
                else:
                    area_chart = _area_layer(plot_df, 0.85, "Videos") \
                                 .properties(height=300)
                st.altair_chart(area_chart, use_container_width=True)
        except Exception as _e:
            st.caption(f"(cadence chart unavailable: {_e})")

    # ── Videos per day for the picked league ──────────────────────────
    # Reads precomputed payload from dashboard_cache (rebuilt by
    # daily_refresh / hourly_rss). Falls back to inline aggregation if
    # the cache row hasn't been populated yet.
    if league:
        try:
            from src import dashboard_cache as _dc_vpd_lg
            _vpd_payload_lg = _cached_dc_read(db, "videos_per_day",
                                              _dc_vpd_lg.scope_league(league))
            _vpd_payload_lg = (_vpd_payload_lg or {}).get("payload") or {}
            _dates_lg = [pd.to_datetime(d).date()
                         for d in (_vpd_payload_lg.get("days") or [])]
            _vals_lg = list(_vpd_payload_lg.get("counts") or [])
            if not _vals_lg:
                from src.database import _fetch_all as _fa_vpd_lg
                from collections import Counter as _Counter_lg
                _vpd_clubs_lg = [c for c in clubs_only if c.get("id")]
                _vpd_ids_lg = [c["id"] for c in _vpd_clubs_lg]
                _vpd_rows_lg = _fa_vpd_lg(
                    db.client.table("videos")
                      .select("published_at")
                      .gte("published_at", SEASON_SINCE)
                      .in_("channel_id", _vpd_ids_lg)
                ) if _vpd_ids_lg else []
                _vpd_counts_lg = _Counter_lg()
                for _r in _vpd_rows_lg:
                    _pa = _r.get("published_at") or ""
                    if not _pa:
                        continue
                    try:
                        _d = pd.to_datetime(_pa, utc=True).date()
                    except Exception:
                        continue
                    _vpd_counts_lg[_d] += 1
                _dates_lg = sorted(_vpd_counts_lg.keys())
                _vals_lg = [_vpd_counts_lg[d] for d in _dates_lg]
            if _vals_lg:
                import plotly.graph_objects as _go_vpd_lg
                _avg_lg = sum(_vals_lg) / len(_vals_lg) if _vals_lg else 0
                _bar_colors_vpd_lg = [
                    "#FFA15A" if d.weekday() >= 5 else "#636EFA"
                    for d in _dates_lg
                ]
                st.subheader(f"📈 Videos per day — {league}")
                st.caption(
                    f"{sum(_vals_lg):,} videos across {len(_dates_lg)} "
                    f"days (avg {_avg_lg:.1f}/day). Weekends in orange."
                )
                fig_vpd_lg = _go_vpd_lg.Figure()
                fig_vpd_lg.add_trace(_go_vpd_lg.Bar(
                    x=_dates_lg, y=_vals_lg,
                    marker_color=_bar_colors_vpd_lg,
                    hovertemplate="<b>%{x|%a %b %d, %Y}</b><br>"
                                  "%{y} video(s)<extra></extra>",
                ))
                fig_vpd_lg.add_hline(y=_avg_lg, line_dash="dash",
                                     line_color="rgba(255,255,255,0.35)",
                                     annotation_text=f"avg {_avg_lg:.1f}",
                                     annotation_position="top right",
                                     annotation_font_color="rgba(255,255,255,0.55)")
                fig_vpd_lg.update_layout(
                    height=320,
                    xaxis=dict(title="", showgrid=False),
                    yaxis=dict(title="Videos published",
                               showgrid=True,
                               gridcolor="rgba(255,255,255,0.08)"),
                    margin=dict(t=30, b=40, l=60, r=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FAFAFA"),
                    showlegend=False, bargap=0.15,
                )
                st.plotly_chart(fig_vpd_lg, use_container_width=True)
        except Exception as _e:
            st.caption(f"(videos-per-day chart unavailable: {_e})")

    # ── Publishing heatmap (one league) ───────────────────────────────
    if league:
        try:
            from src import dashboard_cache as _dc_hm_lg
            _hm_lg = _dc_hm_lg.read(db, "publishing_heatmap", _dc_hm_lg.scope_league(league))
            _hm_payload = (_hm_lg or {}).get("payload") or {}
            _hm_counts = _hm_payload.get("counts")
            _hm_sums = _hm_payload.get("sum_views")
            if _hm_counts and _hm_sums:
                _hm_avg = [[(_hm_sums[r][c] // _hm_counts[r][c])
                            if _hm_counts[r][c] else 0
                            for c in range(24)] for r in range(7)]
                _render_publishing_heatmap_grid(
                    _hm_counts, _hm_avg,
                    f"📅 Publishing heatmap — {league}",
                    f" ({league} channels combined)")
        except Exception as _e:
            st.caption(f"(publishing heatmap unavailable: {_e})")

    # ── Views concentration per club (one league only) ────────────────
    if league:
        try:
            from src import dashboard_cache as _dc_conc
            _conc = _dc_conc.read(db, "concentration", _dc_conc.scope_league(league))
            _conc_rows = (_conc or {}).get("payload", {}).get("rows", []) if _conc else []
            # Include both Club and League channels (e.g. @seriea); skip
            # only channels with too few videos to be meaningful.
            _conc_rows = [r for r in _conc_rows
                          if r.get("entity_type") in ("Club", "League")
                          and r.get("n_videos", 0) >= 5]
            if _conc_rows:
                import plotly.graph_objects as _go_c
                st.subheader(f"📊 Views concentration — {league}")
                st.caption("How concentrated are season views in each club's catalog? "
                           "Bar = % of videos that account for 80% of total views. "
                           "Lower = a few hits drive the channel (long tail). "
                           "Higher = views spread evenly across uploads. "
                           "Hover for top-1 / top-10 share and median vs avg.")
                # Sort: most concentrated (smallest pct_to_80) at the top
                _conc_rows = sorted(_conc_rows, key=lambda r: r["pct_to_80"])
                _names = [r["name"] for r in _conc_rows]
                _pcts = [r["pct_to_80"] for r in _conc_rows]
                _customdata = [
                    [r["n_to_80"], r["n_videos"], r["top1_pct"], r["top10_pct"],
                     fmt_num(r["median_views"]), fmt_num(r["avg_views"]),
                     fmt_num(r["total_views"])]
                    for r in _conc_rows
                ]
                # Bar color from per-channel primary
                _color_map_local = get_global_color_map() or {}
                _bar_colors_c = [_color_map_local.get(n, "#888") for n in _names]
                fig_c = _go_c.Figure()
                fig_c.add_trace(_go_c.Bar(
                    x=_pcts, y=_names, orientation="h",
                    marker_color=_bar_colors_c,
                    customdata=_customdata,
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Top %{customdata[0]} of %{customdata[1]} videos = 80% of views<br>"
                        "Top 1 video = %{customdata[2]:.0f}% · "
                        "Top 10 = %{customdata[3]:.0f}%<br>"
                        "Median: %{customdata[4]} · Avg: %{customdata[5]}<br>"
                        "Total season views: %{customdata[6]}<extra></extra>"
                    ),
                ))
                # 20% reference line: classic Pareto threshold
                fig_c.add_vline(x=20, line_dash="dash", line_color="#888",
                                annotation_text="20%", annotation_position="top",
                                annotation_font_color="#888")
                fig_c.update_layout(
                    height=max(300, 28 * len(_names) + 80),
                    xaxis=dict(title="% of videos to reach 80% of season views",
                               range=[0, max(60, max(_pcts) * 1.15)],
                               ticksuffix="%",
                               showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
                    yaxis=dict(title="", autorange="reversed"),
                    margin=dict(t=30, b=50, l=160),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FAFAFA"),
                    showlegend=False,
                )
                st.plotly_chart(fig_c, use_container_width=True)
        except Exception as _e:
            st.caption(f"(concentration chart unavailable: {_e})")

    # ── Posting cadence: zero-video days per club (one league only) ────
    # Reads precomputed `zero_day_counts` from dashboard_cache. Falls
    # back to inline aggregation on cache miss.
    if league:
        try:
            from src import dashboard_cache as _dc_zd
            from datetime import date as _date
            _zd_payload = _cached_dc_read(db, "zero_day_counts",
                                          _dc_zd.scope_league(league))
            _zd_payload = (_zd_payload or {}).get("payload") or {}
            _zd_rows = list(_zd_payload.get("rows") or [])
            _total_days = int(_zd_payload.get("total_days") or 0)
            _start = pd.to_datetime(_zd_payload.get("since") or SEASON_SINCE).date()
            if not _zd_rows:
                from src.database import _fetch_all as _fa
                _scope_ids_zd = [c["id"] for c in clubs_only if c.get("id")]
                _vid_rows = _fa(
                    db.client.table("videos")
                      .select("channel_id,published_at")
                      .gte("published_at", SEASON_SINCE)
                      .in_("channel_id", _scope_ids_zd)
                ) if _scope_ids_zd else []
                _today = _date.today()
                _start = pd.to_datetime(SEASON_SINCE).date()
                _total_days = (_today - _start).days + 1
                _days_by_ch: dict[str, set] = {}
                for _r in _vid_rows:
                    _cid = _r.get("channel_id")
                    _pa = _r.get("published_at") or ""
                    if not _cid or not _pa:
                        continue
                    try:
                        _d = pd.to_datetime(_pa, utc=True).date()
                    except Exception:
                        continue
                    _days_by_ch.setdefault(_cid, set()).add(_d)
                _ch_by_id_zd = {c["id"]: c for c in clubs_only}
                for _cid in _scope_ids_zd:
                    _ch = _ch_by_id_zd.get(_cid) or {}
                    _published_days = len(_days_by_ch.get(_cid, set()))
                    _zd_rows.append({
                        "name": _ch.get("name") or _cid,
                        "zero_days": max(0, _total_days - _published_days),
                        "published_days": _published_days,
                    })
            # Re-attach total_days for tooltip + sort desc.
            for _r in _zd_rows:
                _r.setdefault("total_days", _total_days)
            _zd_rows.sort(key=lambda r: r.get("zero_days", 0), reverse=True)
            if _zd_rows:
                st.subheader(f"📅 Days with zero videos — {league}")
                st.caption(
                    f"Out of {_total_days} season days "
                    f"(since {_start.strftime('%b %d, %Y')}), how many "
                    f"each club went a full day without publishing. "
                    f"Lower = more consistent rhythm."
                )
                _color_map_zd = get_global_color_map() or {}
                _zd_names = [r["name"] for r in _zd_rows]
                _zd_vals = [r["zero_days"] for r in _zd_rows]
                _zd_colors = [_color_map_zd.get(n, "#888") for n in _zd_names]
                _zd_custom = [
                    [r["published_days"], r["total_days"],
                     (r["published_days"] / r["total_days"] * 100) if r["total_days"] else 0]
                    for r in _zd_rows
                ]

                fig_zd = go.Figure()
                fig_zd.add_trace(go.Bar(
                    x=_zd_names, y=_zd_vals,
                    marker_color=_zd_colors,
                    customdata=_zd_custom,
                    hovertemplate=(
                        "<b>%{x}</b><br>"
                        "Zero-video days: %{y}<br>"
                        "Days with at least 1 video: %{customdata[0]} of %{customdata[1]} "
                        "(%{customdata[2]:.0f}%)<extra></extra>"
                    ),
                ))
                fig_zd.update_layout(
                    height=420,
                    xaxis=dict(title="", tickangle=-35,
                               showgrid=False),
                    yaxis=dict(title="Days without any new video this season",
                               showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
                    margin=dict(t=30, b=120, l=60),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FAFAFA"),
                    showlegend=False,
                )
                st.plotly_chart(fig_zd, use_container_width=True)
        except Exception as _e:
            st.caption(f"(zero-day chart unavailable: {_e})")

    # Top Season Videos lists moved to the Season Top Videos page.

else:
    # ══════════════════════════════════════════════════════════════
    # ZOOM LEVEL 3: ONE CLUB
    # ══════════════════════════════════════════════════════════════
    import plotly.graph_objects as go

    from src.filters import render_club_header
    render_club_header(club, all_channels)

    # Read precomputed season stats from channel row — no video query needed for KPIs
    long_views = int(club.get("season_long_views") or 0)
    short_views = int(club.get("season_short_views") or 0)
    live_views = int(club.get("season_live_views") or 0)
    long_videos = int(club.get("season_long_videos") or 0)
    short_videos = int(club.get("season_short_videos") or 0)
    live_videos = int(club.get("season_live_videos") or 0)
    total_views = long_views + short_views + live_views
    total_videos = long_videos + short_videos + live_videos
    if total_videos == 0:
        st.info(f"No season videos for {club['name']} yet.")
        st.stop()
    avg_vpv = total_views // max(total_videos, 1)
    long_vpv = long_views // max(long_videos, 1)
    short_vpv = short_views // max(short_videos, 1)
    live_vpv = live_views // max(live_videos, 1)
    long_dur = int(club.get("season_long_dur_avg") or 0)
    short_dur = int(club.get("season_short_dur_avg") or 0)
    live_dur = 0  # not precomputed

    total_likes = int(club.get("season_likes") or 0)
    total_comments = int(club.get("season_comments") or 0)
    long_likes = int(club.get("season_long_likes") or 0)
    long_comments = int(club.get("season_long_comments") or 0)
    short_likes = int(club.get("season_short_likes") or 0)
    short_comments = int(club.get("season_short_comments") or 0)
    live_likes = int(club.get("season_live_likes") or 0)
    live_comments = int(club.get("season_live_comments") or 0)

    def _fmt(v):
        v = int(v)
        return fmt_num(v) if v else "-"

    def _dur(secs):
        s = int(secs or 0)
        if not s:
            return "-"
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    def _fmt_of(v):
        f = v.get("format")
        if f in ("long", "short", "live"):
            return f
        return "long" if v.get("duration_seconds", 0) >= 60 else "short"

    def _rate(num, den):
        return (num / den * 100) if den else 0.0
    def _pct(x):
        return f"{x:.2f}%" if x else "-"

    # ── Club colors (fallback to defaults; never black on dark bg) ───
    def _safe(c, fallback):
        if not c:
            return fallback
        u = c.upper().strip()
        # Treat pure/near black as invisible on dark bg → fallback
        if u in ("#000000", "#000", "#111111", "#0A0A0A"):
            return fallback
        return c
    _c1 = _safe(club.get("color"), "#636EFA")
    _c2 = _safe(club.get("color2"), "#FFFFFF")
    if _c2.upper() == _c1.upper():
        _c2 = "#FFFFFF" if _c1.upper() != "#FFFFFF" else "#AAAAAA"
    LONG_COLOR = _c1
    SHORT_COLOR = _c2
    LIVE_COLOR = "#FFA15A"

    # ── Banner: three donut charts ──────────────────────────────
    has_live = live_videos > 0
    if has_live:
        pie_colors = [LONG_COLOR, SHORT_COLOR, LIVE_COLOR]
        pie_labels = ["Long", "Shorts", "Live"]
    else:
        pie_colors = [LONG_COLOR, SHORT_COLOR]
        pie_labels = ["Long", "Shorts"]

    def _vals(l, s, lv):
        return [l, s, lv] if has_live else [l, s]

    _total_v_banner = long_views + short_views + live_views
    _total_n_banner = long_videos + short_videos + live_videos
    _avg_vpv_banner = _total_v_banner // max(_total_n_banner, 1)
    # _rate already returns a percentage (num / den * 100). Don't double-multiply.
    _eng_rate = _rate(total_likes + total_comments, total_views)

    # ── Per-metric league/overall rank (season-scoped) ─────────
    # Mirrors the Channels Z3 banner — answers "how does this club
    # stack up?" inline under each KPI. Computed against the same Top-5
    # clubs cohort that drives every other rank on the site.
    _clubs_cohort = [c for c in all_channels
                     if c.get("entity_type") not in ("League", "Player",
                                                     "Federation", "OtherClub",
                                                     "WomenClub")]
    _ch_lg_z3 = get_league_for_channel(club)
    _peers_z3 = [c for c in _clubs_cohort if get_league_for_channel(c) == _ch_lg_z3]

    def _season_views(c):
        return ((c.get("season_long_views") or 0)
                + (c.get("season_short_views") or 0)
                + (c.get("season_live_views") or 0))
    def _season_videos(c):
        return ((c.get("season_long_videos") or 0)
                + (c.get("season_short_videos") or 0)
                + (c.get("season_live_videos") or 0))
    def _season_avg_vpv(c):
        v = _season_videos(c)
        return _season_views(c) // v if v else 0
    def _season_eng_rate(c):
        v = _season_views(c)
        if not v:
            return 0.0
        return ((c.get("season_likes") or 0) + (c.get("season_comments") or 0)) / v * 100

    _season_metric_getters = {
        "views":    _season_views,
        "videos":   _season_videos,
        "vpv":      _season_avg_vpv,
        "likes":    lambda c: c.get("season_likes") or 0,
        "comments": lambda c: c.get("season_comments") or 0,
        "eng":      _season_eng_rate,
    }

    def _rank_z3(metric, scope):
        pool = _peers_z3 if scope == "league" else _clubs_cohort
        sorted_pool = sorted(pool, key=_season_metric_getters[metric], reverse=True)
        try:
            return next(i + 1 for i, c in enumerate(sorted_pool) if c["id"] == club["id"]), len(sorted_pool)
        except StopIteration:
            return None, len(sorted_pool)

    def _rank_html_z3(metric):
        lr, lt = _rank_z3(metric, "league")
        orr, ot = _rank_z3(metric, "overall")
        parts = []
        if lr and _ch_lg_z3:
            parts.append(f"#{lr}/{lt} in {_ch_lg_z3}")
        if orr:
            parts.append(f"#{orr}/{ot} overall")
        return (f"<div style='color:#888;font-size:0.8rem;margin-top:-6px'>{' · '.join(parts)}</div>"
                if parts else "")

    # KPI banner row
    # _rank_html_z3() returns wrapped HTML; the kpi_row helper supplies
    # its own subtitle styling, so extract the inner text only.
    def _rank_subtitle_z3(metric: str) -> str:
        import re as _re
        html = _rank_html_z3(metric) or ""
        m = _re.search(r">([^<]+)<", html)
        return m.group(1).strip() if m else ""

    st.markdown(kpi_row([
        ("Total Views",     fmt_num(_total_v_banner),     _rank_subtitle_z3("views")),
        ("Total Videos",    fmt_num(_total_n_banner),     _rank_subtitle_z3("videos")),
        ("Avg Views/Video", fmt_num(_avg_vpv_banner),     _rank_subtitle_z3("vpv")),
        ("Total Likes",     fmt_num(total_likes),         _rank_subtitle_z3("likes")),
        ("Total Comments",  fmt_num(total_comments),      _rank_subtitle_z3("comments")),
        ("Engagement Rate", f"{_eng_rate:.2f}%",           _rank_subtitle_z3("eng")),
    ]), unsafe_allow_html=True)

    # Pie charts row
    def _make_pie_club(values, labels, colors, hover_suffix, title):
        chart_title(title)
        fig = go.Figure(go.Pie(
            labels=labels, values=values, marker=dict(colors=colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
        ))
        fig.update_layout(
            showlegend=False, height=260,
            margin=dict(t=10, b=10, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        return fig

    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
    with col_p1:
        st.plotly_chart(_make_pie_club(_vals(long_views, short_views, live_views), pie_labels, pie_colors, "views", "Views"), use_container_width=True)
    with col_p2:
        st.plotly_chart(_make_pie_club(_vals(long_videos, short_videos, live_videos), pie_labels, pie_colors, "videos", "Videos"), use_container_width=True)
    with col_p3:
        st.plotly_chart(_make_pie_club(_vals(long_vpv, short_vpv, live_vpv), pie_labels, pie_colors, "views/video", "Views/Video"), use_container_width=True)
    with col_p4:
        st.plotly_chart(_make_pie_club(_vals(long_likes, short_likes, live_likes), pie_labels, pie_colors, "likes", "Likes"), use_container_width=True)
    with col_p5:
        st.plotly_chart(_make_pie_club(_vals(long_comments, short_comments, live_comments), pie_labels, pie_colors, "comments", "Comments"), use_container_width=True)

    # ── Breakdown table ────────────────────────────────────────
    st.subheader("Breakdown")
    st.markdown(f"""
<table style="width:100%;border-collapse:collapse;font-size:14px;color:#FAFAFA">
<thead><tr style="border-bottom:2px solid #444">
<th style="text-align:left;padding:6px 12px">Format</th>
<th style="text-align:right;padding:6px 12px">Views</th>
<th style="text-align:right;padding:6px 12px">Videos</th>
<th style="text-align:right;padding:6px 12px">Views/Video</th>
<th style="text-align:right;padding:6px 12px">Avg Duration</th>
<th style="text-align:right;padding:6px 12px">Likes</th>
<th style="text-align:right;padding:6px 12px">Like Rate</th>
<th style="text-align:right;padding:6px 12px">Comments</th>
<th style="text-align:right;padding:6px 12px">Comment Rate</th>
</tr></thead>
<tbody>
<tr style="border-bottom:1px solid #262730"><td style="padding:6px 12px;color:{LONG_COLOR}">Long</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_vpv)}</td>
<td style="text-align:right;padding:6px 12px">{_dur(long_dur)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(long_likes, long_views))}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(long_comments, long_views))}</td></tr>
<tr style="border-bottom:1px solid #262730"><td style="padding:6px 12px;color:{SHORT_COLOR}">Shorts</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_vpv)}</td>
<td style="text-align:right;padding:6px 12px">{_dur(short_dur)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(short_likes, short_views))}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(short_comments, short_views))}</td></tr>
{"" if not has_live else f'''<tr style="border-bottom:1px solid #262730"><td style="padding:6px 12px;color:{LIVE_COLOR}">Live</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_vpv)}</td>
<td style="text-align:right;padding:6px 12px">{_dur(live_dur)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(live_likes, live_views))}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(live_comments, live_views))}</td></tr>'''}
<tr style="font-weight:600"><td style="padding:6px 12px">Total</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(avg_vpv)}</td>
<td style="text-align:right;padding:6px 12px">-</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(total_likes, total_views))}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(total_comments, total_views))}</td></tr>
</tbody></table>
""", unsafe_allow_html=True)

    # ── Fetch videos only for monthly/category/top-videos sections ──
    with st.spinner(f"Loading {club['name']} season videos…"):
        vids = db.get_season_videos_by_channel(club["id"], since=SEASON_SINCE)

    # ── Videos per month (stacked) ─────────────────────────────
    if not vids:
        st.caption("No video details available for charts.")
        st.stop()
    df_vids = pd.DataFrame(vids)
    df_vids["published_at"] = pd.to_datetime(df_vids["published_at"], utc=True)
    df_vids["month"] = df_vids["published_at"].dt.strftime("%Y-%m")
    df_vids["fmt"] = df_vids.apply(_fmt_of, axis=1)

    monthly = df_vids.groupby(["month", "fmt"]).agg(
        videos=("id", "count"),
        views=("view_count", "sum"),
    ).reset_index()
    months = sorted(df_vids["month"].unique())

    def _series(fmt, metric):
        d = {m: 0 for m in months}
        for _, r in monthly[monthly["fmt"] == fmt].iterrows():
            d[r["month"]] = r[metric]
        return [d[m] for m in months]

    # 24h published-timeline strip moved to Latest Videos only — Season is
    # the long-horizon view, not the right place for "right now" cards.

    # ── Videos per day (this club, full season) ──────────────
    # Reuses df_vids — no extra DB query needed at Z3.
    try:
        _vpd_dates_c = df_vids["published_at"].dt.tz_convert(None).dt.date
        _vpd_counts_c = _vpd_dates_c.value_counts().sort_index()
        if len(_vpd_counts_c):
            _dates_c = list(_vpd_counts_c.index)
            _vals_c = list(_vpd_counts_c.values)
            _avg_c = float(_vpd_counts_c.mean())
            _bar_colors_vpd_c = [
                "#FFA15A" if d.weekday() >= 5 else "#636EFA" for d in _dates_c
            ]
            st.subheader(f"📈 Videos per day — {club['name']}")
            st.caption(
                f"{int(sum(_vals_c)):,} videos across {len(_dates_c)} "
                f"days with at least one upload (avg {_avg_c:.1f}/day "
                f"on active days). Weekends in orange."
            )
            fig_vpd_c = go.Figure()
            fig_vpd_c.add_trace(go.Bar(
                x=_dates_c, y=_vals_c,
                marker_color=_bar_colors_vpd_c,
                hovertemplate="<b>%{x|%a %b %d, %Y}</b><br>"
                              "%{y} video(s)<extra></extra>",
            ))
            fig_vpd_c.add_hline(y=_avg_c, line_dash="dash",
                                line_color="rgba(255,255,255,0.35)",
                                annotation_text=f"avg {_avg_c:.1f}",
                                annotation_position="top right",
                                annotation_font_color="rgba(255,255,255,0.55)")
            fig_vpd_c.update_layout(
                height=320,
                xaxis=dict(title="", showgrid=False),
                yaxis=dict(title="Videos published", showgrid=True,
                           gridcolor="rgba(255,255,255,0.08)"),
                margin=dict(t=30, b=40, l=60, r=20),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                showlegend=False, bargap=0.15,
            )
            st.plotly_chart(fig_vpd_c, use_container_width=True)
    except Exception as _e:
        st.caption(f"(videos-per-day chart unavailable: {_e})")

    st.subheader("Monthly output")

    # Avg-views-per-video can't be stacked or summed across formats —
    # each (fmt, month) cell is a ratio. Compute it explicitly here.
    def _series_vpv(fmt):
        v = _series(fmt, "views")
        n = _series(fmt, "videos")
        return [int(v[i] / n[i]) if n[i] else 0 for i in range(len(months))]

    col_m1, col_m2, col_m3 = st.columns(3)
    with col_m1:
        fig_mviews = go.Figure()
        fig_mviews.add_trace(go.Bar(name="Long", x=months, y=_series("long", "views"), marker_color=LONG_COLOR))
        fig_mviews.add_trace(go.Bar(name="Shorts", x=months, y=_series("short", "views"), marker_color=SHORT_COLOR))
        if has_live:
            fig_mviews.add_trace(go.Bar(name="Live", x=months, y=_series("live", "views"), marker_color=LIVE_COLOR))
        fig_mviews.update_layout(title="Views per Month", barmode="stack",
                                 xaxis_title="", yaxis_title="", margin=dict(t=40, b=70),
                                 legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                 font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_mviews, use_container_width=True)
    with col_m2:
        fig_mv = go.Figure()
        fig_mv.add_trace(go.Bar(name="Long", x=months, y=_series("long", "videos"), marker_color=LONG_COLOR))
        fig_mv.add_trace(go.Bar(name="Shorts", x=months, y=_series("short", "videos"), marker_color=SHORT_COLOR))
        if has_live:
            fig_mv.add_trace(go.Bar(name="Live", x=months, y=_series("live", "videos"), marker_color=LIVE_COLOR))
        fig_mv.update_layout(title="Videos per Month", barmode="stack",
                             xaxis_title="", yaxis_title="", margin=dict(t=40, b=70),
                             legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_mv, use_container_width=True)
    with col_m3:
        # Grouped (not stacked) — averages don't aggregate.
        fig_mvpv = go.Figure()
        fig_mvpv.add_trace(go.Bar(name="Long", x=months, y=_series_vpv("long"), marker_color=LONG_COLOR))
        fig_mvpv.add_trace(go.Bar(name="Shorts", x=months, y=_series_vpv("short"), marker_color=SHORT_COLOR))
        if has_live:
            fig_mvpv.add_trace(go.Bar(name="Live", x=months, y=_series_vpv("live"), marker_color=LIVE_COLOR))
        fig_mvpv.update_layout(title="Avg Views/Video per Month", barmode="group",
                               xaxis_title="", yaxis_title="", margin=dict(t=40, b=70),
                               legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
                               paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                               font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_mvpv, use_container_width=True)

    # ── Publishing heatmap (day-of-week × hour, CET) ───────────
    # Reveals the club's editorial rhythm: when do they post highlights,
    # press conferences, shorts? CET-bucketed to match the rest of the
    # app (Daily Recap, KPI counts, etc.). Source data is df_vids which
    # already has published_at parsed and TZ-aware.
    try:
        from zoneinfo import ZoneInfo as _ZI
        _df_h = df_vids.copy()
        _df_h["_pub_cet"] = _df_h["published_at"].dt.tz_convert(_ZI("Europe/Rome"))
        _df_h["_dow"] = _df_h["_pub_cet"].dt.weekday  # 0=Mon … 6=Sun
        _df_h["_hour"] = _df_h["_pub_cet"].dt.hour

        # Counts grid 7×24 — reindex to fill empty cells with 0 so the
        # heatmap renders the full week even on sparse channels.
        _counts = (_df_h.groupby(["_dow", "_hour"]).size()
                   .unstack(fill_value=0)
                   .reindex(index=range(7), columns=range(24), fill_value=0))
        # Average views per cell — used as a secondary hover signal.
        _avg_views = (_df_h.groupby(["_dow", "_hour"])["view_count"].mean()
                      .unstack(fill_value=0)
                      .reindex(index=range(7), columns=range(24), fill_value=0))

        # Peak callout
        _max_n = int(_counts.values.max()) if _counts.values.size else 0
        if _max_n > 0:
            _peak_dow, _peak_h = _counts.stack().idxmax()
            _dow_short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][int(_peak_dow)]
            _peak_caption = (f"Peak slot: **{_dow_short} {int(_peak_h):02d}:00 CET** "
                             f"with {_max_n} posts.")
        else:
            _peak_caption = ""

        st.subheader("📅 Publishing heatmap — when does this club post?")
        st.caption(f"Day-of-week × hour (CET) for the season. Brighter / "
                   f"redder = more videos. Hover for count and average views "
                   f"per slot. {_peak_caption}")

        _hour_labels = [f"{h:02d}" for h in range(24)]
        _dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        # Custom 2D for hover: show avg views as integer, formatted.
        _avg_disp = [[fmt_num(int(round(_avg_views.iat[r, c])))
                      for c in range(24)] for r in range(7)]
        fig_hm = go.Figure(go.Heatmap(
            z=_counts.values,
            x=_hour_labels,
            y=_dow_labels,
            customdata=_avg_disp,
            colorscale=_HEATMAP_SCALE,
            colorbar=dict(title="Posts", thickness=10, x=1.02, len=0.9),
            hovertemplate=("<b>%{y} %{x}:00</b><br>"
                           "%{z} post(s)<br>"
                           "Avg views/post: %{customdata}<extra></extra>"),
            xgap=1, ygap=1,
        ))
        fig_hm.update_layout(
            height=300,
            xaxis=dict(title="Hour (CET)", tickfont=dict(size=10), side="bottom", tickmode="array", tickvals=[f"{h:02d}" for h in range(0, 24, 3)], ticktext=[f"{h:02d}" for h in range(0, 24, 3)]),
            yaxis=dict(title="", autorange="reversed",  # Mon at top
                       tickfont=dict(size=11)),
            margin=dict(t=10, b=40, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_hm, use_container_width=True)
    except Exception as _e:
        st.caption(f"(publishing heatmap unavailable: {_e})")

    # ── Views distribution (Pareto) ────────────────────────────
    st.subheader("Views distribution")
    st.caption("Are season views balanced across videos, or driven by a few hits? "
               "Bars = views per video (sorted high→low). Line = cumulative share of total views.")

    df_par = df_vids.sort_values("view_count", ascending=False).reset_index(drop=True)
    df_par["rank"] = df_par.index + 1
    _total_views = float(df_par["view_count"].sum()) or 1.0
    df_par["cum_pct"] = df_par["view_count"].cumsum() / _total_views * 100.0
    df_par["fmt_label"] = df_par["fmt"].map({"long": "Long", "short": "Shorts", "live": "Live"})
    _bar_colors = df_par["fmt"].map({"long": LONG_COLOR, "short": SHORT_COLOR, "live": LIVE_COLOR}).tolist()

    # Callout chips: top-N concentration
    n_total = len(df_par)
    def _share(n: int) -> float:
        if n <= 0 or n_total == 0:
            return 0.0
        return float(df_par["view_count"].head(n).sum()) / _total_views * 100.0
    top1_pct = _share(1)
    top10_pct = _share(min(10, n_total))
    top20pct_n = max(1, int(round(n_total * 0.2)))
    top20pct_pct = _share(top20pct_n)
    median_v = float(df_par["view_count"].median()) if n_total else 0.0
    avg_v = float(df_par["view_count"].mean()) if n_total else 0.0
    # Videos to reach 80% of views
    n_to_80 = int((df_par["cum_pct"] < 80.0).sum()) + 1 if n_total else 0
    n_to_80 = min(n_to_80, n_total)
    pct_videos_to_80 = (n_to_80 / n_total * 100.0) if n_total else 0.0

    st.markdown(kpi_row([
        ("Top video",     f"{top1_pct:.0f}% of views"),
        ("Top 10 videos", f"{top10_pct:.0f}% of views"),
        (f"Top 20% ({top20pct_n} videos)", f"{top20pct_pct:.0f}% of views"),
        ("Median vs Avg", f"{_fmt(int(median_v))} / {_fmt(int(avg_v))}"),
    ]), unsafe_allow_html=True)

    fig_par = go.Figure()
    fig_par.add_trace(go.Bar(
        x=df_par["rank"], y=df_par["view_count"],
        marker_color=_bar_colors,
        name="Views",
        customdata=df_par[["title", "fmt_label"]].values,
        hovertemplate="#%{x} · %{customdata[1]}<br>%{customdata[0]}<br>Views: %{y:,}<extra></extra>",
    ))
    fig_par.add_trace(go.Scatter(
        x=df_par["rank"], y=df_par["cum_pct"],
        mode="lines", name="Cumulative %",
        line=dict(color="#FAFAFA", width=2),
        yaxis="y2",
        hovertemplate="Top %{x} videos = %{y:.1f}% of views<extra></extra>",
    ))
    # 80% reference line
    fig_par.add_hline(y=80, line_dash="dash", line_color="#888",
                      yref="y2", annotation_text="80%", annotation_position="top left",
                      annotation_font_color="#888")
    fig_par.update_layout(
        title=(f"Top {n_to_80} videos ({pct_videos_to_80:.0f}% of catalog) account for 80% of season views"
               if n_total else "Views distribution"),
        barmode="overlay",
        xaxis=dict(title="Video rank (sorted by views)", showgrid=False),
        yaxis=dict(title="Views per video", showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
        yaxis2=dict(title="Cumulative % of views", overlaying="y", side="right",
                    range=[0, 105], ticksuffix="%", showgrid=False),
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5),
        margin=dict(t=50, b=70),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_par, use_container_width=True)

    # ── Category breakdown ────────────────────────────────────
    from collections import Counter
    cat_count = Counter(v.get("category") or "Other" for v in vids)
    cat_views: dict[str, int] = {}
    for v in vids:
        c = v.get("category") or "Other"
        cat_views[c] = cat_views.get(c, 0) + int(v.get("view_count") or 0)

    if cat_count:
        from src.analytics import build_category_pie
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            st.plotly_chart(build_category_pie(dict(cat_count), "Videos by Category", "videos"), use_container_width=True)
        with col_p2:
            st.plotly_chart(build_category_pie(cat_views, "Views by Category", "views"), use_container_width=True)

    # ── Top season videos — three leaderboards ─────────────────
    # Use the standard Long/Shorts/Live palette here (not the club's
    # brand colors) so the row tags read consistently with every other
    # table on the site.
    from src.analytics import FORMAT_COLORS as _ROW_FMT_COLORS

    def _video_row(i: int, v: dict) -> str:
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        _f = _fmt_of(v)
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[_f]
        fmt_color = _ROW_FMT_COLORS[_f]
        pub = (v.get("published_at") or "")[:10]
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        _cat = (v.get("category") or "").replace("<", "&lt;")
        _cat_color = CATEGORY_COLORS.get(_cat, "#888")
        _cat_span = f' · <span style="color:{_cat_color}">{_cat}</span>' if _cat and _cat != "Other" else ""
        _meta = (f'<span style="color:{fmt_color}">{fmt_label}</span> · '
                 f'{_dur(v.get("duration_seconds", 0))} · '
                 f'<span style="color:#888">{pub}</span>'
                 f'{_cat_span}')
        return f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px;vertical-align:top"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px;display:block"></td>
            <td style="padding:6px 12px;vertical-align:top">
              <div style="display:flex;flex-direction:column;justify-content:space-between;height:62px">
                <a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none;font-weight:700"><br>{title}</a>
                <div style="font-size:12px">{_meta}</div>
              </div>
            </td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('view_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('like_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('comment_count', 0))}</td>
        </tr>"""

    def _render_top_table(header: str, ranked: list[dict]) -> None:
        st.subheader(header)
        rows_html = "".join(_video_row(i, v) for i, v in enumerate(ranked, 1))
        components.html(f"""
        <style>
            .top-vids {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                         font-family:"Source Sans Pro",sans-serif; }}
            .top-vids th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
            .top-vids td {{ border-bottom:1px solid #262730; }}
        </style>
        <table class="top-vids">
        <thead><tr>
            <th style="text-align:right">#</th>
            <th></th>
            <th>Video</th>
            <th style="text-align:right">Views</th>
            <th style="text-align:right">Likes</th>
            <th style="text-align:right">Comments</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
        </table>
        {yt_popup_js()}
        # Row height: 6+6 padding + 68px thumbnail + 1px border ≈ 81px.
        # Header: ~32px. iframe chrome: ~16px buffer.
        """, height=video_table_height(len(ranked)), scrolling=False)

    # Top Season Videos lists moved to the Season Top Videos page.

    # ── Season Shorts: three histograms by duration (side by side) ──
    # All x = exact duration in seconds (0–60).
    #   1. y = total views at that duration  → where the reach is
    #   2. y = number of videos at that duration → where the volume is
    #   3. y = avg views/video at that duration → where the efficiency is
    # Volume + reach without efficiency tell you "this duration prints
    # views because we post a lot there"; efficiency separates a hit
    # length from a high-output one.
    try:
        _shorts = [v for v in (vids or []) if _fmt_of(v) == "short"]
        if _shorts:
            _by_sec_views: dict[int, int] = {}
            _by_sec_count: dict[int, int] = {}
            for v in _shorts:
                _d = int(v.get("duration_seconds") or 0)
                _vw = int(v.get("view_count") or 0)
                _by_sec_views[_d] = _by_sec_views.get(_d, 0) + _vw
                _by_sec_count[_d] = _by_sec_count.get(_d, 0) + 1
            _xs = sorted(_by_sec_views.keys())
            _ys_v = [_by_sec_views[d] for d in _xs]
            _ys_n = [_by_sec_count[d] for d in _xs]
            _ys_avg = [int(_by_sec_views[d] / _by_sec_count[d]) for d in _xs]
            _customdata_v = [[_by_sec_count[d], _ys_avg[i]] for i, d in enumerate(_xs)]
            _customdata_n = [[_by_sec_views[d], _ys_avg[i]] for i, d in enumerate(_xs)]
            _customdata_avg = [[_by_sec_views[d], _by_sec_count[d]] for d in _xs]

            def _make(xs, ys, custom, hover_lines, title, y_title):
                f = go.Figure(go.Bar(
                    x=xs, y=ys, marker_color="#00CC96",
                    customdata=custom,
                    hovertemplate=(
                        "<b>%{x}s</b><br>"
                        + hover_lines
                        + "<extra></extra>"
                    ),
                    showlegend=False,
                ))
                f.update_layout(
                    title=dict(text=title, x=0,
                               font=dict(color="#FAFAFA", size=13)),
                    xaxis=dict(title="Duration (seconds)",
                               showgrid=True,
                               gridcolor="rgba(255,255,255,0.06)",
                               range=[0, 60]),
                    yaxis=dict(title=y_title,
                               showgrid=True,
                               gridcolor="rgba(255,255,255,0.06)",
                               rangemode="tozero"),
                    margin=dict(t=40, b=50, l=60, r=20),
                    height=320,
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#FAFAFA"),
                )
                return f

            _suffix = f" ({len(_shorts)} videos)"
            fig_sd_v = _make(
                _xs, _ys_v, _customdata_v,
                ("Total views: %{y:,}<br>"
                 "Videos: %{customdata[0]}<br>"
                 "Avg / video: %{customdata[1]:,}"),
                "Total views by duration" + _suffix,
                "Sum of views",
            )
            fig_sd_n = _make(
                _xs, _ys_n, _customdata_n,
                ("Videos: %{y}<br>"
                 "Total views: %{customdata[0]:,}<br>"
                 "Avg / video: %{customdata[1]:,}"),
                "Number of videos by duration" + _suffix,
                "Number of videos",
            )
            fig_sd_avg = _make(
                _xs, _ys_avg, _customdata_avg,
                ("Avg / video: %{y:,}<br>"
                 "Total views: %{customdata[0]:,}<br>"
                 "Videos: %{customdata[1]}"),
                "Avg views / video by duration" + _suffix,
                "Avg views / video",
            )
            _col_a, _col_b, _col_c = st.columns(3)
            _col_a.plotly_chart(fig_sd_v, use_container_width=True)
            _col_b.plotly_chart(fig_sd_n, use_container_width=True)
            _col_c.plotly_chart(fig_sd_avg, use_container_width=True)
    except Exception as _e:
        st.caption(f"(Shorts duration histograms unavailable: {_e})")
