"""Auth: Streamlit native OAuth + Supabase for user profiles.

Public pages work without login. Admin pages require Google sign-in + admin role.
"""
from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

ADMIN_EMAIL = "carlodemarchis@gmail.com"


def _get_db():
    from src.database import Database
    return Database(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))


def get_current_user() -> dict | None:
    """Return current user if logged in, else None. Does NOT block."""
    if not st.user.is_logged_in:
        return None

    email = (st.user.email or "").lower().strip()
    name = st.user.name or ""

    if "yt_user" in st.session_state:
        return st.session_state["yt_user"]

    db = _get_db()
    profile = db.get_user_profile(email)

    if not profile:
        role = "admin" if email == ADMIN_EMAIL else "viewer"
        profile = db.upsert_user_profile(email, name, role)

    user = {
        "email": email,
        "display_name": name,
        "role": profile.get("role", "viewer"),
    }
    st.session_state["yt_user"] = user
    return user


def is_admin() -> bool:
    user = get_current_user()
    return user is not None and user.get("role") == "admin"


def require_admin():
    """Block page if user is not an admin. Shows login or access denied."""
    user = get_current_user()
    if user is None:
        st.warning("Please sign in to access this page.")
        try:
            if st.button("Sign in with Google"):
                st.login("google")
        except Exception as e:
            st.error(f"Login unavailable: {e}")
        st.stop()
    if user.get("role") != "admin":
        st.error("Admin access required.")
        st.stop()
    return user


def show_auth_sidebar():
    """Render login/logout + user info in the sidebar."""
    user = get_current_user()
    if user:
        st.sidebar.markdown(f"**{user['display_name'] or user['email']}**")
        if user["role"] == "admin":
            st.sidebar.caption("Admin")
        if st.sidebar.button("Sign out"):
            st.session_state.pop("yt_user", None)
            st.logout()
            st.rerun()
    else:
        with st.sidebar:
            st.markdown("---")
            try:
                if st.button("Sign in with Google"):
                    st.login("google")
            except Exception as e:
                st.caption(f"Login unavailable: {e}")
