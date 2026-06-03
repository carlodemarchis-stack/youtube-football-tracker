"""Admin — Usage analytics (per-user, from usage_events).

Simple owned analytics: who's active, what's popular, last-seen, and a
Substack-style engagement rating per user. Reads the usage_events table
written by src.usage.log_page_view.
"""
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_admin, get_current_user

load_dotenv()
require_admin()

st.title("📊 Usage Analytics")

# Admin's own traffic dominates dev sessions; default the toggle to
# "exclude" so the numbers reflect real users. Reads the current admin
# email dynamically — any admin viewing the page can toggle their own.
_me = (get_current_user() or {}).get("email") or ""
_exclude_self = False
if _me:
    _exclude_self = st.checkbox(
        f"Exclude my traffic ({_me})", value=True, key="usage_exclude_self")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")
db = Database(SUPABASE_URL, SUPABASE_KEY)

WINDOW_DAYS = 30
_since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()

# ── Pull events (paginated) ───────────────────────────────────────────
events = []
off = 0
try:
    while True:
        rs = (db.client.table("usage_events")
              .select("email,page,session_id,created_at")
              .gte("created_at", _since)
              .order("created_at", desc=True)
              .range(off, off + 999).execute().data) or []
        events.extend(rs)
        if len(rs) < 1000:
            break
        off += 1000
except Exception as e:
    st.warning(f"usage_events not available yet ({e}). Run the table "
               "creation SQL in Supabase — see src/usage.py.")
    st.stop()

if _exclude_self and _me:
    events = [e for e in events
              if (e.get("email") or "").lower() != _me.lower()]

if not events:
    st.info("No usage recorded yet. Events start logging once the "
            "usage_events table exists and users browse the app.")
    st.stop()

# Split signed-in users from anonymous home visitors. Anonymous events
# only ever hit the public Home page (see src.usage.log_page_view), so
# they have no per-user analytics worth tracking — we report them as a
# headline-count section at the bottom. Everything else on this page
# treats `events` as signed-in traffic only.
_anon_events = [e for e in events if (e.get("email") or "") == "(anonymous)"]
events = [e for e in events if (e.get("email") or "") != "(anonymous)"]
st.caption(
    f"Signed-in user activity below · {len(_anon_events):,} anonymous "
    "home visits shown separately at the bottom."
)

today = datetime.now(timezone.utc).date()


def _d(ts: str) -> date:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).date()


# ── Aggregate per user ────────────────────────────────────────────────
per = defaultdict(lambda: {"views": 0, "days": set(), "sessions": set(),
                           "last": None, "pages": defaultdict(int)})
page_pop = defaultdict(int)
day_active = defaultdict(set)   # date -> set(emails)
for e in events:
    em = e.get("email") or "?"
    d = _d(e["created_at"])
    u = per[em]
    u["views"] += 1
    u["days"].add(d)
    u["sessions"].add(e.get("session_id"))
    if u["last"] is None or d > u["last"]:
        u["last"] = d
    u["pages"][e.get("page") or "?"] += 1
    page_pop[e.get("page") or "?"] += 1
    day_active[d].add(em)


def rating(active_days: int, last: date) -> tuple[str, str]:
    gap = (today - last).days if last else 999
    if gap > WINDOW_DAYS:
        return "💤", "Dormant"
    if active_days >= 10:
        return "🔥", "Power"
    if active_days >= 4:
        return "⭐", "Active"
    return "🌱", "Casual"


# ── Top-line ──────────────────────────────────────────────────────────
dau = len(day_active.get(today, set()))
wau = len({em for d, ems in day_active.items()
           if (today - d).days < 7 for em in ems})
mau = len(per)
total_views = sum(u["views"] for u in per.values())
c1, c2, c3, c4 = st.columns(4)
c1.metric("Active users (30d)", mau)
c2.metric("WAU (7d)", wau)
c3.metric("DAU (today)", dau)
c4.metric("Page views (30d)", f"{total_views:,}")

# ── Today ────────────────────────────────────────────────────────────
# Mini-recap of the current day's traffic — sits just under the 30-day
# KPI row so the admin can see "what's happening RIGHT NOW" without
# having to read the trend chart's last bar.
_today_events = [e for e in events if _d(e["created_at"]) == today]
st.markdown("#### 📅 Today")
if not _today_events:
    st.caption("No traffic yet today.")
