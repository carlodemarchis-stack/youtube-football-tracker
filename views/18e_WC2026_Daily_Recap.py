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

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from src import dashboard_cache as _dc
from src import theme as _T
from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src.database import Database
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
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
# Default to yesterday UTC — that's the timezone the daily cron uses
# for captured_date in channel_snapshots / video_daily_deltas.
_today_utc = _date.today()
_default = _today_utc - _td(days=1)
_min = _today_utc - _td(days=29)
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
    df["Δ Views"] = df["dv"].astype(int)
    df["New videos"] = (df["new_long"] + df["new_short"]
                        + df["new_live"]).astype(int)

    _t1, _t2 = st.columns(2)
    _PLOT = dict(plot_bgcolor=_T.BG, paper_bgcolor=_T.BG,
                 font=dict(color=_T.TEXT), height=300,
                 margin=dict(t=20, l=0, r=0, b=20))
    with _t1:
        f1 = px.line(df, x="Date", y="Δ Views", markers=True)
        f1.update_traces(line_color=_T.ACCENT, marker_color=_T.ACCENT)
        f1.update_layout(**_PLOT,
                         title="Δ Views per day")
        f1.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
        f1.update_yaxes(gridcolor=_T.BORDER, title="", tickformat="~s")
        st.plotly_chart(f1, width="stretch")
    with _t2:
        long_df = pd.melt(df, id_vars=["Date"],
                          value_vars=["new_long", "new_short", "new_live"],
                          var_name="Format", value_name="Videos")
        long_df["Format"] = long_df["Format"].map({
            "new_long": "Long", "new_short": "Shorts", "new_live": "Live",
        })
        f2 = px.bar(long_df, x="Date", y="Videos", color="Format",
                    color_discrete_map={"Long": "#636EFA",
                                         "Shorts": "#00CC96",
                                         "Live": "#FFA15A"},
                    barmode="stack")
        f2.update_layout(**_PLOT, title="New videos per day (by format)")
        f2.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
        f2.update_yaxes(gridcolor=_T.BORDER, title="")
        st.plotly_chart(f2, width="stretch")
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
        conf_df = (pd.DataFrame(rows)
                   .sort_values("Δ Views (day)", ascending=False))
        st.dataframe(conf_df, hide_index=True, width="stretch",
                     column_config={
                         "Subscribers":   st.column_config.NumberColumn(format="%d"),
                         "Δ Subs (day)":  st.column_config.NumberColumn(format="%+d"),
                         "Total views":   st.column_config.NumberColumn(format="%d"),
                         "Δ Views (day)": st.column_config.NumberColumn(format="%+d"),
                         "New videos":    st.column_config.NumberColumn(format="%d"),
                         "Channels":      st.column_config.NumberColumn(format="%d"),
                     })


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

    _gc1, _gc2 = st.columns(2)
    with _gc1:
        st.subheader("👁️ Biggest view gains")
        rows_v = sorted(leaderboard,
                        key=lambda r: -r["d_views_day"])[:10]
        df_v = pd.DataFrame([{
            "#": i + 1,
            "Channel": r["name"],
            "Conf": r["confed"],
            "Δ Views (day)": r["d_views_day"],
            "Δ Views (7d)": r["d_views_7d"],
        } for i, r in enumerate(rows_v)])
        st.dataframe(df_v, hide_index=True, width="stretch",
                     column_config={
                         "Δ Views (day)": st.column_config.NumberColumn(format="%+d"),
                         "Δ Views (7d)":  st.column_config.NumberColumn(format="%+d"),
                     })

    with _gc2:
        st.subheader("👥 Biggest subscriber gains")
        rows_s = sorted(leaderboard,
                        key=lambda r: -r["d_subs_day"])[:10]
        df_s = pd.DataFrame([{
            "#": i + 1,
            "Channel": r["name"],
            "Conf": r["confed"],
            "Δ Subs (day)": r["d_subs_day"],
            "Δ Subs (7d)": r["d_subs_7d"],
        } for i, r in enumerate(rows_s)])
        st.dataframe(df_s, hide_index=True, width="stretch",
                     column_config={
                         "Δ Subs (day)": st.column_config.NumberColumn(format="%+d"),
                         "Δ Subs (7d)":  st.column_config.NumberColumn(format="%+d"),
                     })


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
    # Hydrate with title/thumbnail/published_at from the videos table.
    _vid_ids = [r["video_id"] for r in top_movers]
    meta: dict[str, dict] = {}
    for cs in range(0, len(_vid_ids), 100):
        rs = (db.client.table("videos")
              .select("id,youtube_video_id,title,thumbnail_url,"
                      "channel_id,published_at")
              .in_("id", _vid_ids[cs:cs + 100]).execute()).data or []
        for r in rs:
            meta[r["id"]] = r
    _ch_lookup = {c["id"]: c for c in wc}

    for _i, r in enumerate(top_movers[:20], 1):
        m = meta.get(r["video_id"]) or {}
        yt = m.get("youtube_video_id") or ""
        url = f"https://www.youtube.com/watch?v={yt}" if yt else "#"
        pub = (m.get("published_at") or "")[:10]
        ch = _ch_lookup.get(m.get("channel_id")) or {}
        cn = ch.get("name") or "?"
        dv = int(r.get("view_delta") or 0)
        vc = int(r.get("view_count") or 0)
        _c0, _cT, _cM, _cR = st.columns([0.35, 1.3, 6, 2.6])
        with _c0:
            st.markdown(f"### {_i}")
        with _cT:
            thumb = m.get("thumbnail_url") or ""
            if thumb:
                st.markdown(
                    f"<a href='{url}' target='_blank' rel='noopener' "
                    f"title='Open on YouTube'>"
                    f"<img src='{thumb}' "
                    f"style='width:100%;border-radius:6px'></a>",
                    unsafe_allow_html=True,
                )
        with _cM:
            ttl = (m.get("title") or "")[:120]\
                .replace("<", "&lt;").replace(">", "&gt;")
            st.markdown(
                f"<div style='line-height:1.5;font-size:13px;color:#c9cdd6'>"
                f"{cn}</div>"
                f"<a href='{url}' target='_blank' rel='noopener' "
                f"style='color:#58A6FF;font-weight:600;font-size:15px;"
                f"text-decoration:none'>{ttl}</a>"
                f"<div style='color:{_T.MUTED_2};font-size:12px;margin-top:2px'>"
                f"published {pub}</div>",
                unsafe_allow_html=True,
            )
        with _cR:
            st.markdown(
                f"<div style='text-align:right;font-size:13px;line-height:1.5'>"
                f"<b style='color:{_T.POS};font-size:16px'>"
                f"+{fmt_num(dv)}</b> views<br>"
                f"<span style='color:{_T.MUTED_2}'>"
                f"{fmt_num(vc)} total</span></div>",
                unsafe_allow_html=True,
            )
        st.markdown(
            "<hr style='border:none;border-top:1px solid #2a2c34;"
            "margin:6px 0 12px 0'>",
            unsafe_allow_html=True,
        )


