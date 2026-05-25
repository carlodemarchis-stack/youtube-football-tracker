"""Lightweight per-user usage tracking (Option A — owned, in Supabase).

One row per *real* page change in `usage_events`. Streamlit re-runs the
whole script on every interaction, so we guard on a url_path change held
in session_state — otherwise a single page view logs dozens of events.

Table (run once in Supabase):
    create table if not exists usage_events (
        id bigint generated always as identity primary key,
        email text,
        page text,
        session_id text,
        created_at timestamptz default now()
    );
    create index if not exists usage_events_email_idx   on usage_events(email);
    create index if not exists usage_events_created_idx  on usage_events(created_at);

Logging is best-effort: any failure (table missing, transient) is
swallowed so it can never break a page render.
"""
from __future__ import annotations

import os
import uuid

import streamlit as st

from src.database import Database
from src.auth import get_current_user


@st.cache_resource(show_spinner=False)
def _db() -> Database:
    return Database(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", ""),
    )


def log_page_view(page: str) -> None:
    """Record a page view for the signed-in user — once per page change."""
    user = get_current_user()
    if not user or not user.get("email"):
        return  # only track identified users
    if st.session_state.get("_usage_last_page") == page:
        return  # same page, just a Streamlit rerun — skip
    st.session_state["_usage_last_page"] = page
    sid = st.session_state.get("_usage_sid")
    if not sid:
        sid = uuid.uuid4().hex[:16]
        st.session_state["_usage_sid"] = sid
    try:
        _db().client.table("usage_events").insert({
            "email": user["email"],
            "page": page,
            "session_id": sid,
        }).execute()
    except Exception:
        pass  # never let analytics break a render
