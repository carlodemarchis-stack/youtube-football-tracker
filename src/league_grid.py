"""League channel grid — a hidden, link-reached view (no menu entry).

Reached from the Season (Z2) page via `?view=league-grid` — same
query-param pattern as the Latest "feed" mode, so it never shows in the
sidebar nav. Renders the "Videos per day" bar chart for every channel in
the selected league, stacked in a list ordered by total season videos,
to eyeball each club's publishing rhythm side by side.
"""
from __future__ import annotations

import urllib.parse as _up
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.database import _fetch_all
from src.channels import COUNTRY_TO_LEAGUE, get_season_since


@st.cache_data(ttl=900, show_spinner=False)
def _load(_db, ids_tuple: tuple[str, ...], since: str) -> dict:
    """{channel_id: {date: count}} of videos published since season start,
    for the league's channels. One paginated scan, bucketed in Python."""
    if not ids_tuple:
        return {}
    rows = _fetch_all(
        _db.client.table("videos")
        .select("channel_id,published_at")
        .gte("published_at", since)
        .in_("channel_id", list(ids_tuple))
    )
    per: dict[str, Counter] = {}
    for r in rows:
        cid = r.get("channel_id")
        pa = r.get("published_at") or ""
        if not cid or not pa:
            continue
        try:
            d = pd.to_datetime(pa, utc=True).date()
        except Exception:
            continue
        per.setdefault(cid, Counter())[d] += 1
    return {cid: dict(c) for cid, c in per.items()}


def _chart(dates: list, vals: list[int], avg: float) -> go.Figure:
    """The per-channel videos-per-day bar (weekends orange + avg line) —
    same style as the Season page's Z3 club chart, compact for stacking."""
    colors = ["#FFA15A" if d.weekday() >= 5 else "#636EFA" for d in dates]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=vals, marker_color=colors,
        hovertemplate="<b>%{x|%a %b %d, %Y}</b><br>"
                      "%{y} video(s)<extra></extra>",
    ))
    fig.add_hline(y=avg, line_dash="dash", line_color="rgba(255,255,255,0.35)",
                  annotation_text=f"avg {avg:.1f}",
                  annotation_position="top right",
                  annotation_font_color="rgba(255,255,255,0.55)")
    fig.update_layout(
        height=240,
        xaxis=dict(title="", showgrid=False),
        yaxis=dict(title="", showgrid=True, gridcolor="rgba(255,255,255,0.08)"),
        margin=dict(t=10, b=30, l=50, r=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"), showlegend=False, bargap=0.15,
    )
    return fig


def render_league_grid(db, league: str, channels: list[dict]) -> None:
    st.title(f"📊 {league} — videos per day, all channels")
    st.markdown(
        f"<a href='?league={_up.quote(league)}' target='_self' "
        f"style='color:#58A6FF;text-decoration:none'>← Back to Season</a>",
        unsafe_allow_html=True,
    )

    league_ch = [
        c for c in channels
        if c.get("entity_type") in ("Club", "League")
        and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip()) == league
        and c.get("id")
    ]
    if not league_ch:
        st.info(f"No channels found for {league}.")
        return

    since = get_season_since(league=league)
    per = _load(db, tuple(sorted(c["id"] for c in league_ch)), since)
    name_of = {c["id"]: (c.get("name") or "?") for c in league_ch}
    totals = {c["id"]: sum((per.get(c["id"]) or {}).values()) for c in league_ch}
    ordered = sorted(league_ch, key=lambda c: -totals.get(c["id"], 0))

    st.caption(
        f"{len(ordered)} channels · videos published per day since "
        f"{since[:10]} · weekends in orange. Ordered by total season videos."
    )

    for c in ordered:
        cid = c["id"]
        cnt = per.get(cid) or {}
        st.markdown(f"**{name_of[cid]}** · {totals.get(cid, 0):,} videos")
        if not cnt:
            st.caption("No videos this season.")
            continue
        dates = sorted(cnt.keys())
        vals = [cnt[d] for d in dates]
        avg = sum(vals) / len(vals) if vals else 0
        st.plotly_chart(_chart(dates, vals, avg), width="stretch",
                        key=f"vpd_grid_{cid}")
        st.markdown("<hr style='border:none;border-top:1px solid #2a2c34;"
                    "margin:4px 0 14px 0'>", unsafe_allow_html=True)
