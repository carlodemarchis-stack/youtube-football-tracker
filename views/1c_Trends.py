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
    read_dashboard_cache as _cached_dc_read,
)
from src import dashboard_cache as _dc

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
                      since: str, until: str) -> tuple[dict[str, int], dict[str, int]]:
    """Return (date → Σ view_delta, date → # distinct videos contributing).
    Paginated to bypass Supabase's 1000-row default. The distinct-video
    counter powers the cohort views/video chart."""
    sums: dict[str, int] = {}
    seen: dict[str, set[str]] = {}
    PAGE = 1000
    cid_list = list(channel_ids_tuple)
    for cs in range(0, len(cid_list), 50):
        chunk = cid_list[cs:cs + 50]
        offset = 0
        while True:
            rs = (db.client.table("video_daily_deltas")
                  .select("video_id,captured_date,view_delta")
                  .in_("channel_id", chunk)
                  .gte("captured_date", since)
                  .lt("captured_date", until)
                  .order("captured_date")
                  .range(offset, offset + PAGE - 1)
                  .execute()).data or []
            for r in rs:
                d = r["captured_date"]
                sums[d] = sums.get(d, 0) + int(r.get("view_delta") or 0)
                seen.setdefault(d, set()).add(r["video_id"])
            if len(rs) < PAGE:
                break
            offset += PAGE
    counts = {d: len(v) for d, v in seen.items()}
    return sums, counts


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

# ── Cache-first data path ─────────────────────────────────────────
# Precomputed payloads come from src/dashboard_cache.refresh_trends_30d:
#   hot tier (rebuilt every cron) — scope_all + 5 × scope_league
#   cold tier (rebuilt nightly)    — 101 × scope_club
# On cache hit, render is one DB row read away. On miss (first load
# right after deploy, brand-new scope, etc.) we fall through to the
# live loaders so the page never shows empty.
if ONE_CLUB:
    _scope_key = _dc.scope_club(g_club["id"])
elif g_league:
    _scope_key = _dc.scope_league(g_league)
else:
    _scope_key = _dc.scope_all()

_cached_row = _cached_dc_read(db, "trends_30d", _scope_key)
_cached_payload = (_cached_row or {}).get("payload") if _cached_row else None
USE_CACHE = bool(_cached_payload and _cached_payload.get("dates"))

# These names are consumed by chart code below; populated either from
# the cached payload or from the live loaders (cache-miss fallback).
view_deltas_by_date: dict[str, int] = {}
video_counts_by_date: dict[str, int] = {}
fmt_by_date: dict[str, dict[str, int]] = {}
_fmt_start_utc = (datetime.combine(start_d - timedelta(days=1),
                                   datetime.min.time(), tzinfo=CET)
                  .astimezone(timezone.utc).isoformat())
_fmt_end_utc = (datetime.combine(end_d + timedelta(days=1),
                                 datetime.min.time(), tzinfo=CET)
                .astimezone(timezone.utc).isoformat())

if USE_CACHE:
    for row in _cached_payload["cohort"]["by_date"]:
        d = row["date"]
        view_deltas_by_date[d] = int(row.get("dv") or 0)
        video_counts_by_date[d] = int(row.get("active_videos") or 0)
        fmt_by_date[d] = {
            "long": int(row.get("new_long") or 0),
            "short": int(row.get("new_short") or 0),
            "live": int(row.get("new_live") or 0),
        }
else:
    st.caption("⏳ Data warming — first render is live; subsequent loads use the precomputed cache.")
    view_deltas_by_date, video_counts_by_date = _load_view_deltas(
        _cids_tuple, since=start_d.isoformat(), until=end_d.isoformat()
    )
    fmt_by_date = _load_format_counts(_cids_tuple, _fmt_start_utc, _fmt_end_utc)
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

# ── Chart geometry + shared Sunday rule layer ─────────────────────
_CHART_HEIGHT = 320
_Y_AXIS_GUTTER = 60
_X_AXIS = alt.Axis(
    labelAngle=-45, title=None, format="%b %d",
    tickCount={"interval": "day", "step": 2},
    labelOverlap=False,
)

# Sundays inside the window — used as dashed vertical rules so the eye can
# segment the trend into weeks. Python weekday(): Mon=0, Sun=6.
_sundays = [d for d in window_dates
            if date.fromisoformat(d).weekday() == 6]
_sun_df = pd.DataFrame({"Date": _sundays})


