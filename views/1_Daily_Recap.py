from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Rome")

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num, yt_popup_js
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    get_global_filter, get_league_for_channel, render_page_subtitle,
)
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG

load_dotenv()
require_login()

st.title("Daily Recap")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()
if not all_channels:
    st.warning("No channel data yet. Run a refresh first.")
    st.stop()

_daily_updated = db.get_last_fetch_time("daily")
render_page_subtitle("Daily activity and new videos", updated_raw=_daily_updated)

ch_by_id = {c["id"]: c for c in all_channels}
color_map = get_global_color_map()
dual = get_global_color_map_dual()


# ── Cached data loaders (5 min TTL — data changes after daily cron) ─
@st.cache_data(ttl=300, show_spinner=False)
def _load_chan_snaps(since_iso: str) -> list[dict]:
    """Cache the 14-day channel snapshots — only changes after daily cron."""
    return db.get_all_snapshots(since_date=since_iso)


@st.cache_data(ttl=300, show_spinner=False)
def _load_snapshot_dates() -> list[str]:
    """Available snapshot dates (for the date-picker default)."""
    rows = (
        db.client.table("channel_snapshots")
        .select("captured_date")
        .order("captured_date", desc=True)
        .limit(2000)
        .execute()
        .data or []
    )
    return sorted({r["captured_date"] for r in rows}, reverse=True)


@st.cache_data(ttl=300, show_spinner=False)
def _load_new_videos(start_ts: str, end_ts: str, channel_ids_tuple: tuple | None) -> list[dict]:
    """Videos published in [start_ts, end_ts), optionally filtered by channel."""
    q = (
        db.client.table("videos")
        .select("*")
        .gte("published_at", start_ts)
        .lt("published_at", end_ts)
    )
    if channel_ids_tuple:
        q = q.in_("channel_id", list(channel_ids_tuple))
    return q.execute().data or []


@st.cache_data(ttl=300, show_spinner=False)
def _load_new_video_channel_ids(start_ts: str, end_ts: str) -> list[dict]:
    """Just {channel_id} per new video — for ranking in the ONE_CLUB case.
    Unfiltered so we can rank the current club against all others."""
    return (
        db.client.table("videos")
        .select("channel_id")
        .gte("published_at", start_ts)
        .lt("published_at", end_ts)
        .execute()
        .data or []
    )


@st.cache_data(ttl=300, show_spinner=False)
def _load_top_video_deltas(captured_date: str, limit: int,
                             channel_ids_tuple: tuple | None) -> list[dict]:
    """Top videos by Δ views on a date — served from pre-computed
    video_daily_deltas. Falls back to an empty list if the table doesn't
    exist yet (fresh install before backfill)."""
    try:
        return db.get_top_video_deltas(
            captured_date,
            limit=limit,
            channel_ids=list(channel_ids_tuple) if channel_ids_tuple else None,
        )
    except Exception:
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _load_videos_by_ids(ids_tuple: tuple) -> list[dict]:
    """Full video rows for a list of IDs — used by the Most Watched detail table."""
    if not ids_tuple:
        return []
    return (
        db.client.table("videos")
        .select("*")
        .in_("id", list(ids_tuple))
        .execute()
        .data or []
    )


@st.cache_data(ttl=300, show_spinner=False)
def _load_video_snapshots_cached(captured_date: str, channel_ids_tuple: tuple | None) -> list[dict]:
    """Per-day video snapshots, server-side filtered by channel via inner
    join to videos. Returns trimmed rows {video_id, view_count, channel_id}."""
    rows = db.get_video_snapshots_for_date_filtered(
        captured_date,
        channel_ids=list(channel_ids_tuple) if channel_ids_tuple else None,
    )
    out = []
    for r in rows:
        v = r.get("videos") or {}
        out.append({
            "video_id": r.get("video_id"),
            "view_count": r.get("view_count") or 0,
            "channel_id": v.get("channel_id") if isinstance(v, dict) else None,
        })
    return out


