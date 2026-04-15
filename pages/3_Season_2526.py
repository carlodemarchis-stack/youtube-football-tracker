from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_include_league, get_global_color_map, get_global_color_map_dual, get_all_leagues_scope, get_league_for_channel

load_dotenv()

st.title("Season 25/26")
st.caption("Stats cover videos **published** on/after 2025-08-01. Views on older videos that happen during the season are not included.")

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
SEASON_SINCE = "2025-08-01"


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 1: ALL LEAGUES — branch by scope
# ══════════════════════════════════════════════════════════════
_scope = get_all_leagues_scope() if league is None else "Overall"

if league is None and _scope == "Overall":
    # Aggregate season stats per league
    import plotly.graph_objects as go
    # Pull only season videos (DB-level filter — much faster than full table)
    @st.cache_data(ttl=300)
    def _load_season_vids(since: str):
        if hasattr(db, "get_season_videos"):
            return db.get_season_videos(since=since)
        # Fallback: old deploy without the helper
        return [v for v in db.get_all_videos() if (v.get("published_at") or "") >= since]
    with st.spinner("Loading season videos across all leagues…"):
        season_vids = _load_season_vids(SEASON_SINCE)

    # map channel_id → league
    ch_by_id = {c["id"]: c for c in all_channels}
    def _ch_league(v):
        ch = ch_by_id.get(v.get("channel_id"))
        if not ch:
            return None
        return get_league_for_channel(ch)

    league_stats: dict[str, dict] = {}
    for v in season_vids:
        lg = _ch_league(v)
        if not lg:
            continue
        fmt = v.get("format") or ("long" if v.get("duration_seconds", 0) >= 60 else "short")
        s = league_stats.setdefault(lg, {"videos": 0, "views": 0, "long_v": 0, "short_v": 0, "long_views": 0, "short_views": 0, "likes": 0, "comments": 0})
        s["videos"] += 1
        s["views"] += v.get("view_count", 0) or 0
        s["likes"] += v.get("like_count", 0) or 0
        s["comments"] += v.get("comment_count", 0) or 0
        if fmt == "long":
            s["long_v"] += 1
            s["long_views"] += v.get("view_count", 0) or 0
        else:
            s["short_v"] += 1
            s["short_views"] += v.get("view_count", 0) or 0

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

    st.subheader("Leagues — Season")
    sorted_leagues = sorted(league_stats.items(), key=lambda kv: kv[1]["views"], reverse=True)
    rows_html = ""
    for lg, s in sorted_leagues:
        vpv = s["views"] // max(s["videos"], 1)
        long_vpv = s["long_views"] // max(s["long_v"], 1)
        short_vpv = s["short_views"] // max(s["short_v"], 1)
        er = ((s["likes"] + s["comments"]) / s["views"] * 100) if s["views"] else 0.0
        rows_html += f"""<tr>
            <td style="padding:6px 12px">{lg}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['views'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['long_views'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['short_views'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['videos'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['long_v'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['short_v'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(long_vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(short_vpv)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['likes'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(s['comments'])}</td>
            <td style="padding:6px 12px;text-align:right">{er:.2f}%</td>
        </tr>"""
    components.html(f"""
    <style>
      table.lg {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                  font-family:"Source Sans Pro",sans-serif; }}
      table.lg th {{ padding:6px 12px; border-bottom:2px solid #444; }}
      table.lg td {{ border-bottom:1px solid #262730; }}
    </style>
    <table class="lg">
      <thead>
        <tr>
          <th colspan="1"></th>
          <th colspan="3" style="text-align:center;color:#636EFA">Views</th>
          <th colspan="3" style="text-align:center;color:#00CC96">Videos</th>
          <th colspan="3" style="text-align:center;color:#FFA15A">Views/Video</th>
          <th colspan="3" style="text-align:center;color:#AB63FA">Engagement</th>
        </tr>
        <tr>
          <th style="text-align:left">League</th>
          <th style="text-align:right">All</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">All</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">All</th>
          <th style="text-align:right">Long</th>
          <th style="text-align:right">Shorts</th>
          <th style="text-align:right">Likes</th>
          <th style="text-align:right">Comments</th>
          <th style="text-align:right">Rate</th>
        </tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    """, height=len(sorted_leagues) * 37 + 110, scrolling=False)

    # Two charts: season views by league, videos by league (stacked)
    lg_names = [lg for lg, _ in sorted_leagues]
    fig_v = go.Figure()
    fig_v.add_trace(go.Bar(name="Long", x=lg_names, y=[s["long_views"] for _, s in sorted_leagues], marker_color="#636EFA"))
    fig_v.add_trace(go.Bar(name="Shorts", x=lg_names, y=[s["short_views"] for _, s in sorted_leagues], marker_color="#00CC96"))
    fig_v.update_layout(title="Season Views by League (Long vs Shorts)", barmode="stack",
                        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
    fig_n = go.Figure()
    fig_n.add_trace(go.Bar(name="Long", x=lg_names, y=[s["long_v"] for _, s in sorted_leagues], marker_color="#636EFA"))
    fig_n.add_trace(go.Bar(name="Shorts", x=lg_names, y=[s["short_v"] for _, s in sorted_leagues], marker_color="#00CC96"))
    fig_n.update_layout(title="Season Videos by League (Long vs Shorts)", barmode="stack",
                        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
    c1, c2 = st.columns(2)
    c1.plotly_chart(fig_v, use_container_width=True)
    c2.plotly_chart(fig_n, use_container_width=True)
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
            clubs_only = [ch for ch in league_channels if ch.get("entity_type") != "League"]

    if not clubs_only:
        st.info("No clubs in this league yet.")
        st.stop()

    color_map = get_global_color_map()
    dual_colors = get_global_color_map_dual()

    # Compute season stats per channel
    season_rows = []
    _prog = st.progress(0.0, text=f"Loading season videos for {len(clubs_only)} clubs…")
    for _i, ch in enumerate(clubs_only, 1):
        _prog.progress(_i / max(len(clubs_only), 1), text=f"Loading {ch['name']} ({_i}/{len(clubs_only)})…")
        season_vids = db.get_season_videos_by_channel(ch["id"], since=SEASON_SINCE)

        # Split by format (use 'format' field if available, fallback to duration < 60s)
        def _f(v):
            f = v.get("format")
            if f in ("long", "short", "live"):
                return f
            return "long" if v.get("duration_seconds", 0) >= 60 else "short"
        long_vids = [v for v in season_vids if _f(v) == "long"]
        short_vids = [v for v in season_vids if _f(v) == "short"]
        live_vids = [v for v in season_vids if _f(v) == "live"]

        all_count = len(season_vids)
        all_views = sum(v.get("view_count", 0) for v in season_vids)
        long_count = len(long_vids)
        long_views = sum(v.get("view_count", 0) for v in long_vids)
        short_count = len(short_vids)
        short_views = sum(v.get("view_count", 0) for v in short_vids)
        live_count = len(live_vids)
        live_views = sum(v.get("view_count", 0) for v in live_vids)

        long_dur = sum(v.get("duration_seconds", 0) for v in long_vids) // max(long_count, 1) if long_count else 0
        short_dur = sum(v.get("duration_seconds", 0) for v in short_vids) // max(short_count, 1) if short_count else 0

        likes = sum(v.get("like_count", 0) or 0 for v in season_vids)
        comments = sum(v.get("comment_count", 0) or 0 for v in season_vids)
        eng_rate = ((likes + comments) / all_views * 100) if all_views else 0.0
        season_rows.append({
            "name": ch["name"], "handle": ch.get("handle", ""),
            "all_views": all_views, "all_videos": all_count,
            "all_vpv": all_views // max(all_count, 1) if all_count else 0,
            "long_views": long_views, "long_videos": long_count,
            "long_vpv": long_views // max(long_count, 1) if long_count else 0,
            "short_views": short_views, "short_videos": short_count,
            "short_vpv": short_views // max(short_count, 1) if short_count else 0,
            "live_views": live_views, "live_videos": live_count,
            "live_vpv": live_views // max(live_count, 1) if live_count else 0,
            "long_dur": long_dur, "short_dur": short_dur,
            "likes": likes, "comments": comments, "eng_rate": eng_rate,
        })

    _prog.empty()
    df = pd.DataFrame(season_rows)

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

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        fig_v = go.Figure(go.Pie(
            labels=pie_labels,
            values=_zvals(total_long_views, total_short_views, total_live_views),
            marker=dict(colors=pie_colors),
            hole=0.45,
            textinfo="percent+label",
            textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} views<extra></extra>",
        ))
        fig_v.update_layout(
            title=dict(text="Views", x=0.5),
            showlegend=False,
            height=300,
            margin=dict(t=40, b=20, l=20, r=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_v, use_container_width=True)

    with col_p2:
        fig_n = go.Figure(go.Pie(
            labels=pie_labels,
            values=_zvals(total_longs, total_shorts, total_lives),
            marker=dict(colors=pie_colors),
            hole=0.45,
            textinfo="percent+label",
            textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} videos<extra></extra>",
        ))
        fig_n.update_layout(
            title=dict(text="Videos", x=0.5),
            showlegend=False,
            height=300,
            margin=dict(t=40, b=20, l=20, r=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_n, use_container_width=True)

    with col_p3:
        fig_vpv = go.Figure(go.Pie(
            labels=pie_labels,
            values=_zvals(long_vpv, short_vpv, live_vpv),
            marker=dict(colors=pie_colors),
            hole=0.45,
            textinfo="percent+label",
            textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} views/video<extra></extra>",
        ))
        fig_vpv.update_layout(
            title=dict(text="Views/Video", x=0.5),
            showlegend=False,
            height=300,
            margin=dict(t=40, b=20, l=20, r=20),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
        )
        st.plotly_chart(fig_vpv, use_container_width=True)

    # Default sort: all views descending
    df = df.sort_values("all_views", ascending=False).reset_index(drop=True)

    # ── Build table rows ────────────────────────────────────────
    rows_html = ""
    for _, row in df.iterrows():
        c1, c2 = dual_colors.get(row["name"], (color_map.get(row["name"], "#636EFA"), "#FFFFFF"))
        dot_inner = f'<span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative;cursor:pointer"><span style="display:block;width:8px;height:8px;border-radius:50%;background:{c2};position:absolute;top:3px;left:3px"></span></span>'
        handle = row.get("handle", "")
        if handle:
            yt_url = f"https://www.youtube.com/{handle}"
            dot = f'<a href="{yt_url}" target="_blank" style="text-decoration:none">{dot_inner}</a>'
        else:
            dot = dot_inner

        def _v(val):
            v = int(val)
            return fmt_num(v) if v else '-'

        def _dur(secs):
            s = int(secs)
            if not s:
                return '-'
            m, sec = divmod(s, 60)
            return f"{m}:{sec:02d}"

        rows_html += f"""<tr>
            <td style="padding:6px 12px">{dot}</td>
            <td style="padding:6px 12px" data-val="{row['name']}">{row['name']}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_views']}">{_v(row['all_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_views']}">{_v(row['long_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_views']}">{_v(row['short_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_videos']}">{_v(row['all_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_videos']}">{_v(row['long_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_videos']}">{_v(row['short_videos'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['all_vpv']}">{_v(row['all_vpv'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['long_vpv']}">{_v(row['long_vpv'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['short_vpv']}">{_v(row['short_vpv'])}</td>
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
        .st-table {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                     font-family:"Source Sans Pro",sans-serif; background:transparent; }}
        .st-table th {{ padding:6px 12px; user-select:none; }}
        .st-table th[data-col] {{ cursor:pointer; }}
        .st-table th[data-col]:hover {{ color:#636EFA; }}
        .st-table td {{ padding:6px 12px; border-bottom:1px solid #262730; }}
        .st-table a {{ color:inherit; text-decoration:none; }}
        .st-table .active {{ color:#636EFA; }}
    </style>
    <table class="st-table">
    <thead>
    <tr>
        <th colspan="2"></th>
        <th colspan="3" style="text-align:center;border-bottom:2px solid #636EFA;color:#636EFA">Views</th>
        <th colspan="3" style="text-align:center;border-bottom:2px solid #00CC96;color:#00CC96">Videos</th>
        <th colspan="3" style="text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A">Views/Video</th>
        <th colspan="2" style="text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA">Avg Duration</th>
        <th colspan="3" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">Engagement</th>
    </tr>
    <tr style="border-bottom:2px solid #444">
        <th style="width:30px"></th>
        <th data-col="1" data-type="str" style="text-align:left">Channel</th>
        <th data-col="2" data-type="num" style="text-align:right" class="active">All ▼</th>
        <th data-col="3" data-type="num" style="text-align:right">Long</th>
        <th data-col="4" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="5" data-type="num" style="text-align:right">All</th>
        <th data-col="6" data-type="num" style="text-align:right">Long</th>
        <th data-col="7" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="8" data-type="num" style="text-align:right">All</th>
        <th data-col="9" data-type="num" style="text-align:right">Long</th>
        <th data-col="10" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="11" data-type="num" style="text-align:right">Long</th>
        <th data-col="12" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="13" data-type="num" style="text-align:right">Likes</th>
        <th data-col="14" data-type="num" style="text-align:right">Comments</th>
        <th data-col="15" data-type="num" style="text-align:right">Rate</th>
    </tr>
    </thead>
    <tbody>{rows_html}</tbody>
    </table>
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

else:
    # ══════════════════════════════════════════════════════════════
    # ZOOM LEVEL 3: ONE CLUB
    # ══════════════════════════════════════════════════════════════
    import plotly.graph_objects as go

    from src.filters import render_club_header
    render_club_header(club, all_channels)

    with st.spinner(f"Loading {club['name']} season videos…"):
        vids = db.get_season_videos_by_channel(club["id"], since=SEASON_SINCE)
    if not vids:
        st.info(f"No season videos for {club['name']} yet.")
        st.stop()

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

    def _is_long(v):
        return _fmt_of(v) == "long"

    longs = [v for v in vids if _fmt_of(v) == "long"]
    shorts = [v for v in vids if _fmt_of(v) == "short"]
    lives = [v for v in vids if _fmt_of(v) == "live"]

    total_videos = len(vids)
    total_views = sum(v.get("view_count", 0) for v in vids)
    long_videos = len(longs)
    long_views = sum(v.get("view_count", 0) for v in longs)
    short_videos = len(shorts)
    short_views = sum(v.get("view_count", 0) for v in shorts)
    live_videos = len(lives)
    live_views = sum(v.get("view_count", 0) for v in lives)
    avg_vpv = total_views // max(total_videos, 1)
    long_vpv = long_views // max(long_videos, 1)
    short_vpv = short_views // max(short_videos, 1)
    live_vpv = live_views // max(live_videos, 1)
    long_dur = sum(v.get("duration_seconds", 0) for v in longs) // max(long_videos, 1) if long_videos else 0
    short_dur = sum(v.get("duration_seconds", 0) for v in shorts) // max(short_videos, 1) if short_videos else 0
    live_dur = sum(v.get("duration_seconds", 0) for v in lives) // max(live_videos, 1) if live_videos else 0

    # Likes + comments
    def _sumk(arr, k):
        return sum(v.get(k, 0) or 0 for v in arr)
    total_likes = _sumk(vids, "like_count")
    total_comments = _sumk(vids, "comment_count")
    long_likes = _sumk(longs, "like_count");   long_comments = _sumk(longs, "comment_count")
    short_likes = _sumk(shorts, "like_count"); short_comments = _sumk(shorts, "comment_count")
    live_likes = _sumk(lives, "like_count");   live_comments = _sumk(lives, "comment_count")

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

    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
    with col_p1:
        fig_v = go.Figure(go.Pie(
            labels=pie_labels, values=_vals(long_views, short_views, live_views),
            marker=dict(colors=pie_colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} views<extra></extra>",
        ))
        fig_v.update_layout(title=dict(text="Views", x=0.5), showlegend=False, height=300,
                            margin=dict(t=40, b=20, l=20, r=20),
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                            font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_v, use_container_width=True)
    with col_p2:
        fig_n = go.Figure(go.Pie(
            labels=pie_labels, values=_vals(long_videos, short_videos, live_videos),
            marker=dict(colors=pie_colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} videos<extra></extra>",
        ))
        fig_n.update_layout(title=dict(text="Videos", x=0.5), showlegend=False, height=300,
                            margin=dict(t=40, b=20, l=20, r=20),
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                            font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_n, use_container_width=True)
    with col_p3:
        fig_vpv = go.Figure(go.Pie(
            labels=pie_labels, values=_vals(long_vpv, short_vpv, live_vpv),
            marker=dict(colors=pie_colors), hole=0.45,
            textinfo="percent+label", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f} views/video<extra></extra>",
        ))
        fig_vpv.update_layout(title=dict(text="Views/Video", x=0.5), showlegend=False, height=300,
                              margin=dict(t=40, b=20, l=20, r=20),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              font=dict(color="#FAFAFA"))
        st.plotly_chart(fig_vpv, use_container_width=True)
    with col_p4:
        st.metric("Total Likes", fmt_num(total_likes))
        st.caption(f"Like rate: {_pct(_rate(total_likes, total_views))}")
    with col_p5:
        st.metric("Total Comments", fmt_num(total_comments))
        st.caption(f"Comment rate: {_pct(_rate(total_comments, total_views))}")

    # ── Breakdown table ────────────────────────────────────────
    st.subheader("Breakdown")
    st.markdown(f"""
<table style="width:100%;border-collapse:collapse;font-size:14px;color:#FAFAFA">
<thead><tr style="border-bottom:2px solid #444">
<th style="text-align:left;padding:6px 12px">Format</th>
<th style="text-align:right;padding:6px 12px">Videos</th>
<th style="text-align:right;padding:6px 12px">Views</th>
<th style="text-align:right;padding:6px 12px">Views/Video</th>
<th style="text-align:right;padding:6px 12px">Avg Duration</th>
<th style="text-align:right;padding:6px 12px">Likes</th>
<th style="text-align:right;padding:6px 12px">Comments</th>
<th style="text-align:right;padding:6px 12px">Like Rate</th>
<th style="text-align:right;padding:6px 12px">Comment Rate</th>
</tr></thead>
<tbody>
<tr style="border-bottom:1px solid #262730"><td style="padding:6px 12px;color:{LONG_COLOR}">Long</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_vpv)}</td>
<td style="text-align:right;padding:6px 12px">{_dur(long_dur)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(long_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(long_likes, long_views))}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(long_comments, long_views))}</td></tr>
<tr style="border-bottom:1px solid #262730"><td style="padding:6px 12px;color:{SHORT_COLOR}">Shorts</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_vpv)}</td>
<td style="text-align:right;padding:6px 12px">{_dur(short_dur)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(short_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(short_likes, short_views))}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(short_comments, short_views))}</td></tr>
{"" if not has_live else f'''<tr style="border-bottom:1px solid #262730"><td style="padding:6px 12px;color:{LIVE_COLOR}">Live</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_vpv)}</td>
<td style="text-align:right;padding:6px 12px">{_dur(live_dur)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(live_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(live_likes, live_views))}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(live_comments, live_views))}</td></tr>'''}
<tr style="font-weight:600"><td style="padding:6px 12px">Total</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_videos)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_views)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(avg_vpv)}</td>
<td style="text-align:right;padding:6px 12px">-</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_likes)}</td>
<td style="text-align:right;padding:6px 12px">{_fmt(total_comments)}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(total_likes, total_views))}</td>
<td style="text-align:right;padding:6px 12px">{_pct(_rate(total_comments, total_views))}</td></tr>
</tbody></table>
""", unsafe_allow_html=True)

    # ── Videos per month (stacked) ─────────────────────────────
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
        top_rows += f"""<tr>
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank"><img src="{thumb}" style="width:120px;height:68px;object-fit:cover;border-radius:4px"></a></td>
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a></td>
            <td style="padding:6px 12px"><span style="color:{fmt_color}">{fmt_label}</span></td>
            <td style="padding:6px 12px;text-align:right">{_dur(v.get('duration_seconds', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('view_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('like_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right">{_fmt(v.get('comment_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right;color:#888">{pub}</td>
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
        <th>Title</th>
        <th>Format</th>
        <th style="text-align:right">Duration</th>
        <th style="text-align:right">Views</th>
        <th style="text-align:right">Likes</th>
        <th style="text-align:right">Comments</th>
        <th style="text-align:right">Published</th>
    </tr></thead>
    <tbody>{top_rows}</tbody>
    </table>
    """, height=len(top) * 92 + 80, scrolling=False)
