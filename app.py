from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv
from src.auth import show_auth_sidebar, is_admin
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
public_pages = [
    st.Page("pages/0_Home.py", title="Home", default=True),
    st.Page("pages/2_Clubs.py", title="Channels"),
    st.Page("pages/3_Season_2526.py", title="Season 25/26"),
    st.Page("pages/4_Top_Videos.py", title="Top Videos"),
    st.Page("pages/6_AI_Analysis.py", title="AI Analysis"),
    st.Page("pages/5_Club_Comparison.py", title="Compare"),
]

admin_pages = [
    st.Page("pages/7_Refresh_Data.py", title="Data"),
    st.Page("pages/8_Channel_Management.py", title="Channel Management"),
    st.Page("pages/9_User_Management.py", title="User Management"),
]

nav = {"": public_pages}
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
