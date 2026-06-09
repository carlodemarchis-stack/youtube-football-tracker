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
from src.filters import is_top5_cohort, is_club
import pandas as pd
import altair as alt
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_filter, render_page_subtitle,
    get_global_color_map, get_global_color_map_dual,
)
from src.dot import channel_badge
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
    if is_top5_cohort(c)
]

g_league, g_club = get_global_filter()
ONE_CLUB = g_club is not None
# Centralised channel-decoration maps (clubs → dual_dot, leagues → flag).
_color_map = get_global_color_map()
_dual_map = get_global_color_map_dual()

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

# YouTube API latency notice — shown ONLY while the top-5 cohort still
# has frozen channels on its most-recent snapshot day; auto-hides once
# the resnap jobs catch up. (This page's charts are top-5; WC2026 has
# its own banner on the WC2026 Trends page.)
from src import freshness as _fr

@st.cache_data(ttl=300, show_spinner=False)
def _latency_frozen(_cohort: str) -> int:
    return _fr.cohort_frozen_count(db, _cohort)[0]

try:
    if _latency_frozen("top5") > 0:
        st.markdown(_fr.LATENCY_NOTICE_HTML, unsafe_allow_html=True)
except Exception:
    pass

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

# ── AI vibe note (Z1 + Z2) ───────────────────────────────────────
# Precomputed nightly by src.dashboard_cache.refresh_trends_30d_vibe.
# Z3 (single club) intentionally skipped — a 30-day vibe for one
# channel is thin enough that the chart series tells the story.
if not ONE_CLUB:
    _vibe_scope = _dc.scope_league(g_league) if g_league else _dc.scope_all()
    try:
        _vibe_row = _cached_dc_read(db, "trends_30d_vibe", _vibe_scope)
        _vibe_html = (_vibe_row or {}).get("payload", {}).get("html") or ""
    except Exception:
        _vibe_html = ""
    if _vibe_html:
        st.markdown(
            f'<div style="background:#1a1c24;border-left:3px solid #58A6FF;'
            f'padding:12px 16px;margin:8px 0 18px 0;border-radius:4px;'
            f'font-size:14px;line-height:1.6;color:#FAFAFA">'
            f'<span style="color:#888;font-size:11px;font-weight:600;'
            f'letter-spacing:0.5px;text-transform:uppercase">'
            f'🤖 30-day shape · updated nightly</span>'
            f'<div style="margin-top:6px">{_vibe_html}</div></div>',
            unsafe_allow_html=True,
        )

# KPI row — totals across the 30 days
total_dv = sum(v for v in view_deltas_by_date.values())
total_new = sum(b["long"] + b["short"] + b["live"]
                for b in fmt_by_date.values())
total_long = sum(b["long"] for b in fmt_by_date.values())
total_short = sum(b["short"] for b in fmt_by_date.values())
total_live = sum(b["live"] for b in fmt_by_date.values())

# Archive-share split — what % of the cohort's 30-day Δ views came from
# videos OLDER than their 30-day tracking window (i.e. back-catalogue
# still earning). Pulled from the cached payload's cohort.split; falls
# back to 0/0 when the cache row predates the schema bump.
_split = ((_cached_payload or {}).get("cohort") or {}).get("split") or {}
_archive_pct = float(_split.get("archive_share_pct") or 0.0)
_archive_abs = int(_split.get("archive_view_delta") or 0)
_fresh_abs = int(_split.get("fresh_view_total") or 0)

from src.analytics import fmt_num, kpi_row
st.markdown(kpi_row([
    ("Δ Channel views (30d)", fmt_num(total_dv), "across cohort channels"),
    ("🗄️ Older videos (>30d)",
     f"≈{_archive_pct:.1f}%" if _split else "—",
     f"≈{fmt_num(_archive_abs)} of {fmt_num(total_dv)} Δ views"),
    ("New videos (30d)", fmt_num(total_new),
     f"{fmt_num(total_long)} / {fmt_num(total_short)} / {fmt_num(total_live)} L/S/Lv"),
    ("Daily avg Δ views", fmt_num(int(total_dv / max(len(window_dates), 1))),
     "per day"),
]), unsafe_allow_html=True)

st.caption(
    "🗄️ **Older videos (>30d)** = the share of the last 30 days' view "
    "**growth** that came from videos **published more than 30 days ago** — "
    "i.e. total channel view growth in the window minus the views earned by "
    "videos published *within* the last 30 days. A high share means a "
    "channel's older videos, not its new ones, are driving its current views. "
    "*Approximate — YouTube's channel-wide counter and per-video counts don't "
    "reconcile perfectly.*"
)

st.markdown("---")

