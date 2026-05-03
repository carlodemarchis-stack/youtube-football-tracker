from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num, yt_popup_js
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_include_league, get_global_color_map, get_global_color_map_dual, get_all_leagues_scope, get_league_for_channel, render_page_subtitle
from src.auth import require_login
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG, get_season_since, LEAGUE_SEASON_START
from src.dot import dual_dot, channel_badge

load_dotenv()
require_login()

st.title("Season 25/26")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

if not all_channels:
    st.warning("No channel data yet. Go to **Refresh Data** to fetch data first.")
    st.stop()

league, club = get_global_filter()
now = datetime.now(timezone.utc)
SEASON_SINCE = get_season_since(channel=club, league=league)
_daily_updated = db.get_last_fetch_time("daily")
_rss_updated = db.get_last_fetch_time("hourly_rss")
_season_updated = max(filter(None, [_daily_updated, _rss_updated]), default=None)
render_page_subtitle(
    f"Season performance since {SEASON_SINCE}",
    updated_raw=_season_updated,
    caveat=f"Stats cover videos published on/after {SEASON_SINCE}. Views on older videos that happen during the season are not included.",
)


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 1: ALL LEAGUES — branch by scope
# ══════════════════════════════════════════════════════════════
_scope = get_all_leagues_scope() if league is None else "Overall"

