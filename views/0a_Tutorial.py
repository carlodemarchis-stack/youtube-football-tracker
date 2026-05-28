"""📖 Tutorial — extracted from the original logged-out home.

Holds the explanatory walk-through (global filter, each page group,
fine print, daily-refresh schedule, About) that used to live below
the gainers tables on the home page. Lifted verbatim so logged-in
users get a minimal home and the longer-form content has a dedicated
home of its own.

No login required — anyone can read it.
"""
from __future__ import annotations

import streamlit as st

from src.channels import current_season_label_safe as _csl_home

st.title("📖 Tutorial")
st.caption(
    "How the tracker is organised, what each page does, and the "
    "house rules. Pulled out of the home page so it doesn't get in "
    "the way once you're signed in."
)

st.page_link("views/0_Home.py", label="← Back to Home")

st.markdown("---")

st.markdown(
    ("""
    ### 🎛️ Global filter
    At the top of the core **Top 5 Leagues** pages you'll find a cascading
    filter that sets the zoom level for those views. Your selection persists
    as you navigate. (Home, the FIFA World Cup 2026 sub-app, and the
    standalone *Others* pages don't use it — WC2026 has its own
    Confederation → Team filter instead.)

    - **All Leagues** — site-wide view with a secondary scope selector:
      *Overall* (aggregated by league), *Leagues only* (league channels),
      or *All clubs* (every club across leagues)
    - **One League** — narrows to a single competition; pick *All Clubs*
      or *All Clubs + League channel*
    - **One Club** — focuses those pages on a single channel

    So in effect every Top 5 Leagues page is three pages in one — the
    same charts and tables, re-scoped from the whole site down to a
    single club.

    ---

    ### ⚽ Top 5 Leagues

    The core of the site — Serie A, Premier League, La Liga, Bundesliga
    and Ligue 1, all responding to the global filter above. A quick
    sign-in opens every page below.

    **Daily Recap** — what happened yesterday: view and subscriber
    gains, leaderboards, new uploads, most-watched videos, and 14-day
    trend charts. Adapts from league-level summaries down to single-club detail.

    **Latest Videos** — the most recently published videos across all
    tracked channels. Sortable by views, likes, comments, duration, and
    age. Includes a 24h timeline strip and a live-now banner at the top.

    **30-Day Trends** — the past 30 days of cohort momentum: Δ channel
    views per day, new videos per day by format, and an archive-share
    metric showing how much of the growth came from videos older than
    30 days post-publish. At Z1 you see per-league breakdowns; at Z2,
    per-channel within a league. Top-25 videos by window-Δ at the bottom
    of the page. AI-written "30-day shape" note at the top.

    **Season ({_season_label})** — current-season rhythm and cadence:
    videos-per-day, publishing heatmap, monthly output by format
    (long / Shorts / Live), view concentration per league or club,
    and zero-publish-day counts.

    **Season Top** — the season's hits in three ranked tables — top
    videos by views, by likes, and by comments — for whatever scope the
    global filter is set to (all leagues / one league / one club).

    **All-time** — league and club rankings: subscribers, views,
    video counts (long vs shorts), views/sub, views/video. Sortable
    tables and bar charts at every zoom level.

    **All-Time Top** — the 100 most-viewed videos *ever* for your
    selection: rank-by-views chart, rank-vs-year scatter, year
    distribution, theme pie, and a sortable video list. Different lens
    from Season Top (lifetime, not just this season).

    **No. 1 Videos** — every channel's single most-viewed video,
    side by side: the one upload that defines each club's reach.

    ---

    ### 🏆 FIFA World Cup 2026

    Its own sidebar group — a self-contained sub-app, separate from the
    Top-5 league views and unaffected by the global filter. It has its
    own **Confederation → Team** filter (top of these pages; the
    selection persists as you move between them).

    **All Channels** — the official YouTube channels of all 48 qualified
    national teams, plus FIFA and the 6 confederations. When a country
    runs more than one official channel, the stats are summed into the
    country's row with a +N alt chip; every channel is also listed
    individually. Sortable by subscribers, views, season output and
    views/video.

    **Latest Videos** — the most recently published videos across every
    WC2026 channel: the same feed as the core Latest page (sortable
    list, format/scheduled controls, live-now banner), scoped to the
    World Cup, with a 24h published-timeline strip that groups by
    confederation — or by team / a full thumbnail strip as you filter
    down.

    **Trends** — view gains and videos published (long / shorts / live)
    day by day on the road to the tournament, plus a biggest-movers
    leaderboard, built from a daily snapshot of every WC2026 channel.

    ---

    ### 🧪 The Lab — experimental views

    **Outliers** — channels whose profile sits unusually far from their
    peers on key metrics. Surfaces "interesting" channels worth a look.

    **Other Social** — beyond YouTube: each club's footprint across
    Instagram, X, Facebook, TikTok, Threads, LinkedIn (and more). Badge
    grid linking to every account, plus a sortable follower-count
    leaderboard with one column per platform.

    **Digital Footprint** — a club's whole online presence in one view:
    YouTube alongside every other tracked platform, sized by reach.

    ---

    ### 🗂️ Others — Players, Other Clubs & Women

    These are tracked but deliberately excluded from every league/club
    view, leaderboard, and aggregate above. They live on their own
    pages so they don't compete with the big-5 clubs in rankings.

    **Players** — top football players' personal YouTube channels
    (Cristiano, Messi, Neymar, Mbappé, Haaland…). Standalone
    leaderboard with subs, views, posting activity, and career status.

    **Other Clubs** — top global clubs *outside* the big-5 European
    leagues (Brazil, Argentina, Turkey, Portugal, Netherlands,
    Scotland, Saudi Arabia, MLS, Liga MX). Standalone leaderboard +
    posting activity.

    **Women** — top women's football clubs (Barça Femení, Lyon Féminin,
    Chelsea Women, Arsenal Women…). Standalone leaderboard, isolated
    from the men's-team views and aggregates.
    """).format(_season_label=_csl_home())
)

