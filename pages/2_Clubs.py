from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.analytics import compute_channel_comparison, fmt_num
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_league_for_channel, get_include_league, get_global_color_map, get_global_color_map_dual, get_all_leagues_scope
from src.channels import COUNTRY_TO_LEAGUE
from src.auth import get_current_user, is_admin

load_dotenv()

st.title("Channels")

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


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 1: ALL LEAGUES
# ══════════════════════════════════════════════════════════════
_scope = get_all_leagues_scope() if league is None else "Overall"

if league is None and _scope == "Overall":
    # Aggregate stats per league
    league_stats = {}
    for ch in all_channels:
        lg = get_league_for_channel(ch)
        if not lg:
            continue
        if lg not in league_stats:
            league_stats[lg] = {"channels": 0, "clubs": 0, "leagues": 0, "total_subs": 0, "clubs_subs": 0, "league_subs": 0, "total_views": 0, "total_videos": 0, "long": 0, "shorts": 0}
        league_stats[lg]["channels"] += 1  # includes both Club and League entities
        subs = ch.get("subscriber_count", 0)
        league_stats[lg]["total_subs"] += subs
        if ch.get("entity_type") == "League":
            league_stats[lg]["league_subs"] += subs
            league_stats[lg]["leagues"] += 1
        else:
            league_stats[lg]["clubs_subs"] += subs
            league_stats[lg]["clubs"] += 1
        league_stats[lg]["total_views"] += ch.get("total_views", 0)
        league_stats[lg]["total_videos"] += ch.get("video_count", 0)
        league_stats[lg]["long"] += ch.get("long_form_count", 0) + ch.get("live_count", 0)
        league_stats[lg]["shorts"] += ch.get("shorts_count", 0)

    if league_stats:
        st.subheader("Leagues")
        # Sort by total subs descending by default
        sorted_leagues = sorted(league_stats.items(), key=lambda kv: kv[1]["total_subs"], reverse=True)
        rows_html = ""
        for lg_name, s in sorted_leagues:
            avg_v = s["total_views"] // max(s["total_videos"], 1)
            vps = s['total_views'] // max(s['total_subs'], 1)
            avg_club_subs = s['clubs_subs'] // max(s['clubs'], 1)
            channels_val = s['clubs'] + s['leagues']
            rows_html += f"""<tr>
                <td style="padding:6px 12px" data-val="{lg_name}">{lg_name}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{channels_val}">{s['clubs']}+{s['leagues']}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['total_subs']}">{fmt_num(s['total_subs'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['clubs_subs']}">{fmt_num(s['clubs_subs'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{avg_club_subs}">{fmt_num(avg_club_subs)}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['league_subs']}">{fmt_num(s['league_subs'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['total_views']}">{fmt_num(s['total_views'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{vps}">{fmt_num(vps)}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['total_videos']}">{fmt_num(s['total_videos'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['long']}">{fmt_num(s['long'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{s['shorts']}">{fmt_num(s['shorts'])}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{avg_v}">{fmt_num(avg_v)}</td>
            </tr>"""

        _lg_table_height = len(sorted_leagues) * 37 + 100
        components.html(f"""
        <style>
            .lg-table {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                         font-family:"Source Sans Pro",sans-serif; background:transparent; }}
            .lg-table th {{ padding:6px 12px; user-select:none; }}
            .lg-table th[data-col] {{ cursor:pointer; }}
            .lg-table th[data-col]:hover {{ color:#636EFA; }}
            .lg-table td {{ padding:6px 12px; border-bottom:1px solid #262730; }}
            .lg-table .active {{ color:#636EFA; }}
        </style>
        <table class="lg-table">
        <thead>
        <tr style="border-bottom:2px solid #444">
            <th data-col="0" data-type="str" style="text-align:left">League</th>
            <th data-col="1" data-type="num" style="text-align:right">Channels</th>
            <th data-col="2" data-type="num" style="text-align:right" class="active">Subscribers ▼</th>
            <th data-col="3" data-type="num" style="text-align:right">Subs Clubs</th>
            <th data-col="4" data-type="num" style="text-align:right">Avg Subs/Club</th>
            <th data-col="5" data-type="num" style="text-align:right">Subs League</th>
            <th data-col="6" data-type="num" style="text-align:right">Total Views</th>
            <th data-col="7" data-type="num" style="text-align:right">Views/Sub</th>
            <th data-col="8" data-type="num" style="text-align:right">Videos</th>
            <th data-col="9" data-type="num" style="text-align:right">Long</th>
            <th data-col="10" data-type="num" style="text-align:right">Shorts</th>
            <th data-col="11" data-type="num" style="text-align:right">Views/Video</th>
        </tr>
        </thead>
        <tbody>{rows_html}</tbody>
        </table>
        <script>
        (function() {{
            const table = document.querySelector('.lg-table');
            const tbody = table.querySelector('tbody');
            const headers = table.querySelectorAll('th[data-col]');
            let currentCol = 2;
            let currentAsc = false;
            function sortTable(colIdx, type) {{
                const rows = Array.from(tbody.rows);
                const isStr = type === 'str';
                if (colIdx === currentCol) {{ currentAsc = !currentAsc; }}
                else {{ currentCol = colIdx; currentAsc = isStr; }}
                rows.sort((a, b) => {{
                    const va = a.cells[colIdx].dataset.val || '';
                    const vb = b.cells[colIdx].dataset.val || '';
                    let cmp;
                    if (isStr) cmp = va.localeCompare(vb, undefined, {{sensitivity:'base'}});
                    else cmp = (parseFloat(va) || 0) - (parseFloat(vb) || 0);
                    return currentAsc ? cmp : -cmp;
                }});
                rows.forEach(r => tbody.appendChild(r));
                headers.forEach(h => {{
                    h.classList.remove('active');
                    h.textContent = h.textContent.replace(/ [▲▼]/g, '');
                }});
                const a = table.querySelector('th[data-col="' + colIdx + '"]');
                a.classList.add('active');
                a.textContent += currentAsc ? ' ▲' : ' ▼';
            }}
            headers.forEach(h => {{
                h.addEventListener('click', function() {{
                    sortTable(parseInt(this.dataset.col), this.dataset.type || 'num');
                }});
            }});
        }})();
        </script>
        """, height=_lg_table_height, scrolling=False)

        # League comparison charts — one per table dimension
        lg_df = pd.DataFrame([
            {"League": lg, **stats}
            for lg, stats in league_stats.items()
        ])
        lg_df["avg_views_per_video"] = (lg_df["total_views"] / lg_df["total_videos"].replace(0, 1)).astype(int)
        lg_df["views_per_sub"] = (lg_df["total_views"] / lg_df["total_subs"].replace(0, 1)).astype(int)

        def make_league_bar(data, y_col, title):
            sorted_data = data.sort_values(y_col, ascending=False)
            fig = px.bar(sorted_data, x="League", y=y_col, color="League", title=title,
                         category_orders={"League": sorted_data["League"].tolist()})
            fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="", margin=dict(t=40, b=20))
            return fig

        st.plotly_chart(make_league_bar(lg_df, "total_subs", "Subscribers by League"), use_container_width=True)
        st.plotly_chart(make_league_bar(lg_df, "total_views", "Total Views by League"), use_container_width=True)
        st.plotly_chart(make_league_bar(lg_df, "views_per_sub", "Views/Sub by League"), use_container_width=True)
        st.plotly_chart(make_league_bar(lg_df, "total_videos", "Videos by League"), use_container_width=True)
        # Stacked: Videos — Long vs Shorts per league
        import plotly.graph_objects as go
        stacked_order = lg_df.sort_values("total_videos", ascending=False)
        fig_stack = go.Figure()
        fig_stack.add_trace(go.Bar(name="Long", x=stacked_order["League"], y=stacked_order["long"], marker_color="#636EFA"))
        fig_stack.add_trace(go.Bar(name="Shorts", x=stacked_order["League"], y=stacked_order["shorts"], marker_color="#00CC96"))
        fig_stack.update_layout(title="Videos by League (Long vs Shorts)", barmode="stack",
                                xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                                legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
        st.plotly_chart(fig_stack, use_container_width=True)
        st.plotly_chart(make_league_bar(lg_df, "avg_views_per_video", "Views/Video by League"), use_container_width=True)

    last_fetch = max((c.get("last_fetched") or "" for c in all_channels), default="Never") or "Never"
    st.caption(f"Last updated: {last_fetch}")


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 2: ONE LEAGUE, ALL CLUBS
# ══════════════════════════════════════════════════════════════
elif club is None:
    league_channels = get_channels_for_filter(all_channels, league)
    include_league = get_include_league()

    # Club list: include league channel when "All Clubs + League" is selected
    if league is None:
        # All Leagues + scope (Leagues only / Clubs only) — already filtered by scope
        clubs_only = league_channels
    elif include_league:
        clubs_only = league_channels
    else:
        clubs_only = [ch for ch in league_channels if ch.get("entity_type") != "League"]

    # Totals banner for the selection
    total_subs = sum(ch.get("subscriber_count", 0) for ch in clubs_only)
    total_views = sum(ch.get("total_views", 0) for ch in clubs_only)
    total_videos = sum(ch.get("video_count", 0) for ch in clubs_only)
    avg_vpv = total_views // max(total_videos, 1)
    avg_vps = total_views // max(total_subs, 1)
    cols = st.columns(5)
    cols[0].metric("Total Subscribers", fmt_num(total_subs))
    cols[1].metric("Total Views", fmt_num(total_views))
    cols[2].metric("Views/Sub", fmt_num(avg_vps))
    cols[3].metric("Total Videos", fmt_num(total_videos))
    cols[4].metric("Avg Views/Video", fmt_num(avg_vpv))
    if not clubs_only:
        st.info("No clubs in this league yet.")
        st.stop()

    # Reserve a slot for AI chat right below totals (hidden for now)
    # _ai_chat_slot = st.container()

    df = compute_channel_comparison(clubs_only)
    color_map = get_global_color_map()
    dual_colors = get_global_color_map_dual()

    # Add derived columns
    df["views_per_sub"] = (df["total_views"] / df["subscriber_count"].replace(0, 1)).astype(int)

    # Default sort: subscribers descending
    df = df.sort_values("subscriber_count", ascending=False).reset_index(drop=True)

    # ── Build table rows with data-val for JS sorting ────────
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
        launched = (row.get("launched_at") or "")[:4] or "-"
        launched_val = (row.get("launched_at") or "9999")[:4]
        rows_html += f"""<tr>
            <td style="padding:6px 12px">{dot}</td>
            <td style="padding:6px 12px" data-val="{row['name']}">{row['name']}</td>
            <td style="padding:6px 12px;text-align:center" data-val="{launched_val}">{launched}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['subscriber_count']}">{fmt_num(row['subscriber_count'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['total_views']}">{fmt_num(row['total_views'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['views_per_sub']}">{fmt_num(row['views_per_sub'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['video_count']}">{fmt_num(row['video_count'])}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row.get('long_form_count', 0) + row.get('live_count', 0)}">{fmt_num(row.get('long_form_count', 0) + row.get('live_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row.get('shorts_count', 0)}">{fmt_num(row.get('shorts_count', 0))}</td>
            <td style="padding:6px 12px;text-align:right" data-val="{row['avg_views_per_video']}">{fmt_num(row['avg_views_per_video'])}</td>
        </tr>"""

    # ── Sortable table via components.html (pure JS, no server round-trip) ──
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
    <tr style="border-bottom:2px solid #444">
        <th style="width:30px"></th>
        <th data-col="1" data-type="str" style="text-align:left">Channel</th>
        <th data-col="2" data-type="num" style="text-align:center">Since</th>
        <th data-col="3" data-type="num" style="text-align:right" class="active">Subscribers ▼</th>
        <th data-col="4" data-type="num" style="text-align:right">Total Views</th>
        <th data-col="5" data-type="num" style="text-align:right">Views/Sub</th>
        <th data-col="6" data-type="num" style="text-align:right">Videos</th>
        <th data-col="7" data-type="num" style="text-align:right">Long</th>
        <th data-col="8" data-type="num" style="text-align:right">Shorts</th>
        <th data-col="9" data-type="num" style="text-align:right">Views/Video</th>
    </tr>
    </thead>
    <tbody>{rows_html}</tbody>
    </table>
    <script>
    (function() {{
        const table = document.querySelector('.st-table');
        const tbody = table.querySelector('tbody');
        const headers = table.querySelectorAll('th[data-col]');
        let currentCol = 3;
        let currentAsc = false;

        function sortTable(colIdx, type) {{
            const rows = Array.from(tbody.rows);
            const isStr = type === 'str';
            if (colIdx === currentCol) {{
                currentAsc = !currentAsc;
            }} else {{
                currentCol = colIdx;
                currentAsc = isStr;  // asc for text, desc for numbers
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

            // Update header indicators
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

    # Charts ordered by subscribers (descending)
    sorted_names = df.sort_values("subscriber_count", ascending=False)["name"].tolist()

    def make_bar(data, y_col, title):
        fig = px.bar(data, x="name", y=y_col, color="name", color_discrete_map=color_map, title=title,
                     category_orders={"name": sorted_names})
        fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="", margin=dict(t=40, b=20))
        return fig

    st.plotly_chart(make_bar(df, "subscriber_count", "Total Subscribers per Channel"), use_container_width=True)
    st.plotly_chart(make_bar(df, "total_views", "Total Views across All Videos"), use_container_width=True)
    st.plotly_chart(make_bar(df, "views_per_sub", "Views per Subscriber (Engagement Efficiency)"), use_container_width=True)
    # Stacked bar: Videos (Long vs Shorts)
    import plotly.graph_objects as go
    fig_vids = go.Figure()
    fig_vids.add_trace(go.Bar(name="Long", x=sorted_names, y=df.set_index("name").loc[sorted_names, "long_form_count"].fillna(0), marker_color="#636EFA"))
    fig_vids.add_trace(go.Bar(name="Shorts", x=sorted_names, y=df.set_index("name").loc[sorted_names, "shorts_count"].fillna(0), marker_color="#00CC96"))
    fig_vids.update_layout(
        title="Total Videos Published (Long vs Shorts)", barmode="stack",
        xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#FAFAFA"),
    )
    st.plotly_chart(fig_vids, use_container_width=True)
    st.plotly_chart(make_bar(df, "avg_views_per_video", "Average Views per Video"), use_container_width=True)

    last_fetch = max((c.get("last_fetched") or "" for c in clubs_only), default="Never") or "Never"
    st.caption(f"Last updated: {last_fetch}")

    # ── AI Chat — hidden for now ──
    # user = get_current_user()
    # if user:
    #     try:
    #         api_key = st.secrets["app"]["anthropic_api_key"]
    #         from src.ai_chat import run_chat
    #         with _ai_chat_slot:
    #             run_chat(
    #                 api_key=api_key,
    #                 db=db,
    #                 user_email=user["email"],
    #                 is_admin=is_admin(),
    #                 clubs=clubs_only,
    #                 top100_stats=top100_stats,
    #                 league_name=league,
    #             )
    #     except Exception:
    #         pass  # Silently skip if API key not configured


# ══════════════════════════════════════════════════════════════
# ZOOM LEVEL 3: ONE CLUB
# ══════════════════════════════════════════════════════════════
else:
    channel = club

    # Club stats card
    st.subheader(f"{channel['name']}")
    cols = st.columns(4)
    cols[0].metric("Subscribers", fmt_num(channel.get('subscriber_count', 0)))
    cols[1].metric("Total Views", fmt_num(channel.get('total_views', 0)))
    cols[2].metric("Videos", fmt_num(channel.get('video_count', 0)))
    avg = channel.get("total_views", 0) // max(channel.get("video_count", 1), 1)
    cols[3].metric("Avg Views/Video", fmt_num(avg))

    # Videos
    videos = db.get_videos_by_channel(channel["id"])
    if not videos:
        st.info("No videos fetched for this channel yet.")
        st.stop()

    df = pd.DataFrame(videos)
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)

    # Upload frequency by year
    st.subheader("Upload Frequency")
    df["year"] = df["published_at"].dt.year
    yearly_uploads = df.groupby("year").size().reset_index(name="count")
    fig = px.bar(yearly_uploads, x="year", y="count", labels={"year": "Year", "count": "Videos in Top 100"})
    fig.update_layout(xaxis=dict(dtick=1, title="Year"), yaxis_title="Videos in Top 100")
    st.plotly_chart(fig, use_container_width=True)

    # Duration distribution
    st.subheader("Duration Distribution")
    df["duration_min"] = df["duration_seconds"] / 60
    fig2 = px.histogram(df, x="duration_min", nbins=30, labels={"duration_min": "Duration (minutes)"})
    fig2.update_layout(yaxis_title="Count")
    st.plotly_chart(fig2, use_container_width=True)

    # Year-over-year
    st.subheader("Year-over-Year Performance")
    df["year"] = df["published_at"].dt.year
    yearly = df.groupby("year").agg(
        videos=("youtube_video_id", "count"),
        total_views=("view_count", "sum"),
        avg_views=("view_count", "mean"),
    ).reset_index()
    yearly["avg_views"] = yearly["avg_views"].astype(int)
    fig4 = px.bar(yearly, x="year", y="total_views", labels={"year": "Year", "total_views": "Total Views"})
    st.plotly_chart(fig4, use_container_width=True)

    # Top 100 videos table
    st.subheader("Top 100 Videos")
    top = df.sort_values("view_count", ascending=False).head(100).reset_index(drop=True)
    table_df = top[["title", "view_count", "like_count", "comment_count", "published_at", "duration_seconds", "category"]].copy()
    table_df["view_count"] = table_df["view_count"].apply(fmt_num)
    table_df["like_count"] = table_df["like_count"].apply(fmt_num)
    table_df["comment_count"] = table_df["comment_count"].apply(fmt_num)
    table_df["published_at"] = table_df["published_at"].dt.strftime("%Y-%m-%d")
    table_df["duration_seconds"] = table_df["duration_seconds"].apply(lambda s: f"{s // 60}:{s % 60:02d}" if s else "0:00")
    table_df.index = range(1, len(table_df) + 1)
    table_df.index.name = "Rank"
    st.dataframe(
        table_df.rename(columns={
            "title": "Title",
            "view_count": "Views",
            "like_count": "Likes",
            "comment_count": "Comments",
            "published_at": "Published",
            "duration_seconds": "Duration",
            "category": "Theme",
        }),
        use_container_width=True,
    )

    # Top 100 This Season
    SEASON_SINCE = "2025-08-01"
    season_vids = db.get_season_videos_by_channel(channel["id"], since=SEASON_SINCE)
    if season_vids:
        st.subheader("Top 100 This Season")
        sdf = pd.DataFrame(season_vids).head(100)
        s_total_views = int(sdf["view_count"].sum())
        s_avg_views = int(sdf["view_count"].mean())
        scols = st.columns(3)
        scols[0].metric("Season Videos", fmt_num(len(season_vids)))
        scols[1].metric("Top 100 Views", fmt_num(s_total_views))
        scols[2].metric("Avg Views", fmt_num(s_avg_views))

        s_table = sdf[["title", "view_count", "like_count", "comment_count", "published_at", "duration_seconds", "category"]].copy()
        s_table["published_at"] = pd.to_datetime(s_table["published_at"], utc=True)
        s_table["view_count"] = s_table["view_count"].apply(fmt_num)
        s_table["like_count"] = s_table["like_count"].apply(fmt_num)
        s_table["comment_count"] = s_table["comment_count"].apply(fmt_num)
        s_table["published_at"] = s_table["published_at"].dt.strftime("%Y-%m-%d")
        s_table["duration_seconds"] = s_table["duration_seconds"].apply(lambda s: f"{s // 60}:{s % 60:02d}" if s else "0:00")
        s_table.index = range(1, len(s_table) + 1)
        s_table.index.name = "Rank"
        st.dataframe(
            s_table.rename(columns={
                "title": "Title",
                "view_count": "Views",
                "like_count": "Likes",
                "comment_count": "Comments",
                "published_at": "Published",
                "duration_seconds": "Duration",
                "category": "Theme",
            }),
            use_container_width=True,
        )