if league is None and _scope == "Overall":
    # Aggregate season stats per league — using precomputed channel columns (zero video queries)
    import plotly.graph_objects as go

    league_stats: dict[str, dict] = {}
    for ch in all_channels:
        if ch.get("entity_type") in ("Player", "Federation", "OtherClub", "WomenClub"):
            continue  # Players + Federations live on their own pages
        lg = get_league_for_channel(ch)
        if not lg:
            continue
        s = league_stats.setdefault(lg, {
            "videos": 0, "views": 0, "long_v": 0, "short_v": 0, "long_views": 0, "short_views": 0,
            "likes": 0, "comments": 0, "long_likes": 0, "short_likes": 0, "long_comments": 0, "short_comments": 0,
            "live_v": 0, "live_views": 0, "live_likes": 0, "live_comments": 0,
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
        # Total duration (in seconds) per format = avg-per-video * video-count;
        # sum across channels then divide back out at render time = league-wide
        # weighted average duration. Same approach as the per-channel rows.
        s["long_dur_total"]  += int(ch.get("season_long_dur_avg")  or 0) * ln
        s["short_dur_total"] += int(ch.get("season_short_dur_avg") or 0) * sn

    if not league_stats:
        st.info("No season data yet.")
        st.stop()

    total_views = sum(s["views"] for s in league_stats.values())
    total_videos = sum(s["videos"] for s in league_stats.values())
    total_likes = sum(s["likes"] for s in league_stats.values())
    total_comments = sum(s["comments"] for s in league_stats.values())
    avg_vpv = total_views // max(total_videos, 1)
    eng_rate = ((total_likes + total_comments) / total_views * 100) if total_views else 0.0
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Total Season Views", fmt_num(total_views))
    col2.metric("Total Season Videos", fmt_num(total_videos))
    col3.metric("Avg Views/Video", fmt_num(avg_vpv))
    col4.metric("Total Likes", fmt_num(total_likes))
    col5.metric("Total Comments", fmt_num(total_comments))
    col6.metric("Engagement Rate", f"{eng_rate:.2f}%", help="(Likes + Comments) / Views")

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

    def _make_pie(values, labels, colors, hover_suffix, title):
        fig = go.Figure(go.Pie(
            labels=labels, values=values, marker=dict(colors=colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
        ))
        fig.update_layout(
            title=dict(text=title, x=0.5), showlegend=False, height=300,
            margin=dict(t=40, b=20, l=20, r=20),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        return fig

    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
    with col_p1:
        st.plotly_chart(_make_pie(_zv(total_long_views, total_short_views, total_live_views), _pie_labels, _pie_colors, "views", "Views"), use_container_width=True)
    with col_p2:
        st.plotly_chart(_make_pie(_zv(total_longs, total_shorts, total_lives), _pie_labels, _pie_colors, "videos", "Videos"), use_container_width=True)
    with col_p3:
        st.plotly_chart(_make_pie(_zv(long_vpv, short_vpv, live_vpv), _pie_labels, _pie_colors, "views/video", "Views/Video"), use_container_width=True)
    with col_p4:
        st.plotly_chart(_make_pie(_zv(total_long_likes, total_short_likes, total_live_likes), _pie_labels, _pie_colors, "likes", "Likes"), use_container_width=True)
    with col_p5:
        st.plotly_chart(_make_pie(_zv(total_long_comments, total_short_comments, total_live_comments), _pie_labels, _pie_colors, "comments", "Comments"), use_container_width=True)

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
          <th colspan="4" style="text-align:center;border-bottom:2px solid #636EFA;color:#636EFA">Views</th>
          <th colspan="4" style="text-align:center;border-bottom:2px solid #00CC96;color:#00CC96">Videos</th>
          <th colspan="4" style="text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A">Views/Video</th>
          <th colspan="2" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Avg Duration</th>
          <th colspan="2" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">Engagement</th>
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
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    </div>
    """, height=len(sorted_leagues) * 37 + 130, scrolling=False)

    # Two charts: season views by league, videos by league (stacked Long/Shorts/Live)
    lg_names = [lg for lg, _ in sorted_leagues]
    fig_v = go.Figure()
    fig_v.add_trace(go.Bar(name="Long",   x=lg_names, y=[s["long_views"]  for _, s in sorted_leagues], marker_color="#636EFA"))
    fig_v.add_trace(go.Bar(name="Shorts", x=lg_names, y=[s["short_views"] for _, s in sorted_leagues], marker_color="#00CC96"))
    fig_v.add_trace(go.Bar(name="Live",   x=lg_names, y=[s["live_views"]  for _, s in sorted_leagues], marker_color="#FFA15A"))
    fig_v.update_layout(title="Season Views by League (Long / Shorts / Live)", barmode="stack",
                        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
    fig_n = go.Figure()
    fig_n.add_trace(go.Bar(name="Long",   x=lg_names, y=[s["long_v"]   for _, s in sorted_leagues], marker_color="#636EFA"))
    fig_n.add_trace(go.Bar(name="Shorts", x=lg_names, y=[s["short_v"]  for _, s in sorted_leagues], marker_color="#00CC96"))
    fig_n.add_trace(go.Bar(name="Live",   x=lg_names, y=[s["live_v"]   for _, s in sorted_leagues], marker_color="#FFA15A"))
    fig_n.update_layout(title="Season Videos by League (Long / Shorts / Live)", barmode="stack",
                        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
    c1, c2 = st.columns(2)
    c1.plotly_chart(fig_v, use_container_width=True)
    c2.plotly_chart(fig_n, use_container_width=True)

    # ── All channels table — precomputed columns (zero video queries) ───
    st.subheader("All Channels — Season")
    color_map = get_global_color_map()
    dual_colors = get_global_color_map_dual()
    include_league = get_include_league()

    ch_rows = []
    for ch in all_channels:
        if ch.get("entity_type") in ("Player", "Federation", "OtherClub", "WomenClub"):
            continue  # isolated entity types live on their own pages
        if not include_league and ch.get("entity_type") == "League":
            continue
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
        </tr>"""

    _ch_table_height = len(ch_rows) * 37 + 100
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
        <th colspan="4" style="text-align:center;border-bottom:2px solid #636EFA;color:#636EFA">Views</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #00CC96;color:#00CC96">Videos</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A">Views/Video</th>
        <th colspan="2" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Avg Duration</th>
        <th colspan="2" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">Engagement</th>
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
            clubs_only = [ch for ch in league_channels if ch.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub")]

    if not clubs_only:
        st.info("No clubs in this league yet.")
        st.stop()

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
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Total Views", fmt_num(total_views))
    k2.metric("Total Videos", fmt_num(total_videos))
    k3.metric("Avg Views/Video", fmt_num(total_views // max(total_videos, 1)))
    k4.metric("Total Likes", fmt_num(total_likes))
    k5.metric("Total Comments", fmt_num(total_comments))
    k6.metric("Engagement Rate", f"{total_eng_rate:.2f}%", help="(Likes + Comments) / Views")

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
        fig = go.Figure(go.Pie(
            labels=labels, values=values, marker=dict(colors=colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
        ))
        fig.update_layout(
            title=dict(text=title, x=0.5), showlegend=False, height=300,
            margin=dict(t=40, b=20, l=20, r=20),
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
    _table_height = len(df) * 37 + 100
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
        <th colspan="4" style="text-align:center;border-bottom:2px solid #636EFA;color:#636EFA">Views</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #00CC96;color:#00CC96">Videos</th>
        <th colspan="4" style="text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A">Views/Video</th>
        <th colspan="2" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Avg Duration</th>
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
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
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
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
    )
    st.plotly_chart(fig_vids, use_container_width=True)

    # Grouped bar: Avg Views/Video (Long vs Shorts)
    sorted_df_vpv = df.sort_values("all_vpv", ascending=False)
    chart_names_vpv = sorted_df_vpv["name"].tolist()
    fig_vpv_bar = go.Figure()
    fig_vpv_bar.add_trace(go.Bar(name="Long", x=chart_names_vpv, y=sorted_df_vpv["long_vpv"], marker_color="#636EFA"))
    fig_vpv_bar.add_trace(go.Bar(name="Shorts", x=chart_names_vpv, y=sorted_df_vpv["short_vpv"], marker_color="#00CC96"))
    fig_vpv_bar.update_layout(
        title="Avg Views/Video (Long vs Shorts)", barmode="group",
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
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
            marker_color="#EF553B",
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

    # KPI banner row
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Total Views", fmt_num(_total_v_banner))
    k2.metric("Total Videos", fmt_num(_total_n_banner))
    k3.metric("Avg Views/Video", fmt_num(_avg_vpv_banner))
    k4.metric("Total Likes", fmt_num(total_likes))
    k5.metric("Total Comments", fmt_num(total_comments))
    k6.metric("Engagement Rate", f"{_eng_rate:.2f}%", help="(Likes + Comments) / Views")

    # Pie charts row
    def _make_pie_club(values, labels, colors, hover_suffix, title):
        fig = go.Figure(go.Pie(
            labels=labels, values=values, marker=dict(colors=colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
        ))
        fig.update_layout(
            title=dict(text=title, x=0.5), showlegend=False, height=300,
            margin=dict(t=40, b=20, l=20, r=20),
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

    st.subheader("Monthly output")
    col_m1, col_m2 = st.columns(2)
    with col_m1:
        fig_mv = go.Figure()
        fig_mv.add_trace(go.Bar(name="Long", x=months, y=_series("long", "videos"), marker_color=LONG_COLOR))
        fig_mv.add_trace(go.Bar(name="Shorts", x=months, y=_series("short", "videos"), marker_color=SHORT_COLOR))
        if has_live:
            fig_mv.add_trace(go.Bar(name="Live", x=months, y=_series("live", "videos"), marker_color=LIVE_COLOR))
        fig_mv.update_layout(title="Videos per Month", barmode="stack",
                             xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                             legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
                             paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                             font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_mv, use_container_width=True)
    with col_m2:
        fig_mviews = go.Figure()
        fig_mviews.add_trace(go.Bar(name="Long", x=months, y=_series("long", "views"), marker_color=LONG_COLOR))
        fig_mviews.add_trace(go.Bar(name="Shorts", x=months, y=_series("short", "views"), marker_color=SHORT_COLOR))
        if has_live:
            fig_mviews.add_trace(go.Bar(name="Live", x=months, y=_series("live", "views"), marker_color=LIVE_COLOR))
        fig_mviews.update_layout(title="Views per Month", barmode="stack",
                                 xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                                 legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                 font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_mviews, use_container_width=True)

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

    # ── Top 20 season videos ───────────────────────────────────
    st.subheader("Top Season Videos")
    top = sorted(vids, key=lambda v: v.get("view_count", 0), reverse=True)[:20]
    top_rows = ""
    for i, v in enumerate(top, 1):
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        _f = _fmt_of(v)
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[_f]
        fmt_color = {"long": LONG_COLOR, "short": SHORT_COLOR, "live": LIVE_COLOR}[_f]
        pub = (v.get("published_at") or "")[:10]
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        _cat = (v.get("category") or "").replace("<", "&lt;")
        _cat_span = f' · <span style="color:#666">{_cat}</span>' if _cat and _cat != "Other" else ""
        _meta = f'<span style="color:{fmt_color}">{fmt_label}</span> · {_dur(v.get("duration_seconds", 0))}{_cat_span} · <span style="color:#888">{pub}</span>'
        top_rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px"><img src="{thumb}" style="width:120px;height:68px;object-fit:cover;border-radius:4px"></td>
            <td style="padding:6px 12px">
              <div style="font-size:12px;margin-bottom:2px">{_meta}</div>
              <a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a>
            </td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('view_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('like_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('comment_count', 0))}</td>
        </tr>"""

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
    <tbody>{top_rows}</tbody>
    </table>
    {yt_popup_js()}
    """, height=len(top) * 92 + 80, scrolling=False)
