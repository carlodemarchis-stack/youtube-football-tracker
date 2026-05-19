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
from src.charts import readable_hover
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

# ── Current PT quota day (audit indicator) ────────────────────────
try:
    from zoneinfo import ZoneInfo as _ZI
    _pt_now = _dt.datetime.now(_ZI("America/Los_Angeles"))
    _utc_now = _dt.datetime.now(_dt.timezone.utc)
    st.markdown(
        f"<div style='background:#1a1c24;border-left:3px solid #58A6FF;"
        f"padding:8px 14px;margin:8px 0 14px 0;font-size:13px;color:#FAFAFA'>"
        f"<b>Current quota day (PT):</b> "
        f"<code style='background:transparent;color:#58A6FF;font-size:14px'>"
        f"{_pt_now.date().isoformat()}</code> &nbsp;·&nbsp; "
        f"<span style='color:#888'>PT now: {_pt_now.strftime('%H:%M:%S %Z')}"
        f" &nbsp;·&nbsp; UTC now: {_utc_now.strftime('%H:%M:%S UTC')}</span>"
        f"<br><span style='color:#888;font-size:11px'>"
        f"All writes today are bucketed under this PT date — "
        f"rolls over to the next day at 00:00 PT (08:00 UTC during PST, "
        f"07:00 UTC during PDT).</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
except Exception:
    pass

db = admin_db()

# Map key_tail (last 4 chars of the API key value — what's stored in
# youtube_quota_log) to the env var name. The env var name IS the label
# the operator recognises, so we use it directly. The last-3-chars
# suffix is shown in its own column for quick disambiguation.
# Display order: main → daily → hourly → heavy → interactive → backup.
# Cards / rows render in this order (not by units descending), so the
# operator's eye lands in a predictable place each visit.
_ENV_VAR_NAMES = [
    "YOUTUBE_API_KEY",
    "YOUTUBE_API_KEY_DAILY",
    "YOUTUBE_API_KEY_HOURLY",
    "YOUTUBE_API_KEY_HEAVY",
    "YOUTUBE_API_KEY_INTERACTIVE",
    "YOUTUBE_API_KEY_BACKUP",
]
KEY_LABELS: dict[str, str] = {}  # key_tail -> env var name
KEY_SUFFIX: dict[str, str] = {}  # key_tail -> last-3 chars of the key value
KEY_ORDER:  dict[str, int] = {}  # key_tail -> sort index (0 = top)
for _i, env_name in enumerate(_ENV_VAR_NAMES):
    v = os.environ.get(env_name, "").strip()
    if v:
        tail4 = v[-4:]
        KEY_LABELS[tail4] = env_name
        KEY_SUFFIX[tail4] = v[-3:]
        KEY_ORDER[tail4] = _i


def _fetch_rows(since: _dt.date):
    """Read youtube_quota_log rows since the given date. Returns list
    of dicts. Empty if the table doesn't exist yet (pre-migration).
    Internal __alert sentinel rows are filtered out — they're used by
    the daily-summary de-dup logic in _flush_quota_log() and don't
    represent actual usage."""
    try:
        r = db.client.table("youtube_quota_log") \
            .select("*") \
            .gte("date", since.isoformat()) \
            .neq("key_tail", "__alert") \
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
    # Stable env-var order, unknown tails at the end
    for tail, b in sorted(by_key_today.items(),
                          key=lambda kv: (KEY_ORDER.get(kv[0], 999), kv[0])):
        env_name = KEY_LABELS.get(tail, f"unknown ({tail})")
        suffix = KEY_SUFFIX.get(tail, tail[-3:] if tail else "")
        pct = _pct(b["units"])
        subtitle = (
            f"suffix …{suffix} · "
            f"{b['calls']:,} calls · "
            f"{pct}% of 10,000"
            + (f" · {b['rotations_from']}↪" if b["rotations_from"] else "")
        )
        cards.append((f"🔑 {env_name}", f"{b['units']:,}", subtitle))
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
        "<th style='padding:6px 12px;text-align:left'>Env var</th>"
        "<th style='padding:6px 12px;text-align:left'>Key suffix</th>"
        "<th style='padding:6px 12px;text-align:left'>Script</th>"
        "<th style='padding:6px 12px;text-align:right'>Units</th>"
        "<th style='padding:6px 12px;text-align:right'>Calls</th>"
        "<th style='padding:6px 12px;text-align:right'>Rotated from</th>"
        "<th style='padding:6px 12px;text-align:right'>Rotated to</th>"
        "<th style='padding:6px 12px;text-align:left'>Last used (UTC)</th>"
        "</tr></thead><tbody>"
    ]
    # Sort by env-var order, then by units desc within each key so the
    # heaviest script under a given key floats to the top.
    for r in sorted(today_rows,
                     key=lambda x: (
                         KEY_ORDER.get(x.get("key_tail") or "", 999),
                         -int(x.get("units", 0)),
                         x.get("script") or "",
                     )):
        tail = r["key_tail"]
        env_name = KEY_LABELS.get(tail, f"unknown ({tail})")
        suffix = KEY_SUFFIX.get(tail, tail[-3:] if tail else "")
        pct = _pct(int(r.get("units", 0)))
        color = _color(pct)
        last = (r.get("last_used_at") or "")[:19].replace("T", " ")
        table_html.append(
            "<tr>"
            f"<td style='padding:6px 12px;font-family:monospace;font-size:12px'>{env_name}</td>"
            f"<td style='padding:6px 12px;color:#888;font-family:monospace'>…{suffix}</td>"
            f"<td style='padding:6px 12px;color:#888'>{r.get('script') or '—'}</td>"
            f"<td style='padding:6px 12px;text-align:right;color:{color};"
            f"font-weight:600'>{int(r.get('units',0)):,}</td>"
            f"<td style='padding:6px 12px;text-align:right'>{int(r.get('calls',0)):,}</td>"
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
# Filter out the internal __alert sentinel rows (used by the daily-summary
# de-dup logic in src.youtube_api._flush_quota_log) — they're not real
# consumption data.
rows = [r for r in rows if r.get("key_tail") != "__alert"]
if rows:
    import pandas as pd
    import plotly.express as px

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    daily = (df.groupby(["date", "key_tail"], as_index=False)["units"]
               .sum())

    # Stable ordering for the legend (matches the KPI cards above)
    tail_order: list[str] = []
    seen = set()
    for env_name in _ENV_VAR_NAMES:
        v = os.environ.get(env_name, "").strip()
        if not v:
            continue
        tail = v[-4:]
        if tail in set(daily["key_tail"]) and tail not in seen:
            tail_order.append(tail)
            seen.add(tail)
    for t in daily["key_tail"].unique():
        if t not in seen:
            tail_order.append(t)
            seen.add(t)

    # Friendly env-var name for the legend
    daily["env_var"] = daily["key_tail"].map(
        lambda t: KEY_LABELS.get(t, f"unknown ({t})"))
    env_order = [KEY_LABELS.get(t, f"unknown ({t})") for t in tail_order]

    fig = px.bar(
        daily,
        x="date", y="units",
        color="env_var",
        barmode="group",  # unstacked — one bar per key per day, side-by-side
        category_orders={"env_var": env_order},
        labels={"env_var": "API key", "units": "Units", "date": ""},
    )
    fig.update_layout(
        height=360,
        plot_bgcolor="#0E1117",
        paper_bgcolor="#0E1117",
        font=dict(color="#FAFAFA"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(t=40, l=0, r=0, b=20),
    )
    fig.update_xaxes(gridcolor="#262730", tickformat="%b %d")
    # Fix the Y-axis to the per-key daily quota ceiling so bars are
    # comparable across days and a bar visibly hitting the top means
    # the key actually maxed out.
    fig.update_yaxes(gridcolor="#262730", range=[0, 10000], dtick=2000)
    readable_hover(fig, x_date=True)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Raw data"):
        st.dataframe(df.sort_values(["date", "key_tail"], ascending=False),
                     hide_index=True, width="stretch")
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
