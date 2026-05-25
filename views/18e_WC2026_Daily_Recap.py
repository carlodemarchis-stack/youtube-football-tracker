"""FIFA World Cup 2026 — Daily Recap.

A cohort-wide overview of the WC2026 channels' most recent activity:
headline KPIs for the last complete day, a per-confederation Δ-views
chart, the cohort's Δ-views trend, and the newest uploads. The WC
analog of the top-5 Daily Recap, grouped by confederation.

Deliberately cohort-wide (no Confederation→Team filter) — it's an
at-a-glance summary; drill-down lives on the All Channels / Latest /
Viral / Trends pages.

Reads the precomputed `wc2026_trends` cache (cohort + per-confederation
Δ-views series, written by scripts/daily_wc2026.py); falls back to a
live compute on a cold cache so the page is never blank.
"""
from __future__ import annotations

import os
from datetime import date as _date

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_login
from src.cached_db import (
    get_all_channels as _cached_channels,
    get_recent_videos as _cached_recent,
    read_dashboard_cache as _cached_dc_read,
)
from src import dashboard_cache as _dc
from src.analytics import fmt_num, kpi_row
from src import theme as _T

load_dotenv()
require_login()

st.title("FIFA World Cup 2026 — Daily Recap")
st.caption(
    "An at-a-glance summary of the WC2026 cohort — the 48 national teams "
    "+ FIFA + 6 confederations — for the most recent complete day. "
    "Δ views are channel-wide; videos published are split long / shorts "
    "/ live. Use the All Channels, Latest, Viral and Trends pages to "
    "drill into a confederation or team."
)

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


all_channels = _cached_channels(db)
wc = [c for c in all_channels if (c.get("competitions") or {}).get("wc2026")]
if not wc:
    st.warning("No channels with `competitions.wc2026` set yet — see the "
               "**All Channels** page for setup steps.")
    st.stop()

# Cache-first (cohort-wide); live fallback so a cold cache never blanks.
_row = _cached_dc_read(db, "wc2026_trends", _dc.scope_all())
payload = (_row or {}).get("payload") if _row else None
if not payload or not payload.get("cohort"):
    with st.spinner("Computing recap (cache warming)…"):
        payload = _dc.compute_wc2026_trends(db, all_channels)

dates = payload.get("dates") or []
cohort_by = (payload.get("cohort") or {}).get("by_date") or []
groups = (payload.get("breakdown") or {}).get("groups") or []

if not dates or not cohort_by:
    st.info("No WC2026 trend data yet — the daily cron writes the first "
            "series after two days of snapshots.")
    st.stop()

last = cohort_by[-1]
last_date = payload.get("window_end") or dates[-1]
last7 = cohort_by[-7:]
dv_last = int(last.get("dv") or 0)
nv_last = int(last.get("new_long", 0) + last.get("new_short", 0)
              + last.get("new_live", 0))
dv7 = sum(int(d.get("dv") or 0) for d in last7)
nv7 = sum(int(d.get("new_long", 0) + d.get("new_short", 0)
              + d.get("new_live", 0)) for d in last7)

st.markdown(kpi_row([
    ("Latest complete day", _absd(last_date),
     f"{len(wc)} channels tracked"),
    ("Δ views (last day)", fmt_num(dv_last), "channel-wide"),
    ("Videos published (last day)", fmt_num(nv_last),
     f"{fmt_num(last.get('new_long', 0))} long · "
     f"{fmt_num(last.get('new_short', 0))} short · "
     f"{fmt_num(last.get('new_live', 0))} live"),
    ("Δ views (last 7 days)", fmt_num(dv7), f"{fmt_num(nv7)} videos"),
]), unsafe_allow_html=True)

_PLOT = dict(
    plot_bgcolor=_T.BG, paper_bgcolor=_T.BG,
    font=dict(color=_T.TEXT), height=340,
    margin=dict(t=30, l=0, r=0, b=20),
)

gc1, gc2 = st.columns(2)

