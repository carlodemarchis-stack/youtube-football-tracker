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
st.subheader("📅 Snapshot overview — last 3 days")
all_snaps_meta = db.client.table("channel_snapshots") \
    .select("captured_date,channel_id,subscriber_count,total_views,video_count,captured_at") \
    .order("captured_date", desc=True) \
    .execute().data or []

# Group by date
_by_date: dict[str, list[dict]] = {}
for s in all_snaps_meta:
    if filter_cids is not None and s["channel_id"] not in filter_cids:
        continue
    _by_date.setdefault(s["captured_date"], []).append(s)

if not _by_date:
    st.warning("No channel snapshots found.")
    st.stop()

_sorted_dates = sorted(_by_date.keys(), reverse=True)[:3]

cols = st.columns(len(_sorted_dates))
for i, d in enumerate(_sorted_dates):
    snaps = _by_date[d]
    n_ch = len(snaps)
    total_subs = sum(int(s.get("subscriber_count") or 0) for s in snaps)
    total_views = sum(int(s.get("total_views") or 0) for s in snaps)
    total_vids = sum(int(s.get("video_count") or 0) for s in snaps)
    # Earliest capture time
    cap_times = [s.get("captured_at") or "" for s in snaps if s.get("captured_at")]
    earliest = min(cap_times)[:16].replace("T", " ") if cap_times else "—"

    # Compute deltas vs next older date
    _next_dates = sorted(_by_date.keys(), reverse=True)
    _next_idx = _next_dates.index(d) + 1 if d in _next_dates else None
    if _next_idx and _next_idx < len(_next_dates):
        prev_snaps = _by_date[_next_dates[_next_idx]]
        prev_subs = sum(int(s.get("subscriber_count") or 0) for s in prev_snaps)
        prev_views = sum(int(s.get("total_views") or 0) for s in prev_snaps)
        prev_vids = sum(int(s.get("video_count") or 0) for s in prev_snaps)
        d_subs = f"+{fmt_num(total_subs - prev_subs)}" if total_subs >= prev_subs else fmt_num(total_subs - prev_subs)
        d_views = f"+{fmt_num(total_views - prev_views)}" if total_views >= prev_views else fmt_num(total_views - prev_views)
        d_vids = f"+{total_vids - prev_vids}"
    else:
        d_subs = d_views = d_vids = "—"

    with cols[i]:
        st.markdown(f"**{d}**")
        st.metric("Channels", n_ch)
        st.metric("Δ Subs", d_subs if d_subs != "—" else "—")
        st.markdown(f'<p style="margin-top:-16px;font-size:13px;color:#888">Total {fmt_num(total_subs)}</p>', unsafe_allow_html=True)
        st.metric("Δ Views", d_views if d_views != "—" else "—")
        st.markdown(f'<p style="margin-top:-16px;font-size:13px;color:#888">Total {fmt_num(total_views)}</p>', unsafe_allow_html=True)
        st.metric("Δ Videos", d_vids if d_vids != "—" else "—")
        st.markdown(f'<p style="margin-top:-16px;font-size:13px;color:#888">Total {fmt_num(total_vids)}</p>', unsafe_allow_html=True)
        st.caption(f"Captured: {earliest} UTC")

st.markdown("---")