# ── Read global filter ──────────────────────────────────────
g_league, g_club = get_global_filter()
ONE_CLUB = g_club is not None
# The channel_id(s) to restrict computations to.
# Players are always excluded — they live on their own page.
_non_player_ids = {c["id"] for c in all_channels if c.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")}
if ONE_CLUB:
    filter_cids = {g_club["id"]}
elif g_league:
    filter_cids = {c["id"] for c in all_channels
                   if c.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")
                   and get_league_for_channel(c) == g_league}
else:
    filter_cids = _non_player_ids

# ── Date picker ────────────────────────────────────────────────
# Default to CET yesterday. Pick the most recent snapshot that falls on or
# before CET yesterday (skip today's snapshot which may be incomplete).
_cet_today = datetime.now(CET).date()
_cet_yesterday = _cet_today - timedelta(days=1)
try:
    _snap_dates = _load_snapshot_dates()
    # Find the most recent snapshot on or before CET yesterday
    _valid = [d for d in _snap_dates if d <= _cet_yesterday.isoformat()]
    if _valid:
        default_day = date.fromisoformat(_valid[0])
    elif _snap_dates:
        default_day = date.fromisoformat(_snap_dates[-1])  # oldest available
    else:
        default_day = _cet_yesterday
except Exception:
    default_day = _cet_yesterday

picked = st.date_input("Recap for", value=default_day, max_value=datetime.now(CET).date())
day = picked if isinstance(picked, date) else default_day
prev_day = day - timedelta(days=1)

day_iso = day.isoformat()
prev_iso = prev_day.isoformat()

# ── Load snapshots ──────────────────────────────────────────
LOOKBACK_DAYS = 14
lookback_iso = (day - timedelta(days=LOOKBACK_DAYS)).isoformat()

with st.spinner(f"Loading snapshots for {day_iso}…"):
    # 1) Channel snapshots (cached)
    chan_snaps = _load_chan_snaps(lookback_iso)

# Snapshots strictly on `day`, optionally filtered to selected channel(s)
chan_day = {
    s["channel_id"]: s for s in chan_snaps
    if s["captured_date"] == day_iso
    and (filter_cids is None or s["channel_id"] in filter_cids)
}

# Previous snapshot per channel: latest captured_date strictly < day_iso
chan_prev: dict[str, dict] = {}
prev_date_by_cid: dict[str, str] = {}
for s in chan_snaps:
    d = s.get("captured_date", "")
    if d >= day_iso:
        continue
    cid = s["channel_id"]
    if filter_cids is not None and cid not in filter_cids:
        continue
    if d > prev_date_by_cid.get(cid, ""):
        prev_date_by_cid[cid] = d
        chan_prev[cid] = s

# 2) Video snapshots — filtered server-side when filter_cids is set,
# cached for 5min. Single roundtrip per date (was: multiple paginated
# fetches + a batched video→channel lookup).
_prev_snap_date = max(prev_date_by_cid.values(), default=None) if prev_date_by_cid else None
_fc_tuple = tuple(sorted(filter_cids)) if filter_cids is not None else None
vid_snaps_day = _load_video_snapshots_cached(day_iso, _fc_tuple)
vid_snaps_prev = _load_video_snapshots_cached(_prev_snap_date, _fc_tuple) if _prev_snap_date else []

vmap_day = {s["video_id"]: s for s in vid_snaps_day}
vmap_prev = {s["video_id"]: s for s in vid_snaps_prev}

_no_snaps = (not chan_day and not vid_snaps_day)
if _no_snaps:
    st.info(
        f"No snapshot data captured for **{day_iso}** yet. "
        "The daily cron runs at 03:15 UTC; if you just enabled it, come back tomorrow."
    )
else:
    # Show what we're diffing against (may not be "yesterday" if cron has gaps)
    _prev_dates = sorted({d for d in prev_date_by_cid.values()}, reverse=True)
    if _prev_dates:
        _latest_prev = _prev_dates[0]
        if _latest_prev != prev_iso:
            st.caption(f"Comparing **{day_iso}** vs previous snapshot (most recent: {_latest_prev}).")
    else:
        st.caption(f"No earlier snapshot available — deltas unavailable until the next cron run.")

# ── Fetch new videos published on that day ────────────────────
# Use CET day boundaries (midnight CET), converted to UTC for DB query
_day_start_cet = datetime(day.year, day.month, day.day, tzinfo=CET)
_day_end_cet = _day_start_cet + timedelta(days=1)
start_ts = _day_start_cet.astimezone(timezone.utc).isoformat()
end_ts = _day_end_cet.astimezone(timezone.utc).isoformat()
new_video_rows = _load_new_videos(start_ts, end_ts, _fc_tuple)

# ── Compute per-channel deltas ────────────────────────────────
def _ch_delta(cid: str, field: str) -> int | None:
    a = chan_day.get(cid)
    b = chan_prev.get(cid)
    if not a or not b:
        return None
    return int(a.get(field, 0) or 0) - int(b.get(field, 0) or 0)

# ── Headline KPIs ─────────────────────────────────────────────
total_new_videos = len(new_video_rows)
total_view_delta = sum((_ch_delta(cid, "total_views") or 0) for cid in chan_day.keys())

# Count by format
_fmt_counts: dict[str, int] = {"long": 0, "short": 0, "live": 0}
for v in new_video_rows:
    fmt = (v.get("format") or "").lower()
    if fmt not in ("long", "short", "live"):
        fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
    _fmt_counts[fmt] = _fmt_counts.get(fmt, 0) + 1

if ONE_CLUB:
    # ── Compute per-club ranks for daily KPIs ──────────────────
    # Build unfiltered day/prev from chan_snaps (already loaded, 14-day lookback)
    _all_day: dict[str, dict] = {}
    _all_prev: dict[str, dict] = {}
    _all_prev_date: dict[str, str] = {}
    for s in chan_snaps:
        d = s.get("captured_date", "")
        cid = s["channel_id"]
        if d == day_iso:
            _all_day[cid] = s
        elif d < day_iso:
            if d > _all_prev_date.get(cid, ""):
                _all_prev_date[cid] = d
                _all_prev[cid] = s

    # View delta per club
    _all_view_deltas: dict[str, int] = {}
    for cid in _all_day:
        a = _all_day.get(cid)
        b = _all_prev.get(cid)
        if a and b:
            _all_view_deltas[cid] = int(a.get("total_views", 0) or 0) - int(b.get("total_views", 0) or 0)

    # New videos per club (unfiltered — needed to rank this club vs all others)
    _all_new_rows = _load_new_video_channel_ids(start_ts, end_ts)
    _all_new_counts: dict[str, int] = {}
    for v in _all_new_rows:
        _all_new_counts[v["channel_id"]] = _all_new_counts.get(v["channel_id"], 0) + 1

    _clubs_only = [c for c in all_channels if c.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    _ch_league = get_league_for_channel(g_club)
    _peers = [c for c in _clubs_only if get_league_for_channel(c) == _ch_league]

    def _daily_rank(values: dict[str, int | float], scope_channels: list[dict]) -> tuple[int | None, int]:
        """Rank g_club within scope_channels by values dict (higher=better)."""
        pool = [(c["id"], values.get(c["id"], 0)) for c in scope_channels]
        pool.sort(key=lambda x: x[1], reverse=True)
        try:
            rank = next(i + 1 for i, (cid, _) in enumerate(pool) if cid == g_club["id"])
        except StopIteration:
            rank = None
        return rank, len(pool)

    def _daily_rank_html(values: dict[str, int | float]) -> str:
        lr, lt = _daily_rank(values, _peers)
        orr, ot = _daily_rank(values, _clubs_only)
        parts = []
        if lr:
            parts.append(f"#{lr}/{lt} in {_ch_league}")
        if orr:
            parts.append(f"#{orr}/{ot} overall")
        return f"<div style='color:#888;font-size:0.8rem;margin-top:-6px'>{' · '.join(parts)}</div>" if parts else ""

    # Club banner
    cur_snap = chan_day.get(g_club["id"], {})
    _c1, _c2 = dual.get(g_club["name"], (color_map.get(g_club["name"], "#636EFA"), "#FFFFFF"))
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;margin:12px 0 20px 0">
          <div style="width:16px;height:16px;border-radius:50%;background:{_c1};
                      box-shadow:4px 0 0 {_c2};border:1px solid rgba(255,255,255,0.3)"></div>
          <div style="font-size:22px;font-weight:600;color:#FAFAFA">{g_club['name']}</div>
          <div style="color:#888;font-size:14px">
            {fmt_num(int(cur_snap.get('subscriber_count', 0) or 0))} subs ·
            {fmt_num(int(cur_snap.get('total_views', 0) or 0))} total views
          </div>
        </div>""",
        unsafe_allow_html=True,
    )
    _fmt_combined = f"{_fmt_counts['long']} / {_fmt_counts['short']} / {_fmt_counts['live']}"
    k1, k2, k3 = st.columns(3)
    k1.metric("👁️ Δ Channel Views", f"{'+' if total_view_delta >= 0 else ''}{fmt_num(total_view_delta)}")
    k1.markdown(_daily_rank_html(_all_view_deltas), unsafe_allow_html=True)
    k2.metric("🎬 New videos", fmt_num(total_new_videos))
    k2.markdown(_daily_rank_html(_all_new_counts), unsafe_allow_html=True)
    k3.metric("📺 Long / Shorts / Live", _fmt_combined)
else:
    # Most active club: posted most videos yesterday
    club_new_counts: dict[str, int] = {}
    for v in new_video_rows:
        club_new_counts[v["channel_id"]] = club_new_counts.get(v["channel_id"], 0) + 1
    most_active = max(club_new_counts.items(), key=lambda kv: kv[1], default=(None, 0))
    most_active_name = ch_by_id.get(most_active[0], {}).get("name", "—") if most_active[0] else "—"

    _fmt_combined = f"{_fmt_counts['long']} / {_fmt_counts['short']} / {_fmt_counts['live']}"
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("👁️ Δ Channel Views", f"{'+' if total_view_delta >= 0 else ''}{fmt_num(total_view_delta)}")
    k2.metric("🎬 New videos", fmt_num(total_new_videos))
    k3.metric("📺 Long / Shorts / Live", _fmt_combined)
    # Auto-shrink long channel names so they don't truncate
    _ma_name = most_active_name
    _ma_size = "1.8rem" if len(_ma_name) <= 18 else "1.3rem" if len(_ma_name) <= 25 else "1.0rem"
    k4.markdown(f"""<div>
        <div style="font-size:0.875rem;color:#999;margin-bottom:2px">🔥 Most active</div>
        <div style="font-size:{_ma_size};font-weight:700;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="{_ma_name} — {most_active[1]} videos posted">{_ma_name}</div>
        <div style="font-size:0.8rem;color:#888">{most_active[1]} videos posted</div>
    </div>""", unsafe_allow_html=True)

# ── Trend chart ──────────────────────────────────────────────
# Show Δ Views and New Videos per day across all available snapshot dates.
st.markdown("---")
st.subheader("📈 Daily trends")

# We already loaded chan_snaps (lookback window). Build per-date aggregates.
# Group all channel snapshots by captured_date
_snap_by_date: dict[str, dict[str, dict]] = {}  # date -> {cid -> snap}
for s in chan_snaps:
    d = s.get("captured_date", "")
    cid = s["channel_id"]
    if filter_cids is not None and cid not in filter_cids:
        continue
    _snap_by_date.setdefault(d, {})[cid] = s

_all_dates = sorted(_snap_by_date.keys())

# Exclude today (incomplete data) from the trend chart
_today_iso = datetime.now(CET).date().isoformat()
_all_dates = [d for d in _all_dates if d < _today_iso]

if len(_all_dates) >= 2:
    # Δ Channel Views per day — single-series total (channel_snapshots delta).
    # New Videos per day  — stacked area by format (Long / Shorts / Live),
    #                       computed from the videos table's published_at + format.
    trend_rows = []
    for i in range(1, len(_all_dates)):
        d = _all_dates[i]
        d_prev = _all_dates[i - 1]
        cur_snaps = _snap_by_date.get(d, {})
        prev_snaps = _snap_by_date.get(d_prev, {})
        dv_total = 0
        for cid, snap in cur_snaps.items():
            prev = prev_snaps.get(cid)
            if not prev:
                continue
            dv_total += int(snap.get("total_views") or 0) - int(prev.get("total_views") or 0)
        trend_rows.append({"Date": d, "Δ Channel Views": dv_total})

    # Per-day Long / Shorts / Live counts from videos.published_at (CET-bucketed).
    # Paginates to bypass Supabase's default 1000-row limit — without this
    # the "All Leagues / All Clubs" view truncates and recent days show 0.
    @st.cache_data(ttl=300, show_spinner=False)
    def _load_videos_for_format_trend(start_iso: str, end_iso: str,
                                      channel_ids_tuple: tuple | None) -> list[dict]:
        """Lightweight scan: just published_at + format + duration over window."""
        page = 1000
        offset = 0
        out: list[dict] = []
        while True:
            q = (db.client.table("videos")
                 .select("published_at,format,duration_seconds,channel_id")
                 .gte("published_at", start_iso)
                 .lt("published_at", end_iso)
                 .range(offset, offset + page - 1))
            if channel_ids_tuple:
                q = q.in_("channel_id", list(channel_ids_tuple))
            rows = q.execute().data or []
            out.extend(rows)
            if len(rows) < page:
                break
            offset += page
        return out

    # Align format chart's x-axis to the views chart's: both span _all_dates[1:].
    # (views chart drops _all_dates[0] because deltas need a previous snapshot)
    _aligned_dates = set(_all_dates[1:]) if len(_all_dates) >= 2 else set()
    _trend_start_iso = (datetime.combine(date.fromisoformat(_all_dates[1]),
                                         datetime.min.time(), tzinfo=CET)
                        .astimezone(timezone.utc).isoformat())
    _trend_end_iso = (datetime.combine(date.fromisoformat(_today_iso),
                                       datetime.min.time(), tzinfo=CET)
                      .astimezone(timezone.utc).isoformat())
    _videos_in_window = _load_videos_for_format_trend(
        _trend_start_iso, _trend_end_iso, _fc_tuple
    )

    fmt_rows: list[dict] = []
    # Pre-seed every date in the aligned window with zeros so days with no
    # videos still appear (avoids gaps that visually break x-axis alignment).
    fmt_buckets: dict[str, dict[str, int]] = {
        d: {"long": 0, "short": 0, "live": 0} for d in _aligned_dates
    }
    for v in _videos_in_window:
        pub = v.get("published_at") or ""
        try:
            dt_cet = datetime.fromisoformat(pub.replace("Z", "+00:00")).astimezone(CET)
        except Exception:
            continue
        dkey = dt_cet.date().isoformat()
        if dkey not in _aligned_dates:
            continue
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        fmt_buckets[dkey][fmt] += 1
    for d, b in fmt_buckets.items():
        for label, key in (("Long", "long"), ("Shorts", "short"), ("Live", "live")):
            fmt_rows.append({"Date": d, "Format": label, "New Videos": b[key]})

    import altair as alt
    trend_df = pd.DataFrame(trend_rows)

    # Shared chart geometry so the two charts visually align across columns.
    # minExtent on the y-axis pins the gutter width, so the plotted x-axis
    # starts at the same screen X in both charts (otherwise label widths
    # like "1,200,000" vs "30" misalign the columns by ~30px).
    _CHART_HEIGHT = 280
    _Y_AXIS_GUTTER = 60          # px reserved for y-axis labels in both charts
    _X_AXIS = alt.Axis(labelAngle=-45, title=None)

    tc1, tc2 = st.columns(2)
    with tc1:
        st.caption("👁️ Δ Channel Views per day")
        _ymax_v = max(r["Δ Channel Views"] for r in trend_rows) if trend_rows else 0
        _ymin_v = min(r["Δ Channel Views"] for r in trend_rows) if trend_rows else 0
        _pad_v = max((_ymax_v - _ymin_v) * 0.1, 1)
        c1 = alt.Chart(trend_df).mark_line(color="#636EFA", strokeWidth=2).encode(
            x=alt.X("Date:N", axis=_X_AXIS),
            y=alt.Y("Δ Channel Views:Q",
                    scale=alt.Scale(domain=[_ymin_v - _pad_v, _ymax_v + _pad_v]),
                    title=None,
                    axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
            tooltip=["Date", alt.Tooltip("Δ Channel Views:Q", format=",")],
        ).properties(height=_CHART_HEIGHT).interactive(bind_y=False)
        st.altair_chart(c1, use_container_width=True)

    with tc2:
        # Inline legend in the caption (instead of Altair's bottom legend) so
        # the chart's plot area matches the left chart's height exactly. A
        # bottom legend would steal ~30px of vertical space and misalign the
        # two x-axis baselines.
        _FORMAT_COLORS = {"Long": "#636EFA", "Shorts": "#FF6B6B", "Live": "#00CC96"}
        _legend_html = "  ".join(
            f'<span style="display:inline-flex;align-items:center;gap:4px">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'border-radius:2px;background:{c}"></span>'
            f'<span style="font-size:0.8rem;color:#aaa">{lbl}</span></span>'
            for lbl, c in _FORMAT_COLORS.items()
        )
        st.markdown(
            f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
            f'line-height:1.6">🎬 New videos per day — by format &nbsp;&nbsp; '
            f'{_legend_html}</div>',
            unsafe_allow_html=True,
        )
        if fmt_rows:
            fmt_df = pd.DataFrame(fmt_rows)
            c2 = alt.Chart(fmt_df).mark_area(opacity=0.85).encode(
                x=alt.X("Date:N", axis=_X_AXIS),
                y=alt.Y("New Videos:Q", stack="zero", title=None,
                        axis=alt.Axis(minExtent=_Y_AXIS_GUTTER)),
                color=alt.Color(
                    "Format:N",
                    sort=["Long", "Shorts", "Live"],
                    scale=alt.Scale(
                        domain=list(_FORMAT_COLORS.keys()),
                        range=list(_FORMAT_COLORS.values()),
                    ),
                    legend=None,  # rendered above as inline HTML
                ),
                order=alt.Order("Format:N", sort="ascending"),
                tooltip=["Date", "Format", "New Videos"],
            ).properties(height=_CHART_HEIGHT).interactive(bind_y=False)
            st.altair_chart(c2, use_container_width=True)
        else:
            st.caption("No videos in this window.")
else:
    st.caption("Need at least 2 snapshot dates to show trends. Come back after the next cron run.")

# ── Per-league summary ────────────────────────────────────────
# Hide when viewing a single club (it would be a one-row table for that club's league).
if not ONE_CLUB:
    st.markdown("---")
    st.subheader("📊 Per-league summary")
    lg_agg: dict[str, dict] = {}
    # Per-league per-club video counts — used to determine most-active club per league
    lg_club_counts: dict[str, dict[str, int]] = {}

    for cid, snap in chan_day.items():
        ch = ch_by_id.get(cid)
        if not ch:
            continue
        lg = get_league_for_channel(ch)
        if not lg:
            continue
        agg = lg_agg.setdefault(lg, {"new_videos": 0, "long": 0, "short": 0, "live": 0, "view_delta": 0, "clubs": 0})
        agg["clubs"] += 1
        agg["view_delta"] += _ch_delta(cid, "total_views") or 0

    for v in new_video_rows:
        ch = ch_by_id.get(v["channel_id"])
        if not ch:
            continue
        lg = get_league_for_channel(ch)
        if lg and lg in lg_agg:
            lg_agg[lg]["new_videos"] += 1
            vfmt = (v.get("format") or "").lower()
            if vfmt not in ("long", "short", "live"):
                vfmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
            lg_agg[lg][vfmt] = lg_agg[lg].get(vfmt, 0) + 1
            # Track per-league, per-club counts (exclude the league channel and Players)
            if ch.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub"):
                lg_club_counts.setdefault(lg, {})[ch["id"]] = \
                    lg_club_counts.setdefault(lg, {}).get(ch["id"], 0) + 1

    if lg_agg:
        lg_rows = ""
        for lg, agg in sorted(lg_agg.items(), key=lambda kv: kv[1]["view_delta"], reverse=True):
            view_col = "#00CC96" if agg["view_delta"] > 0 else ("#EF553B" if agg["view_delta"] < 0 else "#888")
            _lg_fmt = f"{agg['long']} / {agg['short']} / {agg['live']}"

            # Most active club in this league
            clubs_in_lg = lg_club_counts.get(lg, {})
            if clubs_in_lg:
                top_cid, top_n = max(clubs_in_lg.items(), key=lambda kv: kv[1])
                top_ch = ch_by_id.get(top_cid) or {}
                top_name = top_ch.get("name", "—")
                _c1, _c2 = dual.get(top_name, (color_map.get(top_name, "#636EFA"), "#FFFFFF"))
                top_active = (
                    f"<span style='display:inline-block;width:9px;height:9px;border-radius:50%;"
                    f"background:{_c1};box-shadow:2px 0 0 {_c2};border:1px solid rgba(255,255,255,0.25);"
                    f"margin-right:7px;vertical-align:middle'></span>"
                    f"{top_name} <span style='color:#888;font-size:12px'>· {top_n}</span>"
                )
            else:
                top_active = "<span style='color:#666'>—</span>"

            lg_rows += f"""<tr>
                <td style="padding:8px 12px;font-weight:600">{LEAGUE_FLAG.get(lg, '')} {lg}</td>
                <td style="padding:8px 12px;text-align:right">{agg['clubs']}</td>
                <td style="padding:8px 12px;text-align:right">{agg['new_videos']}</td>
                <td style="padding:8px 12px;text-align:right">{_lg_fmt}</td>
                <td style="padding:8px 12px;text-align:right;color:{view_col}">{'+' if agg['view_delta'] >= 0 else ''}{fmt_num(agg['view_delta'])}</td>
                <td style="padding:8px 12px">{top_active}</td>
            </tr>"""
        components.html(f"""
        <style>
          .lg {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                 font-family:"Source Sans Pro",sans-serif; }}
          .lg th {{ padding:8px 12px; border-bottom:2px solid #444; text-align:left; }}
          .lg td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
        </style>
        <table class="lg"><thead><tr>
          <th>League</th>
          <th style="text-align:right">Channels</th>
          <th style="text-align:right">Videos</th>
          <th style="text-align:right">Long / Shorts / Live</th>
          <th style="text-align:right">Δ Views</th>
          <th>🔥 Most active club</th>
        </tr></thead><tbody>{lg_rows}</tbody></table>
        """, height=len(lg_agg) * 42 + 60, scrolling=False)

# ── Gainer leaderboards side-by-side ─ skip when viewing one club ─
if ONE_CLUB:
    # New videos published by this club on the picked day
    st.subheader(f"🎬 Videos published on {day.strftime('%b %d, %Y')}")
    if not new_video_rows:
        st.caption("No new videos published that day.")
    else:
        _sorted_nv = sorted(new_video_rows, key=lambda v: v.get("view_count", 0) or 0, reverse=True)
        rows = ""
        for v in _sorted_nv:
            yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
            thumb = v.get("thumbnail_url") or ""
            title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
            fmt = v.get("format") or ("long" if (v.get("duration_seconds") or 0) >= 60 else "short")
            fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}.get(fmt, "#AAAAAA")
            fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}.get(fmt, fmt.title())
            _pub_raw = v.get("published_at") or ""
            try:
                pub_time = datetime.fromisoformat(_pub_raw.replace("Z", "+00:00")).astimezone(CET).strftime("%H:%M")
            except Exception:
                pub_time = _pub_raw[11:16]
            rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
                <td style="padding:6px 12px"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px"></td>
                <td style="padding:6px 12px"><a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a></td>
                <td style="padding:6px 12px"><span style="color:{fmt_color}">{fmt_label}</span></td>
                <td style="padding:6px 12px">{v.get('category') or ''}</td>
                <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('view_count') or 0))}</td>
                <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('like_count') or 0))}</td>
                <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('comment_count') or 0))}</td>
                <td style="padding:6px 12px;color:#888;font-size:12px">{pub_time}</td>
            </tr>"""
        components.html(f"""
        <style>
          .nc {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                 font-family:"Source Sans Pro",sans-serif; }}
          .nc th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
          .nc td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
          .nc tr:hover td {{ background:#1a1c24; }}
        </style>
        <table class="nc"><thead><tr>
          <th></th><th>Title</th><th>Format</th><th>Theme</th>
          <th style="text-align:right">Views</th>
          <th style="text-align:right">Likes</th>
          <th style="text-align:right">Comments</th>
          <th>Posted</th>
        </tr></thead><tbody>{rows}</tbody></table>
        {yt_popup_js()}
        """, height=len(_sorted_nv) * 80 + 80, scrolling=True)

    # No subscriber-gains leaderboard for a single club — it's a leaderboard of 1.
    col_l = None
    col_r = None
else:
    st.markdown("---")
    col_l, col_r = st.columns(2)


# Season views per channel — read from precomputed column on channels table
_season_views = {c["id"]: int(c.get("season_views", 0) or 0) for c in all_channels}


_gainer_tbl_idx = 0

def _gainer_table(metric: str, title: str, icon: str, positive_is_good: bool = True) -> str:
    global _gainer_tbl_idx
    _gainer_tbl_idx += 1
    _tbl_id = f"gt{_gainer_tbl_idx}"
    is_views = metric == "total_views"
    gainers = []
    for cid in chan_day.keys():
        ch = ch_by_id.get(cid)
        if not ch:
            continue
        d = _ch_delta(cid, metric)
        if d is None:
            continue
        latest = int(chan_day[cid].get(metric, 0) or 0)
        prev_val = latest - d
        sv = _season_views.get(cid, 0) if is_views else 0
        # % growth against season views for view gains, otherwise against previous total
        if is_views and sv:
            pct = (d / sv * 100)
        elif prev_val:
            pct = (d / prev_val * 100)
        else:
            pct = 0
        lg = get_league_for_channel(ch)
        g = {"name": ch["name"], "id": cid, "delta": d, "latest": latest, "pct": pct, "league": lg}
        if is_views:
            g["season_views"] = sv
        gainers.append(g)
    if not gainers:
        return ""
    gainers.sort(key=lambda g: g["delta"], reverse=positive_is_good)
    top = gainers[:25]
    rows_html = ""
    for i, g in enumerate(top, 1):
        c1, c2 = dual.get(g["name"], (color_map.get(g["name"], "#636EFA"), "#FFFFFF"))
        col = "#00CC96" if g["delta"] > 0 else ("#EF553B" if g["delta"] < 0 else "#888")
        sgn = "+" if g["delta"] >= 0 else ""
        pct_s = f'{g["pct"]:+.2f}%' if abs(g["pct"]) >= 0.01 else "+0.00%"
        dot = f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative"><span style="display:block;width:7px;height:7px;border-radius:50%;background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>'
        _sv = g.get("season_views", 0)
        season_col = f'<td style="padding:5px 10px;text-align:right" data-val="{_sv}">{fmt_num(_sv)}</td>' if is_views else ""
        # Column indices: 0=#, 1=dot, 2=name, 3=Total, 4=Season(if views), 5/4=Δ, 6/5=%
        _delta_col_idx = 5 if is_views else 4
        _pct_col_idx = 6 if is_views else 5
        rows_html += f"""<tr>
            <td style="padding:5px 10px;color:#888">{i}</td>
            <td style="padding:5px 10px">{dot}</td>
            <td style="padding:5px 10px">{g['name']}</td>
            <td style="padding:5px 10px;text-align:right" data-val="{g['latest']}">{fmt_num(g['latest'])}</td>
            {season_col}
            <td style="padding:5px 10px;text-align:right;color:{col}" data-val="{g['delta']}">{sgn}{fmt_num(g['delta'])}</td>
            <td style="padding:5px 10px;text-align:right;color:{col};font-size:12px" data-val="{g['pct']:.4f}">{pct_s}</td>
        </tr>"""
    season_hdr = f'<th data-col="4" style="padding:5px 10px;text-align:right;cursor:pointer">Season</th>' if is_views else ""
    _delta_ci = 5 if is_views else 4
    _pct_ci = 6 if is_views else 5
    return f"""
    <style>
      #{_tbl_id} tr:hover td {{ background:#1a1c24; }}
      #{_tbl_id} th[data-col] {{ cursor:pointer; user-select:none; }}
      #{_tbl_id} th[data-col]:hover {{ color:#00CC96; }}
      #{_tbl_id} th.active {{ color:#00CC96; }}
    </style>
    <div style="color:#FAFAFA;font-family:'Source Sans Pro',sans-serif">
    <h4 style="margin:0 0 8px 0">{icon} {title}</h4>
    <table id="{_tbl_id}" style="width:100%;border-collapse:collapse;font-size:13px">
    <thead><tr style="border-bottom:2px solid #444">
      <th style="padding:5px 10px;text-align:left">#</th>
      <th></th>
      <th style="padding:5px 10px;text-align:left">Club</th>
      <th data-col="3" style="padding:5px 10px;text-align:right">Total</th>
      {season_hdr}
      <th data-col="{_delta_ci}" style="padding:5px 10px;text-align:right" class="active">Δ ▼</th>
      <th data-col="{_pct_ci}" style="padding:5px 10px;text-align:right">%</th>
    </tr></thead>
    <tbody>{rows_html}</tbody></table></div>
    <script>
    (function(){{
      const tbl=document.getElementById('{_tbl_id}');
      const tbody=tbl.querySelector('tbody');
      const ths=tbl.querySelectorAll('th[data-col]');
      let curCol={_delta_ci},curAsc=false;
      ths.forEach(th=>{{
        th.addEventListener('click',function(){{
          const col=parseInt(this.dataset.col);
          if(curCol===col){{curAsc=!curAsc}}else{{curCol=col;curAsc=false}}
          const rows=Array.from(tbody.rows);
          rows.sort((a,b)=>{{
            const av=parseFloat(a.cells[col].dataset.val)||0;
            const bv=parseFloat(b.cells[col].dataset.val)||0;
            return curAsc?(av-bv):(bv-av);
          }});
          rows.forEach((r,i)=>{{r.cells[0].textContent=i+1;tbody.appendChild(r)}});
          ths.forEach(h=>{{h.classList.remove('active');h.textContent=h.textContent.replace(/ [▲▼]/g,'')}});
          this.classList.add('active');
          this.textContent+=curAsc?' ▲':' ▼';
        }});
      }});
    }})();
    </script>
    """


if not ONE_CLUB:
    with col_l:
        html = _gainer_table("total_views", "Biggest view gains", "👁️")
        if html:
            components.html(html, height=900)
        else:
            st.caption("No view deltas available for this day.")
    with col_r:
        # Most videos published leaderboard
        pub_counts: dict[str, dict] = {}
        for v in new_video_rows:
            ch = ch_by_id.get(v["channel_id"])
            if not ch:
                continue
            name = ch["name"]
            pub_counts.setdefault(name, {"count": 0, "long": 0, "short": 0, "live": 0, "name": name, "league": get_league_for_channel(ch)})
            pub_counts[name]["count"] += 1
            _vf = v.get("format") or ("long" if (v.get("duration_seconds") or 0) >= 60 else "short")
            if _vf in ("long", "short", "live"):
                pub_counts[name][_vf] += 1
            else:
                pub_counts[name]["long"] += 1
        pub_sorted = sorted(pub_counts.values(), key=lambda r: r["count"], reverse=True)
        if pub_sorted:
            pub_html = ""
            for i, r in enumerate(pub_sorted[:25], 1):
                c1, c2 = dual.get(r["name"], (color_map.get(r["name"], "#636EFA"), "#FFFFFF"))
                dot = f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative"><span style="display:block;width:7px;height:7px;border-radius:50%;background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>'
                lsl = f'{r["long"]} / {r["short"]} / {r["live"]}'
                pub_html += f"""<tr>
                    <td style="padding:5px 10px;color:#888">{i}</td>
                    <td style="padding:5px 10px">{dot}</td>
                    <td style="padding:5px 10px">{r['name']}</td>
                    <td style="padding:5px 10px;text-align:center">{lsl}</td>
                    <td style="padding:5px 10px;text-align:right;font-weight:600">{r['count']}</td>
                </tr>"""
            components.html(f"""
            <div style="color:#FAFAFA;font-family:'Source Sans Pro',sans-serif">
            <h4 style="margin:0 0 8px 0">🎬 Most videos published</h4>
            <table style="width:100%;border-collapse:collapse;font-size:13px">
            <thead><tr style="border-bottom:2px solid #444">
              <th style="padding:5px 10px;text-align:left">#</th>
              <th></th>
              <th style="padding:5px 10px;text-align:left">Club</th>
              <th style="padding:5px 10px;text-align:center">Long / Shorts / Live</th>
              <th style="padding:5px 10px;text-align:right">Videos</th>
            </tr></thead>
            <tbody>{pub_html}</tbody></table></div>
            """, height=900)
        else:
            st.caption("No clubs published videos on this day.")

# ── Most watched videos yesterday (by Δ views from snapshots) ──
st.markdown("---")
_mw_scope = ""
_top_n = 10 if ONE_CLUB else 20

# Prefer the pre-computed video_daily_deltas table (single indexed query,
# returns only top-N rows). Fall back to computing from vmap_day/vmap_prev
# if the table is empty (before backfill has run).
_top_deltas = _load_top_video_deltas(day_iso, _top_n, _fc_tuple)

if _top_deltas:
    trending = [
        {"id": r["video_id"], "delta": int(r.get("view_delta") or 0),
         "views": int(r.get("view_count") or 0)}
        for r in _top_deltas
        if int(r.get("view_delta") or 0) > 0
    ]
else:
    # Fallback: diff in-memory (pre-backfill behaviour)
    trending = []
    for vid_dbid, snap in vmap_day.items():
        prev = vmap_prev.get(vid_dbid)
        if not prev:
            continue
        d = int(snap.get("view_count", 0) or 0) - int(prev.get("view_count", 0) or 0)
        if d <= 0:
            continue
        trending.append({"id": vid_dbid, "delta": d,
                         "views": int(snap.get("view_count", 0) or 0)})
    trending.sort(key=lambda t: t["delta"], reverse=True)
    trending = trending[:_top_n]

st.subheader(f"🔥 {_mw_scope}Most watched videos on {day_iso}")

if not trending:
    st.caption("No video snapshot data available for this day yet.")
else:
    top_ids = [t["id"] for t in trending]
    vid_rows = _load_videos_by_ids(tuple(sorted(top_ids)))
    vid_by_id = {v["id"]: v for v in vid_rows}

    _tv_rows = ""
    for i, t in enumerate(trending[:_top_n], 1):
        v = vid_by_id.get(t["id"])
        if not v:
            continue
        ch = ch_by_id.get(v["channel_id"]) or {}
        club_c1, _ = dual.get(ch.get("name", ""), (color_map.get(ch.get("name", ""), "#636EFA"), "#FFFFFF"))
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        _pub_raw2 = v.get("published_at") or ""
        try:
            pub = datetime.fromisoformat(_pub_raw2.replace("Z", "+00:00")).astimezone(CET).strftime("%Y-%m-%d")
        except Exception:
            pub = _pub_raw2[:10]
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}.get(fmt, "#AAAAAA")
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}.get(fmt, fmt.title())
        _tv_rows += f"""<tr style="border-left:3px solid {club_c1}">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px"></a></td>
            <td style="padding:6px 12px">
                <div style="color:#AAA;font-size:12px;margin-bottom:2px">{LEAGUE_FLAG.get(COUNTRY_TO_LEAGUE.get((ch.get('country') or '').strip(), ''), '')} {ch.get('name', '?')} · <span style="color:{fmt_color}">{fmt_label}</span> · {pub}</div>
                <a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a>
            </td>
            <td style="padding:6px 12px;text-align:right;color:#00CC96;font-weight:600">+{fmt_num(t['delta'])}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(t['views'])}</td>
        </tr>"""

    components.html(f"""
    <style>
      .tv {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
             font-family:"Source Sans Pro",sans-serif; }}
      .tv th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
      .tv td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
      .tv tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="tv">
      <thead><tr>
        <th style="text-align:right">#</th>
        <th></th>
        <th>Video</th>
        <th style="text-align:right">Δ Views</th>
        <th style="text-align:right">Total Views</th>
      </tr></thead>
      <tbody>{_tv_rows}</tbody>
    </table>
    {yt_popup_js()}
    """, height=min(_top_n, len(trending)) * 86 + 80, scrolling=True)

# ── New videos published on this day (skip for single club — already shown above) ──
if new_video_rows and not ONE_CLUB:
    st.markdown("---")
    st.subheader(f"🎬 {_mw_scope}New videos published on {day.strftime('%b %d, %Y')}")
    _mw_sorted = sorted(new_video_rows, key=lambda v: int(v.get("view_count") or 0), reverse=True)
    _mw_top_n = 10 if ONE_CLUB else 20
    _mw_rows = ""
    for i, v in enumerate(_mw_sorted[:_mw_top_n], 1):
        ch = ch_by_id.get(v["channel_id"]) or {}
        club_c1, _ = dual.get(ch.get("name", ""), (color_map.get(ch.get("name", ""), "#636EFA"), "#FFFFFF"))
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}.get(fmt, "#AAAAAA")
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}.get(fmt, fmt.title())
        views = int(v.get("view_count") or 0)
        likes = int(v.get("like_count") or 0)
        comments = int(v.get("comment_count") or 0)
        _mw_rows += f"""<tr style="border-left:3px solid {club_c1}">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px"></a></td>
            <td style="padding:6px 12px">
                <div style="color:#AAA;font-size:12px;margin-bottom:2px">{LEAGUE_FLAG.get(COUNTRY_TO_LEAGUE.get((ch.get('country') or '').strip(), ''), '')} {ch.get('name', '?')} · <span style="color:{fmt_color}">{fmt_label}</span></div>
                <a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a>
            </td>
            <td style="padding:6px 12px;text-align:right;font-weight:600">{fmt_num(views)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(likes)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(comments)}</td>
        </tr>"""

    components.html(f"""
    <style>
      .mw {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
             font-family:"Source Sans Pro",sans-serif; }}
      .mw th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
      .mw td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
      .mw tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="mw">
      <thead><tr>
        <th style="text-align:right">#</th>
        <th></th>
        <th>Video</th>
        <th style="text-align:right">Views</th>
        <th style="text-align:right">Likes</th>
        <th style="text-align:right">Comments</th>
      </tr></thead>
      <tbody>{_mw_rows}</tbody>
    </table>
    {yt_popup_js()}
    """, height=min(_mw_top_n, len(_mw_sorted)) * 86 + 80, scrolling=True)
