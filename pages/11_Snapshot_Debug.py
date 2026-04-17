"""Snapshot Debug — admin tool to inspect raw channel & video snapshots."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.auth import require_login, is_admin
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    get_global_filter, get_league_for_channel,
)

load_dotenv()
require_login()
if not is_admin():
    st.error("Admin only.")
    st.stop()

st.title("Snapshot Debug")
st.caption("Raw snapshot data for every tracked channel — use this to diagnose gaps and anomalies.")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Missing SUPABASE_URL / SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)

# ── Global filter ────────────────────────────────────────────
all_channels = get_global_channels() or db.get_all_channels()
ch_by_id = {c["id"]: c for c in all_channels}
color_map = get_global_color_map()
dual = get_global_color_map_dual()

g_league, g_club = get_global_filter()
if g_club:
    filter_cids = {g_club["id"]}
elif g_league:
    filter_cids = {c["id"] for c in all_channels if get_league_for_channel(c) == g_league}
else:
    filter_cids = None

filtered_channels = [c for c in all_channels if filter_cids is None or c["id"] in filter_cids]

# ── Overview: snapshot dates ─────────────────────────────────
st.subheader("📅 Snapshot dates overview")
all_snaps_meta = db.client.table("channel_snapshots") \
    .select("captured_date,channel_id") \
    .order("captured_date", desc=True) \
    .execute().data or []

date_counts: dict[str, int] = {}
for s in all_snaps_meta:
    if filter_cids is not None and s["channel_id"] not in filter_cids:
        continue
    d = s["captured_date"]
    date_counts[d] = date_counts.get(d, 0) + 1

if not date_counts:
    st.warning("No channel snapshots found.")
    st.stop()

cols = st.columns(min(len(date_counts), 6))
for i, (d, cnt) in enumerate(sorted(date_counts.items(), reverse=True)):
    cols[i % len(cols)].metric(d, f"{cnt} channels")

st.markdown("---")

# ── Fetch all snapshot data ──────────────────────────────────
all_full = db.client.table("channel_snapshots") \
    .select("channel_id,captured_date,subscriber_count,total_views,video_count,captured_at") \
    .order("captured_date", desc=True) \
    .execute().data or []

snaps_by_ch: dict[str, list[dict]] = {}
for s in all_full:
    if filter_cids is not None and s["channel_id"] not in filter_cids:
        continue
    snaps_by_ch.setdefault(s["channel_id"], []).append(s)

# ── All-channels table ───────────────────────────────────────
st.subheader("🔍 All channels — snapshot summary")
if g_club:
    st.caption(f"Filtered to **{g_club['name']}**")
elif g_league:
    st.caption(f"Filtered to **{g_league}**")
else:
    st.caption(f"Showing all **{len(filtered_channels)}** channels")

rows_html = ""
for ch in sorted(filtered_channels, key=lambda c: c["name"]):
    ch_snaps = sorted(snaps_by_ch.get(ch["id"], []), key=lambda x: x["captured_date"])
    n_snaps = len(ch_snaps)
    if n_snaps == 0:
        rows_html += f"""<tr>
            <td style="padding:5px 8px">{ch['name']} ⚠️</td>
            <td style="padding:5px 8px;text-align:center">0</td>
            <td style="padding:5px 8px">—</td><td style="padding:5px 8px">—</td>
            <td style="padding:5px 8px;text-align:right">—</td>
            <td style="padding:5px 8px;text-align:right">—</td>
            <td style="padding:5px 8px;text-align:right">—</td>
            <td style="padding:5px 8px;text-align:right">—</td>
            <td style="padding:5px 8px;text-align:right">—</td>
            <td style="padding:5px 8px;text-align:right">—</td>
        </tr>"""
        continue

    latest = ch_snaps[-1]
    subs = int(latest.get("subscriber_count") or 0)
    views = int(latest.get("total_views") or 0)
    vids = int(latest.get("video_count") or 0)
    last_date = latest["captured_date"]
    first_date = ch_snaps[0]["captured_date"]
    flag = "⚠️" if n_snaps < len(date_counts) else ""

    if n_snaps >= 2:
        prev = ch_snaps[-2]
        d_subs = subs - int(prev.get("subscriber_count") or 0)
        d_views = views - int(prev.get("total_views") or 0)
        d_vids = vids - int(prev.get("video_count") or 0)
        d_sub_col = "#00CC96" if d_subs > 0 else ("#EF553B" if d_subs < 0 else "#888")
        d_view_col = "#00CC96" if d_views > 0 else ("#EF553B" if d_views < 0 else "#888")
        d_vid_col = "#00CC96" if d_vids > 0 else ("#EF553B" if d_vids < 0 else "#888")
        d_subs_s = f'<span style="color:{d_sub_col}">{d_subs:+,}</span>'
        d_views_s = f'<span style="color:{d_view_col}">{d_views:+,}</span>'
        d_vids_s = f'<span style="color:{d_vid_col}">{d_vids:+,}</span>'
    else:
        d_subs_s = d_views_s = d_vids_s = '<span style="color:#555">—</span>'

    rows_html += f"""<tr class="ch-row" data-chid="{ch['id']}" style="cursor:pointer">
        <td style="padding:5px 8px">{ch['name']} {flag}</td>
        <td style="padding:5px 8px;text-align:center">{n_snaps}</td>
        <td style="padding:5px 8px">{first_date}</td>
        <td style="padding:5px 8px">{last_date}</td>
        <td style="padding:5px 8px;text-align:right">{subs:,}</td>
        <td style="padding:5px 8px;text-align:right">{d_subs_s}</td>
        <td style="padding:5px 8px;text-align:right">{views:,}</td>
        <td style="padding:5px 8px;text-align:right">{d_views_s}</td>
        <td style="padding:5px 8px;text-align:right">{vids:,}</td>
        <td style="padding:5px 8px;text-align:right">{d_vids_s}</td>
    </tr>"""

components.html(f"""
<style>
  .adbg {{ width:100%; border-collapse:collapse; font-size:13px; color:#FAFAFA;
           font-family:"Source Sans Pro",sans-serif; }}
  .adbg th {{ padding:5px 8px; border-bottom:2px solid #444; text-align:left;
              position:sticky; top:0; background:#0E1117; z-index:1; }}
  .adbg td {{ border-bottom:1px solid #262730; }}
  .adbg tr:hover td {{ background:#1a1c24; }}
</style>
<table class="adbg"><thead><tr>
  <th>Channel</th><th style="text-align:center"># Snaps</th>
  <th>First</th><th>Latest</th>
  <th style="text-align:right">Subs</th><th style="text-align:right">Δ Subs</th>
  <th style="text-align:right">Views</th><th style="text-align:right">Δ Views</th>
  <th style="text-align:right">Videos</th><th style="text-align:right">Δ Vids</th>
</tr></thead><tbody>{rows_html}</tbody></table>
""", height=min(len(filtered_channels), 30) * 32 + 60, scrolling=True)

# ── Detail: single channel drill-down ────────────────────────
st.markdown("---")
st.subheader("📋 Channel detail")
ch_names = sorted([c["name"] for c in filtered_channels])
selected_name = st.selectbox("Select channel to inspect", ch_names, key="debug_ch_sel")
selected_ch = next((c for c in filtered_channels if c["name"] == selected_name), None)

if selected_ch:
    ch_snaps = sorted(snaps_by_ch.get(selected_ch["id"], []), key=lambda x: x["captured_date"])
    if not ch_snaps:
        st.info(f"No snapshots for {selected_name}.")
    else:
        st.caption(f"**{len(ch_snaps)}** snapshots for **{selected_name}**")

        # Channel snapshot timeline
        detail_html = ""
        prev_snap = None
        for snap in ch_snaps:
            subs = int(snap.get("subscriber_count") or 0)
            views = int(snap.get("total_views") or 0)
            vids = int(snap.get("video_count") or 0)
            cap_date = snap.get("captured_date", "")
            cap_at = (snap.get("captured_at") or "")[:19].replace("T", " ")

            if prev_snap:
                d_subs = subs - int(prev_snap.get("subscriber_count") or 0)
                d_views = views - int(prev_snap.get("total_views") or 0)
                d_vids = vids - int(prev_snap.get("video_count") or 0)
                sub_col = "#00CC96" if d_subs > 0 else ("#EF553B" if d_subs < 0 else "#888")
                view_col = "#00CC96" if d_views > 0 else ("#EF553B" if d_views < 0 else "#888")
                vid_col = "#00CC96" if d_vids > 0 else ("#EF553B" if d_vids < 0 else "#888")
                d_subs_s = f'<span style="color:{sub_col}">{d_subs:+,}</span>'
                d_views_s = f'<span style="color:{view_col}">{d_views:+,}</span>'
                d_vids_s = f'<span style="color:{vid_col}">{d_vids:+,}</span>'
            else:
                d_subs_s = d_views_s = d_vids_s = '<span style="color:#555">—</span>'

            detail_html += f"""<tr>
                <td style="padding:6px 10px">{cap_date}</td>
                <td style="padding:6px 10px;color:#888;font-size:12px">{cap_at}</td>
                <td style="padding:6px 10px;text-align:right">{subs:,}</td>
                <td style="padding:6px 10px;text-align:right">{d_subs_s}</td>
                <td style="padding:6px 10px;text-align:right">{views:,}</td>
                <td style="padding:6px 10px;text-align:right">{d_views_s}</td>
                <td style="padding:6px 10px;text-align:right">{vids:,}</td>
                <td style="padding:6px 10px;text-align:right">{d_vids_s}</td>
            </tr>"""
            prev_snap = snap

        components.html(f"""
        <style>
          .dbg {{ width:100%; border-collapse:collapse; font-size:13px; color:#FAFAFA;
                  font-family:"Source Sans Pro",sans-serif; }}
          .dbg th {{ padding:6px 10px; border-bottom:2px solid #444; text-align:left;
                     position:sticky; top:0; background:#0E1117; }}
          .dbg td {{ border-bottom:1px solid #262730; }}
          .dbg tr:hover td {{ background:#1a1c24; }}
        </style>
        <table class="dbg"><thead><tr>
          <th>Date</th><th>Captured at</th>
          <th style="text-align:right">Subs</th><th style="text-align:right">Δ Subs</th>
          <th style="text-align:right">Views</th><th style="text-align:right">Δ Views</th>
          <th style="text-align:right">Videos</th><th style="text-align:right">Δ Vids</th>
        </tr></thead><tbody>{detail_html}</tbody></table>
        """, height=len(ch_snaps) * 36 + 60, scrolling=True)

        # Video snapshots for this channel
        st.markdown("---")
        st.subheader(f"🎬 Video snapshots for {selected_name}")
        vid_rows = db.client.table("videos") \
            .select("id,youtube_video_id,title,published_at") \
            .eq("channel_id", selected_ch["id"]) \
            .order("published_at", desc=True) \
            .limit(200) \
            .execute().data or []

        if vid_rows:
            st.caption(f"Showing up to 200 most recent videos ({len(vid_rows)} returned)")
            vid_ids = [v["id"] for v in vid_rows]

            all_vsnaps: list[dict] = []
            for i in range(0, len(vid_ids), 100):
                batch = vid_ids[i:i+100]
                all_vsnaps += db.client.table("video_snapshots") \
                    .select("video_id,captured_date,view_count,like_count,comment_count") \
                    .in_("video_id", batch) \
                    .order("captured_date", desc=True) \
                    .execute().data or []

            vsnap_by_vid: dict[str, list[dict]] = {}
            for vs in all_vsnaps:
                vsnap_by_vid.setdefault(vs["video_id"], []).append(vs)

            vrows_html = ""
            for vid in vid_rows[:50]:
                snaps_for = sorted(vsnap_by_vid.get(vid["id"], []), key=lambda x: x["captured_date"])
                snap_count = len(snaps_for)
                title = (vid.get("title") or "")[:60].replace("<", "&lt;")
                pub = (vid.get("published_at") or "")[:10]

                if snap_count >= 2:
                    latest_v = snaps_for[-1]
                    prev_v = snaps_for[-2]
                    dv = int(latest_v.get("view_count") or 0) - int(prev_v.get("view_count") or 0)
                    dv_col = "#00CC96" if dv > 0 else ("#EF553B" if dv < 0 else "#888")
                    views_now = f'{int(latest_v.get("view_count") or 0):,}'
                    delta_s = f'<span style="color:{dv_col}">{dv:+,}</span>'
                    dates_s = f'{snaps_for[0]["captured_date"]} → {snaps_for[-1]["captured_date"]}'
                elif snap_count == 1:
                    views_now = f'{int(snaps_for[0].get("view_count") or 0):,}'
                    delta_s = '<span style="color:#555">—</span>'
                    dates_s = snaps_for[0]["captured_date"]
                else:
                    views_now = "—"
                    delta_s = "—"
                    dates_s = "no snapshots"

                vrows_html += f"""<tr>
                    <td style="padding:5px 8px;font-size:12px">{pub}</td>
                    <td style="padding:5px 8px;font-size:12px">{title}</td>
                    <td style="padding:5px 8px;text-align:right">{views_now}</td>
                    <td style="padding:5px 8px;text-align:right">{delta_s}</td>
                    <td style="padding:5px 8px;text-align:center">{snap_count}</td>
                    <td style="padding:5px 8px;font-size:11px;color:#888">{dates_s}</td>
                </tr>"""

            components.html(f"""
            <style>
              .vdbg {{ width:100%; border-collapse:collapse; font-size:13px; color:#FAFAFA;
                       font-family:"Source Sans Pro",sans-serif; }}
              .vdbg th {{ padding:5px 8px; border-bottom:2px solid #444; text-align:left;
                          position:sticky; top:0; background:#0E1117; }}
              .vdbg td {{ border-bottom:1px solid #262730; }}
            </style>
            <table class="vdbg"><thead><tr>
              <th>Published</th><th>Title</th>
              <th style="text-align:right">Views</th><th style="text-align:right">Δ Views</th>
              <th style="text-align:center"># Snaps</th><th>Snap range</th>
            </tr></thead><tbody>{vrows_html}</tbody></table>
            """, height=min(50, len(vid_rows)) * 32 + 60, scrolling=True)
        else:
            st.caption("No videos found for this channel.")

# ── Raw counts ───────────────────────────────────────────────
st.markdown("---")
st.subheader("📊 Raw counts")
c1, c2, c3 = st.columns(3)
ch_count = db.client.table("channel_snapshots").select("id", count="exact").limit(1).execute()
v_count = db.client.table("video_snapshots").select("id", count="exact").limit(1).execute()
vid_count = db.client.table("videos").select("id", count="exact").limit(1).execute()
c1.metric("Channel snapshots (total rows)", f"{ch_count.count:,}")
c2.metric("Video snapshots (total rows)", f"{v_count.count:,}")
c3.metric("Videos tracked", f"{vid_count.count:,}")
