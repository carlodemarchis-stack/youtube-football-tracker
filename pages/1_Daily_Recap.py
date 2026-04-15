from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    get_league_for_channel,
)

load_dotenv()

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

# ── Date picker ────────────────────────────────────────────────
default_day = (datetime.now(timezone.utc) - timedelta(days=1)).date()
picked = st.date_input("Recap for", value=default_day, max_value=datetime.now(timezone.utc).date())
day = picked if isinstance(picked, date) else default_day
prev_day = day - timedelta(days=1)

day_iso = day.isoformat()
prev_iso = prev_day.isoformat()

# ── Load snapshots for day and the previous day ───────────────
with st.spinner(f"Loading snapshots for {day_iso}…"):
    chan_snaps = db.get_all_snapshots(since_date=prev_iso)
    vid_snaps_day = db.get_video_snapshots_for_date(day_iso)
    vid_snaps_prev = db.get_video_snapshots_for_date(prev_iso)
chan_day = {s["channel_id"]: s for s in chan_snaps if s["captured_date"] == day_iso}
chan_prev = {s["channel_id"]: s for s in chan_snaps if s["captured_date"] == prev_iso}
vmap_day = {s["video_id"]: s for s in vid_snaps_day}
vmap_prev = {s["video_id"]: s for s in vid_snaps_prev}

if not chan_day and not vid_snaps_day:
    st.info(
        f"No snapshot data captured for **{day_iso}** yet. "
        "The daily cron runs at 03:15 UTC; if you just enabled it, come back tomorrow."
    )
    st.stop()

# ── Fetch new videos published on that day ────────────────────
# (published_at is ISO timestamp; filter to the UTC day window)
start_ts = f"{day_iso}T00:00:00+00:00"
end_ts = f"{(day + timedelta(days=1)).isoformat()}T00:00:00+00:00"
new_video_rows = (
    db.client.table("videos")
    .select("*")
    .gte("published_at", start_ts)
    .lt("published_at", end_ts)
    .execute()
    .data or []
)

# ── Compute per-channel deltas ────────────────────────────────
def _ch_delta(cid: str, field: str) -> int | None:
    a = chan_day.get(cid)
    b = chan_prev.get(cid)
    if not a or not b:
        return None
    return int(a.get(field, 0) or 0) - int(b.get(field, 0) or 0)

# ── Headline KPIs ─────────────────────────────────────────────
total_new_videos = len(new_video_rows)
total_sub_delta = sum((_ch_delta(cid, "subscriber_count") or 0) for cid in chan_day.keys())
total_view_delta = sum((_ch_delta(cid, "total_views") or 0) for cid in chan_day.keys())

# Most active club: posted most videos yesterday
club_new_counts: dict[str, int] = {}
for v in new_video_rows:
    club_new_counts[v["channel_id"]] = club_new_counts.get(v["channel_id"], 0) + 1
most_active = max(club_new_counts.items(), key=lambda kv: kv[1], default=(None, 0))
most_active_name = ch_by_id.get(most_active[0], {}).get("name", "—") if most_active[0] else "—"

k1, k2, k3, k4 = st.columns(4)
k1.metric("🎬 New videos", fmt_num(total_new_videos))
k2.metric("📈 Δ Subscribers", f"{'+' if total_sub_delta >= 0 else ''}{fmt_num(total_sub_delta)}")
k3.metric("👁️ Δ Views", f"{'+' if total_view_delta >= 0 else ''}{fmt_num(total_view_delta)}")
k4.metric("🔥 Most active", f"{most_active_name}", help=f"{most_active[1]} videos posted")

st.markdown("---")

# ── New videos yesterday (highlighted) ────────────────────────
st.subheader(f"🎬 New videos published on {day_iso}")
if not new_video_rows:
    st.caption("No new videos published that day.")
else:
    # Sort by current view count desc
    sorted_v = sorted(new_video_rows, key=lambda v: v.get("view_count", 0) or 0, reverse=True)

    def _dur(secs):
        s = int(secs or 0)
        if not s:
            return "-"
        m, sec = divmod(s, 60)
        return f"{m}:{sec:02d}"

    rows = ""
    for v in sorted_v:
        ch = ch_by_id.get(v["channel_id"]) or {}
        club_c1, club_c2 = dual.get(ch.get("name", ""), (color_map.get(ch.get("name", ""), "#636EFA"), "#FFFFFF"))
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        fmt = v.get("format") or ("long" if (v.get("duration_seconds") or 0) >= 60 else "short")
        fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}.get(fmt, "#AAAAAA")
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}.get(fmt, fmt.title())
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        pub_time = (v.get("published_at") or "")[11:16]  # HH:MM
        rows += f"""<tr style="border-left:3px solid {club_c1}">
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank"><img src="{thumb}" style="width:120px;height:68px;object-fit:cover;border-radius:4px"></a></td>
            <td style="padding:6px 12px">
                <div style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{club_c1};border:1px solid rgba(255,255,255,0.3);vertical-align:middle;margin-right:6px"></div>
                <span style="color:#AAA;font-size:13px">{ch.get('name', '?')}</span>
            </td>
            <td style="padding:6px 12px"><a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none">{title}</a></td>
            <td style="padding:6px 12px"><span style="color:{fmt_color}">{fmt_label}</span></td>
            <td style="padding:6px 12px;text-align:right">{_dur(v.get('duration_seconds'))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('view_count') or 0))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('like_count') or 0))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('comment_count') or 0))}</td>
            <td style="padding:6px 12px;color:#888;font-size:12px">{pub_time}</td>
        </tr>"""

    components.html(f"""
    <style>
      .nv {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
             font-family:"Source Sans Pro",sans-serif; }}
      .nv th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
      .nv td {{ border-bottom:1px solid #262730; }}
    </style>
    <table class="nv">
      <thead><tr>
        <th></th><th>Club</th><th>Title</th><th>Format</th>
        <th style="text-align:right">Duration</th>
        <th style="text-align:right">Views</th>
        <th style="text-align:right">Likes</th>
        <th style="text-align:right">Comments</th>
        <th>Posted</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """, height=len(sorted_v) * 92 + 80, scrolling=True)

