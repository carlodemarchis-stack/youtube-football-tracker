from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    get_global_filter, get_league_for_channel,
)
from src.channels import LEAGUE_FLAG

load_dotenv()
require_login()

st.title("Daily Recap")
st.caption("What happened yesterday across every tracked club — new videos, growth, momentum.")

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

ch_by_id = {c["id"]: c for c in all_channels}
color_map = get_global_color_map()
dual = get_global_color_map_dual()

# ── Read global filter ──────────────────────────────────────
g_league, g_club = get_global_filter()
ONE_CLUB = g_club is not None
# The channel_id(s) to restrict computations to (None = all)
if ONE_CLUB:
    filter_cids = {g_club["id"]}
elif g_league:
    filter_cids = {c["id"] for c in all_channels if get_league_for_channel(c) == g_league}
else:
    filter_cids = None  # all

# ── Date picker ────────────────────────────────────────────────
# Default to the second-most-recent snapshot date (= last COMPLETE day).
# The latest snapshot was captured early this morning — so its date has almost
# no published videos yet.  The day before is the last full 24-hour window.
try:
    _recent_snaps = db.client.table("channel_snapshots") \
        .select("captured_date").order("captured_date", desc=True).limit(2000).execute().data
    _snap_dates = sorted({s["captured_date"] for s in _recent_snaps}, reverse=True)
    if len(_snap_dates) >= 2:
        default_day = date.fromisoformat(_snap_dates[1])  # second-most-recent
    elif _snap_dates:
        default_day = date.fromisoformat(_snap_dates[0])  # only one day exists
    else:
        default_day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
except Exception:
    default_day = (datetime.now(timezone.utc) - timedelta(days=1)).date()

picked = st.date_input("Recap for", value=default_day, max_value=datetime.now(timezone.utc).date())
day = picked if isinstance(picked, date) else default_day
prev_day = day - timedelta(days=1)

day_iso = day.isoformat()
prev_iso = prev_day.isoformat()

# ── Load snapshots for day + recent lookback window ──────────
# We diff vs the most recent snapshot strictly older than `day` (per channel),
# so gaps in the daily cron don't break deltas.
LOOKBACK_DAYS = 14
lookback_iso = (day - timedelta(days=LOOKBACK_DAYS)).isoformat()
with st.spinner(f"Loading snapshots for {day_iso}…"):
    chan_snaps = db.get_all_snapshots(since_date=lookback_iso)
    vid_snaps_day = db.get_video_snapshots_for_date(day_iso)
    vid_snaps_range = db.get_video_snapshots_range(lookback_iso, day_iso)

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

# To filter video_snapshots we need to know which videos belong to the selected channel(s).
# Easiest path: look up the channel_id on each referenced video.
if filter_cids is not None:
    _needed_vids = {s["video_id"] for s in vid_snaps_day} | {s["video_id"] for s in vid_snaps_range}
    _vid_rows = []
    _batch = list(_needed_vids)
    for i in range(0, len(_batch), 500):
        _vid_rows += (
            db.client.table("videos")
            .select("id,channel_id")
            .in_("id", _batch[i:i+500])
            .execute()
            .data or []
        )
    _vid_channel = {r["id"]: r["channel_id"] for r in _vid_rows}
    vid_snaps_day = [s for s in vid_snaps_day if _vid_channel.get(s["video_id"]) in filter_cids]
    vid_snaps_range = [s for s in vid_snaps_range if _vid_channel.get(s["video_id"]) in filter_cids]

