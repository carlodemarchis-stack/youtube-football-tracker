"""FIFA World Cup 2026 — Viral Videos.

The WC2026 analog of the top-5 Viral page (views/1d_Viral.py): the
biggest breakouts in their first 30 days across the WC2026 cohort —
48 national teams + FIFA + 6 confederations + per-country alt channels.
Same metric: videos ≤30 days old ranked by the "Blend" score =
views gained ÷ √subscribers, momentum-gated so only genuine spikes
qualify.

Reads the precomputed `viral` list from the `wc2026_trends` cache
(top-10, cohort-wide). When the WC2026 confederation/team filter
narrows the scope, recomputes live for that scope (cache holds only
the cohort-wide list).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
import altair as alt
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_login
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src import dashboard_cache as _dc
from src.dot import channel_badge
from src.analytics import fmt_num
from src import theme as _T

load_dotenv()
require_login()

st.title("🚀 World Cup 2026 — Viral Videos")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()
db = Database(SUPABASE_URL, SUPABASE_KEY)

CET = ZoneInfo("Europe/Rome")
LOOKBACK_DAYS = 30

channels = _cached_channels(db)
wc = [c for c in channels if (c.get("competitions") or {}).get("wc2026")]
if not wc:
    st.warning("No channels with `competitions.wc2026` set yet — see the "
               "**All Channels** page for setup steps.")
    st.stop()

# WC2026 sub-app filter (confederation → team), shared across WC2026 pages.
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)
_wc_confed, _wc_team = get_wc2026_filter()
wc = scope_wc2026(wc, _wc_confed, _wc_team)
if not wc:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_wc_confed, _wc_team)}**.")
    st.stop()
FILTERED = bool(_wc_confed or _wc_team)

ch_ids = [c["id"] for c in wc if c.get("id")]
_cids_tuple = tuple(sorted(ch_ids))

st.caption(
    "Videos ≤30 days old, ranked by views gained ÷ √subscribers "
    "(magnitude balanced with audience reach), momentum-gated so only "
    "genuine spikes qualify. Each row's chart is its 30-day daily-views "
    "trajectory."
)
if FILTERED:
    st.caption(f"Filtered: {_wc_scope_label(_wc_confed, _wc_team)} · "
               f"{len(ch_ids)} channel(s)")

today_cet = datetime.now(CET).date()
start_d = today_cet - timedelta(days=LOOKBACK_DAYS)
end_d = today_cet  # exclusive
window_dates = [(start_d + timedelta(days=i)).isoformat()
                for i in range(LOOKBACK_DAYS)]

# ── Chart geometry (daily ticks + Sunday rules), shared with Trends ───
_CHART_HEIGHT = 220
_Y_AXIS_GUTTER = 60
_X_AXIS = alt.Axis(labelAngle=-45, title=None, format="%b %d",
                   tickCount={"interval": "day", "step": 1}, labelOverlap=False)
_sundays = [d for d in window_dates if date.fromisoformat(d).weekday() == 6]
_sun_df = pd.DataFrame({"Date": _sundays})


def _with_sundays(chart):
    if not _sundays:
        return chart
    rule = alt.Chart(_sun_df).mark_rule(
        color=_T.MUTED, strokeDash=[3, 3], opacity=0.45, strokeWidth=1,
    ).encode(x="Date:T")
    return (rule + chart).properties(height=_CHART_HEIGHT)


# ── Viral list (cache-first for the full cohort; live for sub-scopes) ──
VIRAL_FLOOR = 100_000
VIRAL_SPIKE = 3.0


@st.cache_data(ttl=1800, show_spinner=False)
def _load_viral(cids_tuple: tuple[str, ...], since_date: str,
                until_date: str, _subs: dict, limit: int) -> list[dict]:
    if not cids_tuple:
        return []
    import math
    import statistics
    series: dict[str, list] = {}
    vch: dict[str, str] = {}
    cids = list(cids_tuple)
    for cs in range(0, len(cids), 50):
        chunk = cids[cs:cs + 50]
        off = 0
        while True:
            rs = (db.client.table("video_daily_deltas")
                  .select("video_id,channel_id,captured_date,view_delta")
                  .in_("channel_id", chunk)
                  .gte("captured_date", since_date)
                  .lt("captured_date", until_date)
                  .order("captured_date")
                  .range(off, off + 999).execute().data) or []
            for r in rs:
                series.setdefault(r["video_id"], []).append(
                    (r["captured_date"], int(r.get("view_delta") or 0)))
                vch[r["video_id"]] = r["channel_id"]
            if len(rs) < 1000:
                break
            off += 1000
    if not series:
        return []
    scored = []
    for vid, s in series.items():
        s = _dc._gapfill_series(s)   # spread catch-up days across the gaps
        deltas = [d for _, d in s]
        peak = max(deltas)
        med = statistics.median(deltas) if deltas else 0
        if peak < VIRAL_FLOOR or peak < VIRAL_SPIKE * max(med, 1):
            continue
        total = sum(deltas)
        sub = max(int(_subs.get(vch[vid], 0)), 1)
        scored.append((total / math.sqrt(sub), vid, peak, total, sub, s))
    scored.sort(reverse=True)
    top = scored[:limit]
    ids = [t[1] for t in top]
    meta: dict[str, dict] = {}
    for cs in range(0, len(ids), 100):
        rs = (db.client.table("videos")
              .select("id,youtube_video_id,title,channel_id,"
                      "thumbnail_url,published_at")
              .in_("id", ids[cs:cs + 100]).execute().data) or []
        for r in rs:
            meta[r["id"]] = r
    out = []
    for score, vid, peak, total, sub, s in top:
        m = meta.get(vid)
        if not m:
            continue
        out.append({
            "youtube_video_id": m.get("youtube_video_id"),
            "title": m.get("title") or "",
            "thumbnail_url": m.get("thumbnail_url") or "",
            "published_at": m.get("published_at"),
            "channel_id": m.get("channel_id"),
            "peak": int(round(peak)), "total": int(round(total)), "subs": sub,
            "reach": total / sub,
            "series": [[d, int(round(x))] for d, x in s],
        })
    return out


# Cohort-wide → read the precomputed cache. Sub-scope → recompute live.
_viral: list[dict] = []
if not FILTERED:
    _row = _cached_dc_read(db, "wc2026_trends", _dc.scope_all())
    _payload = (_row or {}).get("payload") if _row else None
    if _payload and _payload.get("viral") is not None:
        _viral = list(_payload.get("viral") or [])
if not _viral:
    _subs_by_id = {c["id"]: int(c.get("subscriber_count") or 0) for c in wc}
    _viral = _load_viral(_cids_tuple, start_d.isoformat(), end_d.isoformat(),
                         _subs_by_id, 10)

if not _viral:
    st.info("No videos cleared the viral threshold in this scope over the "
            "last 30 days.")
    st.stop()

_ch_by_id = {c["id"]: c for c in wc}
_name_by_id = {c["id"]: (c.get("name") or "?") for c in wc}

for _i, _v in enumerate(_viral, 1):
    _url = f"https://www.youtube.com/watch?v={_v['youtube_video_id']}"
    _pub = (_v["published_at"] or "")[:10]
    _c0, _cT, _cM, _cR = st.columns([0.35, 1.3, 6, 2.6])
    with _c0:
        st.markdown(f"### {_i}")
    with _cT:
        _thumb = _v.get("thumbnail_url") or ""
        if _thumb:
            st.markdown(
                f"<a href='{_url}' target='_blank' rel='noopener' "
                f"title='Open on YouTube'>"
                f"<img src='{_thumb}' style='width:100%;border-radius:6px'></a>",
                unsafe_allow_html=True,
            )
    with _cM:
        _ch = _ch_by_id.get(_v["channel_id"]) or {}
        _badge = channel_badge(_ch, None, None, 16)
        _cn = _ch.get("name") or _name_by_id.get(_v["channel_id"], "?")
        _ttl = _v["title"][:90].replace("<", "&lt;").replace(">", "&gt;")
        st.markdown(
            f"<div style='line-height:1.5'>"
            f"<div style='font-size:13px'>{_badge}"
            f"<span style='vertical-align:middle;margin-left:6px;"
            f"color:#c9cdd6'>{_cn}</span></div>"
            f"<a href='{_url}' target='_blank' rel='noopener' "
            f"style='color:#58A6FF;font-weight:600;font-size:15px;"
            f"text-decoration:none'>{_ttl}</a>"
            f"<div style='color:{_T.MUTED_2};font-size:12px;margin-top:2px'>"
            f"published {_pub}</div></div>",
            unsafe_allow_html=True,
        )
    with _cR:
        st.markdown(
            f"<div style='text-align:right;font-size:13px;line-height:1.5'>"
            f"<b style='color:{_T.POS};font-size:16px'>+{fmt_num(_v['total'])}</b> "
            f"views (30d)<br>"
            f"<span style='color:{_T.MUTED_2}'>peak +{fmt_num(_v['peak'])}/day · "
            f"{_v['reach'] * 100:.0f}% of subs</span></div>",
            unsafe_allow_html=True,
        )
    # Trajectory chart always visible (no expander).
    _vdf = pd.DataFrame([{"Date": d, "Δ Views": x} for d, x in _v["series"]])
    _vdf["Date"] = pd.to_datetime(_vdf["Date"])
    _vc = alt.Chart(_vdf).mark_area(
        line={"color": _T.ACCENT}, color=alt.Gradient(
            gradient="linear",
            stops=[alt.GradientStop(color=_T.BG, offset=0),
                   alt.GradientStop(color=_T.ACCENT, offset=1)],
            x1=1, x2=1, y1=1, y2=0),
        opacity=0.6,
    ).encode(
        x=alt.X("Date:T", axis=_X_AXIS),
        y=alt.Y("Δ Views:Q", title=None,
                axis=alt.Axis(format="~s", minExtent=_Y_AXIS_GUTTER)),
        tooltip=[alt.Tooltip("Date:T", title="Date", format="%a %b %d"),
                 alt.Tooltip("Δ Views:Q", format=",")],
    ).properties(height=_CHART_HEIGHT)
    st.altair_chart(_with_sundays(_vc), width="stretch")
    st.markdown("<hr style='border:none;border-top:1px solid #2a2c34;"
                "margin:6px 0 20px 0'>", unsafe_allow_html=True)
