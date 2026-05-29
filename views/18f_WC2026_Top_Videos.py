"""🏆 World Cup 2026 — All-Time Top Videos.

Scope-adaptive list of the most-viewed videos across the WC2026
cohort. Reads the WC2026 (Confederation, Team) filter that app.py
renders above the page title:

  Z1  All confederations → Top 100 across all 62 channels.
  Z2  One confederation  → Top 100 within that confederation.
  Z3  One team           → Top 100 for that team alone.

Sort is strictly by view_count. The candidate pool is the videos
table — anything ingested by hourly_wc2026.py, daily_wc2026.py, or
the one-off popular backfill (scripts/refresh_popular_wc2026.py)
is eligible. Without the popular backfill the cohort top-100 is
biased toward in-season uploads; once it's run, the lifetime
iconic videos rise to the top organically.

Card layout mirrors views/18d_WC2026_Viral.py — same row vocabulary
so the WC2026 sub-app reads consistently.
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_login
from src.cached_db import get_all_channels as _cached_channels
from src.dot import channel_badge
from src.analytics import fmt_num
from src import theme as _T
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)

load_dotenv()
require_login()

st.title("🏆 World Cup 2026 — Top Videos")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)

# WC2026 cohort — full set first, then narrow with the filter.
wc_all = [c for c in _cached_channels(db)
          if (c.get("competitions") or {}).get("wc2026")]
if not wc_all:
    st.info("No WC2026 channels in the DB yet — run "
            "`python3 scripts/import_wc2026.py` first.")
    st.stop()

_confed, _team = get_wc2026_filter()
wc = scope_wc2026(wc_all, _confed, _team)
if not wc:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_confed, _team)}**.")
    st.stop()

# Three-level subtitle so the user always knows what "top" means.
if _team:
    _scope_text = f"📍 **{_team}** — top 100 most-viewed videos on this channel."
elif _confed:
    _scope_text = (f"🌐 **{_confed}** — top 100 most-viewed videos across "
                   f"{len(wc)} channel(s) in this confederation.")
else:
    _scope_text = (f"🌍 **All confederations** — top 100 most-viewed videos "
                   f"across the full {len(wc)}-channel WC2026 cohort.")
st.caption(_scope_text)
st.caption(
    "Sorted strictly by view count. The candidate pool is whatever's in "
    "our `videos` table — recent uploads (via daily/hourly ingestion) "
    "plus the popular all-time backfill. So this lifts iconic historical "
    "videos (e.g. classic goals) alongside today's content."
)


# ── Fetch top-100 in scope ────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _load_top_in_channels(channel_ids: tuple[str, ...], limit: int = 100):
    """Cached wrapper for db.get_top_videos_in_channels."""
    if not channel_ids:
        return []
    return db.get_top_videos_in_channels(list(channel_ids), limit=limit)


_ch_ids = tuple(c["id"] for c in wc)
videos = _load_top_in_channels(_ch_ids, limit=100)
if not videos:
    st.warning("No videos in the DB for this scope yet — once "
               "`scripts/refresh_popular_wc2026.py` runs (or the hourly "
               "ingest catches the first uploads), they'll appear here.")
    st.stop()


# ── KPIs ──────────────────────────────────────────────────────────
_t_views    = sum(int(v.get("view_count") or 0) for v in videos)
_t_likes    = sum(int(v.get("like_count") or 0) for v in videos)
_t_comments = sum(int(v.get("comment_count") or 0) for v in videos)
_avg_views  = _t_views // max(len(videos), 1)

_k1, _k2, _k3, _k4 = st.columns(4)
_k1.metric("🎬 Videos", str(len(videos)))
_k2.metric("👁️ Total views", fmt_num(_t_views))
_k3.metric("🎯 Avg views / video", fmt_num(_avg_views))
_k4.metric("❤️ Total likes", fmt_num(_t_likes))

st.markdown("---")

# ── Ranked list (same card vocabulary as the WC2026 Viral page) ──
_ch_by_id = {c["id"]: c for c in wc_all}

for _i, _v in enumerate(videos, 1):
    _yt = _v.get("youtube_video_id") or ""
    _url = f"https://www.youtube.com/watch?v={_yt}" if _yt else "#"
    _pub = (_v.get("published_at") or "")[:10]
    _views    = int(_v.get("view_count") or 0)
    _likes    = int(_v.get("like_count") or 0)
    _comments = int(_v.get("comment_count") or 0)

    _c0, _cT, _cM, _cR = st.columns([0.35, 1.3, 6, 2.6])
    with _c0:
        st.markdown(f"### {_i}")
    with _cT:
        _thumb = _v.get("thumbnail_url") or ""
        if _thumb:
            st.markdown(
                f"<a href='{_url}' target='_blank' rel='noopener' "
                f"title='Open on YouTube'>"
                f"<img src='{_thumb}' "
                f"style='width:100%;border-radius:6px'></a>",
                unsafe_allow_html=True,
            )
    with _cM:
        # `channels` joined dict comes from the supabase query; fall
        # back to our local cohort lookup if it's missing.
        _ch_join = _v.get("channels") or {}
        _ch = _ch_by_id.get(_v.get("channel_id")) or {}
        _cn = _ch_join.get("name") or _ch.get("name") or "?"
        _badge = channel_badge(_ch, None, None, 16)
        _ttl = (_v.get("title") or "")[:120]\
            .replace("<", "&lt;").replace(">", "&gt;")
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
            f"<b style='color:{_T.TEXT};font-size:16px'>{fmt_num(_views)}</b> "
            f"views<br>"
            f"<span style='color:{_T.MUTED_2}'>"
            f"{fmt_num(_likes)} likes · {fmt_num(_comments)} comments"
            f"</span></div>",
            unsafe_allow_html=True,
        )
    st.markdown(
        "<hr style='border:none;border-top:1px solid #2a2c34;"
        "margin:6px 0 16px 0'>",
        unsafe_allow_html=True,
    )
