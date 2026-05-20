"""30-Day Trends — trailing-30-day cohort view of publishing output and
per-video view growth across the global filter scope.

Sits between Latest Videos and Season. Reuses the chart geometry from
Daily Recap so the page feels familiar, but anchors to a fixed 30-day
window relative to "today" (CET) rather than a picker date.

Data sources:
  - video_daily_deltas — summed Δ views per day for videos in the cohort
  - videos.published_at + format — Long/Shorts/Live counts per day

The Δ-views series leverages the gap-filled video_daily_deltas so the
30-day curve has no synthetic holes from API batch misses (see today's
backfill: 6,066 single-day fills across the top-5 leagues).
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
import altair as alt
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_filter, render_page_subtitle,
)
from src.channels import COUNTRY_TO_LEAGUE
from src import theme as _T

load_dotenv()
require_login()

st.title("📈 30-Day Trends")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()
db = Database(SUPABASE_URL, SUPABASE_KEY)

from src.cached_db import (
    get_all_channels as _cached_channels,
    get_last_fetch_time as _cached_last_fetch,
)

CET = ZoneInfo("Europe/Rome")
LOOKBACK_DAYS = 30

all_channels = get_global_channels() or _cached_channels(db)
# Same cohort policy as Latest Videos / Daily Recap: clubs + leagues only.
# Tangential entities (players, federations, governing bodies, other/women
# clubs) live on their own pages and would muddy the headline trends.
all_channels = [
    c for c in all_channels
    if c.get("entity_type") not in ("Player", "Federation",
                                    "GoverningBody",
                                    "OtherClub", "WomenClub")
]

g_league, g_club = get_global_filter()
ONE_CLUB = g_club is not None

if ONE_CLUB:
    from src.filters import render_club_header
    render_club_header(g_club, all_channels)
    ch_ids = [g_club["id"]]
elif g_league:
    ch_ids = [
        c["id"] for c in all_channels
        if c.get("entity_type") == "League" and c.get("name") == g_league
    ] + [
        c["id"] for c in all_channels
        if c.get("entity_type") == "Club"
        and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip()) == g_league
    ]
else:
    ch_ids = [c["id"] for c in all_channels]

render_page_subtitle(
    f"Trailing {LOOKBACK_DAYS} days · {len(ch_ids)} channels in scope",
    updated_raw=_cached_last_fetch(db, "daily_snapshot"),
)

if not ch_ids:
    st.info("No channels in the current filter scope.")
    st.stop()

today_cet = datetime.now(CET).date()
# Trend window: [today-30, today) — exclude today since it's partial.
start_d = today_cet - timedelta(days=LOOKBACK_DAYS)
end_d   = today_cet  # exclusive
window_dates = [
    (start_d + timedelta(days=i)).isoformat()
    for i in range(LOOKBACK_DAYS)
]


# ── Δ views per day (from video_daily_deltas, summed over cohort) ──
@st.cache_data(ttl=1800, show_spinner=False)
def _load_view_deltas(channel_ids_tuple: tuple[str, ...],
                      since: str, until: str) -> dict[str, int]:
    """Return {captured_date: Σ view_delta} for videos whose channel_id is
    in the cohort. Paginated to bypass Supabase's 1000-row default."""
    sums: dict[str, int] = {}
    PAGE = 1000
    cid_list = list(channel_ids_tuple)
    # Chunk channel_ids so the in_() filter stays under URL-length limits.
    for cs in range(0, len(cid_list), 50):
        chunk = cid_list[cs:cs + 50]
        offset = 0
        while True:
            rs = (db.client.table("video_daily_deltas")
                  .select("captured_date,view_delta")
                  .in_("channel_id", chunk)
                  .gte("captured_date", since)
                  .lt("captured_date", until)
                  .order("captured_date")
                  .range(offset, offset + PAGE - 1)
                  .execute()).data or []
            for r in rs:
                d = r["captured_date"]
                sums[d] = sums.get(d, 0) + int(r.get("view_delta") or 0)
            if len(rs) < PAGE:
                break
            offset += PAGE
    return sums


# ── New uploads per day by format (from videos.published_at) ──
@st.cache_data(ttl=1800, show_spinner=False)
def _load_format_counts(channel_ids_tuple: tuple[str, ...],
                        start_iso: str, end_iso: str) -> dict[str, dict[str, int]]:
    """Return {captured_date (CET): {long, short, live}} for videos published
    in [start_iso, end_iso) belonging to cohort channels."""
    out: dict[str, dict[str, int]] = {}
    PAGE = 1000
    cid_list = list(channel_ids_tuple)
    for cs in range(0, len(cid_list), 50):
        chunk = cid_list[cs:cs + 50]
        offset = 0
        while True:
            rs = (db.client.table("videos")
                  .select("published_at,format,duration_seconds")
                  .in_("channel_id", chunk)
                  .gte("published_at", start_iso)
                  .lt("published_at", end_iso)
                  .order("published_at")
                  .range(offset, offset + PAGE - 1)
                  .execute()).data or []
            for v in rs:
                pub = v.get("published_at") or ""
                try:
                    dt_cet = datetime.fromisoformat(
                        pub.replace("Z", "+00:00")
                    ).astimezone(CET)
                except Exception:
                    continue
                dkey = dt_cet.date().isoformat()
                fmt = (v.get("format") or "").lower()
                if fmt not in ("long", "short", "live"):
                    fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
                bucket = out.setdefault(dkey, {"long": 0, "short": 0, "live": 0})
                bucket[fmt] += 1
            if len(rs) < PAGE:
                break
            offset += PAGE
    return out


_cids_tuple = tuple(sorted(ch_ids))

# Pull both series for the window.
view_deltas_by_date = _load_view_deltas(_cids_tuple,
                                        since=start_d.isoformat(),
                                        until=end_d.isoformat())