def _with_sundays(chart):
    """Layer dashed Sunday rules behind a chart. Returns a layered chart
    or the original if there are no Sundays in the window."""
    if not _sundays:
        return chart
    rule = alt.Chart(_sun_df).mark_rule(
        color=_T.MUTED, strokeDash=[3, 3], opacity=0.45, strokeWidth=1,
    ).encode(x="Date:T")
    return (rule + chart).properties(height=_CHART_HEIGHT)


# ── Chart 1 — Δ video views per day (cohort total) ───────────────
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
    st.altair_chart(_with_sundays(c1), width="stretch")
else:
    st.caption("No tracked-video deltas in this window.")

# ── Chart 2 — New videos per day by format ───────────────────────
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
    st.altair_chart(_with_sundays(c2), width="stretch")
else:
    st.caption("No new videos in this window.")

# ── Chart 3 — Δ views per active video per day (cohort total) ────
# Cohort-level engagement efficiency: total view-delta ÷ # distinct
# videos that received an update on that day. Levels the daily reading
# regardless of how many videos happen to be in the rolling window.
per_video_rows = []
for d in window_dates:
    dv = view_deltas_by_date.get(d, 0)
    cnt = video_counts_by_date.get(d, 0)
    per_video_rows.append({
        "Date": d,
        "Δ Views / video": int(dv / cnt) if cnt else 0,
        "Active videos": cnt,
    })
per_video_df = pd.DataFrame(per_video_rows)

