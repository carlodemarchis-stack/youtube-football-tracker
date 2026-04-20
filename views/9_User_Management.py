from __future__ import annotations

import os
import io
import csv
from datetime import datetime, timezone

import streamlit as st
from dotenv import load_dotenv

from src.auth import require_admin, ROLE_LEVELS
from src.database import Database

load_dotenv()

require_admin()

st.title("User Management")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
db = Database(SUPABASE_URL, SUPABASE_KEY)

users = db.get_all_users()

if not users:
    st.info("No registered users yet.")
    st.stop()

# ── Summary KPIs ────────────────────────────────────────────
_total = len(users)
_onboarded = sum(1 for u in users if u.get("onboarded"))
_admins = sum(1 for u in users if (u.get("role") or "").lower() == "admin")
_premium = sum(1 for u in users if (u.get("role") or "").lower() == "premium")
_viewers = _total - _admins - _premium

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total users", _total)
k2.metric("Onboarded", f"{_onboarded}/{_total}")
k3.metric("🛡️ Admins", _admins)
k4.metric("⭐ Premium", _premium)
k5.metric("👤 Viewers", _viewers)

# ── Export CSV ──────────────────────────────────────────────
_csv_buf = io.StringIO()
_writer = csv.DictWriter(
    _csv_buf,
    fieldnames=["email", "first_name", "last_name", "company", "linkedin_url",
                "role", "onboarded", "display_name", "created_at", "last_login"],
    extrasaction="ignore",
)
_writer.writeheader()
for u in users:
    _writer.writerow(u)

st.download_button(
    "⬇️ Download CSV",
    data=_csv_buf.getvalue(),
    file_name=f"users_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv",
    mime="text/csv",
)

# ── Filters ─────────────────────────────────────────────────
search = st.text_input("Search (name, email, company)", placeholder="dazn, carlo, @acme.com…").strip().lower()
c1, c2 = st.columns([1, 1])
with c1:
    role_filter = st.multiselect("Role", ["admin", "premium", "viewer"], default=[])
with c2:
    onboarded_filter = st.selectbox("Onboarding", ["All", "Onboarded only", "Pending only"])

def _matches(u: dict) -> bool:
    if role_filter and (u.get("role") or "viewer").lower() not in role_filter:
        return False
    if onboarded_filter == "Onboarded only" and not u.get("onboarded"):
        return False
    if onboarded_filter == "Pending only" and u.get("onboarded"):
        return False
    if search:
        hay = " ".join([
            u.get("email") or "", u.get("display_name") or "",
            u.get("first_name") or "", u.get("last_name") or "",
            u.get("company") or "",
        ]).lower()
        if search not in hay:
            return False
    return True

filtered = [u for u in users if _matches(u)]
st.caption(f"Showing **{len(filtered)}** of {_total} users.")
st.markdown("---")

ROLES = ["viewer", "premium", "admin"]
ROLE_ICONS = {"admin": "🛡️", "premium": "⭐", "viewer": "👤"}


def _fmt_date(raw: str | None) -> str:
    if not raw:
        return "—"
    return str(raw)[:10]


def _row(u: dict):
    email = u["email"]
    current_role = (u.get("role") or "viewer").lower()
    if current_role not in ROLES:
        current_role = "viewer"

    first = (u.get("first_name") or "").strip()
    last = (u.get("last_name") or "").strip()
    full_name = f"{first} {last}".strip() or u.get("display_name") or email.split("@")[0]
    company = (u.get("company") or "").strip()
    linkedin = (u.get("linkedin_url") or "").strip()
    onboarded = bool(u.get("onboarded"))

    col_info, col_meta, col_role, col_save = st.columns([3, 3, 2, 1])

    with col_info:
        badge = ROLE_ICONS.get(current_role, "")
        status = "" if onboarded else " <span style='background:#F5A62344;color:#F5A623;padding:1px 6px;border-radius:3px;font-size:10px;margin-left:6px'>pending</span>"
        st.markdown(
            f"{badge} **{full_name}**{status}<br>"
            f"<span style='color:#888;font-size:12px'>{email}</span>",
            unsafe_allow_html=True,
        )

    with col_meta:
        bits = []
        if company:
            bits.append(f"🏢 {company}")
        if linkedin:
            bits.append(f"<a href='{linkedin}' target='_blank' style='color:#0A66C2;text-decoration:none'>💼 LinkedIn</a>")
        bits.append(f"<span style='color:#666'>joined {_fmt_date(u.get('created_at'))}</span>")
        if u.get("last_login"):
            bits.append(f"<span style='color:#666'>last {_fmt_date(u.get('last_login'))}</span>")
        st.markdown(
            "<div style='font-size:13px;line-height:1.6'>" + " · ".join(bits) + "</div>",
            unsafe_allow_html=True,
        )

    with col_role:
        new_role = st.selectbox(
            "Role",
            ROLES,
            index=ROLES.index(current_role),
            key=f"role_sel_{email}",
            label_visibility="collapsed",
        )

    with col_save:
        if new_role != current_role:
            if st.button("Save", key=f"save_{email}", type="primary"):
                db.set_user_role(email, new_role)
                st.success(f"{email} → {new_role}")
                st.rerun()
        else:
            st.caption("—")


for u in filtered:
    _row(u)
    st.markdown("<hr style='margin:4px 0;border-color:#222'>", unsafe_allow_html=True)
