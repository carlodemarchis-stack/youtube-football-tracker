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
        # Sort by total subs descending by default
        sorted_leagues = sorted(league_stats.items(), key=lambda kv: kv[1]["total_subs"], reverse=True)

        # Totals banner (metric cards, consistent with zoom-2)
        tot = {
            "clubs": sum(s["clubs"] for _, s in sorted_leagues),
            "leagues": sum(s["leagues"] for _, s in sorted_leagues),
            "total_subs": sum(s["total_subs"] for _, s in sorted_leagues),
            "total_views": sum(s["total_views"] for _, s in sorted_leagues),
            "total_videos": sum(s["total_videos"] for _, s in sorted_leagues),
        }
        tot_vps = tot["total_views"] // max(tot["total_subs"], 1)
        tot_avg_v = tot["total_views"] // max(tot["total_videos"], 1)
        _mcols = st.columns(5)
        _mcols[0].metric("Total Subscribers", fmt_num(tot["total_subs"]))
        _mcols[1].metric("Total Views", fmt_num(tot["total_views"]))
        _mcols[2].metric("Views/Sub", fmt_num(tot_vps))
        _mcols[3].metric("Total Videos", fmt_num(tot["total_videos"]))
        _mcols[4].metric("Avg Views/Video", fmt_num(tot_avg_v))


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
            <th style="text-align:left">League</th>
            <th style="text-align:right">Channels</th>
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
        lg_df["avg_subs_per_club"] = (lg_df["clubs_subs"] / lg_df["clubs"].replace(0, 1)).astype(int)

        def make_league_bar(data, y_col, title):
            sorted_data = data.sort_values(y_col, ascending=False)
            fig = px.bar(sorted_data, x="League", y=y_col, color="League", title=title,
                         category_orders={"League": sorted_data["League"].tolist()})
            fig.update_layout(showlegend=False, xaxis_title="", yaxis_title="", margin=dict(t=40, b=20))
            return fig

        # Stacked: Videos — Long vs Shorts per league
        import plotly.graph_objects as go
        stacked_order = lg_df.sort_values("total_videos", ascending=False)
        fig_stack = go.Figure()
        fig_stack.add_trace(go.Bar(name="Long", x=stacked_order["League"], y=stacked_order["long"], marker_color="#636EFA"))
        fig_stack.add_trace(go.Bar(name="Shorts", x=stacked_order["League"], y=stacked_order["shorts"], marker_color="#00CC96"))
        fig_stack.update_layout(title="Videos by League (Long vs Shorts)", barmode="stack",
                                xaxis_title="", yaxis_title="", margin=dict(t=40, b=20),
                                legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))

        charts = [
            make_league_bar(lg_df, "total_subs", "Subscribers by League"),
            make_league_bar(lg_df, "clubs_subs", "Subs — Clubs by League"),
            make_league_bar(lg_df, "avg_subs_per_club", "Avg Subs per Club by League"),
            make_league_bar(lg_df, "league_subs", "Subs — League Channel by League"),
            make_league_bar(lg_df, "total_views", "Total Views by League"),
            make_league_bar(lg_df, "views_per_sub", "Views/Sub by League"),
            make_league_bar(lg_df, "total_videos", "Videos by League"),
            fig_stack,
            make_league_bar(lg_df, "avg_views_per_video", "Views/Video by League"),
        ]
        for i in range(0, len(charts), 2):
            cols = st.columns(2)
            for j, fig in enumerate(charts[i:i+2]):
                cols[j].plotly_chart(fig, use_container_width=True)

    # ── Growth by League (from channel_snapshots) ───────────────
    from src.growth import group_by_channel as _gbc_l1, delta as _gdelta_l1, _parse_date as _pd_l1
    _since_l1 = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    _snap_rows_l1 = db.get_all_snapshots(since_date=_since_l1)
    # Map channel_id → league
    _ch_to_league = {c["id"]: get_league_for_channel(c) for c in all_channels}

    # Aggregate per (date, league)
    from collections import defaultdict
    _lg_day: dict[tuple[str, str], int] = defaultdict(int)
    _league_subs_latest: dict[str, int] = defaultdict(int)
    for r in _snap_rows_l1:
        lg = _ch_to_league.get(r["channel_id"])
        if not lg:
            continue
        _lg_day[(r["captured_date"], lg)] += int(r.get("subscriber_count", 0) or 0)

    # Build per-league sorted series
    _by_lg: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for (d, lg), v in _lg_day.items():
        _by_lg[lg].append((d, v))
    for lg in _by_lg:
        _by_lg[lg].sort(key=lambda t: t[0])

    st.subheader("Growth")
    if not _by_lg or not any(len(v) >= 2 for v in _by_lg.values()):
        st.caption("Snapshot history not long enough yet — the daily cron will accumulate data.")
    else:
        # Fastest-growing league (Δ7d, fallback Δ30d)
        _lg_deltas = []
        for lg, series in _by_lg.items():
            if len(series) < 2:
                continue
            # reshape as snapshot-like for delta()
            pseudo = [{"captured_date": d, "subscriber_count": v} for d, v in series]
            d7 = _gdelta_l1(pseudo, "subscriber_count", 7)
            d30 = _gdelta_l1(pseudo, "subscriber_count", 30)
            _lg_deltas.append({"league": lg, "latest": series[-1][1], "d7": d7, "d30": d30})

        has_any_d7 = any(x["d7"] is not None for x in _lg_deltas)
        key = "d7" if has_any_d7 else "d30"
        label = "Δ Subs 7d" if has_any_d7 else "Δ Subs 30d"
        _lg_deltas.sort(key=lambda x: x[key] or -10**18, reverse=True)

        kcols = st.columns(len(_lg_deltas))
        for i, row in enumerate(_lg_deltas):
            val = row[key]
            s = "–" if val is None else (f"+{fmt_num(val)}" if val >= 0 else f"{fmt_num(val)}")
            kcols[i].metric(f"{row['league']} — {label}", s, help=f"Current: {fmt_num(row['latest'])} subs")

        # Multi-line: total subs per league over time
        LEAGUE_COLORS = {
            "Serie A": "#0066CC", "Premier League": "#37003C", "La Liga": "#FF4B44",
            "Bundesliga": "#D3010C", "Ligue 1": "#091C3E", "MLS": "#001F5B",
        }
        import plotly.graph_objects as go
        fig_lg = go.Figure()
        for lg, series in _by_lg.items():
            if len(series) < 2:
                continue
            fig_lg.add_trace(go.Scatter(
                x=[d for d, _ in series], y=[v for _, v in series],
                mode="lines+markers", name=lg,
                line=dict(color=LEAGUE_COLORS.get(lg, "#AAAAAA"), width=2),
                hovertemplate=f"<b>{lg}</b><br>%{{x}}: %{{y:,.0f}} subs<extra></extra>",
            ))
        fig_lg.update_layout(
            title="Total subscribers per league (last 60 days)",
            height=360, margin=dict(t=40, b=20),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
            xaxis=dict(showgrid=False),
            yaxis=dict(showgrid=True, gridcolor="#262730"),
            legend=dict(orientation="h", y=-0.15),
        )
        st.plotly_chart(fig_lg, use_container_width=True)

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
        # All Leagues + scope (Leagues only / All clubs) — already filtered by scope
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

    _charts = [
        make_bar(df, "subscriber_count", "Total Subscribers per Channel"),
        make_bar(df, "total_views", "Total Views across All Videos"),
        make_bar(df, "views_per_sub", "Views per Subscriber (Engagement Efficiency)"),
        fig_vids,
        make_bar(df, "avg_views_per_video", "Average Views per Video"),
    ]
    # All Leagues → Leagues only: render 2 per row
    if league is None and _scope == "Leagues only":
        for _i in range(0, len(_charts), 2):
            _cols = st.columns(2)
            for _j, _f in enumerate(_charts[_i:_i+2]):
                _cols[_j].plotly_chart(_f, use_container_width=True)
    else:
        for _f in _charts:
            st.plotly_chart(_f, use_container_width=True)

    # ── Growth section (from channel_snapshots) ──────────────────
    from src.growth import group_by_channel as _gbc, delta as _gdelta
    _all_ids = [c["id"] for c in clubs_only]
    _since = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    _snap_rows = db.get_all_snapshots(since_date=_since)
    _snap_rows = [r for r in _snap_rows if r["channel_id"] in set(_all_ids)]
    _by_ch = _gbc(_snap_rows)

    # Build gainer rows
    _gainers = []
    for ch in clubs_only:
        snaps = _by_ch.get(ch["id"], [])
        if len(snaps) < 2:
            continue
        d7 = _gdelta(snaps, "subscriber_count", 7)
        d30 = _gdelta(snaps, "subscriber_count", 30)
        last_subs = snaps[-1].get("subscriber_count", 0) or 0
        _gainers.append({
            "name": ch["name"], "id": ch["id"],
            "handle": ch.get("handle", ""),
            "subs": last_subs, "d7": d7 or 0, "d30": d30 or 0,
            "has_d7": d7 is not None, "has_d30": d30 is not None,
        })

    st.subheader("Growth")
    if not _gainers:
        st.caption("Snapshot history not long enough yet — the daily cron will accumulate data. Come back in a day or two.")
    else:
        # Leaderboard: top 10 by Δ 7d (fallback Δ 30d if 7d not ready for anyone)
        has_any_d7 = any(g["has_d7"] for g in _gainers)
        sort_key = "d7" if has_any_d7 else "d30"
        label = "Δ Subs 7d" if has_any_d7 else "Δ Subs 30d"
        lead = sorted(_gainers, key=lambda g: g[sort_key], reverse=True)[:10]
        lead_rows = ""
        for i, g in enumerate(lead, 1):
            c1, c2 = dual_colors.get(g["name"], (color_map.get(g["name"], "#636EFA"), "#FFFFFF"))
            dot = f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative"><span style="display:block;width:7px;height:7px;border-radius:50%;background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>'
            delta_val = g[sort_key]
            sgn = "+" if delta_val >= 0 else ""
            col = "#00CC96" if delta_val > 0 else ("#EF553B" if delta_val < 0 else "#888")
            lead_rows += f"""<tr>
                <td style="padding:6px 12px;color:#888">{i}</td>
                <td style="padding:6px 12px">{dot}</td>
                <td style="padding:6px 12px">{g['name']}</td>
                <td style="padding:6px 12px;text-align:right">{fmt_num(g['subs'])}</td>
                <td style="padding:6px 12px;text-align:right;color:{col}">{sgn}{fmt_num(delta_val)}</td>
            </tr>"""
        components.html(f"""
        <style>
          .gainers {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                      font-family:"Source Sans Pro",sans-serif; }}
          .gainers th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
          .gainers td {{ border-bottom:1px solid #262730; }}
        </style>
        <table class="gainers"><thead><tr>
          <th>#</th><th></th><th>Club</th>
          <th style="text-align:right">Subscribers</th>
          <th style="text-align:right">{label}</th>
        </tr></thead><tbody>{lead_rows}</tbody></table>
        """, height=len(lead) * 37 + 80, scrolling=False)

        # Multi-line trajectory (last ~60 days), one line per club
        import plotly.graph_objects as go
        fig_traj = go.Figure()
        for ch in clubs_only:
            snaps = _by_ch.get(ch["id"], [])
            if len(snaps) < 2:
                continue
            c1, _ = dual_colors.get(ch["name"], (color_map.get(ch["name"], "#636EFA"), "#FFFFFF"))
            fig_traj.add_trace(go.Scatter(
                x=[s["captured_date"] for s in snaps],
                y=[s.get("subscriber_count", 0) or 0 for s in snaps],
                mode="lines", name=ch["name"],
                line=dict(color=c1, width=2),
                hovertemplate=f"<b>{ch['name']}</b><br>%{{x}}: %{{y:,.0f}} subs<extra></extra>",
            ))
        if fig_traj.data:
            fig_traj.update_layout(
                title="Subscriber trajectory (last 60 days)",
                height=380, margin=dict(t=40, b=20),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="#262730"),
                legend=dict(orientation="h", y=-0.15),
            )
            st.plotly_chart(fig_traj, use_container_width=True)

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

    # Club colors (never black on dark bg)
    def _safe_color(c, fallback):
        if not c:
            return fallback
        u = c.upper().strip()
        if u in ("#000000", "#000", "#111111", "#0A0A0A"):
            return fallback
        return c
    CLUB_C1 = _safe_color(channel.get("color"), "#636EFA")
    CLUB_C2 = _safe_color(channel.get("color2"), "#FFFFFF")
    if CLUB_C2.upper() == CLUB_C1.upper():
        CLUB_C2 = "#FFFFFF" if CLUB_C1.upper() != "#FFFFFF" else "#AAAAAA"

    from src.filters import render_club_header
    render_club_header(channel, all_channels)

    import plotly.graph_objects as go

    # ── KPI banner (same structure as All-Clubs banner) + inline ranks ───
    subs_ch = channel.get('subscriber_count', 0) or 0
    total_views_ch = channel.get("total_views", 0) or 0
    video_count_ch = channel.get("video_count", 0) or 0
    vps_ch = total_views_ch // max(subs_ch, 1)
    avg_vpv_ch = total_views_ch // max(video_count_ch, 1)

    # compute ranks across different metrics, vs league peers and vs all clubs
    from src.filters import get_league_for_channel as _get_lg
    _clubs = [c for c in all_channels if c.get("entity_type") != "League"]
    _ch_league = _get_lg(channel)
    _peers = [c for c in _clubs if _get_lg(c) == _ch_league]
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

    cols = st.columns(5)
    with cols[0]:
        st.metric("Total Subscribers", fmt_num(subs_ch))
        st.markdown(_rank_html("subs"), unsafe_allow_html=True)
    with cols[1]:
        st.metric("Total Views", fmt_num(total_views_ch))
        st.markdown(_rank_html("views"), unsafe_allow_html=True)
    with cols[2]:
        st.metric("Views/Sub", fmt_num(vps_ch))
        st.markdown(_rank_html("vps"), unsafe_allow_html=True)
    with cols[3]:
        st.metric("Total Videos", fmt_num(video_count_ch))
        st.markdown(_rank_html("videos"), unsafe_allow_html=True)
    with cols[4]:
        st.metric("Avg Views/Video", fmt_num(avg_vpv_ch))
        st.markdown(_rank_html("vpv"), unsafe_allow_html=True)

    # ── Content breakdown — donuts for lifetime format split ───
    import plotly.graph_objects as go
    long_n = channel.get("long_form_count", 0) or 0
    short_n = channel.get("shorts_count", 0) or 0
    live_n = channel.get("live_count", 0) or 0
    fmt_total = long_n + short_n + live_n

    if fmt_total > 0:
        fig = go.Figure(go.Pie(
            labels=["Long", "Shorts", "Live"],
            values=[long_n, short_n, live_n],
            marker=dict(colors=[CLUB_C1, CLUB_C2, "#FFA15A"]),
            hole=0.55,
            textinfo="label+percent", textposition="inside",
            hovertemplate="%{label}: %{value:,.0f}<extra></extra>",
        ))
        fig.update_layout(
            title=dict(text="Videos published (lifetime)", x=0.5),
            showlegend=False, height=320,
            margin=dict(t=40, b=20, l=20, r=20),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
            annotations=[dict(text=fmt_num(fmt_total), x=0.5, y=0.5, showarrow=False,
                              font=dict(size=18, color="#FAFAFA"))],
        )
        st.plotly_chart(fig, use_container_width=True)

    # ── Growth (from channel_snapshots) ─────────────────────────
    from src.growth import delta as _gdelta, delta_since as _gdelta_since, days_covered as _gdays
    _snaps = db.get_channel_snapshots(channel["id"])

    st.subheader("Growth")
    if not _snaps or len(_snaps) < 2:
        st.caption(
            "Growth tracking starts now — the daily snapshot job has "
            f"{len(_snaps)} data point{'s' if len(_snaps) != 1 else ''}. "
            "Come back tomorrow for the first delta."
        )
    else:
        def _sgn(n):
            if n is None:
                return "–"
            s = "+" if n >= 0 else ""
            return f"{s}{fmt_num(n)}"
        d7 = _gdelta(_snaps, "subscriber_count", 7)
        d30 = _gdelta(_snaps, "subscriber_count", 30)
        dssn = _gdelta_since(_snaps, "subscriber_count", "2025-08-01")
        v7 = _gdelta(_snaps, "total_views", 7)
        v30 = _gdelta(_snaps, "total_views", 30)
        vssn = _gdelta_since(_snaps, "total_views", "2025-08-01")

        gcols = st.columns(6)
        gcols[0].metric("Subs Δ 7d", _sgn(d7))
        gcols[1].metric("Subs Δ 30d", _sgn(d30))
        gcols[2].metric("Subs since season", _sgn(dssn))
        gcols[3].metric("Views Δ 7d", _sgn(v7))
        gcols[4].metric("Views Δ 30d", _sgn(v30))
        gcols[5].metric("Views since season", _sgn(vssn))

        # Sparkline: subscriber trajectory
        dates = [s["captured_date"] for s in _snaps]
        subs_series = [s.get("subscriber_count", 0) or 0 for s in _snaps]
        fig_g = go.Figure(go.Scatter(
            x=dates, y=subs_series, mode="lines+markers",
            line=dict(color=CLUB_C1, width=2),
            marker=dict(size=6, color=CLUB_C1),
            hovertemplate="%{x}: %{y:,.0f} subs<extra></extra>",
        ))
        fig_g.update_layout(
            title=dict(text=f"Subscriber trajectory ({_gdays(_snaps)} days tracked)", x=0.02),
            height=260, margin=dict(t=40, b=20, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
            xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="#262730"),
        )
        st.plotly_chart(fig_g, use_container_width=True)

    st.caption("See **Top Videos** for the all-time top 100 and **Season 25/26** for current-season activity.")
