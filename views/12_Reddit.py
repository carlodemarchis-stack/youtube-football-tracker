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

import os
import streamlit.components.v1 as components

from src.auth import require_login
from src.analytics import fmt_num, fmt_date
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
)
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG
from src.database import Database
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
last_fetched = max((s.get("last_fetched") or "" for s in subs), default="")

c1, c2, c3 = st.columns(3)
c1.metric("Subreddits tracked", len(subs))
c2.metric("Total subscribers", fmt_num(total_subs))
c3.metric("Last update", fmt_date(last_fetched) if last_fetched else "—")

# ── Routing: detail view if ?subreddit=... else overview ────
pick = st.query_params.get("subreddit", "")

if not pick:
    # ── Overview table ──────────────────────────────────────
    st.markdown("---")

    all_channels = get_global_channels()
    if not all_channels:
        try:
            _db = Database(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))
            all_channels = _db.get_all_channels()
        except Exception:
            all_channels = []
    ch_by_id = {c["id"]: c for c in all_channels}
    color_map = get_global_color_map() or {}
    dual = get_global_color_map_dual() or {}

    def _connected_html(s_row: dict) -> str:
        cid = s_row.get("channel_id")
        if not cid or cid not in ch_by_id:
            return "<span style='color:#666'>—</span>"
        ch = ch_by_id[cid]
        lg = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper(), ch.get("country") or "")
        flag = LEAGUE_FLAG.get(lg, "")
        label = ch.get("name", "?")
        if ch.get("entity_type") == "League":
            label = f"{label} (league)"
        c1, c2 = dual.get(ch.get("name", ""), (color_map.get(ch.get("name", ""), "#636EFA"), "#FFFFFF"))
        dot = (
            f"<span style='display:inline-block;width:10px;height:10px;border-radius:50%;"
            f"background:{c1};box-shadow:3px 0 0 {c2};border:1px solid rgba(255,255,255,0.25);"
            f"margin-right:10px;vertical-align:middle'></span>"
        )
        # Link navigates the PARENT Streamlit page (target=_top escapes the iframe)
        href = f"?subreddit={s_row['subreddit']}"
        return (
            f"{dot}<span>{flag} "
            f"<a href='{href}' target='_top' style='color:#FAFAFA;text-decoration:none'>{label}</a>"
            f"</span>"
        )

    # Precompute top posts per subreddit
    enriched = []
    for s in subs:
        top_post = None
        try:
            top_post = get_top_post_today(s["id"])
        except Exception:
            pass
        enriched.append((s, top_post))
    enriched.sort(key=lambda pair: int(pair[0].get("subscribers") or 0), reverse=True)

    rows_html = ""
    for s, top_post in enriched:
        name = s["subreddit"]
        subs_n = int(s.get("subscribers") or 0)
        title = (top_post or {}).get("title", "—") or "—"
        title_safe = title.replace("<", "&lt;").replace(">", "&gt;")
        top_link = (top_post or {}).get("permalink") or ""
        title_cell = (
            f"<a href='{top_link}' target='_blank' style='color:#FAFAFA;text-decoration:none'>{title_safe}</a>"
            if top_link else title_safe
        )
        score = int((top_post or {}).get("score") or 0)
        ncom = int((top_post or {}).get("num_comments") or 0)
        rows_html += f"""<tr>
            <td style="padding:8px 12px">{_connected_html(s)}</td>
            <td style="padding:8px 12px">
              <a href='https://reddit.com/r/{name}' target='_blank'
                 style='color:#FF4500;text-decoration:none'>r/{name}</a>
            </td>
            <td style="padding:8px 12px;text-align:right;font-weight:600">{fmt_num(subs_n)}</td>
            <td style="padding:8px 12px">{title_cell}</td>
            <td style="padding:8px 12px;text-align:right">▲ {fmt_num(score)}</td>
            <td style="padding:8px 12px;text-align:right">💬 {fmt_num(ncom)}</td>
        </tr>"""

    components.html(f"""
    <style>
      * {{ box-sizing:border-box; }}
      body {{ margin:0; font-family:"Source Sans Pro",sans-serif; color:#FAFAFA; }}
      .rdt {{ width:100%; border-collapse:collapse; font-size:14px; }}
      .rdt th {{ padding:8px 12px; border-bottom:2px solid #444; text-align:left;
                 color:#AAA; font-weight:600; font-size:13px; }}
      .rdt td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
      .rdt tr:hover td {{ background:#1a1c24; }}
      a:hover {{ text-decoration:underline !important; }}
    </style>
    <table class="rdt">
      <thead><tr>
        <th>Connected to</th><th>Subreddit</th>
        <th style="text-align:right">Subscribers</th>
        <th>Top post (24h)</th>
        <th style="text-align:right">Score</th>
        <th style="text-align:right">Comments</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    """, height=(len(enriched) + 2) * 56 + 20, scrolling=False)
    st.stop()

# ── Per-subreddit detail ────────────────────────────────────
sub = next((s for s in subs if s["subreddit"] == pick), None)
if not sub:
    st.error(f"Subreddit '{pick}' not found.")
    st.link_button("← Back to overview", "?")
    st.stop()

st.link_button("← Back to overview", "?")

sub_id = sub["id"]

# Header KPIs
k1, k2, k3 = st.columns(3)
k1.metric("Subscribers", fmt_num(sub.get("subscribers") or 0))
k2.metric("Official?", "✓" if sub.get("is_official") else "fan-run")
k3.metric("Last fetched", fmt_date(sub.get("last_fetched")) if sub.get("last_fetched") else "—")

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
