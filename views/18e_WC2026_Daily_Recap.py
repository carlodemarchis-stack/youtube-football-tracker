"""FIFA World Cup 2026 — Daily Recap.

Phase-1 rewrite for full structural parity with views/1_Daily_Recap.py
(top-5), adapted to the WC2026 (Confederation → Team) hierarchy.

Filter awareness:
  Z1  All confederations → cohort-wide (62 channels)
  Z2  One confederation  → that confed's channels
  Z3  One team           → that team's channel(s)
  → Read from src.wc2026_filter.get_wc2026_filter(); rendered above
    the page title by app.py.

Sections:
  - Date picker (defaults to yesterday UTC)
  - Headline KPI strip (scope-aware; rank chips when Z3)
  - 📈 Daily trends — Δ views + new videos by format, last 14 days
  - 🌍 By confederation (Z1 only) — sortable table
  - 📊 Gainers (Z1 + Z2) — subs Δ + views Δ side-by-side
  - 🔥 Most watched on this day — by view_delta from video_daily_deltas
  - 🆕 New videos published on this day (scope-filtered)

Phase 2 (separate) will add the AI commentary note via an extension
to src/ai_note.compose_payload.

Caching:
  - wc2026_trends (already exists, refreshed nightly) feeds the trends
    chart at Z1 + Z2 (cohort + per-confed series are pre-computed).
  - Z3 (single team) computes the small series live from the team's
    channel_snapshots — ~30 rows, negligible.
  - Gainers / most-watched / new-videos run live queries — small
    scoped result sets (62 channels max, single date).
"""
from __future__ import annotations

import os
from datetime import date as _date, timedelta as _td

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import components_compat as components
from src import dashboard_cache as _dc
from src import theme as _T
from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src.database import Database
from src.dot import channel_badge  # noqa: F401  (kept for fallback)
from src.wc2026_badge import wc2026_badge
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)


# Shared chart geometry — mirrors views/1_Daily_Recap.py so the WC2026
# recap reads visually identical to the Top-5 recap.
_CHART_HEIGHT  = 280
_Y_AXIS_GUTTER = 60
_X_AXIS = alt.Axis(
    labelAngle=-45, title=None, format="%b %d %a",
    tickCount={"interval": "day", "step": 1},
    labelOverlap=False,
)
_FORMAT_COLORS = {"Long": _T.ACCENT, "Shorts": _T.POS, "Live": _T.WARN}


# Standardised video-list left cell (matches CONVENTIONS §6-B from
# views/1_Daily_Recap.py). Thumbnail + 3-row stack: channel / title /
# context. Same CSS class names so the layout is pixel-identical.
_VCELL_CSS = (
    ".v-row{display:flex;align-items:flex-start;gap:10px}"
    ".v-info{min-width:0;display:flex;flex-direction:column;"
    "justify-content:space-between;height:62px;flex:1}"
    ".v-channel{font-size:12px;display:flex;align-items:center;gap:6px;"
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis}"
    f".v-title{{color:{_T.TEXT};text-decoration:none;font-size:13px;"
    "line-height:1.25;font-weight:700;display:-webkit-box;"
    "-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden;"
    "text-overflow:ellipsis}"
    ".v-meta{font-size:12px;white-space:nowrap;overflow:hidden;"
    "text-overflow:ellipsis}"
)

load_dotenv()
require_login()


# ── Boot ──────────────────────────────────────────────────────────
st.title("FIFA World Cup 2026 — Daily Recap")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()
db = Database(SUPABASE_URL, SUPABASE_KEY)


def _absd(iso: str) -> str:
    try:
        return _date.fromisoformat(str(iso)[:10]).strftime("%b %d, %Y")
    except Exception:
        return str(iso)


def _wc_meta(c):
    return (c.get("competitions") or {}).get("wc2026") or {}


# ── Channel set + scope ───────────────────────────────────────────
all_channels = _cached_channels(db)
wc_all = [c for c in all_channels
          if (c.get("competitions") or {}).get("wc2026")]
if not wc_all:
    st.warning("No channels with `competitions.wc2026` set yet — see the "
               "**All Channels** page for setup steps.")
    st.stop()

_confed, _team = get_wc2026_filter()
wc = scope_wc2026(wc_all, _confed, _team)
if not wc:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_confed, _team)}**.")
    st.stop()

