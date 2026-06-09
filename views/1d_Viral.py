"""Viral Videos — biggest breakouts in their first 30 days.

Extracted from the 30-Day Trends page into its own surface (sits right
after Latest Videos). Same metric: videos ≤30 days old ranked by the
"Blend" score = views gained ÷ √subscribers, momentum-gated so only
genuine spikes qualify. Reads the precomputed `viral` list from the
trends_30d cache (10 for Z1/Z2, 5 for Z3); live fallback on cache miss.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import streamlit as st
from src.filters import is_top5_cohort
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
from src.analytics import fmt_num
from src import theme as _T

load_dotenv()
require_login()

st.title("🚀 Viral Videos")

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
all_channels = [
    c for c in all_channels
    if is_top5_cohort(c)
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
    f"Breakouts in the trailing {LOOKBACK_DAYS} days · {len(ch_ids)} channels in scope",
    updated_raw=_cached_last_fetch(db, "daily_snapshot"),
)

if not ch_ids:
    st.info("No channels in the current filter scope.")
    st.stop()

today_cet = datetime.now(CET).date()
start_d = today_cet - timedelta(days=LOOKBACK_DAYS)
end_d = today_cet  # exclusive
window_dates = [(start_d + timedelta(days=i)).isoformat()
                for i in range(LOOKBACK_DAYS)]
_cids_tuple = tuple(sorted(ch_ids))

# ── Cache scope ───────────────────────────────────────────────────────
if ONE_CLUB:
    _scope_key = _dc.scope_club(g_club["id"])
elif g_league:
    _scope_key = _dc.scope_league(g_league)
else:
    _scope_key = _dc.scope_all()
_cached_row = _cached_dc_read(db, "trends_30d", _scope_key)
_cached_payload = (_cached_row or {}).get("payload") if _cached_row else None
USE_CACHE = bool(_cached_payload and _cached_payload.get("dates"))

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


# ── Viral list (cache-first; live fallback) ───────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)
def _load_viral(cids_tuple: tuple[str, ...], since_date: str,
                until_date: str, _subs: dict, limit: int) -> list[dict]:
    if not cids_tuple:
        return []
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
    raw = {vid: sorted(s) for vid, s in series.items()}
    # First pass to pick candidates, then fetch publish dates only for those
    # and drop each one's catch-up first delta before the final scoring.
    cand = []
    for vid, s0 in raw.items():
        sc = _dc._score_viral_series(s0, int(_subs.get(vch[vid], 0)))
        if sc:
            cand.append((sc[0], vid))
    cand.sort(reverse=True)
    cand_ids = [vid for _, vid in cand[:max(limit * 5, 50)]]
    pub: dict[str, str] = {}
    for cs in range(0, len(cand_ids), 200):
        rs = (db.client.table("videos").select("id,published_at")
              .in_("id", cand_ids[cs:cs + 200]).execute().data) or []
        for r in rs:
            pub[r["id"]] = r.get("published_at")
    scored = []
    for vid in cand_ids:
        sc = _dc._score_viral_series(
            _dc._drop_catchup_first(raw[vid], pub.get(vid)),
            int(_subs.get(vch[vid], 0)))
        if not sc:
            continue
        blend, peak, total, s = sc
        sub = max(int(_subs.get(vch[vid], 0)), 1)
        scored.append((blend, vid, peak, total, sub, s))
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


_name_by_id = {c["id"]: (c.get("name") or "?") for c in all_channels}
_viral_limit = 5 if ONE_CLUB else 10
_viral = list((_cached_payload or {}).get("viral") or []) if USE_CACHE else []
# Cache stores up to 10/10/5; trim to this page's scope limit.
_viral = _viral[:_viral_limit]
if not _viral:
    _subs_by_id = {c["id"]: int(c.get("subscriber_count") or 0)
                   for c in all_channels}
    _viral = _load_viral(_cids_tuple, start_d.isoformat(), end_d.isoformat(),
                         _subs_by_id, _viral_limit)

st.caption(
    "Videos ≤30 days old, ranked by views gained ÷ √subscribers (magnitude "
    "balanced with audience reach), momentum-gated so only genuine spikes "
    "qualify. Each row's chart is its 30-day daily-views trajectory."
)

if not _viral:
    st.info("No videos cleared the viral threshold in this scope over the "
            "last 30 days.")
    st.stop()

_ch_by_id = {c["id"]: c for c in all_channels}
_cmap = get_global_color_map()
_dmap = get_global_color_map_dual()

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
        _badge = channel_badge(_ch, _cmap, _dmap, 16)
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
