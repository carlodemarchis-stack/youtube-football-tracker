from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

from src.database import Database
from src.analytics import compute_channel_comparison, compute_tier_stats, compute_theme_distribution, fmt_num
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_league_for_channel, get_include_league, get_global_color_map, get_global_color_map_dual, get_all_leagues_scope
from src.channels import COUNTRY_TO_LEAGUE

load_dotenv()

st.title("Top 100 Videos")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

if not all_channels:
    st.warning("No channel data yet.")
    st.stop()

league, club = get_global_filter()


# Top-N is all we ever need for charts/tables on this page.
@st.cache_data(ttl=300)
def _load_top_videos_global(limit: int = 500):
    if hasattr(db, "get_top_videos"):
        return db.get_top_videos(limit=limit)
    return db.get_all_videos()[:limit]


@st.cache_data(ttl=300)
def _load_top_videos_for_channels(channel_ids: tuple, limit: int = 500):
    if hasattr(db, "get_top_videos_in_channels"):
        return db.get_top_videos_in_channels(list(channel_ids), limit=limit)
    return [v for v in db.get_all_videos() if v.get("channel_id") in set(channel_ids)][:limit]


# ── Get videos based on filter ────────────────────────────────
if club:
    # One club
    from src.filters import render_club_header
    render_club_header(club, all_channels)
    raw_videos = db.get_videos_by_channel(club["id"])
    if not raw_videos:
        st.warning(f"No videos for {club['name']} yet.")
        st.stop()
    df = pd.DataFrame(raw_videos)
    df["channel_name"] = club["name"]
    color_field = "category"
    color_map = None
elif league:
    # One league — fetch top-N only for that league's channels
    league_channels = get_channels_for_filter(all_channels, league)
    ch_ids = tuple(ch["id"] for ch in league_channels)
    all_videos = _load_top_videos_for_channels(ch_ids, limit=500)
    df = pd.DataFrame(all_videos)
    if df.empty:
        df["channel_name"] = []
    else:
        df["channel_name"] = df["channels"].apply(lambda c: c["name"] if c else "Unknown")
    league_names = {ch["name"] for ch in league_channels}
    df = df[df["channel_name"].isin(league_names)] if not df.empty else df
    color_field = "channel_name"
    color_map = get_global_color_map()
else:
    # All leagues — fetch global top-N only
    scope = get_all_leagues_scope()
    all_videos = _load_top_videos_global(limit=500)
    df = pd.DataFrame(all_videos)
    df["channel_name"] = df["channels"].apply(lambda c: c["name"] if c else "Unknown")
    ch_by_name = {ch["name"]: ch for ch in all_channels}
    if scope == "Leagues only":
        keep = {n for n, c in ch_by_name.items() if c.get("entity_type") == "League"}
        df = df[df["channel_name"].isin(keep)]
        color_field = "channel_name"
        color_map = get_global_color_map()
    elif scope == "All clubs":
        keep = {n for n, c in ch_by_name.items() if c.get("entity_type") != "League"}
        df = df[df["channel_name"].isin(keep)]
        ch_to_league = {n: get_league_for_channel(c) for n, c in ch_by_name.items()}
        df["league"] = df["channel_name"].map(ch_to_league).fillna("Other")
        color_field = "league"
        color_map = None
    else:
        ch_to_league = {n: get_league_for_channel(c) for n, c in ch_by_name.items()}
        df["league"] = df["channel_name"].map(ch_to_league).fillna("Other")
        color_field = "league"
        color_map = None

if df.empty:
    st.warning("No video data for this selection.")
    st.stop()

