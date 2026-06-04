"""FIFA World Cup 2026 — Trends.

View growth and publishing output (videos by format) for every
official WC2026 channel — the 48 qualified national teams + FIFA +
6 confederations + per-country alt channels — on the road to the
tournament.

Data source: the `channel_snapshots` table, written daily by
scripts/daily_wc2026.py (00:00 UTC ≈ 02:00 CET). This page is
read-only — no YouTube API calls, no writes. Deliberately isolated
from the global filter, same as the WC2026 All Channels page.

Window is anchored at 2026-05-14, the first day the dedicated cron
gave full coverage of every channel (earlier days only captured the
handful that overlap daily_federations, which would draw a fake cliff).
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import date as _date, datetime as _datetime
from zoneinfo import ZoneInfo as _ZoneInfo

import streamlit as st
from src import components_compat as _components
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, fmt_date, kpi_row
from src.charts import readable_hover
from src.auth import require_login
from src.dot import flag_span, dual_dot
from src.wc2026_badge import (
    TEAM_FLAG as _SHARED_TEAM_FLAG, wc2026_badge,
)
from src import theme as _T
from src.wc_table import td as _td, render_sortable_table as _render_tbl

load_dotenv()
require_login()

# First day the dedicated daily_wc2026 cron gave full coverage of
# every WC2026 channel. Everything on this page starts here.
TRACKING_START = "2026-05-14"


def _absd(iso: str) -> str:
    """Absolute, unambiguous date (e.g. 'May 14, 2026') — used where a
    caption must be specific about the window. Deliberately NOT
    analytics.fmt_date, which is fuzzy/relative ('3d ago')."""
    try:
        return _date.fromisoformat(str(iso)[:10]).strftime("%b %d, %Y")
    except Exception:
        return str(iso)

st.title("FIFA World Cup 2026 — Trends")
st.caption(
    "View gains and videos published (long / shorts / live) for every "
    "official WC2026 channel, day by day, on the road to the "
    "tournament. Snapshotted daily (~02:00 CET). Alt channels are "
    "rolled into their country's totals — same convention as the "
    "All Channels table."
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)

# Live channel list (cached 30 min) — we need the DB `id` to join against
# channel_snapshots, plus competitions.wc2026 for team / role / flag.
channels = _cached_channels(db)
wc = [c for c in channels if (c.get("competitions") or {}).get("wc2026")]
if not wc:
    st.warning(
        "No channels with `competitions.wc2026` set yet — see the "
        "**All Channels** page for setup steps."
    )
    st.stop()

# WC2026 sub-app filter (confederation → team), shared across the
# WC2026 pages. Scoping `wc` here cascades through ch_by_id →
# channel_snapshots query → cohort → biggest movers automatically.
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)
_wc_confed, _wc_team = get_wc2026_filter()
wc = scope_wc2026(wc, _wc_confed, _wc_team)
if not wc:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_wc_confed, _wc_team)}**.")
    st.stop()
if _wc_confed or _wc_team:
    st.caption(f"Filtered: {_wc_scope_label(_wc_confed, _wc_team)} · "
               f"{len(wc)} channel(s)")

# Amber latency banner — shown only when YouTube's API is in an actual
# multi-hour latency event for the WC2026 cohort. The shared
# cohort_frozen_count returns (frozen_n, total) where 'frozen' = same
# total_views as the previous snapshot for that channel.
#
# Why a threshold rather than '> 0' (like top-5): the WC2026 cohort has
# 62 channels at very different activity levels — a handful of low-traffic
# federations (Cape Verde, Curaçao, …) routinely show identical view
# counts day-to-day even when YouTube's API is healthy. So a single
# frozen channel is noise; an API latency event looks like 20+ frozen
# at once. Trigger at ≥25% of the cohort, which is well above the
# baseline noise and well below the typical API-stall pattern (30-60+).
from src import freshness as _fr

_FROZEN_BANNER_PCT = 0.25


@st.cache_data(ttl=300, show_spinner=False)
def _latency_frozen(_cohort: str) -> tuple[int, int]:
    return _fr.cohort_frozen_count(db, _cohort)


try:
    _frozen_n, _frozen_total = _latency_frozen("wc2026")
    if (_frozen_total
            and (_frozen_n / _frozen_total) >= _FROZEN_BANNER_PCT):
        st.markdown(_fr.LATENCY_NOTICE_HTML, unsafe_allow_html=True)
except Exception:
    pass


def _wc(c):
    return c.get("competitions", {}).get("wc2026", {}) or {}


def _team_of(c):
    """Grouping key — alts roll into their country, governing bodies
    keep their own name."""
    return _wc(c).get("team") or c.get("country") or c.get("name") or "—"


# TEAM_FLAG lives in src/wc2026_badge.py now — re-export here so the
# row renderer below keeps its existing name.
TEAM_FLAG = _SHARED_TEAM_FLAG

# id → channel record (snapshots key on the DB id)
ch_by_id = {c["id"]: c for c in wc if c.get("id")}
team_of_id = {cid: _team_of(c) for cid, c in ch_by_id.items()}
# Per team: is it a governing body (FIFA / confederation) — used for the
# marker (two-color dot) when there's no national flag.
team_is_gov: dict[str, bool] = {}
team_color: dict[str, tuple] = {}
team_conf: dict[str, str] = {}
for c in wc:
    t = _team_of(c)
    if c.get("entity_type") == "GoverningBody":
        team_is_gov[t] = True
        team_color.setdefault(t, (c.get("color") or _T.ACCENT,
                                  c.get("color2") or _T.WHITE))
    team_conf.setdefault(t, _wc(c).get("confederation") or "")


def _ch_url(c) -> str:
    """Same URL resolution as the WC2026 All Channels page: explicit
    competitions.wc2026.youtube_url override → @handle → /channel/<id>."""
    w = _wc(c)
    yt_id = c.get("youtube_channel_id") or ""
    handle = (c.get("handle") or "").lstrip("@")
    return (
        w.get("youtube_url")
        or (f"https://www.youtube.com/@{handle}" if handle
            else f"https://www.youtube.com/channel/{yt_id}")
    )


# Team → canonical channel URL. Alts fold into the team row, so link
# the primary (team/governing_body) channel with the most subs; fall
# back to the biggest alt if a team has no primary.
def _is_primary(c) -> bool:
    return (_wc(c).get("role") or "team") in ("team", "governing_body")


team_url: dict[str, str] = {}
for c in sorted(
    wc,
    key=lambda x: (0 if _is_primary(x) else 1,
                   -(int(x.get("subscriber_count") or 0))),
):
    team_url.setdefault(_team_of(c), _ch_url(c))


@st.cache_data(ttl=60, show_spinner=False)
def _wc_data_version(cid_tuple: tuple[str, ...]) -> str:
    """Freshness fingerprint of the two most-recent snapshot days for the
    cohort (~2×N rows). Fed into _load_wc_snapshots' cache key so the
    heavy full-window read busts the instant data changes — a new daily
    row (new captured_date) OR an in-place resnap of a recent day (same
    date, moved total_views, which captured_at doesn't track). Re-probed
    at most once a minute. A rare manual historical interpolation of
    older days isn't caught here — it surfaces on the next daily write."""
    ids = list(cid_tuple)
    if not ids:
        return "0"
    rows = (db.client.table("channel_snapshots")
            .select("captured_date,total_views")
            .in_("channel_id", ids)
            .order("captured_date", desc=True)
            .limit(2 * len(ids))
            .execute().data or [])
    latest = max((r["captured_date"] for r in rows), default="-")
    checksum = sum(int(r.get("total_views") or 0) for r in rows)
    return f"{latest}:{checksum}"


@st.cache_data(ttl=1800, show_spinner=False)
def _load_wc_snapshots(cid_tuple: tuple[str, ...], version: str) -> list[dict]:
    """Paginated read of channel_snapshots for the WC2026 channel ids.
    `version` (from _wc_data_version) isn't read inside — it's only part
    of the cache key, so a data change busts this immediately; the 30-min
    TTL is just a memory ceiling."""
    ids = list(cid_tuple)
    if not ids:
        return []
    out: list[dict] = []
    page = 1000
    offset = 0
    while True:
        resp = (
            db.client.table("channel_snapshots")
            .select("channel_id,captured_date,subscriber_count,"
                    "total_views,video_count,long_form_count,"
                    "shorts_count,live_count")
            .in_("channel_id", ids)
            .gte("captured_date", TRACKING_START)
            .order("captured_date")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = resp.data or []
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


_cids = tuple(sorted(ch_by_id.keys()))
snaps = _load_wc_snapshots(_cids, _wc_data_version(_cids))

# date -> {channel_id -> snapshot row}
by_date: dict[str, dict[str, dict]] = defaultdict(dict)
for s in snaps:
    by_date[s["captured_date"]][s["channel_id"]] = s

# Drop today (CET): the daily_wc2026 cron writes at 00:00 UTC ≈ 02:00 CET,
# which is "today's first row" — but it represents only a few hours of
# growth at best, and routinely hits YouTube's frozen-aggregate window
# so the cohort Δ for today comes back ~0. Either way, today is partial
# and would distort the chart's right edge. Tomorrow's row (May 22 at
# 00:00 UTC) becomes the new "last complete day" automatically.
_today_cet_iso = _datetime.now(_ZoneInfo("Europe/Rome")).date().isoformat()
by_date = {d: v for d, v in by_date.items() if d < _today_cet_iso}

dates = sorted(by_date.keys())


def _g(snap, k) -> int:
    return int((snap or {}).get(k) or 0)


# ── Not enough days yet → show the latest snapshot + explain ──────
if len(dates) < 2:
    _d0 = dates[-1] if dates else None
    if _d0 is None:
        st.info(
            "No snapshots captured yet for the WC2026 channels. The "
            "**Daily WC2026** cron writes the first row at 00:00 UTC."
        )
        st.stop()
    cur = by_date[_d0]
    _views = sum(_g(r, "total_views") for r in cur.values())
    _vids = sum(_g(r, "video_count") for r in cur.values())
    st.markdown(kpi_row([
        ("Latest snapshot", fmt_date(_d0), f"{len(cur)} channels"),
        ("Total views", fmt_num(_views), ""),
        ("Total videos", fmt_num(_vids), ""),
        ("Days tracked", str(len(dates)), "need ≥ 2"),
    ]), unsafe_allow_html=True)
    st.info(
        f"Full daily coverage began {fmt_date(TRACKING_START)} — the "
        "delta charts need at least two days to compare. They'll "
        "appear after the next snapshot (~02:00 CET) and fill out as "
        "the tournament approaches."
    )
    st.stop()

first_d, last_d = dates[0], dates[-1]
import pandas as pd
import plotly.express as px

# Cohort = union of every channel observed on ANY day in the window.
# Was: intersection (only channels present every day) → 39 teams.
# Then: anchored on last_d → still missed channels when last_d was a
# partial cron run (e.g. 2026-06-03 only snapped 39 of 62 channels).
# The union always reflects every WC2026 channel we have data for in
# the window. Per-channel deltas use that channel's own earliest +
# latest available snapshots, so a partial cron day no longer drops
# anyone, and a channel imported mid-window doesn't read as a spike.
cohort: list[str] = sorted({c for d in dates for c in by_date[d]})

# Per-channel first + last snapshots within the window. The reference
# points for KPI + movers deltas. Walking dates forward for first /
# backward for last keeps each lookup at O(window length).
ch_first_snap: dict[str, dict] = {}
ch_last_snap:  dict[str, dict] = {}
for c in cohort:
    for d in dates:
        snap = by_date[d].get(c)
        if snap is not None:
            ch_first_snap[c] = snap
            break
    for d in reversed(dates):
        snap = by_date[d].get(c)
        if snap is not None:
            ch_last_snap[c] = snap
            break


# ── Per-day deltas (charts) ───────────────────────────────────────
# Δ views and net-new videos by format, summed across all channels
# that have snapshots on BOTH days of a pair — so a channel joining
# mid-window doesn't contribute a spike on its first day.
day_rows = []
for i in range(1, len(dates)):
    d, dp = dates[i], dates[i - 1]
    cur, prev = by_date[d], by_date[dp]
    dv = dl = ds = dli = 0
    for c in cohort:
        cc, pp = cur.get(c), prev.get(c)
        if cc is None or pp is None:
            continue
        dv += _g(cc, "total_views") - _g(pp, "total_views")
        dl += _g(cc, "long_form_count") - _g(pp, "long_form_count")
        ds += _g(cc, "shorts_count") - _g(pp, "shorts_count")
        dli += _g(cc, "live_count") - _g(pp, "live_count")
    day_rows.append({"Date": d, "Δ Views": dv,
                     "Long": dl, "Shorts": ds, "Live": dli})
dfd = pd.DataFrame(day_rows)
dfd["Date"] = pd.to_datetime(dfd["Date"])

# ── Window totals (KPI + movers) — per-channel first vs last snap ──
def _net(key: str) -> int:
    return sum(_g(ch_last_snap.get(c), key)
               - _g(ch_first_snap.get(c), key)
               for c in cohort)


net_views = _net("total_views")
net_subs = _net("subscriber_count")
net_long = _net("long_form_count")
net_short = _net("shorts_count")
net_live = _net("live_count")
net_vids = net_long + net_short + net_live

# Per-team rollup (alts + governing bodies fold into their country).
team_dviews: dict[str, int] = defaultdict(int)
team_dsubs: dict[str, int] = defaultdict(int)
team_dl: dict[str, int] = defaultdict(int)
team_ds: dict[str, int] = defaultdict(int)
team_dli: dict[str, int] = defaultdict(int)
for c in cohort:
    t = team_of_id.get(c, "—")
    fsnap = ch_first_snap.get(c)
    lsnap = ch_last_snap.get(c)
    team_dviews[t] += _g(lsnap, "total_views") - _g(fsnap, "total_views")
    team_dsubs[t] += _g(lsnap, "subscriber_count") - _g(fsnap, "subscriber_count")
    team_dl[t] += _g(lsnap, "long_form_count") - _g(fsnap, "long_form_count")
    team_ds[t] += _g(lsnap, "shorts_count") - _g(fsnap, "shorts_count")
    team_dli[t] += _g(lsnap, "live_count") - _g(fsnap, "live_count")
team_dvids = {t: team_dl[t] + team_ds[t] + team_dli[t] for t in team_dviews}


def _sg(x: int) -> str:
    # No leading "+" — a gain is obvious; negatives keep their "-".
    return fmt_num(x)


_n_days = len(dates)
st.markdown(kpi_row([
    ("Tracking since", fmt_date(first_d),
     f"{_n_days} days · {len(cohort)} channels"),
    ("Views gained", _sg(net_views), "across all channels"),
    ("Videos added", _sg(net_vids),
     f"{_sg(net_long)} long · {_sg(net_short)} short · {_sg(net_live)} live"),
    ("Subscribers", _sg(net_subs), "YouTube-rounded · directional"),
]), unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────
_PLOT = dict(
    plot_bgcolor=_T.BG, paper_bgcolor=_T.BG,
    font=dict(color=_T.TEXT), height=340,
    margin=dict(t=30, l=0, r=0, b=20),
)
# The first per-day row is the partial bootstrap day (tracking only
# just began that day), so its delta is noise — drop it from the
# charts only. KPIs/movers above still use the full window.
dfc = dfd.iloc[1:] if len(dfd) > 1 else dfd
gc1, gc2 = st.columns(2)
with gc1:
    st.subheader("👁️ Views gained per day")
    fv = px.line(dfc, x="Date", y="Δ Views", markers=True)
    fv.update_traces(line_color=_T.ACCENT, marker_color=_T.ACCENT)
    fv.update_layout(**_PLOT)
    fv.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
    fv.update_yaxes(gridcolor=_T.BORDER, title="")
    readable_hover(fv, x_date=True)
    st.plotly_chart(fv, width="stretch")
with gc2:
    st.subheader("🎬 Videos added per day")
    fl = px.bar(
        dfc, x="Date", y=["Long", "Shorts", "Live"],
        color_discrete_map={"Long": _T.ACCENT, "Shorts": _T.POS,
                            "Live": _T.WARN},
    )
    fl.update_layout(barmode="stack", **_PLOT)
    fl.update_layout(legend=dict(orientation="h", yanchor="bottom",
                                 y=1.02, xanchor="left", x=0,
                                 title=""))
    fl.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
    fl.update_yaxes(gridcolor=_T.BORDER, title="")
    readable_hover(fl, x_date=True)
    st.plotly_chart(fl, width="stretch")

st.caption(
    f"Per-day change across the {len(cohort)} WC2026 channels. On any "
    "given day pair, only channels with snapshots on both days "
    "contribute (so a newly-imported channel never shows as a spike "
    "on its first day). Video counts are channel-level "
    "totals, so a negative bar means videos were removed or made "
    "private. Subscriber numbers are YouTube-rounded — directional only."
)

# ── Biggest movers — one row per country ──────────────────────────
st.markdown("---")
st.subheader("🚀 Biggest movers")
st.caption(
    f"Cumulative change up to {_absd(last_d)} — each team's "
    f"{_absd(last_d)} channel totals minus its earliest tracked "
    f"snapshot (window starts {_absd(first_d)} but newer additions "
    f"are anchored at their import date so they don't read as spikes). "
    f"All {len(cohort)} WC2026 channels included. "
    "Not a per-day or last-24h figure."
)

def _marker(team: str) -> str:
    """Row marker for a team or governing body. Routes through the
    shared wc2026_badge resolver so the dual-dot palette (UEFA red+blue
    etc.) and flag set (England's GB-ENG sequence) stay consistent
    with every other WC2026 surface."""
    # Synthesize the minimal channel-shape wc2026_badge expects.
    if team_is_gov.get(team):
        return wc2026_badge(
            {"entity_type": "GoverningBody", "name": team}, 14)
    return wc2026_badge(
        {"competitions": {"wc2026": {"team": team}}}, 14)


def _col(x: int) -> str:
    return _T.POS if x > 0 else (_T.NEG if x < 0 else _T.MUTED)


# Same look + sort behaviour as the WC2026 All Channels table (shared
# src.wc_table). "Long / Short / Live" is a composite cell → not
# sortable (4th tuple element False).
_MOVER_COLS = [
    ("#",                   "num", "right"),
    ("Team",                "str", "left"),
    ("Confederation",       "str", "left"),
    ("Δ Views",             "num", "right"),
    ("Δ Videos",            "num", "right"),
    ("Long / Short / Live", "str", "right", False),
    ("Δ Subs",              "num", "right"),
]

ranked = sorted(team_dviews.items(), key=lambda kv: -kv[1])
mover_rows = []
for i, (team, dv) in enumerate(ranked, 1):
    conf = team_conf.get(team, "") or "—"
    nv = team_dvids.get(team, 0)
    L, S, Li = team_dl[team], team_ds[team], team_dli[team]
    dsub = team_dsubs.get(team, 0)
    url = team_url.get(team, "")
    name_html = (
        f"<a href='{url}' target='_blank' rel='noopener'>{team}</a>"
        if url else f"<span>{team}</span>"
    )
    mover_rows.append(
        "<tr>"
        + _td(i, str(i))
        + _td(team.lower(),
              f"<div style='display:flex;align-items:center;gap:8px'>"
              f"{_marker(team)}{name_html}</div>", align="left")
        + _td(conf.lower(),
              f"<span style='color:{_T.MUTED_2}'>{conf}</span>",
              align="left")
        + _td(dv, f"<span style='color:{_col(dv)};font-weight:600'>"
                  f"{_sg(dv)}</span>")
        + _td(nv, f"<span style='color:{_col(nv)}'>{_sg(nv)}</span>")
        + _td(L, f"<span style='color:{_T.MUTED}'>"
                 f"{_sg(L)} / {_sg(S)} / {_sg(Li)}</span>")
        + _td(dsub, f"<span style='color:{_col(dsub)}'>{_sg(dsub)}</span>")
        + "</tr>"
    )

# Expand the iframe to fit every team (was capped at 2000px with an
# inner scrollbar, which hid most of the cohort below the first 6 or
# so rows). Drop scrolling=True so the page's own scroll handles it,
# matching how the other tall WC2026 tables render.
_components.html(
    _render_tbl(_MOVER_COLS, mover_rows, "wc-tbl-movers",
                default_col=3, default_asc=False),
    height=36 * len(ranked) + 80,
    scrolling=False,
)

st.caption(
    "One row per country. Click any column header to sort; click a team "
    "to open its channel on YouTube. Δ Videos is the net change in "
    "channel video count split into long / shorts / live. Alt and "
    "federation channels are summed into their country's row, matching "
    "the All Channels table."
)

# ── Top videos published in the last 30 days (video layer) ─────────
# Cohort-wide → read the precomputed wc2026_trends cache. A narrowed
# confederation/team filter → recompute live for the scoped channels
# (the cache only holds the cohort-wide top-25).
st.markdown("---")
from src import dashboard_cache as _dc
from src.cached_db import read_dashboard_cache as _dc_read
from src.season_top import render_top_season_videos_table

_wc_ids = list(ch_by_id.keys())
_top_videos: list[dict] = []
if not (_wc_confed or _wc_team):
    _tv_row = _dc_read(db, "wc2026_trends", _dc.scope_all())
    _tv_pl = (_tv_row or {}).get("payload") if _tv_row else None
    if _tv_pl and _tv_pl.get("top_videos") is not None:
        _top_videos = list(_tv_pl.get("top_videos") or [])
if not _top_videos and _wc_ids:
    _tc, _sc, _si, _ei, _dts = _dc._trends_30d_window()
    _pubs = _dc._scan_pub_videos(db, _wc_ids, _si, _ei)
    _top_videos = _dc._build_top_videos(_pubs, db, wc, _dts, limit=25)

if _top_videos:
    render_top_season_videos_table(
        _top_videos, ch_by_id,
        header="🏆 Top 25 most-watched videos published in the last 30 days",
        order_by="views", max_height=900,
    )
    st.caption(
        "Most-watched WC2026 videos PUBLISHED in the trailing 30 days, "
        "ranked by current view count. A recently-published video's view "
        "count ≈ the views it earned inside the window."
    )
else:
    st.info("No WC2026 videos published in the last 30 days yet.")
