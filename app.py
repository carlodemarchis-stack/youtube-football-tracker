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
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Navigation ────────────────────────────────────────────────
# Tier 0 — public (visible to everyone, including not signed in)
public_pages = [
    st.Page("views/0_Home.py", title="Home", default=True),
]

# Tier 1 — viewer (any signed-in user)
viewer_pages = [
    st.Page("views/1_Daily_Recap.py", title="Daily Recap"),
    st.Page("views/1b_Latest.py", title="Latest Videos"),
    st.Page("views/2_Clubs.py", title="Channels"),
    st.Page("views/3_Season_2526.py", title="Season 25/26"),
    st.Page("views/4_Top_Videos.py", title="Top Videos"),
    st.Page("views/16_Socials.py", title="Socials"),
    st.Page("views/13_Players.py", title="Players"),
    st.Page("views/14_Federations.py", title="Federations"),
    st.Page("views/15_Other_Clubs.py", title="Other Clubs"),
    # st.Page("views/12_Reddit.py", title="Reddit"),  # disabled — Reddit blocks cloud IPs
]

# Tier 2 — premium (promote users via User Management)
# Viewers see these with a 🔒 suffix; clicking shows an upgrade CTA.
premium_pages_unlocked = [
    st.Page("views/5_Club_Comparison.py", title="Compare"),
    st.Page("views/6_AI_Analysis.py", title="AI Analysis"),
    st.Page("views/10_Ask_Data.py", title="Ask Data"),
]
premium_pages_locked = [
    st.Page("views/5_Club_Comparison.py", title="Compare 🔒"),
    st.Page("views/6_AI_Analysis.py", title="AI Analysis 🔒"),
    st.Page("views/10_Ask_Data.py", title="Ask Data 🔒"),
]

# Tier 3 — admin only
admin_pages = [
    st.Page("views/7_Refresh_Data.py", title="Data"),
    st.Page("views/8_Channel_Management.py", title="Channel Management"),
    st.Page("views/9_User_Management.py", title="User Management"),
    st.Page("views/11_Snapshot_Debug.py", title="Snapshot Debug"),
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
if st.query_params.get("view") == "feed":
    st.session_state["_feed_mode"] = True

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

_no_filter_pages = {"Players", "Federations", "Other Clubs"}
_show_filter = getattr(pg, "title", "") not in _no_filter_pages

if SUPABASE_URL and SUPABASE_KEY:
    db = Database(SUPABASE_URL, SUPABASE_KEY)
    all_channels = db.get_all_channels()
    if all_channels:
        # Always cache the channel list so isolated pages can read it,
        # but only render the league/club filter on pages where it applies.
        st.session_state["_global_channels"] = all_channels
        if _show_filter:
            league, club = render_header_filter(all_channels)
            st.session_state["_global_league"] = league
            st.session_state["_global_club"] = club

if _show_filter:
    st.markdown("---")

# ── YouTube overlay player (one per session) ─────────────────
import streamlit.components.v1 as _components
from src.analytics import yt_overlay_html
_components.html(yt_overlay_html(), height=0)

# ── Sidebar promo ────────────────────────────────────────────
with st.sidebar:
    st.markdown("---")
    st.markdown(
        """
        <div style="font-size:12px;color:#999;line-height:1.6">
            <b style="color:#FAFAFA">YTFT</b> brought to you by<br>
            <b style="color:#FAFAFA">Carlo De Marchis</b><br>
            <i>A guy with a scarf</i><br>
            <a href="https://www.linkedin.com/newsletters/a-guy-with-a-scarf-6998145822441775104/" target="_blank" style="color:#FF6B6B;text-decoration:none">📬 Newsletter</a> ·
            <a href="https://linkedin.com/in/carlodemarchis" target="_blank" style="color:#0A66C2;text-decoration:none">💼 LinkedIn</a><br>
            <a href="https://aguywithascarf.substack.com" target="_blank" style="color:#FF6719;text-decoration:none">✍️ Substack</a><br>
            <a href="https://a-guy-with-a-scarf.mykajabi.com/course" target="_blank" style="color:#F5A623;text-decoration:none">📖 Course</a> ·
            <a href="https://amzn.eu/d/09cuCSkB" target="_blank" style="color:#F5A623;text-decoration:none">📕 Book</a>
        </div>
        """,
        unsafe_allow_html=True,
    )

pg.run()
