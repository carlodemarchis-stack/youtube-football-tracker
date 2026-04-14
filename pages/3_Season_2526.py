from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_include_league, get_global_color_map, get_global_color_map_dual

load_dotenv()

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
SEASON_SINCE = "2025-08-01"


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 1: ALL LEAGUES
# ══════════════════════════════════════════════════════════════
if league is None:
    st.info("Select a league from the filter to view season data.")
    st.stop()


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 2: ONE LEAGUE, ALL CLUBS
# ══════════════════════════════════════════════════════════════
if club is None:
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
    for ch in clubs_only:
        season_vids = db.get_season_videos_by_channel(ch["id"], since=SEASON_SINCE)

        # Split by format (use 'format' field if available, fallback to duration < 60s)
        long_vids = [v for v in season_vids if v.get("format", "long" if v.get("duration_seconds", 0) >= 60 else "short") == "long"]
        short_vids = [v for v in season_vids if v.get("format", "long" if v.get("duration_seconds", 0) >= 60 else "short") == "short"]

        all_count = len(season_vids)
        all_views = sum(v.get("view_count", 0) for v in season_vids)
        long_count = len(long_vids)
        long_views = sum(v.get("view_count", 0) for v in long_vids)
        short_count = len(short_vids)
        short_views = sum(v.get("view_count", 0) for v in short_vids)

        long_dur = sum(v.get("duration_seconds", 0) for v in long_vids) // max(long_count, 1) if long_count else 0
        short_dur = sum(v.get("duration_seconds", 0) for v in short_vids) // max(short_count, 1) if short_count else 0

        season_rows.append({
            "name": ch["name"], "handle": ch.get("handle", ""),
            "all_views": all_views, "all_videos": all_count,
            "all_vpv": all_views // max(all_count, 1) if all_count else 0,
            "long_views": long_views, "long_videos": long_count,
            "long_vpv": long_views // max(long_count, 1) if long_count else 0,
            "short_views": short_views, "short_videos": short_count,
            "short_vpv": short_views // max(short_count, 1) if short_count else 0,
            "long_dur": long_dur, "short_dur": short_dur,
        })

    df = pd.DataFrame(season_rows)

    # Totals banner
    total_views = int(df["all_views"].sum())
    total_videos = int(df["all_videos"].sum())
    total_longs = int(df["long_videos"].sum())
    total_shorts = int(df["short_videos"].sum())
    avg_vpv = total_views // max(total_videos, 1)
    total_long_views = int(df["long_views"].sum())
    total_short_views = int(df["short_views"].sum())

    # (totals shown above pie charts below)

    # Pie charts: long/short split
    import plotly.graph_objects as go

    pie_colors = ["#636EFA", "#00CC96"]
    pie_labels = ["Long", "Shorts"]

    long_vpv = total_long_views // max(total_longs, 1)
    short_vpv = total_short_views // max(total_shorts, 1)

    col_p1, col_p2, col_p3 = st.columns(3)
    with col_p1:
        st.metric("Total Views", fmt_num(total_views))
        fig_v = go.Figure(go.Pie(
            labels=pie_labels,
            values=[total_long_views, total_short_views],
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
        st.metric("Total Videos", fmt_num(total_videos))
        fig_n = go.Figure(go.Pie(
            labels=pie_labels,
            values=[total_longs, total_shorts],
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
        st.metric("Avg Views/Video", fmt_num(avg_vpv))
        fig_vpv = go.Figure(go.Pie(
            labels=pie_labels,
            values=[long_vpv, short_vpv],
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
    # ZOOM LEVEL 3: single club — placeholder for now
    st.info(f"Season detail for {club['name']} coming soon.")