# ── Channel table (same as Clubs page) ───────────────────────
if league and not club:
    now = datetime.now(timezone.utc)
    include_league = get_include_league()
    if include_league:
        table_channels = league_channels
    else:
        table_channels = [ch for ch in league_channels if ch.get("entity_type") != "League"]

    if table_channels:
        ch_df = compute_channel_comparison(table_channels)
        ch_color_map = get_global_color_map()
        ch_dual_colors = get_global_color_map_dual()

        SEASON_SINCE = "2025-08-01"
        t100_rows = []
        t100_stats = {}
        for ch in table_channels:
            vids = db.get_videos_by_channel(ch["id"])
            # All-time top 100
            if not vids:
                at_views = at_avg_age_days = at_avg_dur_s = 0
                at_avg_age_str = at_avg_dur_str = "-"
                at1_views = at1_age_days = at1_dur_s = 0
                at1_age_str = at1_dur_str = "-"
            else:
                vdf = pd.DataFrame(vids).head(100)
                at_views = int(vdf["view_count"].sum())
                if "published_at" in vdf.columns:
                    vdf["published_at"] = pd.to_datetime(vdf["published_at"], utc=True)
                    at_avg_age_days = (now - vdf["published_at"]).dt.days.mean()
                    at_avg_age_str = f"{at_avg_age_days / 365.25:.1f}y"
                    at1_age_days = (now - vdf["published_at"].iloc[0]).days
                    at1_age_str = f"{at1_age_days / 365.25:.1f}y"
                else:
                    at_avg_age_days = at1_age_days = 0
                    at_avg_age_str = at1_age_str = "-"
                at_avg_dur_s = int(vdf["duration_seconds"].mean()) if "duration_seconds" in vdf.columns else 0
                at_avg_dur_str = f"{at_avg_dur_s // 60}:{at_avg_dur_s % 60:02d}"
                at1_views = int(vdf["view_count"].iloc[0])
                at1_dur_s = int(vdf.iloc[0].get("duration_seconds", 0))
                at1_dur_str = f"{at1_dur_s // 60}:{at1_dur_s % 60:02d}"

            # Season top 100
            season_vids = db.get_season_videos_by_channel(ch["id"], since=SEASON_SINCE)
            if not season_vids:
                st_views = st_avg_age_days = st_avg_dur_s = 0
                st_avg_age_str = st_avg_dur_str = "-"
                s1_views = s1_age_days = s1_dur_s = 0
                s1_age_str = s1_dur_str = "-"
            else:
                sdf = pd.DataFrame(season_vids).head(100)
                st_views = int(sdf["view_count"].sum())
                if "published_at" in sdf.columns:
                    sdf["published_at"] = pd.to_datetime(sdf["published_at"], utc=True)
                    st_avg_age_days = (now - sdf["published_at"]).dt.days.mean()
                    st_avg_age_str = f"{int(st_avg_age_days)}d"
                    s1_age_days = (now - sdf["published_at"].iloc[0]).days
                    s1_age_str = f"{int(s1_age_days)}d"
                else:
                    st_avg_age_days = s1_age_days = 0
                    st_avg_age_str = s1_age_str = "-"
                st_avg_dur_s = int(sdf["duration_seconds"].mean()) if "duration_seconds" in sdf.columns else 0
                st_avg_dur_str = f"{st_avg_dur_s // 60}:{st_avg_dur_s % 60:02d}"
                s1_views = int(sdf["view_count"].iloc[0])
                s1_dur_s = int(sdf.iloc[0].get("duration_seconds", 0))
                s1_dur_str = f"{s1_dur_s // 60}:{s1_dur_s % 60:02d}"

            t100_rows.append({"name": ch["name"],
                "at_views": at_views, "at_avg_age_days": at_avg_age_days, "at_avg_dur_s": at_avg_dur_s,
                "at1_views": at1_views, "at1_age_days": at1_age_days, "at1_dur_s": at1_dur_s,
                "st_views": st_views, "st_avg_age_days": st_avg_age_days, "st_avg_dur_s": st_avg_dur_s,
                "s1_views": s1_views, "s1_age_days": s1_age_days, "s1_dur_s": s1_dur_s})
            t100_stats[ch["name"]] = {
                "at_views": at_views, "at_avg_age": at_avg_age_str, "at_avg_dur": at_avg_dur_str,
                "at1_views": at1_views, "at1_age": at1_age_str, "at1_dur": at1_dur_str,
                "st_views": st_views, "st_avg_age": st_avg_age_str, "st_avg_dur": st_avg_dur_str,
                "s1_views": s1_views, "s1_age": s1_age_str, "s1_dur": s1_dur_str}

        if t100_rows:
            t_df = pd.DataFrame(t100_rows)
            ch_df = ch_df.merge(t_df, on="name", how="left")

        ch_df = ch_df.sort_values("at_views", ascending=False, na_position="last").reset_index(drop=True)

        rows_html = ""
        for _, row in ch_df.iterrows():
            c1, c2 = ch_dual_colors.get(row["name"], (ch_color_map.get(row["name"], "#636EFA"), "#FFFFFF"))
            dot_inner = f'<span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative;cursor:pointer"><span style="display:block;width:8px;height:8px;border-radius:50%;background:{c2};position:absolute;top:3px;left:3px"></span></span>'
            handle = row.get("handle", "")
            if handle:
                yt_url = f"https://www.youtube.com/{handle}"
                dot = f'<a href="{yt_url}" target="_blank" style="text-decoration:none">{dot_inner}</a>'
            else:
                dot = dot_inner
            ss = t100_stats.get(row["name"], {})
            rows_html += f"""<tr>
                <td style="padding:6px 12px">{dot}</td>
                <td style="padding:6px 12px" data-val="{row['name']}">{row['name']}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('at_views', 0)}">{fmt_num(ss['at_views']) if ss.get('at_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at_avg_age_days', 0) or 0}">{ss.get('at_avg_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at_avg_dur_s', 0) or 0}">{ss.get('at_avg_dur', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('at1_views', 0)}">{fmt_num(ss['at1_views']) if ss.get('at1_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at1_age_days', 0) or 0}">{ss.get('at1_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at1_dur_s', 0) or 0}">{ss.get('at1_dur', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('st_views', 0)}">{fmt_num(ss['st_views']) if ss.get('st_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('st_avg_age_days', 0) or 0}">{ss.get('st_avg_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('st_avg_dur_s', 0) or 0}">{ss.get('st_avg_dur', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('s1_views', 0)}">{fmt_num(ss['s1_views']) if ss.get('s1_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('s1_age_days', 0) or 0}">{ss.get('s1_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('s1_dur_s', 0) or 0}">{ss.get('s1_dur', '-')}</td>
            </tr>"""

        _table_height = len(ch_df) * 37 + 100
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
            <th colspan="3" style="text-align:center;border-bottom:2px solid #636EFA;color:#636EFA">Top 100</th>
            <th colspan="3" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">#1 Video</th>
            <th colspan="3" style="text-align:center;border-bottom:2px solid #00CC96;color:#00CC96">Season Top 100</th>
            <th colspan="3" style="text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A">Season #1</th>
        </tr>
        <tr style="border-bottom:2px solid #444">
            <th style="width:30px"></th>
            <th data-col="1" data-type="str" style="text-align:left">Channel</th>
            <th data-col="2" data-type="num" style="text-align:right" class="active">Views ▼</th>
            <th data-col="3" data-type="num" style="text-align:right">Avg Age</th>
            <th data-col="4" data-type="num" style="text-align:right">Avg Dur</th>
            <th data-col="5" data-type="num" style="text-align:right">Views</th>
            <th data-col="6" data-type="num" style="text-align:right">Age</th>
            <th data-col="7" data-type="num" style="text-align:right">Dur</th>
            <th data-col="8" data-type="num" style="text-align:right">Views</th>
            <th data-col="9" data-type="num" style="text-align:right">Avg Age</th>
            <th data-col="10" data-type="num" style="text-align:right">Avg Dur</th>
            <th data-col="11" data-type="num" style="text-align:right">Views</th>
            <th data-col="12" data-type="num" style="text-align:right">Age</th>
            <th data-col="13" data-type="num" style="text-align:right">Dur</th>
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