st.caption("⚡ Δ Views per active video per day (cohort)")
_pv_vals = [r["Δ Views / video"] for r in per_video_rows]
if _pv_vals and max(_pv_vals) > 0:
    c3 = alt.Chart(per_video_df).mark_line(
        color=_T.ACCENT, strokeWidth=2, point=True
    ).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("Δ Views / video:Q", title=None,
                axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            alt.Tooltip("Δ Views / video:Q", format=","),
            alt.Tooltip("Active videos:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(c3), width="stretch")
else:
    st.caption("No tracked-video deltas in this window.")

# ── Second row — per-league breakdown ─────────────────────────────
# Only meaningful in Z1 (cross-league) scope; in Z2 it collapses to a
# single line which the chart above already shows, and in Z3 there's
# no league split at all.
if not ONE_CLUB and not g_league:
    from src.channels import LEAGUE_COLOR_CHART
    LEAGUES = ["Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1"]

    # Build the per-league dicts the chart code below expects. Two paths:
    #   • cache hit (USE_CACHE): unpack from payload["breakdown"]
    #   • cache miss: existing live loaders (kept as fallback)
    pl_views: dict[tuple[str, str], int] = {}
    pl_video_counts: dict[tuple[str, str], int] = {}
    pl_new: dict[tuple[str, str], int] = {}

    if USE_CACHE and _cached_payload.get("breakdown", {}).get("group_label") == "league":
        for grp in _cached_payload["breakdown"]["groups"]:
            lg = grp["key"]
            for r in grp["by_date"]:
                d = r["date"]
                pl_views[(lg, d)] = int(r.get("dv") or 0)
                pl_video_counts[(lg, d)] = int(r.get("active_videos") or 0)
                pl_new[(lg, d)] = int(r.get("new_videos") or 0)
    else:
        # ── Live fallback path (cache miss) ────────────────────────────
        # Identical to the original implementation; kept verbatim so the
        # page degrades gracefully when the cron hasn't run yet.
        ch_to_league: dict[str, str] = {}
        for c in all_channels:
            if c.get("entity_type") == "League" and c.get("name") in LEAGUES:
                ch_to_league[c["id"]] = c["name"]
            elif c.get("entity_type") == "Club":
                lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
                if lg in LEAGUES:
                    ch_to_league[c["id"]] = lg

    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_per_league_view_deltas(
        cids_by_league: tuple[tuple[str, tuple[str, ...]], ...],
        since: str, until: str,
    ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
        """Returns ((league, date) → Σ view_delta, (league, date) → # distinct
        videos that contributed). The second value enables the views-per-video
        chart without a second round-trip."""
        sums: dict[tuple[str, str], int] = {}
        # Track distinct videos via per-key sets, then collapse to counts.
        seen: dict[tuple[str, str], set[str]] = {}
        PAGE = 1000
        for league, cids in cids_by_league:
            cids = list(cids)
            for cs in range(0, len(cids), 50):
                chunk = cids[cs:cs + 50]
                offset = 0
                while True:
                    rs = (db.client.table("video_daily_deltas")
                          .select("video_id,captured_date,view_delta")
                          .in_("channel_id", chunk)
                          .gte("captured_date", since)
                          .lt("captured_date", until)
                          .order("captured_date")
                          .range(offset, offset + PAGE - 1)
                          .execute()).data or []
                    for r in rs:
                        key = (league, r["captured_date"])
                        sums[key] = sums.get(key, 0) + int(r.get("view_delta") or 0)
                        seen.setdefault(key, set()).add(r["video_id"])
                    if len(rs) < PAGE:
                        break
                    offset += PAGE
        counts = {k: len(v) for k, v in seen.items()}
        return sums, counts

    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_per_league_new_videos(
        cids_by_league: tuple[tuple[str, tuple[str, ...]], ...],
        start_iso: str, end_iso: str,
    ) -> dict[tuple[str, str], int]:
        """Return {(league, captured_date_cet): count} for videos published
        in the window."""
        out: dict[tuple[str, str], int] = {}
        PAGE = 1000
        for league, cids in cids_by_league:
            cids = list(cids)
            for cs in range(0, len(cids), 50):
                chunk = cids[cs:cs + 50]
                offset = 0
                while True:
                    rs = (db.client.table("videos")
                          .select("published_at")
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
                        key = (league, dt_cet.date().isoformat())
                        out[key] = out.get(key, 0) + 1
                    if len(rs) < PAGE:
                        break
                    offset += PAGE
        return out

    # Live-fallback execution — skipped entirely when the cache hit
    # already populated pl_views / pl_video_counts / pl_new above.
    if not USE_CACHE:
        by_league_ids: dict[str, list[str]] = {lg: [] for lg in LEAGUES}
        for cid, lg in ch_to_league.items():
            by_league_ids[lg].append(cid)
        cids_by_league = tuple(
            (lg, tuple(sorted(by_league_ids.get(lg, []))))
            for lg in LEAGUES
        )
        pl_views, pl_video_counts = _load_per_league_view_deltas(
            cids_by_league, since=start_d.isoformat(), until=end_d.isoformat()
        )
        pl_new = _load_per_league_new_videos(
            cids_by_league, start_iso=_fmt_start_utc, end_iso=_fmt_end_utc
        )

    # Pre-seed (league, date) so missing days plot as 0 rather than gaps.
    pl_views_rows: list[dict] = []
    pl_new_rows: list[dict] = []
    pl_per_video_rows: list[dict] = []
    for lg in LEAGUES:
        for d in window_dates:
            dv = pl_views.get((lg, d), 0)
            cnt = pl_video_counts.get((lg, d), 0)
            pl_views_rows.append({"Date": d, "League": lg, "Δ Views": dv})
            pl_new_rows.append({
                "Date": d, "League": lg,
                "New Videos": pl_new.get((lg, d), 0),
            })
            # Δ views per tracked video that contributed on that day.
            # Zero when no videos are active (clean line, no NaN spike).
            pl_per_video_rows.append({
                "Date": d, "League": lg,
                "Δ Views / video": int(dv / cnt) if cnt else 0,
                "Active videos": cnt,
            })

    pl_views_df = pd.DataFrame(pl_views_rows)
    pl_new_df = pd.DataFrame(pl_new_rows)
    pl_per_video_df = pd.DataFrame(pl_per_video_rows)

    # Inline legend (one chip per league, league brand colour).
    _league_legend_html = "  ".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px">'
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'border-radius:2px;background:{LEAGUE_COLOR_CHART[lg]}"></span>'
        f'<span style="font-size:0.8rem;color:{_T.MUTED_2}">{lg}</span></span>'
        for lg in LEAGUES
    )
    _league_color = alt.Color(
        "League:N",
        sort=LEAGUES,
        scale=alt.Scale(
            domain=LEAGUES,
            range=[LEAGUE_COLOR_CHART[lg] for lg in LEAGUES],
        ),
        legend=None,
    )

    # ── Per-league summary table (sits above chart 4) ────────────────
    # 30-day totals per league — same shape as Daily Recap's per-league
    # block, but aggregated across the trends window rather than one day.
    from src.channels import LEAGUE_FLAG as _LF
    from src.analytics import fmt_num as _fmt
    # Cohort channel counts per league (cheap — small in-memory loop).
    _ch_count_by_lg: dict[str, int] = {lg: 0 for lg in LEAGUES}
    for _c in all_channels:
        if _c.get("entity_type") in ("Club", "League"):
            _lg = COUNTRY_TO_LEAGUE.get((_c.get("country") or "").strip())
            if _lg in _ch_count_by_lg:
                _ch_count_by_lg[_lg] += 1
    # Pull aggregates from pl_views / pl_video_counts / pl_new (already
    # populated above from cache or live fallback) plus per-format from
    # cached payload when available.
    _lg_format_totals: dict[str, dict[str, int]] = {
        lg: {"long": 0, "short": 0, "live": 0} for lg in LEAGUES
    }
    if USE_CACHE and _cached_payload.get("breakdown", {}).get("group_label") == "league":
        for grp in _cached_payload["breakdown"]["groups"]:
            lg = grp["key"]
            for r in grp["by_date"]:
                _lg_format_totals[lg]["long"] += int(r.get("new_long") or 0)
                _lg_format_totals[lg]["short"] += int(r.get("new_short") or 0)
                _lg_format_totals[lg]["live"] += int(r.get("new_live") or 0)
    _lg_summary_rows = []
    for lg in LEAGUES:
        dv_total = sum(pl_views.get((lg, d), 0) for d in window_dates)
        active_avg = sum(pl_video_counts.get((lg, d), 0) for d in window_dates)
        # Δ views / video averaged across the window (use day-by-day means
        # then average — same as the chart). Guard against 0 days.
        day_means = [
            pl_views.get((lg, d), 0) / pl_video_counts.get((lg, d), 1)
            for d in window_dates
            if pl_video_counts.get((lg, d), 0) > 0
        ]
        per_video_avg = int(sum(day_means) / len(day_means)) if day_means else 0
        new_total = sum(pl_new.get((lg, d), 0) for d in window_dates)
        ft = _lg_format_totals[lg]
        _lg_summary_rows.append({
            "lg": lg, "dv": dv_total, "new": new_total,
            "long": ft["long"], "short": ft["short"], "live": ft["live"],
            "per_video": per_video_avg,
            "channels": _ch_count_by_lg[lg],
        })
    _lg_summary_rows.sort(key=lambda r: -r["dv"])

    st.subheader("📊 Per-league summary (30-day totals)")
    _lg_html = ""
    for r in _lg_summary_rows:
        _dv_col = _T.POS if r["dv"] > 0 else (_T.NEG if r["dv"] < 0 else _T.MUTED)
        _sign = "+" if r["dv"] >= 0 else ""
        _lg_html += f"""<tr>
          <td style="padding:8px 12px;font-weight:600">{_LF.get(r['lg'], '')} {r['lg']}</td>
          <td style="padding:8px 12px;text-align:right">{r['channels']}</td>
          <td style="padding:8px 12px;text-align:right;color:{_dv_col}">{_sign}{_fmt(r['dv'])}</td>
          <td style="padding:8px 12px;text-align:right">{_fmt(r['new'])}</td>
          <td style="padding:8px 12px;text-align:right">{r['long']} / {r['short']} / {r['live']}</td>
          <td style="padding:8px 12px;text-align:right">{_fmt(r['per_video'])}</td>
        </tr>"""
    st.markdown(f"""
    <style>
      .ltbl {{ width:100%; border-collapse:collapse; font-size:14px;
              color:{_T.TEXT}; font-family:"Source Sans Pro",sans-serif; }}
      .ltbl th {{ padding:8px 12px; border-bottom:2px solid {_T.BORDER_STRONG}; text-align:left; }}
      .ltbl td {{ border-bottom:1px solid {_T.BORDER}; vertical-align:middle; }}
    </style>
    <table class="ltbl"><thead><tr>
      <th>League</th>
      <th style="text-align:right">Channels</th>
      <th style="text-align:right">Δ Views (30d)</th>
      <th style="text-align:right">Videos</th>
      <th style="text-align:right">Long / Shorts / Live</th>
      <th style="text-align:right">⚡ Δ Views / video (avg)</th>
    </tr></thead><tbody>{_lg_html}</tbody></table>
    """, unsafe_allow_html=True)

    # Chart 3 — Δ views per day, one line per league
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">👁️ Δ Video views per day — by league &nbsp;&nbsp; '
        f'{_league_legend_html}</div>',
        unsafe_allow_html=True,
    )
    cpl1 = alt.Chart(pl_views_df).mark_line(strokeWidth=2, point=False).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("Δ Views:Q", title=None,
                axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
        color=_league_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            "League",
            alt.Tooltip("Δ Views:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(cpl1), width="stretch")

    # Chart 4 — New videos per day, one line per league
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">🎬 New videos per day — by league &nbsp;&nbsp; '
        f'{_league_legend_html}</div>',
        unsafe_allow_html=True,
    )
    cpl2 = alt.Chart(pl_new_df).mark_line(strokeWidth=2, point=False).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("New Videos:Q", title=None,
                axis=alt.Axis(minExtent=_Y_AXIS_GUTTER)),
        color=_league_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            "League",
            alt.Tooltip("New Videos:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(cpl2), width="stretch")

    # Chart 5 — Δ views per active video per day, one line per league.
    # "Active" = videos within their 30-day rolling tracking window that
    # received a non-null view_delta on that day. Levels the playing field
    # between leagues with very different catalogue sizes.
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">⚡ Δ Views per active video per day — by league'
        f' &nbsp;&nbsp; {_league_legend_html}</div>',
        unsafe_allow_html=True,
    )
    cpl3 = alt.Chart(pl_per_video_df).mark_line(strokeWidth=2, point=False).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("Δ Views / video:Q", title=None,
                axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
        color=_league_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            "League",
            alt.Tooltip("Δ Views / video:Q", format=","),
            alt.Tooltip("Active videos:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(cpl3), width="stretch")

# ── Z2 — per-channel breakdown within the picked league ──────────
# Mirrors the Z1 per-league trio (charts 4/5/6) but one line per
# channel in the league. League HQ is sorted first; clubs follow
# alphabetically. Each line uses the channel's own brand colour
# (`channels.color`) — falls back to ACCENT if a channel has none.
elif g_league and not ONE_CLUB:
    # Cohort channels in display order: League HQ first, then clubs A→Z.
    _z2_chans = [c for c in all_channels if c["id"] in set(ch_ids)]
    _z2_chans.sort(key=lambda c: (
        0 if c.get("entity_type") == "League" else 1,
        (c.get("name") or "").lower(),
    ))
    z2_cid_to_name = {c["id"]: c.get("name") or "?" for c in _z2_chans}
    z2_cid_to_color = {
        c["id"]: (c.get("color") or _T.ACCENT) for c in _z2_chans
    }
    z2_names_ordered = [c.get("name") or "?" for c in _z2_chans]

    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_per_channel_view_deltas(
        cids: tuple[str, ...], since: str, until: str,
    ) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
        """((channel_id, date) → Σ view_delta, (channel_id, date) → distinct
        video count). Same pattern as the per-league helper."""
        sums: dict[tuple[str, str], int] = {}
        seen: dict[tuple[str, str], set[str]] = {}
        PAGE = 1000
        cids = list(cids)
        for cs in range(0, len(cids), 50):
            chunk = cids[cs:cs + 50]
            offset = 0
            while True:
                rs = (db.client.table("video_daily_deltas")
                      .select("video_id,channel_id,captured_date,view_delta")
                      .in_("channel_id", chunk)
                      .gte("captured_date", since)
                      .lt("captured_date", until)
                      .order("captured_date")
                      .range(offset, offset + PAGE - 1)
                      .execute()).data or []
                for r in rs:
                    key = (r["channel_id"], r["captured_date"])
                    sums[key] = sums.get(key, 0) + int(r.get("view_delta") or 0)
                    seen.setdefault(key, set()).add(r["video_id"])
                if len(rs) < PAGE:
                    break
                offset += PAGE
        counts = {k: len(v) for k, v in seen.items()}
        return sums, counts

    @st.cache_data(ttl=1800, show_spinner=False)
    def _load_per_channel_new_videos(
        cids: tuple[str, ...], start_iso: str, end_iso: str,
    ) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        PAGE = 1000
        cids = list(cids)
        for cs in range(0, len(cids), 50):
            chunk = cids[cs:cs + 50]
            offset = 0
            while True:
                rs = (db.client.table("videos")
                      .select("channel_id,published_at")
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
                    key = (v["channel_id"], dt_cet.date().isoformat())
                    out[key] = out.get(key, 0) + 1
                if len(rs) < PAGE:
                    break
                offset += PAGE
        return out

    _z2_cids_tuple = tuple(sorted(z2_cid_to_name.keys()))
    ch_views: dict[tuple[str, str], int] = {}
    ch_video_counts: dict[tuple[str, str], int] = {}
    ch_new: dict[tuple[str, str], int] = {}

    if USE_CACHE and _cached_payload.get("breakdown", {}).get("group_label") == "channel":
        for grp in _cached_payload["breakdown"]["groups"]:
            cid = grp["key"]
            for r in grp["by_date"]:
                d = r["date"]
                ch_views[(cid, d)] = int(r.get("dv") or 0)
                ch_video_counts[(cid, d)] = int(r.get("active_videos") or 0)
                ch_new[(cid, d)] = int(r.get("new_videos") or 0)
    else:
        ch_views, ch_video_counts = _load_per_channel_view_deltas(
            _z2_cids_tuple, since=start_d.isoformat(), until=end_d.isoformat()
        )
        ch_new = _load_per_channel_new_videos(
            _z2_cids_tuple, start_iso=_fmt_start_utc, end_iso=_fmt_end_utc
        )

    ch_views_rows, ch_new_rows, ch_per_video_rows = [], [], []
    for cid in _z2_cids_tuple:
        name = z2_cid_to_name[cid]
        for d in window_dates:
            dv = ch_views.get((cid, d), 0)
            cnt = ch_video_counts.get((cid, d), 0)
            ch_views_rows.append({"Date": d, "Channel": name, "Δ Views": dv})
            ch_new_rows.append({"Date": d, "Channel": name,
                                "New Videos": ch_new.get((cid, d), 0)})
            ch_per_video_rows.append({
                "Date": d, "Channel": name,
                "Δ Views / video": int(dv / cnt) if cnt else 0,
                "Active videos": cnt,
            })

    ch_views_df = pd.DataFrame(ch_views_rows)
    ch_new_df = pd.DataFrame(ch_new_rows)
    ch_per_video_df = pd.DataFrame(ch_per_video_rows)

    # Inline legend — chips wrap naturally on narrow viewports.
    _channel_legend_html = "  ".join(
        f'<span style="display:inline-flex;align-items:center;gap:4px;margin-right:4px">'
        f'<span style="display:inline-block;width:10px;height:10px;'
        f'border-radius:2px;background:{z2_cid_to_color[c["id"]]}"></span>'
        f'<span style="font-size:0.8rem;color:{_T.MUTED_2}">{c.get("name")}</span></span>'
        for c in _z2_chans
    )
    _channel_color = alt.Color(
        "Channel:N",
        sort=z2_names_ordered,
        scale=alt.Scale(
            domain=z2_names_ordered,
            range=[z2_cid_to_color[c["id"]] for c in _z2_chans],
        ),
        legend=None,
    )

    # ── Per-channel summary table (sits above chart 4) ───────────────
    from src.analytics import fmt_num as _fmt
    _z2_format_totals: dict[str, dict[str, int]] = {
        c["id"]: {"long": 0, "short": 0, "live": 0} for c in _z2_chans
    }
    if USE_CACHE and _cached_payload.get("breakdown", {}).get("group_label") == "channel":
        for grp in _cached_payload["breakdown"]["groups"]:
            cid = grp["key"]
            if cid in _z2_format_totals:
                for r in grp["by_date"]:
                    _z2_format_totals[cid]["long"] += int(r.get("new_long") or 0)
                    _z2_format_totals[cid]["short"] += int(r.get("new_short") or 0)
                    _z2_format_totals[cid]["live"] += int(r.get("new_live") or 0)

    _ch_summary_rows = []
    for c in _z2_chans:
        cid = c["id"]
        dv_total = sum(ch_views.get((cid, d), 0) for d in window_dates)
        new_total = sum(ch_new.get((cid, d), 0) for d in window_dates)
        day_means = [
            ch_views.get((cid, d), 0) / ch_video_counts.get((cid, d), 1)
            for d in window_dates
            if ch_video_counts.get((cid, d), 0) > 0
        ]
        per_video_avg = int(sum(day_means) / len(day_means)) if day_means else 0
        ft = _z2_format_totals.get(cid, {"long": 0, "short": 0, "live": 0})
        _ch_summary_rows.append({
            "name": c.get("name") or "?",
            "color": c.get("color") or _T.ACCENT,
            "entity_type": c.get("entity_type") or "",
            "dv": dv_total, "new": new_total,
            "long": ft["long"], "short": ft["short"], "live": ft["live"],
            "per_video": per_video_avg,
        })
    _ch_summary_rows.sort(key=lambda r: -r["dv"])

    st.subheader(f"📊 Per-channel summary — {g_league} (30-day totals)")
    _ch_html = ""
    for r in _ch_summary_rows:
        _dv_col = _T.POS if r["dv"] > 0 else (_T.NEG if r["dv"] < 0 else _T.MUTED)
        _sign = "+" if r["dv"] >= 0 else ""
        _dot = (
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'border-radius:2px;background:{r["color"]};vertical-align:middle"></span>'
        )
        _row_label = r["name"]
        if r["entity_type"] == "League":
            _row_label = f"<b>{_row_label}</b>"
        _ch_html += f"""<tr>
          <td style="padding:8px 12px">{_dot}&nbsp;&nbsp;{_row_label}</td>
          <td style="padding:8px 12px;text-align:right;color:{_dv_col}">{_sign}{_fmt(r['dv'])}</td>
          <td style="padding:8px 12px;text-align:right">{_fmt(r['new'])}</td>
          <td style="padding:8px 12px;text-align:right">{r['long']} / {r['short']} / {r['live']}</td>
          <td style="padding:8px 12px;text-align:right">{_fmt(r['per_video'])}</td>
        </tr>"""
    st.markdown(f"""
    <style>
      .ctbl {{ width:100%; border-collapse:collapse; font-size:14px;
              color:{_T.TEXT}; font-family:"Source Sans Pro",sans-serif; }}
      .ctbl th {{ padding:8px 12px; border-bottom:2px solid {_T.BORDER_STRONG}; text-align:left; }}
      .ctbl td {{ border-bottom:1px solid {_T.BORDER}; vertical-align:middle; }}
    </style>
    <table class="ctbl"><thead><tr>
      <th>Channel</th>
      <th style="text-align:right">Δ Views (30d)</th>
      <th style="text-align:right">Videos</th>
      <th style="text-align:right">Long / Shorts / Live</th>
      <th style="text-align:right">⚡ Δ Views / video (avg)</th>
    </tr></thead><tbody>{_ch_html}</tbody></table>
    """, unsafe_allow_html=True)

    # Chart 4 — Δ views per day, one line per channel
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">👁️ Δ Video views per day — by channel &nbsp;&nbsp; '
        f'{_channel_legend_html}</div>',
        unsafe_allow_html=True,
    )
    cz1 = alt.Chart(ch_views_df).mark_line(strokeWidth=1.5, point=False).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("Δ Views:Q", title=None,
                axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
        color=_channel_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            "Channel", alt.Tooltip("Δ Views:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(cz1), width="stretch")

    # Chart 5 — New videos per day, one line per channel
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">🎬 New videos per day — by channel &nbsp;&nbsp; '
        f'{_channel_legend_html}</div>',
        unsafe_allow_html=True,
    )
    cz2 = alt.Chart(ch_new_df).mark_line(strokeWidth=1.5, point=False).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("New Videos:Q", title=None,
                axis=alt.Axis(minExtent=_Y_AXIS_GUTTER)),
        color=_channel_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            "Channel", alt.Tooltip("New Videos:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(cz2), width="stretch")

    # Chart 6 — Δ views per active video per day, one line per channel
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">⚡ Δ Views per active video per day — by channel'
        f' &nbsp;&nbsp; {_channel_legend_html}</div>',
        unsafe_allow_html=True,
    )
    cz3 = alt.Chart(ch_per_video_df).mark_line(strokeWidth=1.5, point=False).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("Δ Views / video:Q", title=None,
                axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
        color=_channel_color,
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%a %b %d, %Y"),
            "Channel",
            alt.Tooltip("Δ Views / video:Q", format=","),
            alt.Tooltip("Active videos:Q", format=","),
        ],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(cz3), width="stretch")

# ── Top 25 videos in the window (all Z levels) ───────────────────
# Read from the cached payload; falls back to a live aggregation if
# the cache is cold or the payload doesn't carry top_videos yet (older
# format).
_top_videos = (
    list(_cached_payload.get("top_videos") or [])
    if USE_CACHE else []
)
if not _top_videos:
    @st.cache_data(ttl=1800, show_spinner=False)
    def _live_top_videos(cids_tuple: tuple[str, ...],
                         since: str, until: str, limit: int = 25) -> list[dict]:
        from collections import defaultdict
        sums: dict[str, int] = defaultdict(int)
        PAGE = 1000
        for cs in range(0, len(cids_tuple), 50):
            chunk = list(cids_tuple)[cs:cs + 50]
            offset = 0
            while True:
                rs = (db.client.table("video_daily_deltas")
                      .select("video_id,view_delta")
                      .in_("channel_id", chunk)
                      .gte("captured_date", since)
                      .lt("captured_date", until)
                      .range(offset, offset + PAGE - 1).execute()).data or []
                for r in rs:
                    sums[r["video_id"]] += int(r.get("view_delta") or 0)
                if len(rs) < PAGE:
                    break
                offset += PAGE
        if not sums:
            return []
        top_ids = sorted(sums, key=lambda v: -sums[v])[:limit]
        out = []
        for cs in range(0, len(top_ids), 100):
            chunk = top_ids[cs:cs + 100]
            rs = (db.client.table("videos")
                  .select("id,youtube_video_id,title,channel_id,thumbnail_url,"
                          "duration_seconds,format,published_at,view_count")
                  .in_("id", chunk).execute()).data or []
            meta = {r["id"]: r for r in rs}
            for vid in chunk:
                m = meta.get(vid)
                if not m:
                    continue
                ch = next((c for c in all_channels if c["id"] == m.get("channel_id")), {})
                out.append({
                    "video_id": vid,
                    "youtube_video_id": m.get("youtube_video_id"),
                    "title": m.get("title") or "",
                    "thumbnail_url": m.get("thumbnail_url") or "",
                    "duration_seconds": int(m.get("duration_seconds") or 0),
                    "format": m.get("format") or "",
                    "published_at": m.get("published_at"),
                    "view_count": int(m.get("view_count") or 0),
                    "delta_in_window": int(sums[vid]),
                    "channel_id": m.get("channel_id"),
                    "channel_name": ch.get("name") or "?",
                    "channel_color": ch.get("color") or "#888",
                })
        out.sort(key=lambda r: -r["delta_in_window"])
        return out[:limit]
    _top_videos = _live_top_videos(_cids_tuple,
                                    start_d.isoformat(),
                                    end_d.isoformat())

if _top_videos:
    from src.analytics import fmt_num as _fmt
    st.markdown("---")
    _scope_label = (
        g_club.get("name") if ONE_CLUB else (g_league or "all top-5 leagues")
    )
    st.subheader(f"🏆 Top 25 videos by Δ views — {_scope_label} (30d)")
    _tv_html = ""
    for i, v in enumerate(_top_videos, 1):
        _yt = f"https://www.youtube.com/watch?v={v.get('youtube_video_id', '')}"
        _title_safe = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        _thumb = v.get("thumbnail_url") or ""
        _fmt_key = (v.get("format") or "").lower()
        if _fmt_key not in ("long", "short", "live"):
            _fmt_key = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        _fmt_color = {"long": _T.ACCENT, "short": _T.POS, "live": _T.WARN}.get(_fmt_key, _T.MUTED_2)
        _fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}.get(_fmt_key, _fmt_key.title())
        _pub_raw = v.get("published_at") or ""
        try:
            _pub_d = datetime.fromisoformat(_pub_raw.replace("Z", "+00:00")).astimezone(CET).strftime("%b %d")
        except Exception:
            _pub_d = "—"
        _dot = (
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'border-radius:2px;background:{v.get("channel_color", "#888")};'
            f'vertical-align:middle"></span>'
        )
        _tv_html += f"""<tr>
          <td style="padding:6px 10px;color:{_T.MUTED};text-align:right;width:32px">{i}</td>
          <td style="padding:6px 10px;width:140px">
            <a href="{_yt}" target="_blank" rel="noopener">
              <img src="{_thumb}" style="width:130px;height:73px;object-fit:cover;border-radius:4px;display:block">
            </a>
          </td>
          <td style="padding:6px 10px">
            <a href="{_yt}" target="_blank" rel="noopener"
               style="color:{_T.TEXT};text-decoration:none;font-weight:500">{_title_safe}</a>
            <div style="margin-top:4px;font-size:12px;color:{_T.MUTED_2}">
              {_dot}&nbsp;{v.get("channel_name", "?")}
              &nbsp;·&nbsp;<span style="color:{_fmt_color}">{_fmt_label}</span>
              &nbsp;·&nbsp;Published {_pub_d}
            </div>
          </td>
          <td style="padding:6px 10px;text-align:right;font-weight:600;color:{_T.POS}">+{_fmt(v.get("delta_in_window", 0))}</td>
          <td style="padding:6px 10px;text-align:right;color:{_T.MUTED_2}">{_fmt(v.get("view_count", 0))}</td>
        </tr>"""
    st.markdown(f"""
    <style>
      .tv25 {{ width:100%; border-collapse:collapse; font-size:14px;
              color:{_T.TEXT}; font-family:"Source Sans Pro",sans-serif; }}
      .tv25 th {{ padding:6px 10px; border-bottom:2px solid {_T.BORDER_STRONG}; text-align:left; }}
      .tv25 td {{ border-bottom:1px solid {_T.BORDER}; vertical-align:middle; }}
    </style>
    <table class="tv25"><thead><tr>
      <th style="width:32px;text-align:right">#</th>
      <th style="width:140px">&nbsp;</th>
      <th>Title</th>
      <th style="text-align:right">Δ Views (30d)</th>
      <th style="text-align:right">Total views</th>
    </tr></thead><tbody>{_tv_html}</tbody></table>
    """, unsafe_allow_html=True)

st.caption(
    f"Window: {start_d.isoformat()} → {(end_d - timedelta(days=1)).isoformat()} "
    f"(trailing {LOOKBACK_DAYS} CET days, today excluded as partial). "
    "Δ Views are summed from per-video daily deltas — only videos within "
    "30 days of publish are tracked, so the trend reflects current-cycle "
    "content. New-video counts come from the videos catalogue and may "
    "include backfilled older uploads."
)