vmap_day = {s["video_id"]: s for s in vid_snaps_day}
# Previous snapshot per video: latest captured_date strictly < day_iso
vmap_prev: dict[str, dict] = {}
vprev_date: dict[str, str] = {}
for s in vid_snaps_range:
    d = s.get("captured_date", "")
    if d >= day_iso:
        continue
    vid = s["video_id"]
    if d > vprev_date.get(vid, ""):
        vprev_date[vid] = d
        vmap_prev[vid] = s

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
# (published_at is ISO timestamp; filter to the UTC day window)
start_ts = f"{day_iso}T00:00:00+00:00"
end_ts = f"{(day + timedelta(days=1)).isoformat()}T00:00:00+00:00"
_q_new = (
    db.client.table("videos")
    .select("*")
    .gte("published_at", start_ts)
    .lt("published_at", end_ts)
)
if filter_cids is not None:
    _q_new = _q_new.in_("channel_id", list(filter_cids))
new_video_rows = _q_new.execute().data or []

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
    k1.metric("🎬 New videos", fmt_num(total_new_videos))
    k2.metric("📺 Long / Shorts / Live", _fmt_combined)
    k3.metric("👁️ Δ Channel Views", f"{'+' if total_view_delta >= 0 else ''}{fmt_num(total_view_delta)}")
else:
    # Most active club: posted most videos yesterday
    club_new_counts: dict[str, int] = {}
    for v in new_video_rows:
        club_new_counts[v["channel_id"]] = club_new_counts.get(v["channel_id"], 0) + 1
    most_active = max(club_new_counts.items(), key=lambda kv: kv[1], default=(None, 0))
    most_active_name = ch_by_id.get(most_active[0], {}).get("name", "—") if most_active[0] else "—"

    _fmt_combined = f"{_fmt_counts['long']} / {_fmt_counts['short']} / {_fmt_counts['live']}"
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🎬 New videos", fmt_num(total_new_videos))
    k2.metric("📺 Long / Shorts / Live", _fmt_combined)
    k3.metric("👁️ Δ Channel Views", f"{'+' if total_view_delta >= 0 else ''}{fmt_num(total_view_delta)}")
    k4.metric("🔥 Most active", f"{most_active_name}", help=f"{most_active[1]} videos posted")

# ── Per-league summary ────────────────────────────────────────
# Hide when viewing a single club (it would be a one-row table for that club's league).
if not ONE_CLUB:
    st.markdown("---")
    st.subheader("📊 Per-league summary")
    lg_agg: dict[str, dict] = {}
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

    if lg_agg:
        lg_rows = ""
        for lg, agg in sorted(lg_agg.items(), key=lambda kv: kv[1]["view_delta"], reverse=True):
            view_col = "#00CC96" if agg["view_delta"] > 0 else ("#EF553B" if agg["view_delta"] < 0 else "#888")
            _lg_fmt = f"{agg['long']} / {agg['short']} / {agg['live']}"
            lg_rows += f"""<tr>
                <td style="padding:8px 12px;font-weight:600">{LEAGUE_FLAG.get(lg, '')} {lg}</td>
                <td style="padding:8px 12px;text-align:right">{agg['clubs']}</td>
                <td style="padding:8px 12px;text-align:right">{agg['new_videos']}</td>
                <td style="padding:8px 12px;text-align:right">{_lg_fmt}</td>
                <td style="padding:8px 12px;text-align:right;color:{view_col}">{'+' if agg['view_delta'] >= 0 else ''}{fmt_num(agg['view_delta'])}</td>
            </tr>"""
        components.html(f"""
        <style>
          .lg {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                 font-family:"Source Sans Pro",sans-serif; }}
          .lg th {{ padding:8px 12px; border-bottom:2px solid #444; text-align:left; }}
          .lg td {{ border-bottom:1px solid #262730; }}
        </style>
        <table class="lg"><thead><tr>
          <th>League</th>
          <th style="text-align:right">Channels</th>
          <th style="text-align:right">Videos</th>
          <th style="text-align:right">Long / Shorts / Live</th>
          <th style="text-align:right">Δ Views</th>
        </tr></thead><tbody>{lg_rows}</tbody></table>
        """, height=len(lg_agg) * 38 + 50, scrolling=False)