# ── Per-confederation Δ-views for the last complete day ────────────
with gc1:
    st.subheader("🌍 Δ views by confederation (last day)")
    conf_rows = []
    color_by_conf = {}
    for g in groups:
        bd = g.get("by_date") or []
        if not bd:
            continue
        conf_rows.append({"Confederation": g.get("name") or g.get("key"),
                          "Δ Views": int(bd[-1].get("dv") or 0)})
        color_by_conf[g.get("name") or g.get("key")] = g.get("color") or "#888"
    conf_df = pd.DataFrame(conf_rows).sort_values("Δ Views", ascending=True)
    if conf_df.empty or conf_df["Δ Views"].abs().sum() == 0:
        st.caption("No per-confederation movement recorded for the last day.")
    else:
        fc = px.bar(conf_df, x="Δ Views", y="Confederation",
                    orientation="h", color="Confederation",
                    color_discrete_map=color_by_conf)
        fc.update_layout(showlegend=False, **_PLOT)
        fc.update_xaxes(gridcolor=_T.BORDER, title="", tickformat="~s")
        fc.update_yaxes(gridcolor=_T.BORDER, title="")
        st.plotly_chart(fc, width="stretch")

# ── Cohort Δ-views trend (last 14 days) ───────────────────────────
with gc2:
    st.subheader("👁️ Cohort Δ views per day")
    _tail = cohort_by[-14:]
    trend_df = pd.DataFrame(
        [{"Date": d.get("date"), "Δ Views": int(d.get("dv") or 0)}
         for d in _tail])
    trend_df["Date"] = pd.to_datetime(trend_df["Date"])
    ft = px.line(trend_df, x="Date", y="Δ Views", markers=True)
    ft.update_traces(line_color=_T.ACCENT, marker_color=_T.ACCENT)
    ft.update_layout(**_PLOT)
    ft.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
    ft.update_yaxes(gridcolor=_T.BORDER, title="", tickformat="~s")
    st.plotly_chart(ft, width="stretch")

st.caption(
    f"Last complete day: {_absd(last_date)}. Per-confederation bars are "
    "that day's channel-wide view growth; the trend line is the whole "
    "cohort over the last 14 days. The current (partial) day is excluded."
)

# ── Newest uploads ────────────────────────────────────────────────
st.markdown("---")
st.subheader("🆕 Newest uploads")
ch_ids = [c["id"] for c in wc if c.get("id")]
ch_by_id = {c["id"]: c for c in wc}
try:
    recent = _cached_recent(db, limit=12, channel_ids=tuple(ch_ids))
except Exception:
    recent = []

if not recent:
    st.caption("No recent uploads found.")
else:
    cols = st.columns(4)
    for i, v in enumerate(recent[:12]):
        yid = v.get("youtube_video_id") or ""
        url = f"https://www.youtube.com/watch?v={yid}" if yid else "#"
        thumb = v.get("thumbnail_url") or ""
        title = (v.get("title") or "")[:70].replace("<", "&lt;").replace(">", "&gt;")
        ch = ch_by_id.get(v.get("channel_id")) or {}
        cn = v.get("channel_name") or ch.get("name") or "?"
        views = int(v.get("view_count") or 0)
        pub = (v.get("published_at") or "")[:10]
        with cols[i % 4]:
            st.markdown(
                f"<a href='{url}' target='_blank' rel='noopener' "
                f"title='Open on YouTube'>"
                f"<img src='{thumb}' style='width:100%;border-radius:6px'></a>"
                f"<div style='font-size:12px;line-height:1.35;margin-top:4px'>"
                f"<span style='color:#c9cdd6'>{cn}</span><br>"
                f"<a href='{url}' target='_blank' rel='noopener' "
                f"style='color:#58A6FF;text-decoration:none'>{title}</a>"
                f"<div style='color:{_T.MUTED_2};margin-top:2px'>"
                f"{fmt_num(views)} views · {pub}</div></div>",
                unsafe_allow_html=True,
            )

st.caption("The 12 most recently published WC2026 videos across all "
           "channels. See the **Latest Videos** page for the full feed.")
