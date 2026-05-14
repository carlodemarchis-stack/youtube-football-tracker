"""YouTube quota monitor — admin page.

Shows per-key daily usage tracked by YouTubeClient (see src/youtube_api.py,
Approach A). The data isn't fetched from Google Cloud's monitoring API
— it's our own per-call accounting, flushed at process exit into the
youtube_quota_log table by `_flush_quota_log()`.

Caveats called out on the page:
  - Counts only what THIS app consumes (not other apps sharing a key).
  - Costs come from a static method→units table (channels.list = 1,
    search.list = 100, etc.).
  - Streamlit calls only land in the table when the Streamlit process
    is killed/restarted; numbers under "streamlit" can lag mid-day.
"""
from __future__ import annotations

import datetime as _dt
import os
from collections import defaultdict

import streamlit as st
from dotenv import load_dotenv

from src.analytics import fmt_num, kpi_row
from src.auth import require_admin
from src.database import admin_db
from src.youtube_api import quota_date_iso  # PT-aligned "today"

load_dotenv()
require_admin()

st.title("YouTube quota monitor")
st.caption(
    "Per-day units consumed by each YouTube API key we use. Days are "
    "anchored to **midnight Pacific Time** (the same reset window "
    "YouTube uses for its quota counter), so buckets here match what "
    "you see in Google Cloud Console. Numbers are self-tracked from the "
    "YouTubeClient call counter and include only this app's "
    "consumption — they won't reflect other apps sharing the same key. "
    "Flushed at process exit; Streamlit columns can lag mid-day until "
    "the page is restarted."
)

db = admin_db()

# Map env var names → key tail so we can label rows by purpose
# instead of cryptic last-4 chars only.
KEY_LABELS: dict[str, str] = {}
for env_name, label in [
    ("YOUTUBE_API_KEY",             "main (Auto)"),
    ("YOUTUBE_API_KEY_DAILY",       "daily snapshot"),
    ("YOUTUBE_API_KEY_HEAVY",       "heavy backfill"),
    ("YOUTUBE_API_KEY_BACKUP",      "backup"),
    ("YOUTUBE_API_KEY_INTERACTIVE", "interactive (local)"),
]:
    v = os.environ.get(env_name, "").strip()
    if v:
        KEY_LABELS[v[-4:]] = f"{label}  ·  …{v[-4:]}"


def _fetch_rows(since: _dt.date):
    """Read youtube_quota_log rows since the given date. Returns list
    of dicts. Empty if the table doesn't exist yet (pre-migration)."""
    try:
        r = db.client.table("youtube_quota_log") \
            .select("*") \
            .gte("date", since.isoformat()) \
            .execute()
        return r.data or []
    except Exception as e:
        st.warning(
            "The `youtube_quota_log` table doesn't exist yet. Apply "
            "`migration_v22.sql` in the Supabase SQL editor, then "
            "this page will start showing data after the next "
            "process exit (each script flushes at the end).\n\n"
            f"Detail: `{e}`"
        )
        return []


# 14-day window. "Today" is in Pacific Time because YouTube's quota
# counter resets at midnight PT — using UTC here would split one
# Google "day" across two of our buckets during late-PT hours.
today_iso = quota_date_iso()
today  = _dt.date.fromisoformat(today_iso)
since  = today - _dt.timedelta(days=13)
rows   = _fetch_rows(since)

# ─── Aggregate today per key_tail ─────────────────────────────────
today_rows = [r for r in rows if r.get("date") == today_iso]
by_key_today: dict[str, dict] = defaultdict(
    lambda: {"units": 0, "calls": 0, "rotations_from": 0,
             "rotations_to": 0, "last_used_at": None,
             "scripts": defaultdict(int)})

for r in today_rows:
    k = r["key_tail"]
    b = by_key_today[k]
    b["units"]          += int(r.get("units", 0))
    b["calls"]          += int(r.get("calls", 0))
    b["rotations_from"] += int(r.get("rotations_from", 0))
    b["rotations_to"]   += int(r.get("rotations_to", 0))
    last = r.get("last_used_at")
    if last and (b["last_used_at"] is None or last > b["last_used_at"]):
        b["last_used_at"] = last
    b["scripts"][r.get("script") or "unknown"] += int(r.get("units", 0))

# Make sure all CONFIGURED keys appear even if they haven't been used
for tail in KEY_LABELS:
    _ = by_key_today[tail]

# ─── KPI strip: 1 card per key for today ──────────────────────────
QUOTA_LIMIT = 10_000  # default daily YouTube Data API v3 limit per project

def _pct(units: int) -> int:
    return min(100, int(round(units * 100 / QUOTA_LIMIT))) if QUOTA_LIMIT else 0

def _color(pct: int) -> str:
    if pct >= 90: return "#FF4B4B"
    if pct >= 70: return "#FFA500"
    if pct >= 40: return "#FFD600"
    return "#00C853"