filtered = df.sort_values("view_count", ascending=False).head(100).reset_index(drop=True)

current_year = datetime.now(timezone.utc).year

# ── Views by Rank chart ───────────────────────────────────────
st.subheader("Views by Rank")
chart_df = filtered[["view_count", "title", "channel_name"]].copy()
chart_df[color_field] = filtered[color_field] if color_field in filtered.columns else filtered["channel_name"]
chart_df["rank"] = range(1, len(chart_df) + 1)

fig = px.bar(
    chart_df, x="rank", y="view_count",
    color=color_field, color_discrete_map=color_map,
    hover_data=["title", "channel_name"],
    labels={"rank": "Rank", "view_count": "Views"},
)
fig.update_layout(
    xaxis=dict(tickvals=[1] + list(range(5, 96, 5)) + [100], title="Rank"),
    yaxis_title="Total Views",
    margin=dict(t=20, b=40),
    bargap=0.1,
)
st.plotly_chart(fig, use_container_width=True)

# ── Rank vs Year ──────────────────────────────────────────────
st.subheader("Rank vs Publication Year")
scatter_df = filtered[["title", "channel_name", "view_count", "published_at"]].copy()
scatter_df[color_field] = filtered[color_field] if color_field in filtered.columns else filtered["channel_name"]
scatter_df["rank"] = range(1, len(scatter_df) + 1)
scatter_df["year"] = pd.to_datetime(scatter_df["published_at"], utc=True).dt.year

