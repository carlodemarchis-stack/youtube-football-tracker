from __future__ import annotations

import os
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.auth import require_admin, show_auth_sidebar
from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import SPORTS, ENTITY_TYPES
from src.analytics import fmt_num

load_dotenv()

require_admin()

st.title("Channel Management")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

db = Database(SUPABASE_URL, SUPABASE_KEY)
yt = YouTubeClient(YOUTUBE_API_KEY)

# ── Add Channel ───────────────────────────────────────────────
st.subheader("Add New Channel")

handle_input = st.text_input("YouTube Handle", placeholder="e.g. @sscnapoli")

if handle_input and st.button("Lookup Channel"):
    with st.spinner("Looking up channel..."):
        info = yt.resolve_handle(handle_input)
    if info:
        st.session_state["lookup_result"] = info
    else:
        st.error(f"Could not find channel for handle: {handle_input}")

if "lookup_result" in st.session_state:
    info = st.session_state["lookup_result"]
    st.success(f"Found: **{info['name']}** ({fmt_num(info['subscriber_count'])} subscribers, {fmt_num(info['video_count'])} videos)")

    with st.form("add_channel"):
        col1, col2 = st.columns(2)
        with col1:
            name = st.text_input("Channel Name", value=info["name"])
            sport = st.selectbox("Sport", SPORTS)
        with col2:
            entity_type = st.selectbox("Entity Type", ENTITY_TYPES)
            country = st.text_input("Country", value=info.get("country", ""))

        submitted = st.form_submit_button("Add Channel", type="primary")
        if submitted:
            channel_data = {
                "name": name,
                "handle": info["handle"],
                "youtube_channel_id": info["youtube_channel_id"],
                "sport": sport,
                "entity_type": entity_type,
                "country": country,
            }
            db.add_channel(channel_data)
            # Also save stats from the lookup
            db.upsert_channel({
                **channel_data,
                "subscriber_count": info.get("subscriber_count", 0),
                "total_views": info.get("total_views", 0),
                "video_count": info.get("video_count", 0),
            })
            st.success(f"Added {name}!")
            del st.session_state["lookup_result"]
            st.rerun()

# ── Existing Channels ─────────────────────────────────────────
st.subheader("Existing Channels")
channels = db.get_all_channels()

if not channels:
    st.info("No channels yet. Add one above or seed defaults.")
    if st.button("Seed default Serie A channels"):
        from src.channels import seed_channels
        seed_channels(db)
        st.success("Seeded 5 default channels!")
        st.rerun()
    st.stop()

# ── Edit dialog ───────────────────────────────────────────────
@st.dialog("Edit Channel")
def edit_channel_dialog(ch):
    with st.form("edit_form"):
        yt_id = ch.get("youtube_channel_id", "")
        st.text_input("YouTube Channel ID", value=yt_id, disabled=True, help="Read-only — from YouTube")
        st.caption(f"[View on YouTube](https://www.youtube.com/channel/{yt_id})")
        edit_name = st.text_input("Name", value=ch.get("name", ""))
        edit_handle = st.text_input("Handle", value=ch.get("handle", ""))
        edit_sport = st.selectbox("Sport", SPORTS, index=SPORTS.index(ch.get("sport", "Football")) if ch.get("sport", "Football") in SPORTS else 0)
        edit_entity = st.selectbox("Entity Type", ENTITY_TYPES, index=ENTITY_TYPES.index(ch.get("entity_type", "Club")) if ch.get("entity_type", "Club") in ENTITY_TYPES else 0)
        edit_country = st.text_input("Country", value=ch.get("country", ""))
        c1, c2 = st.columns(2)
        with c1:
            edit_color = st.color_picker("Primary Color", value=ch.get("color") or "#636EFA")
        with c2:
            edit_color2 = st.color_picker("Secondary Color", value=ch.get("color2") or "#FFFFFF")
        edit_active = st.checkbox("Active", value=ch.get("is_active", True))

        col1, col2 = st.columns(2)
        save = col1.form_submit_button("Save", type="primary")
        delete = col2.form_submit_button("Delete")

        if save:
            db.update_channel(ch["id"], {
                "name": edit_name,
                "handle": edit_handle,
                "sport": edit_sport,
                "entity_type": edit_entity,
                "country": edit_country,
                "color": edit_color,
                "color2": edit_color2,
                "is_active": edit_active,
            })
            st.rerun()
        if delete:
            db.delete_channel(ch["id"])
            st.rerun()

# ── Channel list with edit buttons ────────────────────────────
from src.channels import COUNTRY_TO_LEAGUE

# Group channels by league
league_groups: dict[str, list] = {}
for ch in channels:
    country = ch.get("country", "")
    lg = COUNTRY_TO_LEAGUE.get(country, country) or "Other"
    league_groups.setdefault(lg, []).append(ch)

for league_name, league_chs in sorted(league_groups.items()):
    st.markdown(f"#### {league_name}")
    hdr = st.columns([1, 3, 2, 2, 2, 2, 2, 2, 1])
    hdr[0].markdown("")
    hdr[1].markdown("**Name**")
    hdr[2].markdown("**Handle**")
    hdr[3].markdown("**Type**")
    hdr[4].markdown("**Country**")
    hdr[5].markdown("**Subscribers**")
    hdr[6].markdown("**Data**")
    hdr[7].markdown("**AI**")
    hdr[8].markdown("")
    st.divider()

    for ch in league_chs:
        col_dot, col_name, col_handle, col_type, col_country, col_subs, col_data, col_ai, col_edit = st.columns([1, 3, 2, 2, 2, 2, 2, 2, 1])
        color = ch.get("color") or "#636EFA"
        color2 = ch.get("color2") or "#FFFFFF"
        col_dot.markdown(f'<span style="display:inline-block;width:16px;height:16px;border-radius:50%;background:{color};border:1px solid rgba(255,255,255,0.3);position:relative"><span style="display:block;width:8px;height:8px;border-radius:50%;background:{color2};position:absolute;top:3px;left:3px"></span></span>', unsafe_allow_html=True)
        col_name.write(ch.get("name", ""))
        col_handle.write(ch.get("handle", ""))
        col_type.write(ch.get("entity_type", ""))
        col_country.write(ch.get("country", ""))
        col_subs.write(fmt_num(ch.get('subscriber_count', 0)))
        # Data & AI dates
        fetched = (ch.get("last_fetched") or "")[:10] or "-"
        insights = db.get_insights(ch["id"])
        ai_date = (insights.get("generated_at", "") if insights else "")[:10] or "-"
        stale = bool(fetched != "-" and ai_date != "-" and fetched > ai_date)
        col_data.write(fetched)
        if stale:
            col_ai.markdown(f'<span style="color:#FFA500">{ai_date}</span>', unsafe_allow_html=True)
        else:
            col_ai.write(ai_date)
        if col_edit.button("\u270F\uFE0F", key=f"edit_{ch['id']}"):
            edit_channel_dialog(ch)
