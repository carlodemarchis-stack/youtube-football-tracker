"""Admin — Usage analytics (per-user, from usage_events).

Simple owned analytics: who's active, what's popular, last-seen, and a
Substack-style engagement rating per user. Reads the usage_events table
written by src.usage.log_page_view.
"""
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_admin

load_dotenv()
require_admin()

st.title("📊 Usage Analytics")

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

if not events:
    st.info("No usage recorded yet. Events start logging once the "
            "usage_events table exists and users browse the app.")
    st.stop()

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