# ── Gainer leaderboards side-by-side ──────────────────────────
st.markdown("---")
col_l, col_r = st.columns(2)


def _gainer_table(metric: str, title: str, icon: str, positive_is_good: bool = True) -> str:
    gainers = []
    for cid in chan_day.keys():
        ch = ch_by_id.get(cid)
        if not ch or ch.get("entity_type") == "League":
            continue
        d = _ch_delta(cid, metric)
        if d is None:
            continue
        latest = int(chan_day[cid].get(metric, 0) or 0)
        gainers.append({"name": ch["name"], "id": cid, "delta": d, "latest": latest})
    if not gainers:
        return ""
    gainers.sort(key=lambda g: g["delta"], reverse=positive_is_good)
    top = gainers[:10]
    rows_html = ""
    for i, g in enumerate(top, 1):
        c1, c2 = dual.get(g["name"], (color_map.get(g["name"], "#636EFA"), "#FFFFFF"))
        col = "#00CC96" if g["delta"] > 0 else ("#EF553B" if g["delta"] < 0 else "#888")
        sgn = "+" if g["delta"] >= 0 else ""
        dot = f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3)"></span>'
        rows_html += f"""<tr>
            <td style="padding:5px 10px;color:#888">{i}</td>
            <td style="padding:5px 10px">{dot}</td>
            <td style="padding:5px 10px">{g['name']}</td>
            <td style="padding:5px 10px;text-align:right">{fmt_num(g['latest'])}</td>
            <td style="padding:5px 10px;text-align:right;color:{col}">{sgn}{fmt_num(g['delta'])}</td>
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
    </tr></thead>
    <tbody>{rows_html}</tbody></table></div>
    """


with col_l:
    html = _gainer_table("subscriber_count", "Biggest subscriber gains", "📈")
    if html:
        components.html(html, height=440)
    else:
        st.caption("No subscriber deltas available for this day.")
with col_r:
    html = _gainer_table("total_views", "Biggest view gains", "👁️")
    if html:
        components.html(html, height=440)
    else:
        st.caption("No view deltas available for this day.")

# ── Top trending season videos by Δ views (from video_snapshots) ──
st.markdown("---")
st.subheader(f"🚀 Top videos by Δ views on {day_iso}")
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
    trending.sort(key=lambda t: t["delta"], reverse=True)
    top_ids = [t["id"] for t in trending[:15]]
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
    for i, t in enumerate(trending[:15], 1):
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

# ── Per-league summary ────────────────────────────────────────
st.markdown("---")
st.subheader("📊 Per-league summary")
lg_agg: dict[str, dict] = {}
for cid, snap in chan_day.items():
    ch = ch_by_id.get(cid)
    if not ch or ch.get("entity_type") == "League":
        continue
    lg = get_league_for_channel(ch)
    if not lg:
        continue
    agg = lg_agg.setdefault(lg, {"new_videos": 0, "sub_delta": 0, "view_delta": 0, "clubs": 0})
    agg["clubs"] += 1
    agg["sub_delta"] += _ch_delta(cid, "subscriber_count") or 0
    agg["view_delta"] += _ch_delta(cid, "total_views") or 0

for v in new_video_rows:
    ch = ch_by_id.get(v["channel_id"])
    if not ch:
        continue
    lg = get_league_for_channel(ch)
    if lg and lg in lg_agg:
        lg_agg[lg]["new_videos"] += 1

if lg_agg:
    lg_rows = ""
    for lg, agg in sorted(lg_agg.items(), key=lambda kv: kv[1]["sub_delta"], reverse=True):
        sub_col = "#00CC96" if agg["sub_delta"] > 0 else ("#EF553B" if agg["sub_delta"] < 0 else "#888")
        view_col = "#00CC96" if agg["view_delta"] > 0 else ("#EF553B" if agg["view_delta"] < 0 else "#888")
        lg_rows += f"""<tr>
            <td style="padding:8px 12px;font-weight:600">{lg}</td>
            <td style="padding:8px 12px;text-align:right">{agg['clubs']}</td>
            <td style="padding:8px 12px;text-align:right">{agg['new_videos']}</td>
            <td style="padding:8px 12px;text-align:right;color:{sub_col}">{'+' if agg['sub_delta'] >= 0 else ''}{fmt_num(agg['sub_delta'])}</td>
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
      <th style="text-align:right">Clubs tracked</th>
      <th style="text-align:right">New videos</th>
      <th style="text-align:right">Δ Subs</th>
      <th style="text-align:right">Δ Views</th>
    </tr></thead><tbody>{lg_rows}</tbody></table>
    """, height=len(lg_agg) * 40 + 80, scrolling=False)
