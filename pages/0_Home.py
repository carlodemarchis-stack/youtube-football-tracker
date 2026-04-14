from __future__ import annotations

import streamlit as st

st.title("Serie A YouTube Tracker")
st.markdown(
    """
    Track YouTube stats for Italian Serie A football clubs.

    **Pages:**
    - **Channel Overview** - Compare all channels side by side
    - **Top Videos** - Top 100 most-viewed videos with analytics
    - **Channel Detail** - Deep dive into a single channel
    - **AI Analysis** - Claude-powered insights per channel
    """
)

st.markdown("---")
st.caption("Data sourced from YouTube Data API v3. Stats update on demand via Refresh Data.")