# Z1 = all confederations, Z2 = one confederation, Z3 = one team.
IS_Z1 = (_confed is None and _team is None)
IS_Z2 = (_confed is not None and _team is None)
IS_Z3 = (_team is not None)
_scope_text = _wc_scope_label(_confed, _team)

st.caption(
    "An at-a-glance summary of the WC2026 cohort for the picked day. "
    "Δ views are channel-wide; videos published are split long / "
    "shorts / live. The Confederation→Team filter above re-scopes "
    "every section: leagues table appears at Z1, gainer "
    "leaderboards at Z1+Z2, single-channel detail at Z3."
)


# ── Date picker ──────────────────────────────────────────────────
# Default to the most recent captured_date that actually has WC2026
# snapshot data for the current scope — same approach as the Top-5
# recap. Yesterday-UTC is the *target*, but if the cron missed it (or
# captured_date stamping landed on "today") we drop back to the
# newest day we have, so the page never opens empty.
_today_utc = _date.today()
_min = _today_utc - _td(days=29)


@st.cache_data(ttl=180, show_spinner=False)
def _latest_snap_date(ch_ids: tuple[str, ...]) -> str | None:
    if not ch_ids:
        return None
    try:
        r = (db.client.table("channel_snapshots")
             .select("captured_date")
             .in_("channel_id", list(ch_ids))
             .order("captured_date", desc=True)
             .limit(1).execute().data) or []
    except Exception:
        r = []
    return r[0]["captured_date"] if r else None


_latest = _latest_snap_date(tuple(c["id"] for c in wc))
if _latest:
    _default_str = _latest
    # Cap at today-UTC just in case the latest is somehow in the future.
    if _date.fromisoformat(_default_str) > _today_utc:
        _default_str = _today_utc.isoformat()
    _default = _date.fromisoformat(_default_str)
else:
    _default = _today_utc - _td(days=1)
pick = st.date_input(
    "Pick a day",
    value=_default, min_value=_min, max_value=_today_utc,
    key="wc_recap_date",
)
day_iso = pick.isoformat()
prev_iso = (pick - _td(days=1)).isoformat()
prev7_iso = (pick - _td(days=7)).isoformat()
window_start_iso = (pick - _td(days=13)).isoformat()


# ── Load snapshots (last 14 days, scoped channels) ─────────────────
@st.cache_data(ttl=180, show_spinner=False)
def _load_snaps(ch_ids: tuple[str, ...], start_iso: str, end_iso: str):
    if not ch_ids:
        return []
    pages: list[dict] = []
    offset = 0
    while True:
        resp = (
            db.client.table("channel_snapshots")
            .select("channel_id,captured_date,subscriber_count,total_views")
            .in_("channel_id", list(ch_ids))
            .gte("captured_date", start_iso)
            .lte("captured_date", end_iso)
            .range(offset, offset + 1000 - 1)
            .execute()
        )
        rs = resp.data or []
        pages.extend(rs)
        if len(rs) < 1000:
            break
        offset += 1000
    return pages


_ch_ids = tuple(c["id"] for c in wc)
snaps = _load_snaps(_ch_ids, window_start_iso, day_iso)

# Channel lookup used by _video_left_cell + render loops below.
ch_by_id = {c["id"]: c for c in wc_all}


def _video_left_cell(v: dict, context_html: str = "") -> str:
    """Thumbnail + 3-row stack: channel / title / context. Mirrors
    views/1_Daily_Recap.py's helper byte-for-byte (uses the same
    .v-row / .v-info / .v-channel / .v-title / .v-meta classes).
    Pass color_map / dual = None for WC2026 — channel_badge then falls
    back to the channel's own color/color2 columns."""
    ch = ch_by_id.get(v.get("channel_id")) or {}
    badge = wc2026_badge(ch, 14)
    yt = f"https://www.youtube.com/watch?v={v.get('youtube_video_id', '')}"
    thumb = v.get("thumbnail_url") or ""
    title = (v.get("title") or "")\
        .replace("<", "&lt;").replace(">", "&gt;")
    meta = (f'<div class="v-meta">{context_html}</div>'
            if context_html else "")
    return (
        '<td style="padding:6px 12px;vertical-align:top">'
        '<div class="v-row">'
        f'<a href="{yt}" target="_blank" rel="noopener">'
        f'<img src="{thumb}" style="width:110px;height:62px;'
        'object-fit:cover;border-radius:4px;display:block;'
        'flex-shrink:0"></a>'
        '<div class="v-info">'
        f'<div class="v-channel">{badge} '
        f'<span style="color:{_T.MUTED_2}">'
        f'{(ch.get("name") or "?")}</span></div>'
        f'<a href="{yt}" target="_blank" rel="noopener" class="v-title">'
        f'{title}</a>'
        f'{meta}'
        '</div></div></td>'
    )