# ── Gainer leaderboards side-by-side ─ skip when viewing one club ─
if ONE_CLUB:
    # New videos published by this club on the picked day
    st.subheader(f"🎬 {g_club['name']} — videos published on {day_iso}")
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
            pub_time = (v.get("published_at") or "")[11:16]
            rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
                <td style="padding:6px 12px"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px"></td>
                <td style="padding:6px 12px"><a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a></td>
                <td style="padding:6px 12px"><span style="color:{fmt_color}">{fmt_label}</span></td>
                <td style="padding:6px 12px">{v.get('category') or ''}</td>
                <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('view_count') or 0))}</td>
                <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('like_count') or 0))}</td>
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
          <th>Posted</th>
        </tr></thead><tbody>{rows}</tbody></table>
        """, height=len(_sorted_nv) * 80 + 80, scrolling=True)

    # No subscriber-gains leaderboard for a single club — it's a leaderboard of 1.
    col_l = None
    col_r = None
else:
    st.markdown("---")
    col_l, col_r = st.columns(2)


def _gainer_table(metric: str, title: str, icon: str, positive_is_good: bool = True) -> str:
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
        pct = (d / prev_val * 100) if prev_val else 0
        gainers.append({"name": ch["name"], "id": cid, "delta": d, "latest": latest, "pct": pct})
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
        dot = f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3)"></span>'
        rows_html += f"""<tr>
            <td style="padding:5px 10px;color:#888">{i}</td>
            <td style="padding:5px 10px">{dot}</td>
            <td style="padding:5px 10px">{g['name']}</td>
            <td style="padding:5px 10px;text-align:right">{fmt_num(g['latest'])}</td>
            <td style="padding:5px 10px;text-align:right;color:{col}">{sgn}{fmt_num(g['delta'])}</td>
            <td style="padding:5px 10px;text-align:right;color:{col};font-size:12px">{pct_s}</td>
        </tr>"""
    return f"""
    <div style="color:#FAFAFA;font-family:'Source Sans Pro',sans-serif">
    <h4 style="margin:0 0 8px 0">{icon} {title}</h4>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
    <thead><tr style="border-bottom:2px solid #444">
      <th style="padding:5px 10px;text-align:left">#</th>
      <th></th>
      <th style="padding:5px 10px;text-align:left">Club</th>
      <th style="padding:5px 10px;text-align:right">Current</th>
      <th style="padding:5px 10px;text-align:right">Δ</th>
      <th style="padding:5px 10px;text-align:right">%</th>
    </tr></thead>
    <tbody>{rows_html}</tbody></table></div>
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
            pub_counts.setdefault(name, {"count": 0, "name": name})
            pub_counts[name]["count"] += 1
        pub_sorted = sorted(pub_counts.values(), key=lambda r: r["count"], reverse=True)
        if pub_sorted:
            pub_html = ""
            for i, r in enumerate(pub_sorted[:25], 1):
                c1, c2 = dual.get(r["name"], (color_map.get(r["name"], "#636EFA"), "#FFFFFF"))
                dot = f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3)"></span>'
                pub_html += f"""<tr>
                    <td style="padding:5px 10px;color:#888">{i}</td>
                    <td style="padding:5px 10px">{dot}</td>
                    <td style="padding:5px 10px">{r['name']}</td>
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
              <th style="padding:5px 10px;text-align:right">Videos</th>
            </tr></thead>
            <tbody>{pub_html}</tbody></table></div>
            """, height=900)
        else:
            st.caption("No clubs published videos on this day.")

# ── Top trending season videos by Δ views (from video_snapshots) ──
st.markdown("---")
_trend_title_scope = f"{g_club['name']} — " if ONE_CLUB else ""
st.subheader(f"🚀 {_trend_title_scope}Top videos by Δ views on {day_iso}")
trending = []
for vid_dbid, snap in vmap_day.items():
    prev = vmap_prev.get(vid_dbid)
    if not prev:
        continue
    d = int(snap.get("view_count", 0) or 0) - int(prev.get("view_count", 0) or 0)
    if d <= 0:
        continue
    trending.append({"id": vid_dbid, "delta": d, "views": int(snap.get("view_count", 0) or 0)})

