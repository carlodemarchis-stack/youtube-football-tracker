from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num, yt_popup_js
from src.growth import group_by_channel, delta
from src.filters import get_global_color_map, get_global_color_map_dual
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG

load_dotenv()

# ── Public feed view (accessed via ?view=feed) ───────────────
if st.session_state.get("_feed_mode"):
    from src.filters import get_global_filter, get_global_channels, get_league_for_channel

    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error("Backend not configured.")
        st.stop()

    db = Database(SUPABASE_URL, SUPABASE_KEY)
    all_channels = get_global_channels() or db.get_all_channels()
    color_map = get_global_color_map() or {}
    dual = get_global_color_map_dual() or {}

    # Apply global filter (same as Latest Videos)
    g_league, g_club = get_global_filter()
    if g_club:
        ch_ids = [g_club["id"]]
    elif g_league:
        ch_ids = [c["id"] for c in all_channels if get_league_for_channel(c) == g_league]
    else:
        ch_ids = None

    st.title("Feed")
    fmt_options = ["All", "Long", "Shorts", "Live"]
    fmt_pick = st.segmented_control("Format", fmt_options, default="All")

    latest_raw = db.get_recent_videos(limit=200, channel_ids=ch_ids)

    # Filter out scheduled
    _now_utc = datetime.now(timezone.utc)
    def _is_scheduled(v):
        _ast = v.get("actual_start_time") or ""
        if _ast:
            try:
                return datetime.fromisoformat(_ast.replace("Z", "+00:00")) > _now_utc
            except Exception:
                return False
        if (v.get("format") or "").lower() == "live" and (v.get("duration_seconds") or 0) == 0:
            return True
        return False

    latest_raw = [v for v in latest_raw if not _is_scheduled(v)]

    if fmt_pick and fmt_pick != "All":
        _fmt_map = {"Long": "long", "Shorts": "short", "Live": "live"}
        _target = _fmt_map[fmt_pick]
        latest = [v for v in latest_raw
                  if ((v.get("format") or "").lower() if (v.get("format") or "").lower() in ("long", "short", "live")
                      else ("long" if (v.get("duration_seconds") or 0) >= 60 else "short")) == _target][:100]
    else:
        latest = latest_raw[:100]

    if not latest:
        st.caption("No videos found.")
        st.stop()

    if g_club:
        _scope = f"**{g_club['name']}**"
    elif g_league:
        _scope = f"**{g_league}**"
    else:
        _scope = "all leagues"
    st.caption(f"**{len(latest)}** latest videos · {_scope}")

    ch_by_id = {c["id"]: c for c in all_channels}
    _COUNTRY_FLAG = {"IT": "\U0001F1EE\U0001F1F9", "EN": "\U0001F1EC\U0001F1E7",
                     "ES": "\U0001F1EA\U0001F1F8", "DE": "\U0001F1E9\U0001F1EA",
                     "FR": "\U0001F1EB\U0001F1F7", "US": "\U0001F1FA\U0001F1F8",
                     "GB": "\U0001F1EC\U0001F1E7"}

    cards_html = ""
    for v in latest:
        yt_id = v.get("youtube_video_id", "") or ""
        url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "#"
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
        ch = ch_by_id.get(v.get("channel_id")) or {}
        ch_name = v.get("channel_name", "") or ch.get("name", "")
        flag = _COUNTRY_FLAG.get(ch.get("country", ""), "")
        thumb = v.get("thumbnail_url") or ""
        dur = v.get("duration_seconds") or 0
        dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else ""
        c1, c2 = dual.get(ch_name, (color_map.get(ch_name, "#636EFA"), "#FFFFFF"))

        cards_html += f"""<a href="{url}" target="_blank" rel="noopener" class="card">
          <div class="card-thumb">
            <img src="{thumb}" alt="" loading="lazy">
            {'<span class="card-dur">' + dur_s + '</span>' if dur_s else ''}
          </div>
          <div class="card-info">
            <span class="card-flag">{flag}</span>
            <span class="card-dot" style="background:{c1};box-shadow:2px 0 0 {c2}"></span>
            <span class="card-club">{ch_name}</span>
          </div>
          <div class="card-title" title="{title}">{title}</div>
        </a>"""

    components.html(f"""
    <style>
      * {{ box-sizing:border-box; }}
      body {{ margin:0; font-family:"Source Sans Pro",sans-serif; }}
      .mosaic {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; padding:4px; }}
      .card {{ display:block; text-decoration:none; color:#FAFAFA; border-radius:8px;
               background:#1a1c24; overflow:hidden; transition:transform 0.15s; }}
      .card:hover {{ transform:scale(1.03); background:#22252e; }}
      .card-thumb {{ position:relative; width:100%; aspect-ratio:16/9; overflow:hidden; }}
      .card-thumb img {{ width:100%; height:100%; object-fit:cover; display:block; }}
      .card-dur {{ position:absolute; bottom:4px; right:4px; background:rgba(0,0,0,0.8);
                   color:#fff; font-size:11px; padding:1px 5px; border-radius:3px; font-weight:600; }}
      .card-info {{ padding:6px 8px 2px; display:flex; align-items:center; gap:4px; }}
      .card-flag {{ font-size:14px; }}
      .card-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%;
                   border:1px solid rgba(255,255,255,0.3); flex-shrink:0; }}
      .card-club {{ font-size:11px; color:#999; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .card-title {{ padding:2px 8px 8px; font-size:12px; line-height:1.3;
                     display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
                     overflow:hidden; color:#ddd; }}
    </style>
    <div class="mosaic">{cards_html}</div>
    {yt_popup_js()}
    """, height=max(280, (len(latest) // 5 + 1) * 195), scrolling=True)
    st.stop()

# ── Normal Home page ─────────────────────────────────────────
st.title("YouTube Football Tracker")

# ── Leagues covered ─────────────────────────────────────────────
try:
    _chs = st.session_state.get("_global_channels") or []
    if not _chs:
        SUPABASE_URL = os.getenv("SUPABASE_URL", "")
        SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
        if SUPABASE_URL and SUPABASE_KEY:
            _chs = Database(SUPABASE_URL, SUPABASE_KEY).get_all_channels()
    _league_counts: dict[str, int] = {}
    _league_has_channel: dict[str, bool] = {}
    for _c in _chs:
        _lg = COUNTRY_TO_LEAGUE.get((_c.get("country") or "").upper(), _c.get("country") or "—")
        if _c.get("entity_type") == "League":
            _league_has_channel[_lg] = True
            continue
        _league_counts[_lg] = _league_counts.get(_lg, 0) + 1
    if _league_counts:
        _parts = [
            f"{LEAGUE_FLAG.get(lg, '')} **{lg}** ({n}{'+1' if _league_has_channel.get(lg) else ''})"
            for lg, n in sorted(_league_counts.items(), key=lambda x: -x[1])
        ]
        st.caption(f"Covering {len(_league_counts)} leagues: " + " · ".join(_parts))
except Exception:
    pass

# ── Biggest gainers this week (snapshot-driven) ─────────────────
try:
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    if SUPABASE_URL and SUPABASE_KEY:
        _db = Database(SUPABASE_URL, SUPABASE_KEY)
        _channels = _db.get_all_channels()
        _ch_by_id = {c["id"]: c for c in _channels}
        _since = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        _snaps = _db.get_all_snapshots(since_date=_since)
        _by_ch = group_by_channel(_snaps)

        _gainers = []
        for cid, s in _by_ch.items():
            ch = _ch_by_id.get(cid)
            if not ch or ch.get("entity_type") == "League" or len(s) < 2:
                continue
            d7 = delta(s, "subscriber_count", 7)
            if d7 is None:
                continue
            _gainers.append({"name": ch["name"], "subs": s[-1].get("subscriber_count", 0) or 0, "d7": d7, "handle": ch.get("handle", "")})

        if _gainers:
            _gainers.sort(key=lambda g: g["d7"], reverse=True)
            top5 = _gainers[:5]
            color_map = get_global_color_map()
            dual = get_global_color_map_dual()
            st.subheader("🔥 Biggest gainers this week")
            rows = ""
            for i, g in enumerate(top5, 1):
                c1, c2 = dual.get(g["name"], (color_map.get(g["name"], "#636EFA"), "#FFFFFF"))
                dot = f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative"><span style="display:block;width:7px;height:7px;border-radius:50%;background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>'
                col = "#00CC96" if g["d7"] > 0 else ("#EF553B" if g["d7"] < 0 else "#888")
                sgn = "+" if g["d7"] >= 0 else ""
                rows += f"""<tr>
                    <td style="padding:6px 12px;color:#888">{i}</td>
                    <td style="padding:6px 12px">{dot}</td>
                    <td style="padding:6px 12px">{g['name']}</td>
                    <td style="padding:6px 12px;text-align:right">{fmt_num(g['subs'])}</td>
                    <td style="padding:6px 12px;text-align:right;color:{col}">{sgn}{fmt_num(g['d7'])}</td>
                </tr>"""
            components.html(f"""
            <style>
              .home-g {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                         font-family:"Source Sans Pro",sans-serif; }}
              .home-g th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
              .home-g td {{ border-bottom:1px solid #262730; }}
            </style>
            <table class="home-g"><thead><tr>
              <th>#</th><th></th><th>Club</th>
              <th style="text-align:right">Subscribers</th>
              <th style="text-align:right">Δ Subs 7d</th>
            </tr></thead><tbody>{rows}</tbody></table>
            """, height=len(top5) * 37 + 80, scrolling=False)
except Exception:
    pass  # Snapshot data may not exist yet; degrade silently
st.markdown(
    """
    Track YouTube performance for 100+ football clubs across Europe's top leagues.

    ### Global filter
    At the top of every page you'll find a cascading filter that sets the zoom level
    for the whole site. Your selection persists as you navigate.

    - **All Leagues** — site-wide view with a secondary scope selector:
      *Overall* (aggregated by league), *Leagues only* (league channels),
      or *All clubs* (every club across leagues)
    - **One League** — narrows to a single competition; pick *All Clubs*
      or *All Clubs + League channel*
    - **One Club** — focuses every page on a single channel

    ---

    ### Pages (require login)

    **Daily Recap** — what happened yesterday: view and subscriber
    gains, leaderboards, most watched videos, new uploads, and 14-day
    trend charts. Adapts from league-level summaries down to single-club detail.

    **Latest Videos** — the most recently published videos across all
    tracked channels. Sortable by views, likes, comments, duration, and
    age. Live-now videos appear in a banner at the top.

    **Channels** — league and club rankings: subscribers, views, video
    counts (long vs shorts), views/sub, views/video. Sortable tables and
    bar charts at every zoom level.

    **Season 25/26** — current-season stats (videos published since
    Aug 2025): views, videos, views/video, engagement rate — split by
    long-form, Shorts, and Live. Monthly output charts and category
    breakdowns at the club level.

    **Top Videos** — the 100 most-viewed videos for your selection with
    rank-by-views chart, rank-vs-year scatter, year distribution, theme
    pie, and a sortable video list.
    """
)

st.info(
    """
    **Premium pages** (require premium paid account):

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
st.subheader("About")
st.markdown(
    """
    **YouTube Football Tracker** is built and maintained by **Carlo De Marchis** — a guy with a scarf
    who tracks football YouTube so you don't have to.

    **Becoming a Better B2B Tech Vendor in Sports and Media** — my new online course and book.
    """
)

_about_cols = st.columns(5)
_about_cols[0].link_button("📬 Newsletter", "https://www.linkedin.com/newsletters/a-guy-with-a-scarf-6998145822441775104/", use_container_width=True)
_about_cols[1].link_button("💼 LinkedIn", "https://linkedin.com/in/carlodemarchis", use_container_width=True)
_about_cols[2].link_button("✍️ Substack", "https://aguywithascarf.substack.com", use_container_width=True)
_about_cols[3].link_button("📖 Course", "https://a-guy-with-a-scarf.mykajabi.com/course", use_container_width=True)
_about_cols[4].link_button("📕 Book on Amazon", "https://amzn.eu/d/09cuCSkB", use_container_width=True)

st.markdown("---")
st.caption("Data sourced from YouTube Data API v3. Stats refresh automatically via hourly, daily, and weekly jobs.")
