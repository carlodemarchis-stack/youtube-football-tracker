"""Reddit page — fan-pulse view, isolated from the YouTube side.

V1: list tracked subreddits, show subscribers + recent activity + top post.
Deleting this file, the SQL tables, and the app.py entry cleanly removes
the feature with zero impact on anything else.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

from src.auth import require_login
from src.analytics import fmt_num, fmt_date
from src.filters import get_global_channels
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG
from src.database import Database
import os
from src.reddit_db import (
    get_tracked_subreddits, get_subreddit_by_name,
    get_recent_posts, get_top_post_today, get_snapshots,
)

load_dotenv()
require_login()

st.title("Reddit")
st.caption(
    "Fan pulse from each club's main subreddit — updated hourly. "
    "Complements YouTube (what the club posts) with what the fans actually talk about."
)

try:
    subs = get_tracked_subreddits()
except Exception as e:
    st.error(f"Could not load subreddits: {e}")
    st.info(
        "First time setup:\n"
        "1. Run the SQL migration in Supabase (see `db/reddit_schema.sql`)\n"
        "2. Insert the subreddit seed data\n"
        "3. Set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET / REDDIT_USER_AGENT\n"
        "4. Run `python scripts/hourly_reddit.py` once to populate data"
    )
    st.stop()

if not subs:
    st.warning("No subreddits configured yet. Paste the seed SQL to add some.")
    st.stop()

# ── Summary ─────────────────────────────────────────────────
total_subs = sum(int(s.get("subscribers") or 0) for s in subs)
total_active = sum(int(s.get("active_users") or 0) for s in subs)
last_fetched = max((s.get("last_fetched") or "" for s in subs), default="")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Subreddits tracked", len(subs))
c2.metric("Total subscribers", fmt_num(total_subs))
c3.metric("Active right now", fmt_num(total_active))
c4.metric("Last update", fmt_date(last_fetched) if last_fetched else "—")

# ── Choose a subreddit ──────────────────────────────────────
st.markdown("---")

sub_names = [s["subreddit"] for s in subs]
pick_options = ["— Overview —"] + sub_names
pick = st.selectbox("Subreddit", pick_options, index=0)

if pick == "— Overview —":
    # Build channel_id → (label, entity_type) map so we can show each
    # subreddit's connected club/league with flag.
    all_channels = get_global_channels()
    if not all_channels:
        try:
            _db = Database(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
            all_channels = _db.get_all_channels()
        except Exception:
            all_channels = []
    ch_by_id = {c["id"]: c for c in all_channels}

    def _connected_label(channel_id: str | None) -> str:
        if not channel_id or channel_id not in ch_by_id:
            return "—"
        ch = ch_by_id[channel_id]
        lg = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper(), ch.get("country") or "")
        flag = LEAGUE_FLAG.get(lg, "")
        label = ch.get("name", "?")
        if ch.get("entity_type") == "League":
            return f"{flag} {label} (league)".strip()
        return f"{flag} {label}".strip()

    rows = []
    for s in subs:
        top_post = None
        try:
            top_post = get_top_post_today(s["id"])
        except Exception:
            pass
        name = s["subreddit"]
        rows.append({
            "Connected to": _connected_label(s.get("channel_id")),
            "Subreddit": f"https://reddit.com/r/{name}",
            "Subscribers": int(s.get("subscribers") or 0),
            "Top post (24h)": (top_post or {}).get("title", "—"),
            "Top score": int((top_post or {}).get("score") or 0),
            "Top comments": int((top_post or {}).get("num_comments") or 0),
        })
    df = pd.DataFrame(rows).sort_values("Subscribers", ascending=False)
    df["Subscribers"] = df["Subscribers"].apply(fmt_num)
    df["Top score"] = df["Top score"].apply(fmt_num)
    df["Top comments"] = df["Top comments"].apply(fmt_num)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=(len(df) + 1) * 35 + 3,   # full height, no inner scrollbar
        column_order=["Connected to", "Subreddit", "Subscribers",
                      "Top post (24h)", "Top score", "Top comments"],
        column_config={
            "Subreddit": st.column_config.LinkColumn(
                "Subreddit",
                display_text=r"https?://(?:www\.)?reddit\.com/(r/[^/]+)",
            ),
        },
    )
    st.stop()

# ── Per-subreddit detail ────────────────────────────────────
sub = next((s for s in subs if s["subreddit"] == pick), None)
if not sub:
    st.error("Subreddit not found.")
    st.stop()

sub_id = sub["id"]

# Header KPIs
k1, k2, k3, k4 = st.columns(4)
k1.metric("Subscribers", fmt_num(sub.get("subscribers") or 0))
k2.metric("Active", fmt_num(sub.get("active_users") or 0))
k3.metric("Official?", "✓" if sub.get("is_official") else "fan-run")
k4.metric("Last fetched", fmt_date(sub.get("last_fetched")) if sub.get("last_fetched") else "—")

if sub.get("description"):
    st.caption(sub["description"])

# Subscriber growth chart
try:
    snaps = get_snapshots(sub_id, since_date=(datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat())
except Exception:
    snaps = []

if snaps and len(snaps) >= 2:
    snap_df = pd.DataFrame(snaps)
    st.markdown("### Subscriber growth (30 days)")
    st.line_chart(snap_df.set_index("captured_date")["subscribers"])
    st.markdown("### Activity per day (posts · comments)")
    c1, c2 = st.columns(2)
    with c1:
        st.bar_chart(snap_df.set_index("captured_date")["posts_24h"])
    with c2:
        st.bar_chart(snap_df.set_index("captured_date")["total_comments_24h"])
else:
    st.caption("Growth chart appears once we have 2+ daily snapshots.")

# Recent posts
st.markdown("### Recent posts")
try:
    posts = get_recent_posts(sub_id, limit=25)
except Exception as e:
    st.error(f"Could not load posts: {e}")
    posts = []

if not posts:
    st.info("No posts yet. Wait for the next hourly run.")
    st.stop()

for p in posts:
    created = p.get("created_at") or ""
    when = fmt_date(created) if created else ""
    flair = p.get("flair") or ""
    flair_html = (
        f"<span style='background:#FF450044;color:#FF4500;padding:2px 8px;"
        f"border-radius:3px;font-size:11px;margin-right:8px'>{flair}</span>"
        if flair else ""
    )
    permalink = p.get("permalink") or ""
    title = (p.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
    author = p.get("author") or ""
    score = int(p.get("score") or 0)
    comments = int(p.get("num_comments") or 0)
    ratio = float(p.get("upvote_ratio") or 0.0)

    st.markdown(
        f"""<div style="border-left:3px solid #FF4500;padding:8px 12px;margin:6px 0;background:#1a1c24;border-radius:4px">
          <div style="font-size:12px;color:#888;margin-bottom:4px">
            {flair_html}u/{author} · {when} · <span style="color:#FF4500">▲ {fmt_num(score)}</span> · 💬 {fmt_num(comments)} · {int(ratio*100)}% upvoted
          </div>
          <a href="{permalink}" target="_blank" style="color:#FAFAFA;text-decoration:none;font-size:15px">{title}</a>
        </div>""",
        unsafe_allow_html=True,
    )