# ── Chart geometry + shared Sunday rule layer ─────────────────────
_CHART_HEIGHT = 320
_Y_AXIS_GUTTER = 60
_X_AXIS = alt.Axis(
    labelAngle=-45, title=None, format="%b %d",
    tickCount={"interval": "day", "step": 1},  # every day on the strip
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
st.caption("👁️ Δ Channel views per day (summed across cohort)")
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
    for lg in LEAGUES:
        for d in window_dates:
            pl_views_rows.append({
                "Date": d, "League": lg,
                "Δ Views": pl_views.get((lg, d), 0),
            })
            pl_new_rows.append({
                "Date": d, "League": lg,
                "New Videos": pl_new.get((lg, d), 0),
            })

    pl_views_df = pd.DataFrame(pl_views_rows)
    pl_new_df = pd.DataFrame(pl_new_rows)

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
        if not is_club(_c):
            _lg = COUNTRY_TO_LEAGUE.get((_c.get("country") or "").strip())
            if _lg in _ch_count_by_lg:
                _ch_count_by_lg[_lg] += 1
    # Pull aggregates from pl_views / pl_video_counts / pl_new (already
    # populated above from cache or live fallback) plus per-format from
    # cached payload when available.
    _lg_format_totals: dict[str, dict[str, int]] = {
        lg: {"long": 0, "short": 0, "live": 0} for lg in LEAGUES
    }
    _lg_splits: dict[str, dict] = {lg: {} for lg in LEAGUES}
    if USE_CACHE and _cached_payload.get("breakdown", {}).get("group_label") == "league":
        for grp in _cached_payload["breakdown"]["groups"]:
            lg = grp["key"]
            for r in grp["by_date"]:
                _lg_format_totals[lg]["long"] += int(r.get("new_long") or 0)
                _lg_format_totals[lg]["short"] += int(r.get("new_short") or 0)
                _lg_format_totals[lg]["live"] += int(r.get("new_live") or 0)
            _lg_splits[lg] = grp.get("split") or {}
    _lg_summary_rows = []
    for lg in LEAGUES:
        dv_total = sum(pl_views.get((lg, d), 0) for d in window_dates)
        new_total = sum(pl_new.get((lg, d), 0) for d in window_dates)
        ft = _lg_format_totals[lg]
        _lg_summary_rows.append({
            "lg": lg, "dv": dv_total, "new": new_total,
            "long": ft["long"], "short": ft["short"], "live": ft["live"],
            "channels": _ch_count_by_lg[lg],
            "archive_pct": float(_lg_splits[lg].get("archive_share_pct") or 0.0),
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
          <td style="padding:8px 12px;text-align:right;color:{_T.MUTED_2}">{r['archive_pct']:.1f}%</td>
          <td style="padding:8px 12px;text-align:right">{_fmt(r['new'])}</td>
          <td style="padding:8px 12px;text-align:right">{r['long']} / {r['short']} / {r['live']}</td>
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
      <th style="text-align:right" title="≈ Share of the last 30 days' view growth from videos published more than 30 days ago (channel-wide growth − views of videos published in the window). Estimate.">🗄️ Older (>30d) %</th>
      <th style="text-align:right">Videos</th>
      <th style="text-align:right">Long / Shorts / Live</th>
    </tr></thead><tbody>{_lg_html}</tbody></table>
    """, unsafe_allow_html=True)

    # Chart 3 — Δ views per day, one line per league
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">👁️ Δ Channel views per day — by league &nbsp;&nbsp; '
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

    ch_views_rows, ch_new_rows = [], []
    for cid in _z2_cids_tuple:
        name = z2_cid_to_name[cid]
        for d in window_dates:
            ch_views_rows.append({"Date": d, "Channel": name,
                                  "Δ Views": ch_views.get((cid, d), 0)})
            ch_new_rows.append({"Date": d, "Channel": name,
                                "New Videos": ch_new.get((cid, d), 0)})

    ch_views_df = pd.DataFrame(ch_views_rows)
    ch_new_df = pd.DataFrame(ch_new_rows)

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
    _z2_splits: dict[str, dict] = {c["id"]: {} for c in _z2_chans}
    if USE_CACHE and _cached_payload.get("breakdown", {}).get("group_label") == "channel":
        for grp in _cached_payload["breakdown"]["groups"]:
            cid = grp["key"]
            if cid in _z2_format_totals:
                for r in grp["by_date"]:
                    _z2_format_totals[cid]["long"] += int(r.get("new_long") or 0)
                    _z2_format_totals[cid]["short"] += int(r.get("new_short") or 0)
                    _z2_format_totals[cid]["live"] += int(r.get("new_live") or 0)
                _z2_splits[cid] = grp.get("split") or {}

    _ch_summary_rows = []
    for c in _z2_chans:
        cid = c["id"]
        dv_total = sum(ch_views.get((cid, d), 0) for d in window_dates)
        new_total = sum(ch_new.get((cid, d), 0) for d in window_dates)
        ft = _z2_format_totals.get(cid, {"long": 0, "short": 0, "live": 0})
        _ch_summary_rows.append({
            "ch": c,
            "dv": dv_total, "new": new_total,
            "long": ft["long"], "short": ft["short"], "live": ft["live"],
            "archive_pct": float(_z2_splits.get(cid, {}).get("archive_share_pct") or 0.0),
        })
    _ch_summary_rows.sort(key=lambda r: -r["dv"])

    st.subheader(f"📊 Per-channel summary — {g_league} (30-day totals)")
    _ch_html = ""
    for r in _ch_summary_rows:
        _dv_col = _T.POS if r["dv"] > 0 else (_T.NEG if r["dv"] < 0 else _T.MUTED)
        _sign = "+" if r["dv"] >= 0 else ""
        _badge = channel_badge(r["ch"], _color_map, _dual_map, 14)
        _name = r["ch"].get("name") or "?"
        if r["ch"].get("entity_type") == "League":
            _name = f"<b>{_name}</b>"
        _ch_html += f"""<tr>
          <td style="padding:8px 12px">
            <span style="display:inline-flex;align-items:center;gap:8px">
              {_badge}<span>{_name}</span>
            </span>
          </td>
          <td style="padding:8px 12px;text-align:right;color:{_dv_col}">{_sign}{_fmt(r['dv'])}</td>
          <td style="padding:8px 12px;text-align:right;color:{_T.MUTED_2}">{r['archive_pct']:.1f}%</td>
          <td style="padding:8px 12px;text-align:right">{_fmt(r['new'])}</td>
          <td style="padding:8px 12px;text-align:right">{r['long']} / {r['short']} / {r['live']}</td>
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
      <th style="text-align:right" title="≈ Share of the last 30 days' view growth from videos published more than 30 days ago (channel-wide growth − views of videos published in the window). Estimate.">🗄️ Older (>30d) %</th>
      <th style="text-align:right">Videos</th>
      <th style="text-align:right">Long / Shorts / Live</th>
    </tr></thead><tbody>{_ch_html}</tbody></table>
    """, unsafe_allow_html=True)

    # Chart 4 — Δ views per day, one line per channel
    st.markdown(
        f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
        f'line-height:1.6">👁️ Δ Channel views per day — by channel &nbsp;&nbsp; '
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
                         start_iso: str, end_iso: str,
                         limit: int = 25) -> list[dict]:
        """Most-watched videos PUBLISHED in the window, ranked by
        view_count (cache-miss fallback for the cached top_videos)."""
        rows: list[dict] = []
        PAGE = 1000
        for cs in range(0, len(cids_tuple), 50):
            chunk = list(cids_tuple)[cs:cs + 50]
            offset = 0
            while True:
                rs = (db.client.table("videos")
                      .select("id,youtube_video_id,title,channel_id,"
                              "thumbnail_url,duration_seconds,format,"
                              "published_at,view_count,like_count,"
                              "comment_count,category")
                      .in_("channel_id", chunk)
                      .gte("published_at", start_iso)
                      .lt("published_at", end_iso)
                      .order("published_at")
                      .range(offset, offset + PAGE - 1).execute()).data or []
                rows.extend(rs)
                if len(rs) < PAGE:
                    break
                offset += PAGE
        rows.sort(key=lambda r: -(int(r.get("view_count") or 0)))
        out = []
        for m in rows[:limit]:
            out.append({
                "id": m.get("id"), "video_id": m.get("id"),
                "youtube_video_id": m.get("youtube_video_id"),
                "title": m.get("title") or "",
                "thumbnail_url": m.get("thumbnail_url") or "",
                "duration_seconds": int(m.get("duration_seconds") or 0),
                "format": m.get("format") or "",
                "published_at": m.get("published_at"),
                "view_count": int(m.get("view_count") or 0),
                "like_count": int(m.get("like_count") or 0),
                "comment_count": int(m.get("comment_count") or 0),
                "category": m.get("category") or "",
                "channel_id": m.get("channel_id"),
            })
        return out
    _top_start_utc = (datetime.combine(start_d, datetime.min.time(), tzinfo=CET)
                      .astimezone(timezone.utc).isoformat())
    _top_end_utc = (datetime.combine(end_d, datetime.min.time(), tzinfo=CET)
                    .astimezone(timezone.utc).isoformat())
    _top_videos = _live_top_videos(_cids_tuple, _top_start_utc, _top_end_utc)

if _top_videos:
    from src.season_top import render_top_season_videos_table
    _ch_by_id = {c["id"]: c for c in all_channels}
    st.markdown("---")
    _scope_label = (
        g_club.get("name") if ONE_CLUB else (g_league or "all top-5 leagues")
    )
    render_top_season_videos_table(
        _top_videos, _ch_by_id,
        header=f"🏆 Top 25 most-watched videos published in the last 30 days — {_scope_label}",
        order_by="views",
        max_height=1200,
    )

st.caption(
    f"Window: {start_d.isoformat()} → {(end_d - timedelta(days=1)).isoformat()} "
    f"(trailing {LOOKBACK_DAYS} CET days, today excluded as partial). "
    "Δ Channel views = day-over-day diff of each channel's lifetime "
    "total_views, summed across the cohort — captures growth on ALL "
    "content, including videos older than 30 days. The Top 25 list ranks "
    "videos PUBLISHED in the last 30 days by their view count (the best "
    "fresh uploads of the window)."
)