fig_scatter = go.Figure()
baseline = current_year

for group_name in scatter_df[color_field].unique():
    grp = scatter_df[scatter_df[color_field] == group_name]
    bar_color = color_map.get(group_name, None) if color_map else None
    fig_scatter.add_trace(go.Bar(
        x=grp["rank"],
        y=grp["year"] - baseline,
        base=baseline,
        name=str(group_name),
        marker_color=bar_color,
        customdata=list(zip(grp["title"], grp["view_count"])),
        hovertemplate="Rank %{x}<br>Year: %{y}<br>%{customdata[0]}<br>Views: %{customdata[1]:,}<extra></extra>",
        width=0.8,
    ))

fig_scatter.update_layout(
    yaxis=dict(autorange="reversed", dtick=1, title="Year"),
    xaxis=dict(range=[0.5, 100.5], tickvals=[1] + list(range(5, 96, 5)) + [100], title="Rank"),
    margin=dict(t=20, b=40),
    bargap=0.1,
    barmode="overlay",
)
st.plotly_chart(fig_scatter, use_container_width=True)

# ── Videos by Year ────────────────────────────────────────────
st.subheader("Top 100 Videos by Publication Year")
year_df = filtered.copy()
year_df["year"] = pd.to_datetime(year_df["published_at"], utc=True).dt.year
year_counts = year_df.groupby("year").size().reset_index(name="count").sort_values("year")

fig_year = px.bar(year_counts, x="year", y="count",
                  labels={"year": "Year", "count": "Videos in Top 100"},
                  color_discrete_sequence=["#636EFA"])
fig_year.update_layout(xaxis=dict(dtick=1, title="Year"), yaxis_title="Videos in Top 100", margin=dict(t=20, b=40))
st.plotly_chart(fig_year, use_container_width=True)

# ── Theme Distribution ────────────────────────────────────────
st.subheader("Theme Distribution")
theme_df = compute_theme_distribution(filtered)
if not theme_df.empty:
    fig_theme = px.pie(theme_df, names="category", values="count", hole=0.4)
    fig_theme.update_layout(legend_title="Theme")
    st.plotly_chart(fig_theme, use_container_width=True)

# ── Video Table ───────────────────────────────────────────────
st.subheader("Video List")
table_df = filtered[["title", "channel_name", "view_count", "like_count", "comment_count", "published_at", "duration_seconds", "category"]].copy()
table_df["view_count"] = table_df["view_count"].apply(fmt_num)
table_df["like_count"] = table_df["like_count"].apply(fmt_num)
table_df["comment_count"] = table_df["comment_count"].apply(fmt_num)
table_df["published_at"] = pd.to_datetime(table_df["published_at"], utc=True)
now = pd.Timestamp.now(tz="UTC")
table_df["age"] = ((now - table_df["published_at"]).dt.days / 365.25).apply(lambda y: f"{y:.1f}y")
table_df["published_at"] = table_df["published_at"].dt.strftime("%Y-%m-%d")
table_df["duration_seconds"] = table_df["duration_seconds"].apply(lambda s: f"{s // 60}:{s % 60:02d}" if s else "0:00")
table_df.index = range(1, len(table_df) + 1)
table_df.index.name = "Rank"
st.dataframe(
    table_df[["title", "channel_name", "view_count", "like_count", "comment_count", "published_at", "age", "duration_seconds", "category"]].rename(columns={
        "title": "Title",
        "channel_name": "Channel",
        "view_count": "Views",
        "like_count": "Likes",
        "comment_count": "Comments",
        "published_at": "Published",
        "age": "Age",
        "duration_seconds": "Duration",
        "category": "Theme",
    }),
    use_container_width=True,
)
