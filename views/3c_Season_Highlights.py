"""Season Highlights — match-highlight videos this season, Top-5 only.

A highlight = a video the theme classifier tagged "Highlights": its
title says highlights / sintesi / resumen / Zusammenfassung / résumé …
(src.analytics._THEME_RULES). Score-only titles ("Inter 2-1 Milan")
are not counted — that bucket also holds goal clips and reactions.

Data: the nightly 'season_highlights' dashboard_cache row written by
src.season_compute (same scan as the other Season caches), including
each channel's top 20 highlights, merged here for the current scope.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src import theme as _T
from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src.channels import COUNTRY_TO_LEAGUE, current_season_label_safe
from src.charts import chart_title, readable_hover
from src.database import Database
from src.filters import (
    get_channels_for_filter, get_global_channels, get_global_filter,
    get_include_league, is_top5_cohort, render_club_header,
    render_league_header, render_page_subtitle,
)
from src.season_top import render_top_season_videos_table

load_dotenv()
require_login()

st.title(f"Highlights ({current_season_label_safe()})")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or _cached_channels(db)
ch_by_id = {c["id"]: c for c in all_channels}
league, club = get_global_filter()


def _league_of(c: dict) -> str:
    return COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip(), "")


_DEF = ("Match highlights posted this season — videos whose title says "
        "highlights, sintesi, resumen, Zusammenfassung, résumé…")

# ── Scope (same zoom levels as Season / Season Top) ──────────
if club is not None:
    render_club_header(club, all_channels)
    render_page_subtitle(f"{_DEF} — {club['name']}", updated_raw=None)
    scope = [club]
elif league is not None:
    league_channels = get_channels_for_filter(all_channels, league)
    render_league_header(league, channels_in_scope=league_channels)
    render_page_subtitle(f"{_DEF} — {league}", updated_raw=None)
    scope = [c for c in league_channels if is_top5_cohort(c)
             and (get_include_league() or c.get("entity_type") != "League")]
else:
    render_page_subtitle(f"{_DEF} — all Top 5 leagues", updated_raw=None)
    scope = [c for c in all_channels if is_top5_cohort(c)]

_row = _cached_dc_read(db, "season_highlights", "all")
payload = (_row or {}).get("payload") or {}
per = payload.get("per_channel") or {}
if not per:
    st.info("Highlights are computed nightly — the first numbers appear "
            "after the next season refresh.")
    st.stop()

ids = [c["id"] for c in scope if c["id"] in per]
if not ids:
    st.caption("No channels in scope.")
    st.stop()

# ── KPIs ─────────────────────────────────────────────────────
n = sum(per[i]["n"] for i in ids)
views = sum(per[i]["views"] for i in ids)
season_views = sum(per[i]["season_views"] for i in ids)
short_n = sum(per[i]["short_n"] for i in ids)
st.markdown(kpi_row([
    ("Highlight videos", fmt_num(n)),
    ("Highlight views", fmt_num(views)),
    ("Share of season views", f"{views / max(season_views, 1) * 100:.1f}%"),
    ("Avg views / highlight", fmt_num(views // max(n, 1))),
    ("Shorts cuts", f"{short_n / max(n, 1) * 100:.0f}%"),
]), unsafe_allow_html=True)

if n == 0:
    st.caption("No highlight videos this season in this scope.")
    st.stop()

# ── By league (all-leagues view only) ────────────────────────
if club is None and league is None:
    agg: dict[str, list[int]] = {}
    for i in ids:
        a = agg.setdefault(_league_of(ch_by_id[i]), [0, 0, 0])
        a[0] += per[i]["n"]; a[1] += per[i]["views"]; a[2] += per[i]["season_views"]
    lg_df = pd.DataFrame(
        [(lg, a[0], a[1], a[1] / max(a[2], 1) * 100) for lg, a in agg.items() if lg],
        columns=["League", "Highlights", "Views", "Share"],
    ).sort_values("Share", ascending=False)
    st.subheader("By league")
    st.caption("How much of each league's YouTube views this season come from "
               "highlights — broadcast rights decide who can post them.")
    c1, c2 = st.columns(2)
    with c1:
        chart_title("Share of season views from highlights")
        fig = go.Figure(go.Bar(
            x=lg_df["League"], y=lg_df["Share"], marker_color=_T.ACCENT,
            text=[f"{s:.1f}%" for s in lg_df["Share"]], textposition="outside",
            cliponaxis=False,
            hovertemplate="%{x}: %{y:.1f}% of season views<extra></extra>"))
        fig.update_layout(height=320, margin=dict(t=20, b=10, l=10, r=10),
                          yaxis=dict(ticksuffix="%", rangemode="tozero"))
        st.plotly_chart(fig, width="stretch")
    with c2:
        chart_title("Highlight views")
        _v = lg_df.sort_values("Views", ascending=False)
        fig = go.Figure(go.Bar(
            x=_v["League"], y=_v["Views"], marker_color=_T.ACCENT,
            text=[fmt_num(int(v)) for v in _v["Views"]], textposition="outside",
            cliponaxis=False,
            customdata=_v["Highlights"],
            hovertemplate="%{x}: %{y:,} views · %{customdata} highlights<extra></extra>"))
        fig.update_layout(height=320, margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(fig, width="stretch")

# ── Weekly trend ─────────────────────────────────────────────
wk: dict[str, list[int]] = {}
for i in ids:
    for w, (cnt, v) in (per[i].get("weekly") or {}).items():
        a = wk.setdefault(w, [0, 0]); a[0] += cnt; a[1] += v
if wk:
    st.subheader("Week by week")
    st.caption("Highlight views by the week they were posted (weeks start Monday) "
               "— the matchday rhythm, with dips at international breaks. "
               "Recent weeks have had less time to collect views.")
    wdf = pd.DataFrame([(pd.Timestamp(w), a[0], a[1]) for w, a in wk.items()],
                       columns=["Week", "Highlights", "Views"]).sort_values("Week")
    fig = go.Figure(go.Bar(
        x=wdf["Week"], y=wdf["Views"], marker_color=_T.ACCENT,
        customdata=wdf["Highlights"],
        hovertemplate="Week of %{x|%d %b}<br>%{y:,} views · %{customdata} highlights<extra></extra>"))
    fig.update_layout(height=320, margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, width="stretch")

# ── Per channel ──────────────────────────────────────────────
if club is None:
    st.subheader("Clubs")
    st.caption("Click a column header to sort.")
    rows = []
    for i in ids:
        g, c = per[i], ch_by_id[i]
        if not g["n"]:
            continue
        rows.append({
            "Channel": c.get("name", "?") + (" (league)" if c.get("entity_type") == "League" else ""),
            "League": _league_of(c),
            "Highlights": g["n"],
            "Views": g["views"],
            "Avg views": g["views"] // max(g["n"], 1),
            "% of channel views": round(g["views"] / max(g["season_views"], 1) * 100, 1),
            "Shorts %": round(g["short_n"] / max(g["n"], 1) * 100),
        })
    if rows:
        tdf = pd.DataFrame(rows).sort_values("Views", ascending=False)
        if league is not None:
            tdf = tdf.drop(columns=["League"])
        st.dataframe(
            tdf, hide_index=True, width="stretch",
            height=min(38 * len(tdf) + 40, 720),
            column_config={
                "Views": st.column_config.NumberColumn(format="localized"),
                "Avg views": st.column_config.NumberColumn(format="localized"),
                "% of channel views": st.column_config.NumberColumn(format="%.1f%%"),
                "Shorts %": st.column_config.NumberColumn(format="%d%%"),
            },
        )
    _none = [ch_by_id[i].get("name", "?") for i in ids if not per[i]["n"]]
    if _none:
        st.caption(f"No highlights this season: {', '.join(sorted(_none))}.")

# ── Top highlight videos ─────────────────────────────────────
# Merge each in-scope channel's stored top 20. Exact unless one channel
# alone holds more than 20 of the scope's top 50.
top = sorted((v for i in ids for v in per[i].get("top") or []),
             key=lambda v: -int(v.get("view_count") or 0))[:50]
if top:
    render_top_season_videos_table(
        top, ch_by_id, header=f"Top {len(top)} highlights by views",
        order_by="views", max_height=900,
    )
