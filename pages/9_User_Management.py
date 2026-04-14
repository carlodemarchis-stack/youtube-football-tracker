from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.auth import require_admin, show_auth_sidebar
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

st.subheader(f"Registered Users ({len(users)})")

for user in users:
    col1, col2, col3 = st.columns([3, 2, 1])
    with col1:
        st.markdown(f"**{user.get('display_name', '')}** ({user['email']})")
    with col2:
        st.caption(f"Joined: {user.get('created_at', '')[:10]}")
    with col3:
        current_role = user.get("role", "viewer")
        new_role = "viewer" if current_role == "admin" else "admin"
        label = "Revoke Admin" if current_role == "admin" else "Make Admin"
        if st.button(label, key=f"role_{user['email']}"):
            db.set_user_role(user["email"], new_role)
            st.rerun()
