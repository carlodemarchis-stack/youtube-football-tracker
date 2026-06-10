"""💰 World Cup 2026 — Sponsored.

Videos from the WC2026 cohort that disclose paid promotion (YouTube's
"Includes paid promotion" tag, stored on videos.has_paid_promotion),
scoped by the WC2026 confederation/team filter.

  - 🆕 Latest sponsored videos (newest first).
  - 👁️ Top sponsored videos by views.
  - 📊 Sponsored videos per channel (top 25).

Mirrors the Lab Sponsored page but on the WC2026 cohort + filter.
"""
from __future__ import annotations

import os
from collections import Counter

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.filters import render_page_subtitle
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)
from src.wc2026_badge import wc2026_badge, CONF_COLOR
from src.season_top import render_top_season_videos_table
from src.auth import require_login

load_dotenv()
require_login()

st.title("💰 World Cup 2026 — Sponsored")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = _cached_channels(db)
wc_all = [c for c in all_channels
          if (c.get("competitions") or {}).get("wc2026")]
if not wc_all:
    st.warning("No channels with `competitions.wc2026` set yet.")
    st.stop()

_confed, _team = get_wc2026_filter()
_scope_channels = scope_wc2026(wc_all, _confed, _team)
if not _scope_channels:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_confed, _team)}**.")
    st.stop()

_ch_by_id = {c["id"]: c for c in wc_all}
scope_ids = [c["id"] for c in _scope_channels if c.get("id")]

render_page_subtitle(
    "WC2026 cohort videos that disclose paid promotion (brand deals / "
    "product placement).",
    caveat=("Source: YouTube's 'Includes paid promotion' flag. Covers "
            "every video these channels publish — not just WC2026 "
            "content."),
)
if _confed or _team:
    st.caption(f"Filtered: {_wc_scope_label(_confed, _team)}")


@st.cache_data(ttl=300, show_spinner=False)
def _sponsored(channel_ids: tuple[str, ...], order_col: str,
               limit: int = 50) -> list[dict]:
    if not channel_ids:
        return []
    return (db.client.table("videos")
            .select("id,youtube_video_id,title,channel_id,thumbnail_url,"
                    "duration_seconds,format,published_at,view_count,"
                    "like_count,comment_count,category")
            .in_("channel_id", list(channel_ids))
            .eq("has_paid_promotion", True)
            .order(order_col, desc=True)
            .limit(limit)
            .execute().data) or []


_ids_t = tuple(scope_ids)
_latest = _sponsored(_ids_t, "published_at")
_top = _sponsored(_ids_t, "view_count")

if not _latest and not _top:
    st.info("No sponsored videos found in the current filter scope.")
    st.stop()

_badge = lambda ch: wc2026_badge(ch, 14)

# ── Latest ────────────────────────────────────────────────────────
render_top_season_videos_table(
    _latest, _ch_by_id,
    header="🆕 Latest sponsored videos",
    subtitle="Most recently published, newest first.",
    order_by="views", max_height=1200, badge_resolver=_badge,
)

st.markdown("---")

# ── Top views ─────────────────────────────────────────────────────
render_top_season_videos_table(
    _top, _ch_by_id,
    header="👁️ Top sponsored videos — by views",
    order_by="views", max_height=1200, badge_resolver=_badge,
)

st.markdown("---")


# ── Sponsored videos per channel (top 25) ─────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def _sponsored_counts(channel_ids: tuple[str, ...]) -> dict[str, int]:
    if not channel_ids:
        return {}
    out: list[str] = []
    off = 0
    while True:
        rows = (db.client.table("videos").select("channel_id")
                .in_("channel_id", list(channel_ids))
                .eq("has_paid_promotion", True)
                .range(off, off + 999).execute().data) or []
        out.extend(r["channel_id"] for r in rows if r.get("channel_id"))
        if len(rows) < 1000:
            break
        off += 1000
    return dict(Counter(out))


_counts = _sponsored_counts(_ids_t)
if _counts:
    _TOP_N = 25
    _ranked = sorted(_counts.items(), key=lambda kv: kv[1], reverse=True)
    _shown = _ranked[:_TOP_N]
    # Per-channel colour = its confederation brand colour (the same
    # palette the WC2026 badges use). Reverse so highest is at top.
    def _conf_color(cid: str) -> str:
        w = ((_ch_by_id.get(cid) or {}).get("competitions") or {}).get("wc2026") or {}
        return CONF_COLOR.get(w.get("confederation") or "", "#E0A800")
    _df = pd.DataFrame(
        [{"Channel": (_ch_by_id.get(cid) or {}).get("name", "?"),
          "Sponsored videos": n,
          "_color": _conf_color(cid)}
         for cid, n in _shown]
    ).iloc[::-1]
    st.subheader("📊 Sponsored videos per channel")
    _cap = (f"WC2026 channels in scope with the most disclosed "
            f"paid-promotion videos. {len(_counts)} channel(s) have ≥1; ")
    _cap += (f"showing the top {_TOP_N}." if len(_counts) > _TOP_N
             else "showing all.")
    st.caption(_cap)
    fig = px.bar(_df, x="Sponsored videos", y="Channel",
                 orientation="h", text="Sponsored videos")
    fig.update_traces(marker_color=list(_df["_color"]),
                      textposition="outside", cliponaxis=False)
    fig.update_layout(
        height=max(320, 26 * len(_shown) + 80),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, l=0, r=30, b=20),
        xaxis=dict(title="", showgrid=True,
                   gridcolor="rgba(255,255,255,0.08)"),
        yaxis=dict(title=""),
    )
    st.plotly_chart(fig, width="stretch")