# ── 🆕 New videos published on this day ───────────────────────────
st.subheader(f"🆕 New videos published on {_absd(day_iso)}")
if not new_videos:
    st.caption("No new uploads from this scope on the picked day.")
else:
    cols = st.columns(4)
    _ch_lookup2 = {c["id"]: c for c in wc}
    for i, v in enumerate(new_videos[:24]):
        yid = v.get("youtube_video_id") or ""
        url = f"https://www.youtube.com/watch?v={yid}" if yid else "#"
        thumb = v.get("thumbnail_url") or ""
        title = (v.get("title") or "")[:70]\
            .replace("<", "&lt;").replace(">", "&gt;")
        ch = _ch_lookup2.get(v.get("channel_id")) or {}
        cn = ch.get("name") or "?"
        views = int(v.get("view_count") or 0)
        pub = (v.get("published_at") or "")[:16].replace("T", " ")
        with cols[i % 4]:
            st.markdown(
                f"<a href='{url}' target='_blank' rel='noopener' "
                f"title='Open on YouTube'>"
                f"<img src='{thumb}' "
                f"style='width:100%;border-radius:6px'></a>"
                f"<div style='font-size:12px;line-height:1.35;"
                f"margin-top:4px'>"
                f"<span style='color:#c9cdd6'>{cn}</span><br>"
                f"<a href='{url}' target='_blank' rel='noopener' "
                f"style='color:#58A6FF;text-decoration:none'>{title}</a>"
                f"<div style='color:{_T.MUTED_2};margin-top:2px'>"
                f"{fmt_num(views)} views · {pub}</div></div>",
                unsafe_allow_html=True,
            )
    if len(new_videos) > 24:
        st.caption(f"Showing top 24 of {len(new_videos)} new videos "
                   "on this day. See the **Latest Videos** page for "
                   "the full feed.")
