from __future__ import annotations

import streamlit as st

st.title("YouTube Football Tracker")
st.markdown(
    """
    Track YouTube stats for top leagues football clubs.

    ### Global filter
    At the top of every page you'll find a cascading filter that sets the zoom level
    for the whole site. Your selection persists as you navigate.

    - **All Leagues** — site-wide view. A secondary dropdown lets you pick the
      *scope*:
        - *Overall* — everything aggregated by league
        - *Leagues only* — just league channels
        - *All clubs* — all clubs across every league
    - **One League** — narrows to a single competition; a club dropdown lets you
      also pick *All Clubs* or *All Clubs + League channel*
    - **One Club** — focuses every page on a single channel

    ### Pages
    - **Channels** — league and club rankings with subscribers, views,
      videos (long vs shorts), and engagement metrics; charts adapt to your
      filter's zoom level
    - **Season 25/26** — current-season performance: videos published since
      August 2025, split by long-form vs Shorts
    - **Top Videos** — the top 100 most-viewed videos for your current
      selection, with ranks, years, and theme breakdown
    - **Compare** — pick any clubs across leagues and line them up side by side
    - **AI Analysis** — Claude-powered insights for the selected club
    """
)

st.markdown("---")
st.caption("Data sourced from YouTube Data API v3. Stats update on demand.")