# Keep date_counts for backward compat
date_counts = {d: len(snaps) for d, snaps in _by_date.items()}

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
        first = ch_snaps[0]
        d_subs = subs - int(first.get("subscriber_count") or 0)
        d_views = views - int(first.get("total_views") or 0)
        d_vids = vids - int(first.get("video_count") or 0)
        d_sub_col = "#00CC96" if d_subs > 0 else ("#EF553B" if d_subs < 0 else "#888")
        d_view_col = "#00CC96" if d_views > 0 else ("#EF553B" if d_views < 0 else "#888")
        d_vid_col = "#00CC96" if d_vids > 0 else ("#EF553B" if d_vids < 0 else "#888")
        d_subs_s = f'<span style="color:{d_sub_col}">{d_subs:+,}</span>'
        d_views_s = f'<span style="color:{d_view_col}">{d_views:+,}</span>'
        d_vids_s = f'<span style="color:{d_vid_col}">{d_vids:+,}</span>'
    else:
        d_subs = d_views = d_vids = 0
        d_subs_s = d_views_s = d_vids_s = '<span style="color:#555">—</span>'

    rows_html += f"""<tr class="ch-row" data-chid="{ch['id']}" style="cursor:pointer">
        <td style="padding:5px 8px">{ch['name']} {flag}</td>
        <td style="padding:5px 8px;text-align:center">{n_snaps}</td>
        <td style="padding:5px 8px">{first_date}</td>
        <td style="padding:5px 8px">{last_date}</td>
        <td style="padding:5px 8px;text-align:right" data-v="{subs}">{subs:,}</td>
        <td style="padding:5px 8px;text-align:right" data-v="{d_subs}">{d_subs_s}</td>
        <td style="padding:5px 8px;text-align:right" data-v="{views}">{views:,}</td>
        <td style="padding:5px 8px;text-align:right" data-v="{d_views}">{d_views_s}</td>
        <td style="padding:5px 8px;text-align:right" data-v="{vids}">{vids:,}</td>
        <td style="padding:5px 8px;text-align:right" data-v="{d_vids}">{d_vids_s}</td>
    </tr>"""

components.html(f"""
<style>
  .adbg {{ width:100%; border-collapse:collapse; font-size:13px; color:#FAFAFA;
           font-family:"Source Sans Pro",sans-serif; }}
  .adbg th {{ padding:5px 8px; border-bottom:2px solid #444; text-align:left;
              position:sticky; top:0; background:#0E1117; z-index:1; }}
  .adbg th.sortable {{ cursor:pointer; user-select:none; }}
  .adbg th.sortable:hover {{ color:#00CC96; }}
  .adbg th.sortable::after {{ content:" ⇅"; font-size:10px; color:#555; }}
  .adbg th.sortable.asc::after {{ content:" ▲"; color:#00CC96; }}
  .adbg th.sortable.desc::after {{ content:" ▼"; color:#00CC96; }}
  .adbg td {{ border-bottom:1px solid #262730; }}
  .adbg tr:hover td {{ background:#1a1c24; }}
</style>
<table class="adbg" id="dbgtbl"><thead><tr>
  <th>Channel</th><th style="text-align:center"># Snaps</th>
  <th>First</th><th>Latest</th>
  <th class="sortable" data-col="4" style="text-align:right">Subs</th>
  <th class="sortable" data-col="5" style="text-align:right">Δ Subs</th>
  <th class="sortable" data-col="6" style="text-align:right">Views</th>
  <th class="sortable" data-col="7" style="text-align:right">Δ Views</th>
  <th class="sortable" data-col="8" style="text-align:right">Videos</th>
  <th class="sortable" data-col="9" style="text-align:right">Δ Vids</th>
</tr></thead><tbody>{rows_html}</tbody></table>
<script>
(function() {{
  const tbl = document.getElementById('dbgtbl');
  const ths = tbl.querySelectorAll('th.sortable');
  let curCol = -1, curDir = 0;
  ths.forEach(th => {{
    th.addEventListener('click', () => {{
      const col = parseInt(th.dataset.col);
      if (curCol === col) {{ curDir = curDir === 1 ? -1 : 1; }}
      else {{ curCol = col; curDir = -1; }}
      ths.forEach(h => h.classList.remove('asc','desc'));
      th.classList.add(curDir === 1 ? 'asc' : 'desc');
      const tbody = tbl.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {{
        const av = parseFloat(a.cells[col].getAttribute('data-v')) || 0;
        const bv = parseFloat(b.cells[col].getAttribute('data-v')) || 0;
        return (av - bv) * curDir;
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
}})();
</script>
""", height=min(len(filtered_channels), 30) * 32 + 60, scrolling=True)

# ── Single club: full snapshot timeline ──────────────────────
if g_club:
    ch_snaps = sorted(snaps_by_ch.get(g_club["id"], []), key=lambda x: x["captured_date"])
    if ch_snaps:
        st.markdown("---")
        st.subheader("📋 All snapshots")
        st.caption(f"**{len(ch_snaps)}** snapshots")

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