# Format-count window is CET-bucketed but the query filter is UTC-iso,
# so widen the UTC window by a day on each side to catch CET-bucketed
# borderline videos without losing them.
_fmt_start_utc = (datetime.combine(start_d - timedelta(days=1),
                                   datetime.min.time(), tzinfo=CET)
                  .astimezone(timezone.utc).isoformat())
_fmt_end_utc = (datetime.combine(end_d + timedelta(days=1),
                                 datetime.min.time(), tzinfo=CET)
                .astimezone(timezone.utc).isoformat())
fmt_by_date = _load_format_counts(_cids_tuple, _fmt_start_utc, _fmt_end_utc)
# Trim format counts back to the actual window
fmt_by_date = {d: v for d, v in fmt_by_date.items() if d in window_dates}

# Build dataframes pre-seeded with all dates so missing days render as 0
# rather than visually disappearing.
trend_rows = [
    {"Date": d, "Δ Views": view_deltas_by_date.get(d, 0)}
    for d in window_dates
]
trend_df = pd.DataFrame(trend_rows)

fmt_rows: list[dict] = []
for d in window_dates:
    b = fmt_by_date.get(d, {"long": 0, "short": 0, "live": 0})
    for label, key in (("Long", "long"), ("Shorts", "short"), ("Live", "live")):
        fmt_rows.append({"Date": d, "Format": label, "New Videos": b[key]})
fmt_df = pd.DataFrame(fmt_rows)

# KPI row — totals across the 30 days
total_dv = sum(v for v in view_deltas_by_date.values())
total_new = sum(b["long"] + b["short"] + b["live"]
                for b in fmt_by_date.values())
total_long = sum(b["long"] for b in fmt_by_date.values())
total_short = sum(b["short"] for b in fmt_by_date.values())
total_live = sum(b["live"] for b in fmt_by_date.values())

from src.analytics import fmt_num, kpi_row
st.markdown(kpi_row([
    ("Δ Video views (30d)", fmt_num(total_dv), "across tracked videos"),
    ("New videos (30d)", fmt_num(total_new), ""),
    ("Long / Shorts / Live",
     f"{fmt_num(total_long)} / {fmt_num(total_short)} / {fmt_num(total_live)}",
     ""),
    ("Daily avg Δ views", fmt_num(int(total_dv / max(len(window_dates), 1))),
     "per day"),
]), unsafe_allow_html=True)

st.markdown("---")

# ── Charts — same geometry as Daily Recap so the two pages feel matched ──
_CHART_HEIGHT = 320
_Y_AXIS_GUTTER = 60
_X_AXIS = alt.Axis(
    labelAngle=-45, title=None, format="%b %d",
    tickCount={"interval": "day", "step": 2},  # every 2 days on a 30-day strip
    labelOverlap=False,
)

tc1, tc2 = st.columns(2)

with tc1:
    st.caption("👁️ Δ Video views per day (summed across cohort)")
    _vals = [r["Δ Views"] for r in trend_rows]
    if _vals and max(_vals) > 0:
        _ymax = max(_vals); _ymin = min(_vals)
        _pad = max((_ymax - _ymin) * 0.1, 1)
        c1 = alt.Chart(trend_df).mark_line(
            color=_T.ACCENT, strokeWidth=2, point=True
        ).encode(
            x=alt.X("Date:T", axis=_X_AXIS),
            y=alt.Y("Δ Views:Q",
                    scale=alt.Scale(domain=[_ymin - _pad, _ymax + _pad]),
                    title=None,
                    axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
            tooltip=[
                alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
                alt.Tooltip("Δ Views:Q", format=","),
            ],
        ).properties(height=_CHART_HEIGHT)
        st.altair_chart(c1, width="stretch")
    else:
        st.caption("No tracked-video deltas in this window.")

with tc2:
    _FORMAT_COLORS = {"Long": _T.ACCENT, "Shorts": _T.POS, "Live": _T.WARN}
    _legend_html = "  ".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px">'
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'border-radius:2px;background:{c}"></span>'
        f'<span style="font-size:0.8rem;color:{_T.MUTED_2}">{lbl}</span></span>'
        for lbl, c in _FORMAT_COLORS.items()
    )
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">🎬 New videos per day — by format &nbsp;&nbsp; '
        f'{_legend_html}</div>',
        unsafe_allow_html=True,
    )
    if fmt_rows and total_new > 0:
        c2 = alt.Chart(fmt_df).mark_area(opacity=0.85).encode(
            x=alt.X("Date:T", axis=_X_AXIS),
            y=alt.Y("New Videos:Q", stack="zero", title=None,
                    axis=alt.Axis(minExtent=_Y_AXIS_GUTTER)),
            color=alt.Color(
                "Format:N",
                sort=["Long", "Shorts", "Live"],
                scale=alt.Scale(
                    domain=list(_FORMAT_COLORS.keys()),
                    range=list(_FORMAT_COLORS.values()),
                ),
                legend=None,
            ),
            order=alt.Order("Format:N", sort="ascending"),
            tooltip=[
                alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
                "Format",
                alt.Tooltip("New Videos:Q", format=","),
            ],
        ).properties(height=_CHART_HEIGHT)
        st.altair_chart(c2, width="stretch")
    else:
        st.caption("No new videos in this window.")

st.caption(
    f"Window: {start_d.isoformat()} → {(end_d - timedelta(days=1)).isoformat()} "
    f"(trailing {LOOKBACK_DAYS} CET days, today excluded as partial). "
    "Δ Views are summed from per-video daily deltas — only videos within "
    "30 days of publish are tracked, so the trend reflects current-cycle "
    "content. New-video counts come from the videos catalogue and may "
    "include backfilled older uploads."
)
