from __future__ import annotations

import os
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

st.caption(f"{len(users)} registered user(s). Promote a viewer to premium or admin.")
st.markdown("---")

ROLES = ["viewer", "premium", "admin"]
ROLE_ICONS = {"admin": "🛡️", "premium": "⭐", "viewer": "👤"}

for user in users:
    email = user["email"]
    current_role = (user.get("role") or "viewer").lower()
    if current_role not in ROLES:
        current_role = "viewer"

    col_info, col_role, col_save = st.columns([3, 2, 1])

    with col_info:
        st.markdown(
            f"{ROLE_ICONS.get(current_role, '')} **{user.get('display_name') or email.split('@')[0]}** "
            f"<span style='color:#888'>({email})</span>",
            unsafe_allow_html=True,
        )
        st.caption(f"Joined: {(user.get('created_at') or '')[:10]}")

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
