"""💰 World Cup 2026 — Sponsored.

Two layers on the WC2026 cohort, scoped by the confederation/team filter.

DISCLOSED (YouTube's "Includes paid promotion" flag):
  - 🆕 Latest sponsored videos.
  - 👁️ Top sponsored videos by views.
  - 📊 Sponsored videos per channel.

DETECTED (our heuristic — branded_content_candidates): scan titles /
descriptions for explicit sponsor language and extract the brand,
surfacing branded content the channel never disclosed. Speculative:
  - 🔎 Branded-content candidates (with detected sponsor + signal).
  - 📊 Channels with the most branded candidates.

Mirrors the Top-5 Sponsored page on the WC2026 cohort + filter.
"""
from __future__ import annotations

import os
from collections import Counter

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src import components_compat as components
from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.filters import render_page_subtitle
from src.analytics import fmt_num
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)
from src.wc2026_badge import wc2026_badge, CONF_COLOR
from src.season_top import render_top_season_videos_table
from src.auth import require_login
from src import theme as _T

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


# Per-channel colour = its confederation brand colour (the same palette
# the WC2026 badges use). Module-scoped so both per-channel charts use it.
def _conf_color(cid: str) -> str:
    w = ((_ch_by_id.get(cid) or {}).get("competitions") or {}).get("wc2026") or {}
    return CONF_COLOR.get(w.get("confederation") or "", "#E0A800")


_counts = _sponsored_counts(_ids_t)
if _counts:
    _TOP_N = 25
    _ranked = sorted(_counts.items(), key=lambda kv: kv[1], reverse=True)
    _shown = _ranked[:_TOP_N]
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


# ════════════════════════════════════════════════════════════════
#  EXPERIMENTAL — branded-content DETECTION (beyond YouTube's flag)
# ════════════════════════════════════════════════════════════════
# Everything above reads YouTube's "paid promotion" flag. Below is our
# own DETECTION (branded_content_candidates): we scan titles /
# descriptions for explicit sponsor language ("presented by X",
# multilingual) and extract the sponsor — surfacing branded content
# the channel never ticked the YouTube box for. Heuristic → speculative.
st.markdown("---")

_SIGNAL_LABEL = {
    "presented_by": "“presented by …”",
    "powered_by": "“powered by …”",
    "partnership": "“in partnership with …”",
    "sponsored_by": "“sponsored by …”",
    "brought_by": "“brought to you by …”",
    "promo_code": "promo / discount code",
    "cjk_provided": "提供/협찬 (provided by)",
    "ar_sponsor": "برعاية (sponsored)",
    "youtube_flag": "YouTube-disclosed",
}


@st.cache_data(ttl=300, show_spinner=False)
def _branded_candidates(channel_ids: tuple[str, ...], limit: int = 40):
    if not channel_ids:
        return []
    cand = []
    off = 0
    while True:
        rows = (db.client.table("branded_content_candidates")
                .select("video_id,brand_canonical,signals,has_paid_flag")
                .in_("channel_id", list(channel_ids))
                .eq("is_branded", True)
                # Text-detected ONLY — exclude videos YouTube already
                # flags (those are in the disclosed sections above).
                .eq("has_paid_flag", False)
                .not_.is_("brand_canonical", "null")
                .range(off, off + 999).execute().data) or []
        cand.extend(rows)
        if len(rows) < 1000:
            break
        off += 1000
    if not cand:
        return []
    cmap = {c["video_id"]: c for c in cand}
    ids = list(cmap.keys())
    vids = []
    for i in range(0, len(ids), 150):
        vs = (db.client.table("videos")
              .select("id,youtube_video_id,title,channel_id,"
                      "thumbnail_url,view_count")
              .in_("id", ids[i:i + 150]).execute().data) or []
        vids.extend(vs)
    vids.sort(key=lambda v: -(int(v.get("view_count") or 0)))
    out = []
    for v in vids[:limit]:
        c = cmap.get(v["id"]) or {}
        out.append({**v,
                    "brand": c.get("brand_canonical"),
                    "signals": [s for s in (c.get("signals") or [])
                                if s != "youtube_flag"],
                    "flag": c.get("has_paid_flag")})
    return out


@st.cache_data(ttl=300, show_spinner=False)
def _branded_counts(channel_ids: tuple[str, ...]) -> dict[str, int]:
    """Text-detected branded candidates per channel — undisclosed only
    (has_paid_flag=False), matching the candidates table above."""
    if not channel_ids:
        return {}
    out: list[str] = []
    off = 0
    while True:
        rows = (db.client.table("branded_content_candidates")
                .select("channel_id")
                .in_("channel_id", list(channel_ids))
                .eq("is_branded", True)
                .eq("has_paid_flag", False)
                .range(off, off + 999).execute().data) or []
        out.extend(r["channel_id"] for r in rows if r.get("channel_id"))
        if len(rows) < 1000:
            break
        off += 1000
    return dict(Counter(out))