st.info(
    """
    **Invite-only pages** (require an invite to access):

    **Compare** — pick 2–5 clubs from any league and compare them side by
    side: pie charts, stats table, and top videos head-to-head.

    **AI Analysis** — Claude-powered insights for the selected club:
    strengths, content strategy, and recommendations.

    **Ask Data** — ask natural-language questions about the data and get
    instant answers powered by AI.
    """,
    icon="⭐",
)

st.markdown("---")

# ── About / Promo ────────────────────────────────────────────
st.subheader("ℹ️ About")
st.markdown(
    """
    **YouTube Football Tracker** is built and maintained by **Carlo De Marchis** — a guy with a scarf
    who tracks football YouTube so you don't have to.

    **Becoming a Better B2B Tech Vendor in Sports and Media** — my new online course and book.
    """
)

_about_cols = st.columns(5)
_about_cols[0].link_button("📬 Newsletter", "https://www.linkedin.com/newsletters/a-guy-with-a-scarf-6998145822441775104/", width="stretch")
_about_cols[1].link_button("💼 LinkedIn", "https://linkedin.com/in/carlodemarchis", width="stretch")
_about_cols[2].link_button("✍️ Substack", "https://aguywithascarf.substack.com", width="stretch")
_about_cols[3].link_button("📖 Course", "https://a-guy-with-a-scarf.mykajabi.com/course", width="stretch")
_about_cols[4].link_button("📕 Book on Amazon", "https://amzn.eu/d/09cuCSkB", width="stretch")

st.markdown("---")
st.markdown("#### 🔍 Read the fine print")
st.caption(
    "**Public info only.** Everything shown here — subscribers, views, videos, titles, thumbnails — "
    "is publicly available on YouTube. No private data, no scraping, no login tricks. "
    "Just the YouTube Data API v3, the way Google intended.\n\n"
    "**Not affiliated.** This is an independent, personal project — not "
    "affiliated with, endorsed by, or sponsored by YouTube, Google, FIFA, "
    "or any club, league or federation shown here. Club / league / "
    "competition names, crests and video thumbnails belong to their "
    "respective owners; thumbnails are served by YouTube and link back to "
    "the original videos.\n\n"
    "**Your sign-in.** Signing in stores exactly one thing about you: your "
    "email address (via Google or a one-time email code), used only to "
    "authenticate you and remember your access level. No passwords are "
    "stored, nothing is sold, there are no ads and no third-party tracking. "
    "Sign out any time from the sidebar — want your data removed? Just ask "
    "and it's gone.\n\n"
    "**Limitations.** YouTube rounds subscriber counts down (so 1.23M might actually be 1,234,567). "
    "View counts update with a delay. Videos deleted or made private disappear from our stats. "
    "Themes and key figures are AI-classified — occasionally wrong, usually close. "
    "\"Season\" views cover videos *published* in-season; views on older videos during the season aren't counted.\n\n"
    "**When we fetch data.** New video discovery runs **hourly** via RSS feeds (fast, lightweight). "
    "Full stats refresh runs **daily** — subscriber counts, view counts, and snapshots for ranks and deltas. "
    "A **weekly** sweep recomputes top-100 aggregates and back-fills any missed videos. "
    "**Players**, **Other Clubs**, **Women's clubs** and **WC2026** each have "
    "their own dedicated daily crons, so each of those features can be paused "
    "or killed independently of the main pipeline.\n\n"
    "**Players, Other Clubs and Women's clubs are isolated.** They live on their "
    "own pages and are deliberately excluded from every league/club view, leaderboard, and "
    "aggregate — they don't compete with the big-5 clubs in rankings, don't appear in "
    "All-Time Top, Latest Videos, Season Top, or Compare. Treat them as separate lenses.\n\n"
    "**Work in progress.** This is a research project. Expect it to evolve constantly — "
    "new metrics, new leagues, new views, occasional bugs, and the odd late-night experiment."
)

# YouTube API latency callout — same visual treatment as on the
# home page (so users who land here from a chart anomaly see the
# explanation in the same shape).
st.markdown(
    '<div style="background:#1a1c24;border:1px solid #2a2c34;'
    'border-left:3px solid #FAFAFA;border-radius:4px;'
    'padding:10px 14px;margin:12px 0 0 0;'
    'font-size:14px;line-height:1.5;color:#FAFAFA">'
    '⏱️ <b>YouTube API latency.</b> The public view-count aggregate on '
    'each channel updates on Google\'s schedule — sometimes a few hours, '
    'occasionally 24–36 hours after the views actually happened. If you '
    'see a day reading <b>~0 Δ views</b> while uploads kept flowing, that\'s '
    'almost always YouTube\'s counter sitting frozen rather than a real '
    'collapse in engagement. Our pipeline writes whatever Google returns '
    'at fetch time, then retrofits the missed growth as soon as their API '
    'reflects it. The lifetime totals are always correct; the daily '
    'attribution can shift by a day or two.'
    '</div>',
    unsafe_allow_html=True,
)

st.markdown("---")
st.page_link("views/0_Home.py", label="← Back to Home")