if not trending:
    st.caption("Need 2 consecutive days of video snapshots to compute deltas. Come back tomorrow.")
else:
    _top_n = 5 if ONE_CLUB else 15
    trending.sort(key=lambda t: t["delta"], reverse=True)
    top_ids = [t["id"] for t in trending[:_top_n]]
    # Resolve video metadata
    vid_rows = (
        db.client.table("videos")
        .select("*")
        .in_("id", top_ids)
        .execute()
        .data or []
    )
    vid_by_id = {v["id"]: v for v in vid_rows}

    rows = ""
    for i, t in enumerate(trending[:_top_n], 1):
        v = vid_by_id.get(t["id"])
        if not v:
            continue
        ch = ch_by_id.get(v["channel_id"]) or {}
        club_c1, _ = dual.get(ch.get("name", ""), (color_map.get(ch.get("name", ""), "#636EFA"), "#FFFFFF"))
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        pub = (v.get("published_at") or "")[:10]
        fmt = v.get("format") or "long"
        fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}.get(fmt, "#AAAAAA")
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}.get(fmt, fmt.title())
        rows += f"""<tr style="border-left:3px solid {club_c1}">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px"></a></td>
            <td style="padding:6px 12px">
                <div style="color:#AAA;font-size:12px;margin-bottom:2px">{ch.get('name', '?')} · <span style="color:{fmt_color}">{fmt_label}</span> · {pub}</div>
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
    </style>
    <table class="tv">
      <thead><tr>
        <th style="text-align:right">#</th>
        <th></th>
        <th>Video</th>
        <th style="text-align:right">Δ Views</th>
        <th style="text-align:right">Total Views</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """, height=min(15, len(trending)) * 86 + 80, scrolling=True)

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

if len(_all_dates) >= 2:
    # Count new videos per day
    _all_vids_in_range = (
        db.client.table("videos")
        .select("id,channel_id,published_at")
        .gte("published_at", f"{_all_dates[0]}T00:00:00+00:00")
        .lt("published_at", f"{(date.fromisoformat(_all_dates[-1]) + timedelta(days=1)).isoformat()}T00:00:00+00:00")
    )
    if filter_cids is not None:
        _all_vids_in_range = _all_vids_in_range.in_("channel_id", list(filter_cids))
    _all_vids_in_range = _all_vids_in_range.execute().data or []

    _vids_by_day: dict[str, int] = {}
    for v in _all_vids_in_range:
        vday = (v.get("published_at") or "")[:10]
        _vids_by_day[vday] = _vids_by_day.get(vday, 0) + 1

    trend_rows = []
    for i in range(1, len(_all_dates)):
        d = _all_dates[i]
        d_prev = _all_dates[i - 1]
        cur_snaps = _snap_by_date.get(d, {})
        prev_snaps = _snap_by_date.get(d_prev, {})
        dv_total = 0
        for cid, snap in cur_snaps.items():
            prev = prev_snaps.get(cid)
            if prev:
                dv_total += int(snap.get("total_views") or 0) - int(prev.get("total_views") or 0)
        nv = _vids_by_day.get(d, 0)
        trend_rows.append({"Date": d, "Δ Channel Views": dv_total, "New Videos": nv})

    if trend_rows:
        trend_df = pd.DataFrame(trend_rows)
        # Keep Date as string (YYYY-MM-DD) so x-axis shows only day labels
        trend_df = trend_df.set_index("Date")

        tc1, tc2 = st.columns(2)
        with tc1:
            st.caption("👁️ Δ Channel Views per day")
            st.line_chart(trend_df["Δ Channel Views"], color="#636EFA")
        with tc2:
            st.caption("🎬 New videos per day")
            st.line_chart(trend_df["New Videos"], color="#00CC96")
else:
    st.caption("Need at least 2 snapshot dates to show trends. Come back after the next cron run.")