def _ch_name_link(ch: dict, name: str) -> str:
    """Channel name → YouTube link. Mirrors the Top-5 helper."""
    handle = (ch.get("handle") or "").lstrip("@").strip()
    yt_id = (ch.get("youtube_channel_id") or "").strip()
    if handle:
        url = f"https://www.youtube.com/@{handle}"
    elif yt_id:
        url = f"https://www.youtube.com/channel/{yt_id}"
    else:
        return name
    return (f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="color:inherit;text-decoration:none">{name}</a>')

# Per-channel map of date → snapshot row
chan_by_date: dict[str, dict[str, dict]] = {}
for s in snaps:
    cid = s["channel_id"]
    chan_by_date.setdefault(cid, {})[s.get("captured_date") or ""] = s


def _delta(cid: str, field: str, end_iso: str, start_iso: str) -> int:
    a = chan_by_date.get(cid, {}).get(end_iso)
    b = chan_by_date.get(cid, {}).get(start_iso)
    if not a or not b:
        return 0
    return int((a.get(field) or 0) - (b.get(field) or 0))


# ── Load new-videos-on-day (scoped) ────────────────────────────────
@st.cache_data(ttl=180, show_spinner=False)
def _load_new_videos(ch_ids: tuple[str, ...], d_iso: str):
    """Videos published in [d_iso, d_iso+1) for the given channels."""
    if not ch_ids:
        return []
    d_start = d_iso + "T00:00:00+00:00"
    d_end = (_date.fromisoformat(d_iso) + _td(days=1)).isoformat() \
            + "T00:00:00+00:00"
    rs = (db.client.table("videos")
          .select("id,youtube_video_id,channel_id,title,thumbnail_url,"
                  "published_at,view_count,like_count,comment_count,"
                  "format,duration_seconds")
          .in_("channel_id", list(ch_ids))
          .gte("published_at", d_start)
          .lt("published_at", d_end)
          .order("view_count", desc=True)
          .execute())
    return rs.data or []


new_videos = _load_new_videos(_ch_ids, day_iso)


# ── AI commentary note (Z1 + Z2 only) ─────────────────────────────
# Written nightly by scripts/daily_wc2026.py using the WC2026
# compose / generate functions in src/ai_note.py. Cache keys:
#   Z1 cohort  : f"{day_iso}|wc2026"
#   Z2 confed  : f"{day_iso}|wc2026|{confederation}"
# Z3 (single team) intentionally has no AI note — matches the top-5
# pattern (single-club Daily Recap doesn't get one either) and avoids
# 48 Claude calls/night.
if not IS_Z3:
    _note_key = (f"{day_iso}|wc2026|{_confed}" if IS_Z2
                 else f"{day_iso}|wc2026")
    try:
        _note_row = _cached_dc_read(db, "daily_note", _note_key)
        _note_html = ((_note_row or {}).get("payload") or {}).get("html")
    except Exception:
        _note_html = None
    if _note_html:
        st.markdown(
            f'<div style="background:{_T.SURFACE};'
            f'border-left:3px solid {_T.ACCENT};'
            'border-radius:6px;padding:14px 18px;'
            'margin:8px 0 14px 0;font-size:14px;line-height:1.55;'
            f'color:{_T.TEXT}">{_note_html}</div>',
            unsafe_allow_html=True,
        )


# ── Headline KPIs ─────────────────────────────────────────────────
def _sum_delta(field: str, end_iso: str, start_iso: str) -> int:
    return sum(_delta(c["id"], field, end_iso, start_iso) for c in wc)


_dv_day  = _sum_delta("total_views", day_iso, prev_iso)
_dv_7d   = _sum_delta("total_views", day_iso, prev7_iso)
_dsubs   = _sum_delta("subscriber_count", day_iso, prev_iso)
_n_new   = len(new_videos)

# Format split on new videos
_fmt: dict[str, int] = {"long": 0, "short": 0, "live": 0}
for v in new_videos:
    f = (v.get("format") or "").lower()
    if f not in _fmt:
        f = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
    _fmt[f] += 1
_fmt_sub = (f"{fmt_num(_fmt['long'])} long · "
            f"{fmt_num(_fmt['short'])} short · "
            f"{fmt_num(_fmt['live'])} live")

# Scope label tile
if IS_Z3:
    _scope_tile = ("Channel", _scope_text, "single-team view")
elif IS_Z2:
    _scope_tile = ("Confederation", _scope_text, f"{len(wc)} channels")
else:
    _scope_tile = ("Scope", "All confederations", f"{len(wc)} channels")

st.markdown(kpi_row([
    _scope_tile,
    ("Picked day", _absd(day_iso), f"Δ vs {_absd(prev_iso)}"),
    ("Δ views",   fmt_num(_dv_day),  f"{fmt_num(_dv_7d)} over 7d"),
    ("Δ subs",    fmt_num(_dsubs),   "channel-wide"),
    ("New videos", fmt_num(_n_new),  _fmt_sub),
]), unsafe_allow_html=True)


# ── 📈 Daily trends (last 14 days) ────────────────────────────────
# Cohort + per-confed series are precomputed in wc2026_trends.
# For Z3 (single team) we compute a small live series from snaps.
st.subheader("📈 Daily trends")

_trend_payload = None
_row = _cached_dc_read(db, "wc2026_trends", _dc.scope_all())
if _row:
    _trend_payload = (_row or {}).get("payload") or None

# Build the (date, dv, new_long, new_short, new_live) series for the
# current scope. For Z3 we walk snaps + new_videos query manually.
def _series_for_scope() -> list[dict]:
    if IS_Z1 and _trend_payload:
        return (_trend_payload.get("cohort") or {}).get("by_date") or []
    if IS_Z2 and _trend_payload:
        for g in (_trend_payload.get("breakdown") or {}).get("groups") or []:
            if g.get("name") == _confed or g.get("key") == _confed:
                return g.get("by_date") or []
    # Z3 or cache miss — compute live for the picked scope.
    out: list[dict] = []
    _start = pick - _td(days=13)
    for i in range(14):
        d = (_start + _td(days=i)).isoformat()
        d_prev = (_start + _td(days=i - 1)).isoformat()
        dv = sum(_delta(c["id"], "total_views", d, d_prev) for c in wc)
        out.append({"date": d, "dv": dv, "new_long": 0,
                    "new_short": 0, "new_live": 0})
    # Fill new_videos counts via one extra query covering the window.
    win_start = _start.isoformat() + "T00:00:00+00:00"
    win_end = (pick + _td(days=1)).isoformat() + "T00:00:00+00:00"
    rs = (db.client.table("videos")
          .select("channel_id,published_at,format,duration_seconds")
          .in_("channel_id", list(_ch_ids))
          .gte("published_at", win_start)
          .lt("published_at", win_end)
          .execute()).data or []
    by_day: dict[str, dict[str, int]] = {}
    for v in rs:
        d = (v.get("published_at") or "")[:10]
        f = (v.get("format") or "").lower()
        if f not in ("long", "short", "live"):
            f = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        by_day.setdefault(d, {"long": 0, "short": 0, "live": 0})[f] += 1
    for row in out:
        c = by_day.get(row["date"]) or {}
        row["new_long"] = c.get("long", 0)
        row["new_short"] = c.get("short", 0)
        row["new_live"] = c.get("live", 0)
    return out


series = _series_for_scope()
if series:
    df = pd.DataFrame(series)
    df["Date"] = pd.to_datetime(df["date"])
    df["Δ Channel Views"] = df["dv"].astype(int)

    tc1, tc2 = st.columns(2)

    # ── Left: Δ Channel Views per day (line) ──
    with tc1:
        st.caption("👁️ Δ Channel Views per day")
        _ymax_v = int(df["Δ Channel Views"].max())
        _ymin_v = int(df["Δ Channel Views"].min())
        _pad_v = max((_ymax_v - _ymin_v) * 0.1, 1)
        _c_views = (
            alt.Chart(df)
            .mark_line(color=_T.ACCENT, strokeWidth=2)
            .encode(
                x=alt.X("Date:T", axis=_X_AXIS),
                y=alt.Y(
                    "Δ Channel Views:Q",
                    scale=alt.Scale(domain=[_ymin_v - _pad_v,
                                             _ymax_v + _pad_v]),
                    title=None,
                    axis=alt.Axis(format="~s",
                                   minExtent=_Y_AXIS_GUTTER),
                ),
                tooltip=[
                    alt.Tooltip("Date:T", title="Date",
                                 format="%a %b %d, %Y"),
                    alt.Tooltip("Δ Channel Views:Q", format=","),
                ],
            )
            .properties(height=_CHART_HEIGHT)
        )
        st.altair_chart(_c_views, width="stretch")

    # ── Right: New videos per day, stacked by format (area) ──
    with tc2:
        _legend_html = "  ".join(
            f'<span style="display:inline-flex;align-items:center;gap:4px">'
            f'<span style="display:inline-block;width:10px;height:10px;'
            f'border-radius:2px;background:{c}"></span>'
            f'<span style="font-size:0.8rem;color:{_T.MUTED_2}">{lbl}</span>'
            f'</span>'
            for lbl, c in _FORMAT_COLORS.items()
        )
        st.markdown(
            f'<div style="font-size:14px;color:rgba(250,250,250,0.6);'
            f'line-height:1.6">🎬 New videos per day — by format '
            f'&nbsp;&nbsp; {_legend_html}</div>',
            unsafe_allow_html=True,
        )
        fmt_rows = []
        for r in series:
            for lbl, key in (("Long", "new_long"),
                             ("Shorts", "new_short"),
                             ("Live", "new_live")):
                fmt_rows.append({
                    "Date": r["date"], "Format": lbl,
                    "New Videos": int(r.get(key) or 0),
                })
        if any(r["New Videos"] for r in fmt_rows):
            fmt_df = pd.DataFrame(fmt_rows)
            _c_fmt = (
                alt.Chart(fmt_df)
                .mark_area(opacity=0.85)
                .encode(
                    x=alt.X("Date:T", axis=_X_AXIS),
                    y=alt.Y(
                        "New Videos:Q", stack="zero", title=None,
                        axis=alt.Axis(minExtent=_Y_AXIS_GUTTER),
                    ),
                    color=alt.Color(
                        "Format:N", sort=["Long", "Shorts", "Live"],
                        scale=alt.Scale(
                            domain=list(_FORMAT_COLORS.keys()),
                            range=list(_FORMAT_COLORS.values())),
                        legend=None,
                    ),
                    order=alt.Order("Format:N", sort="ascending"),
                    tooltip=[
                        alt.Tooltip("Date:T", title="Date",
                                     format="%a %b %d, %Y"),
                        "Format",
                        alt.Tooltip("New Videos:Q", format=","),
                    ],
                )
                .properties(height=_CHART_HEIGHT)
            )
            st.altair_chart(_c_fmt, width="stretch")
        else:
            st.caption("No videos in this window.")
else:
    st.caption("No trend data for this scope yet.")


# ── 🌍 By confederation (Z1 only) ─────────────────────────────────
if IS_Z1:
    st.subheader("🌍 By confederation")
    rows = []
    # Group wc channels by confederation; compute totals + day deltas.
    by_conf: dict[str, list[dict]] = {}
    for c in wc:
        k = _wc_meta(c).get("confederation") or "—"
        by_conf.setdefault(k, []).append(c)
    for conf, chs in sorted(by_conf.items()):
        ids = [c["id"] for c in chs]
        # Latest snapshot on day_iso
        subs_today = sum(int(
            (chan_by_date.get(cid, {}).get(day_iso) or {})
            .get("subscriber_count") or 0) for cid in ids)
        views_today = sum(int(
            (chan_by_date.get(cid, {}).get(day_iso) or {})
            .get("total_views") or 0) for cid in ids)
        d_views_day = sum(_delta(cid, "total_views",
                                  day_iso, prev_iso) for cid in ids)
        d_subs_day = sum(_delta(cid, "subscriber_count",
                                 day_iso, prev_iso) for cid in ids)
        nv = sum(1 for v in new_videos if v.get("channel_id") in set(ids))
        rows.append({
            "Confederation": conf,
            "Channels":      len(chs),
            "Subscribers":   subs_today,
            "Δ Subs (day)":  d_subs_day,
            "Total views":   views_today,
            "Δ Views (day)": d_views_day,
            "New videos":    nv,
        })
    if rows:
        rows.sort(key=lambda r: r["Δ Views (day)"], reverse=True)
        conf_html = ""
        for r in rows:
            dv = r["Δ Views (day)"]
            ds = r["Δ Subs (day)"]
            dv_col = (_T.POS if dv > 0
                      else (_T.NEG if dv < 0 else _T.MUTED))
            ds_col = (_T.POS if ds > 0
                      else (_T.NEG if ds < 0 else _T.MUTED))
            conf_html += f"""<tr>
                <td style="padding:8px 12px;font-weight:600">{r['Confederation']}</td>
                <td style="padding:8px 12px;text-align:right">{r['Channels']}</td>
                <td style="padding:8px 12px;text-align:right">{fmt_num(r['Subscribers'])}</td>
                <td style="padding:8px 12px;text-align:right;color:{ds_col}">{'+' if ds >= 0 else ''}{fmt_num(ds)}</td>
                <td style="padding:8px 12px;text-align:right">{fmt_num(r['Total views'])}</td>
                <td style="padding:8px 12px;text-align:right;color:{dv_col}">{'+' if dv >= 0 else ''}{fmt_num(dv)}</td>
                <td style="padding:8px 12px;text-align:right">{r['New videos']}</td>
            </tr>"""
        components.html(f"""
        <style>
          .conf {{ width:100%; border-collapse:collapse; font-size:14px;
                   color:{_T.TEXT};
                   font-family:"Source Sans Pro",sans-serif; }}
          .conf th {{ padding:8px 12px;
                      border-bottom:2px solid {_T.BORDER_STRONG};
                      text-align:left; }}
          .conf td {{ border-bottom:1px solid {_T.BORDER};
                      vertical-align:middle; }}
          .conf tr:hover td {{ background:{_T.SURFACE}; }}
        </style>
        <table class="conf"><thead><tr>
          <th>Confederation</th>
          <th style="text-align:right">Channels</th>
          <th style="text-align:right">Subscribers</th>
          <th style="text-align:right">Δ Subs</th>
          <th style="text-align:right">Total views</th>
          <th style="text-align:right">Δ Views</th>
          <th style="text-align:right">New videos</th>
        </tr></thead><tbody>{conf_html}</tbody></table>
        """, height=len(rows) * 42 + 60, scrolling=False)


# ── 📊 Gainer leaderboards (Z1 + Z2 only) ─────────────────────────
if not IS_Z3:
    _ch_by_id = {c["id"]: c for c in wc}
    leaderboard = []
    for c in wc:
        cid = c["id"]
        leaderboard.append({
            "channel_id": cid,
            "name": c.get("name") or "?",
            "team": _wc_meta(c).get("team") or "—",
            "confed": _wc_meta(c).get("confederation") or "—",
            "d_views_day":  _delta(cid, "total_views", day_iso, prev_iso),
            "d_views_7d":   _delta(cid, "total_views", day_iso, prev7_iso),
            "d_subs_day":   _delta(cid, "subscriber_count", day_iso, prev_iso),
            "d_subs_7d":    _delta(cid, "subscriber_count", day_iso, prev7_iso),
            "subs": int(
                (chan_by_date.get(cid, {}).get(day_iso) or {})
                .get("subscriber_count") or 0),
            "views": int(
                (chan_by_date.get(cid, {}).get(day_iso) or {})
                .get("total_views") or 0),
        })

    def _gainer_html(metric_day: str, metric_7d: str, metric_total: str,
                     header: str, icon: str) -> tuple[str, int]:
        """Build a custom HTML gainer table mirroring views/1_Daily_Recap.py
        — # · dot · channel · Total · Δ day · Δ 7d. Sortable JS attached."""
        rows_s = sorted(leaderboard,
                        key=lambda r: -r[metric_day])[:25]
        if not rows_s:
            return "", 0
        rows_html = ""
        for i, r in enumerate(rows_s, 1):
            ch = _ch_by_id.get(r["channel_id"]) or {}
            dot = wc2026_badge(ch, 14)
            d_day = r[metric_day]
            d_7d  = r[metric_7d]
            col_day = (_T.POS if d_day > 0
                       else (_T.NEG if d_day < 0 else _T.MUTED))
            col_7d  = (_T.POS if d_7d > 0
                       else (_T.NEG if d_7d < 0 else _T.MUTED))
            sgn_day = "+" if d_day >= 0 else ""
            sgn_7d  = "+" if d_7d  >= 0 else ""
            rows_html += f"""<tr>
                <td style="padding:5px 10px;color:{_T.MUTED}">{i}</td>
                <td style="padding:5px 10px">{dot}</td>
                <td style="padding:5px 10px">{_ch_name_link(ch, r['name'])}</td>
                <td style="padding:5px 10px;text-align:right">{fmt_num(r[metric_total])}</td>
                <td style="padding:5px 10px;text-align:right;color:{col_day}">{sgn_day}{fmt_num(d_day)}</td>
                <td style="padding:5px 10px;text-align:right;color:{col_7d}">{sgn_7d}{fmt_num(d_7d)}</td>
            </tr>"""
        tbl_id = f"gt_{metric_day}"
        return f"""
        <style>
          #{tbl_id} tr:hover td {{ background:{_T.SURFACE}; }}
        </style>
        <div style="color:{_T.TEXT};
                    font-family:'Source Sans Pro',sans-serif">
        <h4 style="margin:0 0 8px 0">{icon} {header}</h4>
        <table id="{tbl_id}" style="width:100%;border-collapse:collapse;
                                    font-size:13px">
        <thead><tr style="border-bottom:2px solid {_T.BORDER_STRONG}">
          <th style="padding:5px 10px;text-align:left">#</th>
          <th></th>
          <th style="padding:5px 10px;text-align:left">Channel</th>
          <th style="padding:5px 10px;text-align:right">Total</th>
          <th style="padding:5px 10px;text-align:right">Δ Day</th>
          <th style="padding:5px 10px;text-align:right">Δ 7d</th>
        </tr></thead>
        <tbody>{rows_html}</tbody></table></div>""", len(rows_s)

    _gc1, _gc2 = st.columns(2)
    with _gc1:
        html_v, n_v = _gainer_html("d_views_day", "d_views_7d", "views",
                                    "Biggest view gains", "👁️")
        if html_v:
            components.html(html_v, height=n_v * 32 + 70)
        else:
            st.caption("No view deltas available for this day.")
    with _gc2:
        html_s, n_s = _gainer_html("d_subs_day", "d_subs_7d", "subs",
                                    "Biggest subscriber gains", "👥")
        if html_s:
            components.html(html_s, height=n_s * 32 + 70)
        else:
            st.caption("No subscriber deltas available for this day.")


# ── 🔥 Most watched on this day (by view_delta) ───────────────────
st.subheader(f"🔥 Most watched on {_absd(day_iso)}")
try:
    top_movers = db.get_top_video_deltas(
        day_iso, limit=20, channel_ids=list(_ch_ids))
except Exception as e:
    top_movers = []
    st.caption(f"Couldn't load deltas: {e}")

if not top_movers:
    st.caption("No daily-Δ rows for this scope on the picked day.")
else:
    _vid_ids = [r["video_id"] for r in top_movers]
    meta: dict[str, dict] = {}
    for cs in range(0, len(_vid_ids), 100):
        rs = (db.client.table("videos")
              .select("id,youtube_video_id,title,thumbnail_url,"
                      "channel_id,published_at,format,"
                      "duration_seconds,category")
              .in_("id", _vid_ids[cs:cs + 100]).execute()).data or []
        for r in rs:
            meta[r["id"]] = r

    _tv_rows = ""
    for i, r in enumerate(top_movers[:20], 1):
        v = meta.get(r["video_id"])
        if not v:
            continue
        dv = int(r.get("view_delta") or 0)
        vc = int(r.get("view_count") or 0)
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = ("long" if (v.get("duration_seconds") or 0) >= 60
                   else "short")
        fmt_color = {"long": _T.ACCENT, "short": _T.POS,
                     "live": _T.WARN}.get(fmt, _T.MUTED_2)
        fmt_label = {"long": "Long", "short": "Shorts",
                     "live": "Live"}.get(fmt, fmt.title())
        pub = (v.get("published_at") or "")[:10]
        _ctx = (f'<span style="color:{fmt_color}">{fmt_label}</span>'
                f' · {pub}')
        yt_url = (f"https://www.youtube.com/watch?v="
                  f"{v.get('youtube_video_id', '')}")
        _tv_rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')"
                          style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:{_T.MUTED};
                       vertical-align:top">{i}</td>
            {_video_left_cell(v, _ctx)}
            <td style="padding:6px 12px;text-align:right;color:{_T.POS};
                       font-weight:600">+{fmt_num(dv)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(vc)}</td>
        </tr>"""

    components.html(f"""
    <style>
      .tv {{ width:100%; border-collapse:collapse; font-size:14px;
             color:{_T.TEXT};
             font-family:"Source Sans Pro",sans-serif; }}
      .tv th {{ padding:6px 12px;
                border-bottom:2px solid {_T.BORDER_STRONG};
                text-align:left; }}
      .tv td {{ border-bottom:1px solid {_T.BORDER};
                vertical-align:middle; }}
      .tv tr:hover td {{ background:{_T.SURFACE}; }}
      {_VCELL_CSS}
    </style>
    <table class="tv">
      <thead><tr>
        <th style="text-align:right">#</th>
        <th>Video</th>
        <th style="text-align:right">Δ Views</th>
        <th style="text-align:right">Total Views</th>
      </tr></thead>
      <tbody>{_tv_rows}</tbody>
    </table>
    """, height=min(20, len(top_movers)) * 82 + 60, scrolling=True)


# ── 🆕 New videos published on this day ───────────────────────────
st.subheader(f"🆕 New videos published on {_absd(day_iso)}")
if not new_videos:
    st.caption("No new uploads from this scope on the picked day.")
else:
    _nv_top = sorted(new_videos,
                      key=lambda v: int(v.get("view_count") or 0),
                      reverse=True)[:20]
    _mw_rows = ""
    for i, v in enumerate(_nv_top, 1):
        views = int(v.get("view_count") or 0)
        likes = int(v.get("like_count") or 0)
        comments = int(v.get("comment_count") or 0)
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = ("long" if (v.get("duration_seconds") or 0) >= 60
                   else "short")
        fmt_color = {"long": _T.ACCENT, "short": _T.POS,
                     "live": _T.WARN}.get(fmt, _T.MUTED_2)
        fmt_label = {"long": "Long", "short": "Shorts",
                     "live": "Live"}.get(fmt, fmt.title())
        _ctx = f'<span style="color:{fmt_color}">{fmt_label}</span>'
        yt_url = (f"https://www.youtube.com/watch?v="
                  f"{v.get('youtube_video_id', '')}")
        _mw_rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')"
                          style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:{_T.MUTED};
                       vertical-align:top">{i}</td>
            {_video_left_cell(v, _ctx)}
            <td style="padding:6px 12px;text-align:right;
                       font-weight:600">{fmt_num(views)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(likes)}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(comments)}</td>
        </tr>"""

    components.html(f"""
    <style>
      .mw {{ width:100%; border-collapse:collapse; font-size:14px;
             color:{_T.TEXT};
             font-family:"Source Sans Pro",sans-serif; }}
      .mw th {{ padding:6px 12px;
                border-bottom:2px solid {_T.BORDER_STRONG};
                text-align:left; }}
      .mw td {{ border-bottom:1px solid {_T.BORDER};
                vertical-align:middle; }}
      .mw tr:hover td {{ background:{_T.SURFACE}; }}
      {_VCELL_CSS}
    </style>
    <table class="mw">
      <thead><tr>
        <th style="text-align:right">#</th>
        <th>Video</th>
        <th style="text-align:right">Views</th>
        <th style="text-align:right">Likes</th>
        <th style="text-align:right">Comments</th>
      </tr></thead>
      <tbody>{_mw_rows}</tbody>
    </table>
    """, height=len(_nv_top) * 82 + 60, scrolling=True)
    if len(new_videos) > 20:
        st.caption(f"Showing top 20 of {len(new_videos)} new videos "
                   "on this day. See the **Latest Videos** page for "
                   "the full feed.")