else:
    _t_views = len(_today_events)
    _t_users = len({e.get("email") for e in _today_events})
    _t_pages: dict[str, int] = defaultdict(int)
    for e in _today_events:
        _t_pages[e.get("page") or "?"] += 1
    _top_page, _top_page_n = max(_t_pages.items(), key=lambda kv: kv[1])

    # New users today — user_profiles.created_at matches the local
    # `today` (admin's machine date). Counts signups that finished the
    # onboarding step (anonymous landings don't create a profile row).
    _new_users_today = 0
    try:
        _new_users_today = sum(
            1 for u in db.get_all_users()
            if _d(u.get("created_at", "") or "") == today)
    except Exception:
        _new_users_today = 0

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Page views today", _t_views)
    t2.metric("Active users today", _t_users)
    t3.metric("New users today", _new_users_today)
    t4.metric("Top page today", _top_page, f"{_top_page_n} views")

    # Per-user mini table for today (who visited what).
    _today_per: dict[str, dict] = defaultdict(
        lambda: {"views": 0, "pages": defaultdict(int)})
    for e in _today_events:
        em = e.get("email") or "?"
        _today_per[em]["views"] += 1
        _today_per[em]["pages"][e.get("page") or "?"] += 1
    _today_rows = []
    for em, u in sorted(_today_per.items(), key=lambda kv: -kv[1]["views"]):
        _t_top = max(u["pages"].items(), key=lambda kv: kv[1])[0] if u["pages"] else "—"
        _today_rows.append({
            "User": em,
            "Views today": u["views"],
            "Pages visited": len(u["pages"]),
            "Top page today": _t_top,
        })
    st.dataframe(_today_rows, width="stretch", hide_index=True)

st.markdown("---")

# Active-users-per-day trend (last 30 days, zero-filled)
_days = [today - timedelta(days=i) for i in range(WINDOW_DAYS - 1, -1, -1)]
_trend = pd.DataFrame([
    {"Date": d.isoformat(), "Active users": len(day_active.get(d, set()))}
    for d in _days
])
st.caption("Active users per day")
st.bar_chart(_trend, x="Date", y="Active users", height=200)

# Tier counts
tiers = defaultdict(int)
for em, u in per.items():
    tiers[rating(len(u["days"]), u["last"])[1]] += 1
_TIER_EMOJI = {"Power": "🔥", "Active": "⭐", "Casual": "🌱", "Dormant": "💤"}
st.caption("Engagement — " + "  ·  ".join(
    f"{_TIER_EMOJI[lbl]} {lbl}: {tiers.get(lbl, 0)}"
    for lbl in ("Power", "Active", "Casual", "Dormant")))

st.markdown("---")

# ── Per-user table ────────────────────────────────────────────────────
st.subheader("Per-user")
rows = []
for em, u in per.items():
    emoji, label = rating(len(u["days"]), u["last"])
    top_page = max(u["pages"].items(), key=lambda kv: kv[1])[0] if u["pages"] else "—"
    rows.append({
        "Rating": f"{emoji} {label}",
        "User": em,
        "Views (30d)": u["views"],
        "Active days": len(u["days"]),
        "Sessions": len(u["sessions"]),
        "Last seen": u["last"].isoformat() if u["last"] else "—",
        "Top page": top_page,
    })
rows.sort(key=lambda r: -r["Views (30d)"])
st.dataframe(rows, width="stretch", hide_index=True)

# ── Page popularity ───────────────────────────────────────────────────
st.subheader("Most-visited pages (30d)")
pp = sorted(page_pop.items(), key=lambda kv: -kv[1])
st.dataframe(
    [{"Page": p, "Views": n} for p, n in pp],
    width="stretch", hide_index=True,
)

# ── Anonymous traffic ────────────────────────────────────────────────
# Anonymous visits only ever land on Home (src.usage.log_page_view
# skips admin pages and tags signed-out hits as "(anonymous)"), so the
# only useful signal here is "how many hit Home this period". One
# session → one event, deliberately.
st.markdown("---")
st.subheader("👤 Anonymous home visits")
_an_today = sum(1 for e in _anon_events if _d(e["created_at"]) == today)
_an_7d = sum(1 for e in _anon_events
             if (today - _d(e["created_at"])).days < 7)
_an_30d = len(_anon_events)
a1, a2, a3 = st.columns(3)
a1.metric("Today", _an_today)
a2.metric("Last 7 days", _an_7d)
a3.metric("Last 30 days", _an_30d)
st.caption(
    "Logged once per anonymous session when someone lands on Home — "
    "no per-user breakdown is meaningful (they're all '(anonymous)')."
)