_cands = _branded_candidates(_ids_t)
st.subheader("🔎 Branded-content candidates — detected from the text")
st.caption(
    "⚠️ **Experimental / speculative — and deliberately EXCLUDES "
    "anything YouTube already flags above.** These are WC2026 videos "
    "that did **not** carry YouTube's “paid promotion” disclosure, yet "
    "whose title/description contains explicit sponsor language "
    "(“presented by …”, “powered by …”, plus ES/IT/DE/FR/PT and "
    "CJK/Arabic variants) — so we extracted the brand ourselves. This "
    "is the *undisclosed* branded content the flag misses. It's a "
    "heuristic, so treat it as an informed guess, not a confirmation."
)

if not _cands:
    st.info("No text-detected branded candidates in the current scope.")
else:
    _rows_html = ""
    for v in _cands:
        ch = _ch_by_id.get(v.get("channel_id")) or {}
        yt = v.get("youtube_video_id") or ""
        url = f"https://www.youtube.com/watch?v={yt}" if yt else "#"
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        thumb = v.get("thumbnail_url") or ""
        brand = (v.get("brand") or "—").replace("<", "&lt;")
        sigs = " · ".join(_SIGNAL_LABEL.get(s, s) for s in v["signals"]) or "—"
        badge = wc2026_badge(ch, 16) if ch else ""
        _rows_html += f"""<tr onclick="window.open('{url}','_blank','noopener')" style="cursor:pointer;border-bottom:1px solid {_T.SURFACE}">
          <td style="padding:7px 10px"><img src="{thumb}" style="width:96px;height:54px;object-fit:cover;border-radius:4px;display:block"></td>
          <td style="padding:7px 10px;vertical-align:top">
            <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px">{badge}<span style="color:{_T.MUTED_2};font-size:12px">{ch.get('name','?')}</span></div>
            <div style="color:{_T.TEXT};font-size:13px;line-height:1.35">{title}</div>
          </td>
          <td style="padding:7px 10px;vertical-align:top;white-space:nowrap">
            <span style="background:#E0A80022;color:#E0A800;padding:2px 9px;border-radius:11px;font-size:12px;font-weight:600">{brand}</span>
            <div style="color:{_T.MUTED};font-size:11px;margin-top:4px">{sigs}</div>
          </td>
          <td style="padding:7px 10px;text-align:right;color:{_T.TEXT};font-size:13px;vertical-align:top;white-space:nowrap">{fmt_num(int(v.get('view_count') or 0))}</td>
        </tr>"""

    st.caption(f"Top {len(_cands)} by views — all undisclosed "
               "(none carry YouTube's paid-promotion flag).")
    components.html(f"""
    <style>
      * {{ box-sizing:border-box; }}
      body {{ margin:0; font-family:"Source Sans Pro",sans-serif; background:{_T.BG}; }}
      table {{ width:100%; border-collapse:collapse; }}
      th {{ text-align:left; color:{_T.MUTED}; font-size:11px; font-weight:600;
            text-transform:uppercase; letter-spacing:0.4px; padding:6px 10px;
            border-bottom:1px solid {_T.MUTED}; position:sticky; top:0; background:{_T.BG}; }}
      tr:hover td {{ background:{_T.SURFACE}; }}
      th:last-child, td:last-child {{ text-align:right; }}
    </style>
    <table>
      <thead><tr><th>Video</th><th></th><th>Detected sponsor · signal</th><th>Views</th></tr></thead>
      <tbody>{_rows_html}</tbody>
    </table>
    """, height=min(1200, 80 + 70 * len(_cands)), scrolling=True)

st.markdown("---")

# ── Channels with most branded candidates (detection set) ─────────
_bcounts = _branded_counts(_ids_t)
if _bcounts:
    _BTOP = 25
    _branked = sorted(_bcounts.items(), key=lambda kv: kv[1], reverse=True)
    _bshown = _branked[:_BTOP]
    _bdf = pd.DataFrame(
        [{"Channel": (_ch_by_id.get(cid) or {}).get("name", "?"),
          "Candidates": n,
          "_color": _conf_color(cid)}
         for cid, n in _bshown]
    ).iloc[::-1]
    st.subheader("📊 Channels with the most branded candidates")
    _bc = (f"Text-detected, **undisclosed** branded candidates per "
           f"channel (excludes anything with YouTube's flag — same set "
           f"as the table above). {len(_bcounts)} channel(s) have ≥1; ")
    _bc += (f"showing the top {_BTOP}." if len(_bcounts) > _BTOP
            else "showing all.")
    st.caption(_bc)
    bfig = px.bar(_bdf, x="Candidates", y="Channel", orientation="h",
                  text="Candidates")
    bfig.update_traces(marker_color=list(_bdf["_color"]),
                       textposition="outside", cliponaxis=False)
    bfig.update_layout(
        height=max(320, 26 * len(_bshown) + 80),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=10, l=0, r=30, b=20),
        xaxis=dict(title="", showgrid=True,
                   gridcolor="rgba(255,255,255,0.08)"),
        yaxis=dict(title=""),
    )
    st.plotly_chart(bfig, width="stretch")
