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

    col_info, col_meta, col_role, col_edit = st.columns([3, 3, 2, 1])

    with col_info:
        badge = ROLE_ICONS.get(current_role, "")
        status = (
            " <span style='background:#00CC9633;color:#00CC96;padding:1px 6px;"
            "border-radius:3px;font-size:10px;margin-left:6px'>✓ onboarded</span>"
            if onboarded else
            " <span style='background:#F5A62333;color:#F5A623;padding:1px 6px;"
            "border-radius:3px;font-size:10px;margin-left:6px'>pending</span>"
        )
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
        if new_role != current_role:
            if st.button("Save role", key=f"save_role_{email}", type="primary", use_container_width=True):
                db.set_user_role(email, new_role)
                st.success(f"{email} → {new_role}")
                st.rerun()

    with col_edit:
        edit_key = f"_editing_{email}"
        if st.button("✏️ Edit", key=f"edit_btn_{email}", use_container_width=True):
            st.session_state[edit_key] = not st.session_state.get(edit_key, False)

    # Expanded edit form
    if st.session_state.get(f"_editing_{email}"):
        with st.container(border=True):
            ec1, ec2 = st.columns(2)
            new_first = ec1.text_input("First name", value=first, key=f"ef_{email}")
            new_last = ec2.text_input("Last name", value=last, key=f"el_{email}")
            new_company = st.text_input("Company", value=company, key=f"ec_{email}")
            new_linkedin = st.text_input("LinkedIn URL", value=linkedin, key=f"eli_{email}")
            new_onboarded = st.checkbox("Onboarded", value=onboarded, key=f"eo_{email}",
                                         help="Uncheck to make this user see the welcome card again on next login.")

            bc1, bc2, _ = st.columns([1, 1, 4])
            with bc1:
                if st.button("💾 Save changes", key=f"save_edit_{email}", type="primary"):
                    try:
                        db.client.table("user_profiles").update({
                            "first_name": new_first.strip(),
                            "last_name": new_last.strip(),
                            "company": new_company.strip(),
                            "linkedin_url": new_linkedin.strip(),
                            "onboarded": new_onboarded,
                            "display_name": f"{new_first} {new_last}".strip() or u.get("display_name"),
                        }).eq("email", email).execute()
                        st.session_state.pop(f"_editing_{email}", None)
                        st.success(f"Updated {email}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Save failed: {e}")
            with bc2:
                if st.button("Cancel", key=f"cancel_edit_{email}"):
                    st.session_state.pop(f"_editing_{email}", None)
                    st.rerun()


for u in filtered:
    _row(u)
    st.markdown("<hr style='margin:4px 0;border-color:#222'>", unsafe_allow_html=True)