if by_key_today:
    cards = []
    for tail, b in sorted(by_key_today.items(),
                          key=lambda kv: -kv[1]["units"]):
        label = KEY_LABELS.get(tail, f"unknown · …{tail}")
        pct = _pct(b["units"])
        subtitle = (
            f"{b['calls']:,} calls · "
            f"{pct}% of 10K"
            + (f" · {b['rotations_from']}↪" if b["rotations_from"] else "")
        )
        cards.append((label, fmt_num(b["units"]), subtitle))
    st.markdown(kpi_row(cards), unsafe_allow_html=True)
else:
    st.info("No quota data logged yet today. Counters will appear here "
            "after the first script or Streamlit process exits.")

# ─── Today per key + per script breakdown ─────────────────────────
st.subheader("Today — per key and script")
if today_rows:
    table_html = [
        "<table style='border-collapse:collapse;font-size:13px;width:100%;"
        "color:#FAFAFA;border:0'>"
        "<thead><tr style='border-bottom:1px solid #444;color:#888'>"
        "<th style='padding:6px 12px;text-align:left'>Key</th>"
        "<th style='padding:6px 12px;text-align:left'>Script</th>"
        "<th style='padding:6px 12px;text-align:right'>Units</th>"
        "<th style='padding:6px 12px;text-align:right'>Calls</th>"
        "<th style='padding:6px 12px;text-align:right'>Rotated from</th>"
        "<th style='padding:6px 12px;text-align:right'>Rotated to</th>"
        "<th style='padding:6px 12px;text-align:left'>Last used (UTC)</th>"
        "</tr></thead><tbody>"
    ]
    for r in sorted(today_rows,
                     key=lambda x: (-int(x.get("units", 0)),
                                     x.get("script") or "")):
        tail = r["key_tail"]
        label = KEY_LABELS.get(tail, f"…{tail}")
        pct = _pct(int(r.get("units", 0)))
        color = _color(pct)
        last = (r.get("last_used_at") or "")[:19].replace("T", " ")
        table_html.append(
            "<tr>"
            f"<td style='padding:6px 12px'>{label}</td>"
            f"<td style='padding:6px 12px;color:#888'>{r.get('script') or '—'}</td>"
            f"<td style='padding:6px 12px;text-align:right;color:{color};"
            f"font-weight:600'>{fmt_num(int(r.get('units',0)))}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{fmt_num(int(r.get('calls',0)))}</td>"
            f"<td style='padding:6px 12px;text-align:right;color:#888'>"
            f"{r.get('rotations_from',0) or '—'}</td>"
            f"<td style='padding:6px 12px;text-align:right;color:#888'>"
            f"{r.get('rotations_to',0) or '—'}</td>"
            f"<td style='padding:6px 12px;color:#888'>{last or '—'}</td>"
            "</tr>"
        )
    table_html.append("</tbody></table>")
    st.markdown("".join(table_html), unsafe_allow_html=True)
else:
    st.caption("(no rows for today)")

# ─── 14-day history chart per key ─────────────────────────────────
st.subheader("Last 14 days — per key")
if rows:
    import pandas as pd

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    daily = (df.groupby(["date", "key_tail"], as_index=False)["units"]
               .sum()
               .pivot(index="date", columns="key_tail", values="units")
               .fillna(0))
    # Stable column ordering: configured keys first, by env order
    cols = []
    seen = set()
    for env_name in ["YOUTUBE_API_KEY", "YOUTUBE_API_KEY_DAILY",
                      "YOUTUBE_API_KEY_HEAVY", "YOUTUBE_API_KEY_BACKUP",
                      "YOUTUBE_API_KEY_INTERACTIVE"]:
        v = os.environ.get(env_name, "").strip()
        if not v:
            continue
        tail = v[-4:]
        if tail in daily.columns and tail not in seen:
            cols.append(tail)
            seen.add(tail)
    # Add any unconfigured tails at the end
    for t in daily.columns:
        if t not in seen:
            cols.append(t)
    daily = daily[cols]
    # Rename for legend readability
    daily.columns = [KEY_LABELS.get(t, f"…{t}") for t in daily.columns]
    st.bar_chart(daily, height=320)
    with st.expander("Raw data"):
        st.dataframe(df.sort_values(["date", "key_tail"], ascending=False),
                     hide_index=True, use_container_width=True)
else:
    st.caption("(no data in the last 14 days yet)")

st.divider()
st.caption(
    "⚠️ Notes:"
    " (1) Numbers reflect only this app's consumption — Google's actual"
    " quota counter may be higher if other apps share these keys."
    " (2) Cost per call is from a static method→units table (e.g."
    " `search.list`=100, others=1) — actual cost matches in practice."
    " (3) Streamlit-view consumption shows up here only when the"
    " Streamlit process restarts. Scheduled scripts flush on exit."
)
