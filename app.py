from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv
from src.auth import show_auth_sidebar, is_admin, is_premium, is_logged_in
from src.database import Database
from src.filters import render_header_filter

load_dotenv()

# Promote Streamlit Cloud secrets to env so os.getenv() works everywhere
try:
    for _k in ("SUPABASE_URL", "SUPABASE_KEY", "YOUTUBE_API_KEY"):
        if _k in st.secrets and not os.getenv(_k):
            os.environ[_k] = st.secrets[_k]
except Exception:
    pass

st.set_page_config(
    page_title="Football YouTube Tracker",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Navigation ────────────────────────────────────────────────
# Tier 0 — public (visible to everyone, including not signed in)
public_pages = [
    st.Page("pages/0_Home.py", title="Home", default=True),
]

# Tier 1 — viewer (any signed-in user)
viewer_pages = [
    st.Page("pages/1_Daily_Recap.py", title="Daily Recap"),
    st.Page("pages/1b_Latest.py", title="Latest Videos"),
    st.Page("pages/2_Clubs.py", title="Channels"),
    st.Page("pages/3_Season_2526.py", title="Season 25/26"),
    st.Page("pages/4_Top_Videos.py", title="Top Videos"),
]

# Tier 2 — premium (promote users via User Management)
# Viewers see these with a 🔒 suffix; clicking shows an upgrade CTA.
premium_pages_unlocked = [
    st.Page("pages/5_Club_Comparison.py", title="Compare"),
    st.Page("pages/6_AI_Analysis.py", title="AI Analysis"),
    st.Page("pages/10_Ask_Data.py", title="Ask Data"),
]
premium_pages_locked = [
    st.Page("pages/5_Club_Comparison.py", title="Compare 🔒"),
    st.Page("pages/6_AI_Analysis.py", title="AI Analysis 🔒"),
    st.Page("pages/10_Ask_Data.py", title="Ask Data 🔒"),
]

# Tier 3 — admin only
admin_pages = [
    st.Page("pages/7_Refresh_Data.py", title="Data"),
    st.Page("pages/8_Channel_Management.py", title="Channel Management"),
    st.Page("pages/9_User_Management.py", title="User Management"),
    st.Page("pages/11_Snapshot_Debug.py", title="Snapshot Debug"),
]

nav = {"": public_pages}
if is_logged_in():
    nav[""] = public_pages + viewer_pages
if is_premium():
    nav["Premium"] = premium_pages_unlocked
elif is_logged_in():
    nav["Premium"] = premium_pages_locked
if is_admin():
    nav["Admin"] = admin_pages

pg = st.navigation(nav)
show_auth_sidebar()

# ── Global header filter ──────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if SUPABASE_URL and SUPABASE_KEY:
    db = Database(SUPABASE_URL, SUPABASE_KEY)
    all_channels = db.get_all_channels()
    if all_channels:
        league, club = render_header_filter(all_channels)
        st.session_state["_global_league"] = league
        st.session_state["_global_club"] = club
        st.session_state["_global_channels"] = all_channels

st.markdown("---")

pg.run()
