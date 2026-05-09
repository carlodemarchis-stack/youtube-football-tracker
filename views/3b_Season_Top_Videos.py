"""Season Top Videos — three ranked tables (views / likes / comments)
for the current global filter.

Was inlined at the bottom of every zoom on Season 25/26; split out so
the rhythm-and-cadence Season page stays focused, and the rankings get
their own URL / nav slot.
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src.filters import (
    get_global_filter, get_global_channels, get_channels_for_filter,
    get_league_for_channel, get_include_league, get_all_leagues_scope,
    render_page_subtitle, render_club_header, render_league_header,
    get_global_color_map,
)
from src.channels import get_season_since
from src.auth import require_login
from src.season_top import (
    fetch_top_season_videos,
    render_top_season_videos_table,
)
from src.analytics import fmt_num

load_dotenv()
require_login()

st.title("Season Top")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or _cached_channels(db)
if not all_channels:
    st.warning("No channel data yet.")
    st.stop()

league, club = get_global_filter()
SEASON_SINCE = get_season_since(channel=club, league=league)

# Channel lookup used by the renderer for badge / club name.
ch_by_id = {c["id"]: c for c in all_channels}


# ── Z3: single club ───────────────────────────────────────────
if club is not None:
    render_club_header(club, all_channels)
    render_page_subtitle(f"Top videos this season for {club['name']}",
                         updated_raw=None)
    _ids = [club["id"]]
    _label = club["name"]

# ── Z2: one league ────────────────────────────────────────────
elif league is not None:
    league_channels = get_channels_for_filter(all_channels, league)
    render_league_header(league, channels_in_scope=league_channels)
    render_page_subtitle(f"Top videos this season across {league}",
                         updated_raw=None)
    # Respect the per-league "All Clubs + League" toggle (matches the
    # behavior on the main Season page so the two pages tell the same
    # story under the same filter).
    if get_include_league():
        scope = [c for c in league_channels
                 if c.get("entity_type") not in
                    ("Player", "Federation", "OtherClub", "WomenClub")]
    else:
        scope = [c for c in league_channels
                 if c.get("entity_type") not in
                    ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    _ids = [c["id"] for c in scope]
    _label = league

# ── Z1: all leagues (respect the All-Leagues scope toggle) ────
else:
    _scope_label = get_all_leagues_scope()
    render_page_subtitle("Top videos this season across all leagues",
                         updated_raw=None)
    # On Season the Z1 "Overall" path always includes league channels,
    # so we mirror that here for consistency.
    if _scope_label == "Leagues only":
        scope = [c for c in all_channels if c.get("entity_type") == "League"]
    elif _scope_label == "All clubs":
        scope = [c for c in all_channels
                 if c.get("entity_type") not in
                    ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    else:
        scope = [c for c in all_channels
                 if c.get("entity_type") not in
                    ("Player", "Federation", "OtherClub", "WomenClub")]
    _ids = [c["id"] for c in scope]
    _label = "All Leagues"


if not _ids:
    st.caption("No channels in scope.")
    st.stop()

# ── Fetch the three datasets up front ─────────────────────────
# Done once here so the KPI bar can compute aggregates from the
# same top-100 set the ranked table renders below — no duplicate
# query.
#
# Cache-first read: dashboard_cache holds precomputed payloads for
# Z1 + Z2, refreshed by daily_refresh / hourly_rss. Read path becomes
# one ~80KB JSON pull from dashboard_cache — no live aggregation.
# On cache miss (Z3, or first deploy before the next cron) we fall
# back to the live query path.
_z1 = (club is None and league is None)
_excluded = None
if _z1:
    _excluded = [c["id"] for c in all_channels
                 if c.get("entity_type") in
                    ("Player", "Federation", "OtherClub", "WomenClub")]

top_views = top_likes = top_comments = None

# Try cache for Z1 / Z2. Z3 (single club) doesn't have a cache entry —
# the per-club query is already fast (1-element IN), so live is fine.
if club is None:
    from src import dashboard_cache as _dc
    _scope_key = _dc.scope_league(league) if league else _dc.scope_all()
    _cached = _cached_dc_read(db, "season_top", _scope_key)
    _payload = (_cached or {}).get("payload") or {}
    if _payload.get("top_views"):
        top_views = _payload.get("top_views") or []
        top_likes = _payload.get("top_likes") or []
        top_comments = _payload.get("top_comments") or []

# Cache miss → live query (Z3, or first deploy / new scope).
if top_views is None:
    top_views = fetch_top_season_videos(db, _ids, SEASON_SINCE,
                                        limit=100, order_by="view_count",
                                        excluded_channel_ids=_excluded)
    top_likes = fetch_top_season_videos(db, _ids, SEASON_SINCE,
                                        limit=5, order_by="like_count",
                                        excluded_channel_ids=_excluded)
    top_comments = fetch_top_season_videos(db, _ids, SEASON_SINCE,
                                           limit=5, order_by="comment_count",
                                           excluded_channel_ids=_excluded)

# ── KPI bar (computed from the top-20-by-views set) ───────────
if top_views:
    import pandas as _pd
    _total = sum(int(v.get("view_count") or 0) for v in top_views)
    _n = len(top_views)
    _avg = _total // _n if _n else 0
    _top1 = int(top_views[0].get("view_count") or 0)
    _top1_share = (_top1 / _total * 100) if _total else 0.0
    _now_utc = _pd.Timestamp.now(tz="UTC")
    _ages = (_now_utc - _pd.to_datetime(
        [v.get("published_at") for v in top_views],
        utc=True, errors="coerce",
    )).total_seconds() / 86400.0
    _ages = [a for a in _ages.tolist() if not _pd.isna(a)]
    _avg_age_days = (sum(_ages) / len(_ages)) if _ages else 0.0
    # # of clubs represented in the top 20 — concentration signal at
    # Z1/Z2; collapses to 1 at Z3 so we hide the card there.
    _ch_in_top = len({v.get("channel_id") for v in top_views
                      if v.get("channel_id")})

    def _card(label, value, color):
        return (f'<div style="background:#1a1c24;border-radius:6px;padding:10px 14px;'
                f'border-left:3px solid {color}">'
                f'<div style="color:#888;font-size:11px;font-weight:600;'
                f'text-transform:uppercase;letter-spacing:0.5px">{label}</div>'
                f'<div style="color:#FAFAFA;font-size:22px;font-weight:700;'
                f'margin-top:2px">{value}</div></div>')

    cards = [
        _card(f"Total views (top {_n})", fmt_num(_total),       "#58A6FF"),
        _card("Avg views / video",       fmt_num(_avg),         "#FFA15A"),
        _card("Top 1 share",             f"{_top1_share:.1f}%", "#00CC96"),
        _card("Avg age",                 f"{_avg_age_days:.0f}d", "#EF553B"),
    ]
    # 5th card depends on zoom:
    # - Z1/Z2: # of channels represented in the top set
    # - Z3:    inline Long/Shorts(/Live) breakdown — channels-count
    #          would always read "1" at this zoom.
    if club is None:
        cards.append(_card(f"Channels in top {_n}", str(_ch_in_top), "#AB63FA"))
    else:
        # Format-counts inline. Pre-compute fmt_counts here (the pie
        # block below also computes it but only inside its own scope).
        _fmt_n = {"long": 0, "short": 0, "live": 0}
        for v in top_views:
            f = (v.get("format") or "").lower()
            if f not in _fmt_n:
                f = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
            _fmt_n[f] = _fmt_n.get(f, 0) + 1
        _segs = [
            f'<span style="color:#636EFA">{_fmt_n["long"]}</span>',
            f'<span style="color:#00CC96">{_fmt_n["short"]}</span>',
        ]
        _fmt_label = f"Format L/S (top {_n})"
        if _fmt_n["live"]:
            _segs.append(f'<span style="color:#FFA15A">{_fmt_n["live"]}</span>')
            _fmt_label = f"Format L/S/Lv (top {_n})"
        _fmt_value = '<span style="color:#666"> / </span>'.join(_segs)
        cards.append(_card(_fmt_label, _fmt_value, "#636EFA"))

    n_cols = len(cards)
    st.markdown(
        f'<div style="display:grid;grid-template-columns:repeat({n_cols},1fr);'
        f'gap:10px;margin:4px 0 14px 0">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )

# ── Pie charts: format mix (always) + league mix (Z1 only) ────
if top_views:
    import plotly.graph_objects as _go_pie
    from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_COLOR

    def _fmt_of(v):
        f = (v.get("format") or "").lower()
        if f in ("long", "short", "live"):
            return f
        return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"

    _FMT_COLOR = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}
    _FMT_LABEL = {"long": "Long", "short": "Shorts", "live": "Live"}

    fmt_counts: dict[str, int] = {}
    for v in top_views:
        f = _fmt_of(v)
        fmt_counts[f] = fmt_counts.get(f, 0) + 1
    fmt_labels = [_FMT_LABEL[f] for f in fmt_counts]
    fmt_values = list(fmt_counts.values())
    fmt_colors = [_FMT_COLOR[f] for f in fmt_counts]

    fig_fmt = _go_pie.Figure()
    fig_fmt.add_trace(_go_pie.Pie(
        labels=fmt_labels, values=fmt_values,
        marker=dict(colors=fmt_colors, line=dict(color="#0E1117", width=2)),
        hole=0.45, textinfo="label+percent",
        hovertemplate="<b>%{label}</b><br>%{value} videos · %{percent}<extra></extra>",
    ))
    fig_fmt.update_layout(
        title=dict(text=f"Format mix (top {len(top_views)})", x=0.5,
                   font=dict(color="#FAFAFA", size=14)),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"),
        margin=dict(t=40, b=10, l=10, r=10),
        height=320, showlegend=False,
    )

    if club is None and league is None:
        # Z1: break down the top-100 by league two ways:
        #   1) by count of videos
        #   2) by sum of views (often a different story — one viral hit
        #      can flip the league weighting)
        lg_counts: dict[str, int] = {}
        lg_views: dict[str, int] = {}
        for v in top_views:
            ch = ch_by_id.get(v.get("channel_id")) or {}
            country = (ch.get("country") or "").upper()
            lg = COUNTRY_TO_LEAGUE.get(country) or "Other"
            lg_counts[lg] = lg_counts.get(lg, 0) + 1
            lg_views[lg] = lg_views.get(lg, 0) + int(v.get("view_count") or 0)

        def _lg_pie(values_by_lg: dict[str, int], title: str,
                     hover_unit: str) -> "_go_pie.Figure":
            sorted_items = sorted(values_by_lg.items(),
                                  key=lambda kv: kv[1], reverse=True)
            labels = [k for k, _ in sorted_items]
            vals = [v for _, v in sorted_items]
            colors = [LEAGUE_COLOR.get(k, "#888") for k in labels]
            f = _go_pie.Figure()
            f.add_trace(_go_pie.Pie(
                labels=labels, values=vals,
                marker=dict(colors=colors, line=dict(color="#0E1117", width=2)),
                hole=0.45, textinfo="label+percent", sort=False,
                hovertemplate=("<b>%{label}</b><br>%{value:,} "
                               + hover_unit + " · %{percent}<extra></extra>"),
            ))
            f.update_layout(
                title=dict(text=title, x=0.5,
                           font=dict(color="#FAFAFA", size=14)),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                margin=dict(t=40, b=10, l=10, r=10),
                height=320, showlegend=False,
            )
            return f

        fig_lg_n = _lg_pie(lg_counts,
                           f"League mix — by count (top {len(top_views)})",
                           "videos")
        fig_lg_v = _lg_pie(lg_views,
                           f"League mix — by views (top {len(top_views)})",
                           "views")

        col_fmt, col_lg_n, col_lg_v = st.columns(3)
        col_fmt.plotly_chart(fig_fmt, use_container_width=True)
        col_lg_n.plotly_chart(fig_lg_n, use_container_width=True)
        col_lg_v.plotly_chart(fig_lg_v, use_container_width=True)
    elif league is not None and club is None:
        # Z2: format pie + same count/views breakdown but grouped by
        # channel within the league (instead of by league).
        ch_counts: dict[str, int] = {}
        ch_views: dict[str, int] = {}
        for v in top_views:
            ch = ch_by_id.get(v.get("channel_id")) or {}
            ch_name = ch.get("name") or v.get("channel_name") or "Unknown"
            ch_counts[ch_name] = ch_counts.get(ch_name, 0) + 1
            ch_views[ch_name] = ch_views.get(ch_name, 0) + int(v.get("view_count") or 0)

        _color_map_local = get_global_color_map() or {}

        def _ch_pie(values_by_ch: dict[str, int], title: str,
                     hover_unit: str) -> "_go_pie.Figure":
            sorted_items = sorted(values_by_ch.items(),
                                  key=lambda kv: kv[1], reverse=True)
            labels = [k for k, _ in sorted_items]
            vals = [v for _, v in sorted_items]
            colors = [_color_map_local.get(k, "#888") for k in labels]
            f = _go_pie.Figure()
            f.add_trace(_go_pie.Pie(
                labels=labels, values=vals,
                marker=dict(colors=colors, line=dict(color="#0E1117", width=2)),
                hole=0.45, textinfo="label+percent", sort=False,
                hovertemplate=("<b>%{label}</b><br>%{value:,} "
                               + hover_unit + " · %{percent}<extra></extra>"),
            ))
            f.update_layout(
                title=dict(text=title, x=0.5,
                           font=dict(color="#FAFAFA", size=14)),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#FAFAFA"),
                margin=dict(t=40, b=10, l=10, r=10),
                height=320, showlegend=False,
            )
            return f

        fig_ch_n = _ch_pie(ch_counts,
                           f"Club mix — by count (top {len(top_views)})",
                           "videos")
        fig_ch_v = _ch_pie(ch_views,
                           f"Club mix — by views (top {len(top_views)})",
                           "views")

        col_fmt, col_ch_n, col_ch_v = st.columns(3)
        col_fmt.plotly_chart(fig_fmt, use_container_width=True)
        col_ch_n.plotly_chart(fig_ch_n, use_container_width=True)
        col_ch_v.plotly_chart(fig_ch_v, use_container_width=True)
    # Z3 falls through here with nothing to render — the format
    # breakdown is already inline in the KPI bar above.

# ── Render the three ranked tables ────────────────────────────
render_top_season_videos_table(top_views, ch_by_id,
                               header=f"🏆 Top Season Videos — {_label}",
                               max_height=900)  # ~10 rows visible, scroll for the rest
render_top_season_videos_table(top_likes, ch_by_id,
                               header=f"❤️ Top Liked Videos — {_label}")
render_top_season_videos_table(top_comments, ch_by_id,
                               header=f"💬 Top Commented Videos — {_label}")
