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
from src.dot import dual_dot, channel_badge

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
    # Players are isolated — never show in the public feed
    all_channels = [c for c in all_channels if c.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")]
    color_map = get_global_color_map() or {}
    dual = get_global_color_map_dual() or {}

    # Apply global filter (same as Latest Videos)
    g_league, g_club = get_global_filter()
    if g_club:
        ch_ids = [g_club["id"]]
    elif g_league:
        ch_ids = [c["id"] for c in all_channels if get_league_for_channel(c) == g_league]
    else:
        # all_channels has Players + Federations already filtered out above
        ch_ids = [c["id"] for c in all_channels]

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
    # EN/GB → England subdivision flag (Premier League is English)
    _ENG_FLAG = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
    _COUNTRY_FLAG = {"IT": "\U0001F1EE\U0001F1F9", "EN": _ENG_FLAG,
                     "ES": "\U0001F1EA\U0001F1F8", "DE": "\U0001F1E9\U0001F1EA",
                     "FR": "\U0001F1EB\U0001F1F7", "US": "\U0001F1FA\U0001F1F8",
                     "GB": _ENG_FLAG}

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
            {channel_badge(ch, color_map, dual, 10)}
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
        if _c.get("entity_type") in ("Player", "Federation", "OtherClub", "WomenClub"):
            continue  # Players + Federations live on their own pages
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

    # Also-tracking line: Players + Federations + Other Clubs + Women (isolated)
    _players = [c for c in _chs if c.get("entity_type") == "Player"]
    _feds = [c for c in _chs if c.get("entity_type") == "Federation"]
    _others = [c for c in _chs if c.get("entity_type") == "OtherClub"]
    _women = [c for c in _chs if c.get("entity_type") == "WomenClub"]
    if _players or _feds or _others or _women:
        from src.analytics import fmt_num as _fmt
        _bits = []
        if _players:
            _ps = sum(int(c.get("subscriber_count") or 0) for c in _players)
            _bits.append(f"⚽ **{len(_players)} players** ({_fmt(_ps)} subs)")
        if _feds:
            _fs = sum(int(c.get("subscriber_count") or 0) for c in _feds)
            _bits.append(f"🏛️ **{len(_feds)} federations** ({_fmt(_fs)} subs)")
        if _others:
            _os = sum(int(c.get("subscriber_count") or 0) for c in _others)
            _bits.append(f"🌍 **{len(_others)} other clubs** ({_fmt(_os)} subs)")
        if _women:
            _ws = sum(int(c.get("subscriber_count") or 0) for c in _women)
            _bits.append(f"👩 **{len(_women)} women's clubs** ({_fmt(_ws)} subs)")
        st.caption("Also tracking: " + "  ·  ".join(_bits)
                   + " — see the dedicated pages.")
except Exception:
    pass

_link = "color:#FAFAFA;text-decoration:underline;text-decoration-color:#555"

st.markdown(
    f"""<div style="line-height:1.6">
    Hi, I'm <a href="https://linkedin.com/in/carlodemarchis" target="_blank" style="{_link}"><b>Carlo De Marchis</b></a>
    — <a href="https://www.linkedin.com/newsletters/a-guy-with-a-scarf-6998145822441775104/" target="_blank" style="{_link}"><i>A Guy With A Scarf</i></a>
    — and this is a small side project born from my passion for data, stats and visualisation.
    A starting point to explore how the chain from <b>information → insights → action</b> might
    evolve today, with AI as the substrate that augments each step. It'll keep changing based
    on what I learn and what you tell me.
    </div>""",
    unsafe_allow_html=True,
)

st.markdown(
    f"""<div style="line-height:1.6;margin-top:12px">
    A window into how football clubs use YouTube — built for <b>club social teams</b> looking
    to benchmark and learn from peers, and for <b>curious fans</b> who want to see what their
    team (and everyone else) is actually doing.
    <br><br>
    The idea came from working with
    <a href="https://linkedin.com/in/paolamarinone" target="_blank" style="{_link}">Paola Marinone</a>
    and <a href="https://linkedin.com/in/benguatamer" target="_blank" style="{_link}">Bengu Atamer</a>
    at <a href="https://www.buzzmyvideos.com/" target="_blank" style="{_link}"><b>BuzzMyVideos</b></a> — they pulled me deep into the mechanics of YouTube and the creators
    who live on it. This tracker applies that same lens to football.
    </div>""",
    unsafe_allow_html=True,
)

# ── Daily AI note (yesterday's commentary, computed by daily_refresh) ──
try:
    from zoneinfo import ZoneInfo as _ZI
    from datetime import timedelta as _td
    from src import dashboard_cache as _dc
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    if SUPABASE_URL and SUPABASE_KEY:
        _db_note = Database(SUPABASE_URL, SUPABASE_KEY)
        _y = (datetime.now(_ZI("Europe/Rome")).date() - _td(days=1))
        _note_row = _dc.read(_db_note, "daily_note", _y.isoformat())
        if _note_row and _note_row.get("payload"):
            _p = _note_row["payload"]
            # Prefer pre-decorated HTML (with badge injection); fall back
            # to raw text for older cache rows that pre-date decoration.
            _note_body = (_p.get("html") or _p.get("text") or "").strip()
            if _note_body:
                st.markdown(
                    f'<div style="font-style:italic;color:#cccccc;line-height:1.7;'
                    f'border-left:3px solid #636EFA;padding:8px 14px;margin:18px 0;'
                    f'white-space:pre-line">'
                    f'<div style="font-size:11px;color:#888;text-transform:uppercase;'
                    f'letter-spacing:0.5px;margin-bottom:6px;font-style:normal;'
                    f'white-space:normal">'
                    f'Yesterday in football YouTube · {_y.strftime("%a %b %d")}</div>'
                    f'{_note_body}</div>',
                    unsafe_allow_html=True,
                )
except Exception:
    pass  # never block the page on a missing/broken note

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

        # Top-5 European leagues only — clubs + the 5 league channels.
        _TOP5 = {"Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"}
        _gainers = []
        for cid, s in _by_ch.items():
            ch = _ch_by_id.get(cid)
            if not ch or len(s) < 2:
                continue
            if ch.get("entity_type") not in ("Club", "League"):
                continue
            _lg = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper())
            if _lg not in _TOP5:
                continue
            # YouTube rounds subscriber_count to the nearest 10K — 7d subs delta
            # is too coarse (everyone shows ±100K). Rank by Δ total_views instead.
            d7_views = delta(s, "total_views", 7)
            if d7_views is None:
                continue
            _gainers.append({
                "name": ch["name"],
                "views": int(s[-1].get("total_views", 0) or 0),
                "subs": int(s[-1].get("subscriber_count", 0) or 0),
                "d7_views": int(d7_views),
                "handle": ch.get("handle", ""),
                "_ch": ch,
            })

        if _gainers:
            _gainers.sort(key=lambda g: g["d7_views"], reverse=True)
            top5 = _gainers[:5]
            color_map = get_global_color_map()
            dual = get_global_color_map_dual()
            st.subheader("🔥 Biggest gainers this week")
            rows = ""
            for i, g in enumerate(top5, 1):
                dot = channel_badge(g.get("_ch") or {}, color_map, dual, 14)
                col = "#00CC96" if g["d7_views"] > 0 else ("#EF553B" if g["d7_views"] < 0 else "#888")
                sgn = "+" if g["d7_views"] >= 0 else ""
                rows += f"""<tr>
                    <td style="padding:6px 12px;color:#888">{i}</td>
                    <td style="padding:6px 12px">{dot}</td>
                    <td style="padding:6px 12px">{g['name']}</td>
                    <td style="padding:6px 12px;text-align:right">{fmt_num(g['views'])}</td>
                    <td style="padding:6px 12px;text-align:right;color:{col}">{sgn}{fmt_num(g['d7_views'])}</td>
                </tr>"""
            components.html(f"""
            <style>
              .home-g {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                         font-family:"Source Sans Pro",sans-serif; }}
              .home-g th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
              .home-g td {{ border-bottom:1px solid #262730; }}
            </style>
            <table class="home-g"><thead><tr>
              <th>#</th><th></th><th>Club / League</th>
              <th style="text-align:right">Total Views</th>
              <th style="text-align:right">Δ Views 7d</th>
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

    **Other Social** — beyond YouTube: each club's footprint across
    Instagram, X, Facebook, TikTok, Threads, LinkedIn (and more). Badge
    grid linking to every account, plus a sortable follower-count
    leaderboard with one column per platform.

    **Players** — top football players' personal YouTube channels (Cristiano,
    Messi, Neymar, Mbappé, Haaland…). Standalone leaderboard with subs,
    views, posting activity, and career status. Isolated from clubs/leagues.

    **Federations** — FIFA, UEFA, the five confederations and major national
    associations. Same shape as Players: leaderboard, subs/year, posting
    activity, season video mix.

    **Other Clubs** — top global clubs *outside* the big-5 European leagues
    (Brazil, Argentina, Turkey, Portugal, Netherlands, Scotland, Saudi
    Arabia, MLS, Liga MX). Standalone leaderboard + posting activity.

    **Women** — top women's football clubs (Barça Femení, Lyon Féminin,
    Chelsea Women, Arsenal Women…). Standalone leaderboard, isolated
    from the men's-team views and aggregates.
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
st.markdown("#### 🔍 Read the fine print")
st.caption(
    "**Public info only.** Everything shown here — subscribers, views, videos, titles, thumbnails — "
    "is publicly available on YouTube. No private data, no scraping, no login tricks. "
    "Just the YouTube Data API v3, the way Google intended.\n\n"
    "**Limitations.** YouTube rounds subscriber counts down (so 1.23M might actually be 1,234,567). "
    "View counts update with a delay. Videos deleted or made private disappear from our stats. "
    "Themes and key figures are AI-classified — occasionally wrong, usually close. "
    "\"Season\" views cover videos *published* in-season; views on older videos during the season aren't counted.\n\n"
    "**When we fetch data.** New video discovery runs **hourly** via RSS feeds (fast, lightweight). "
    "Full stats refresh runs **daily** — subscriber counts, view counts, and snapshots for ranks and deltas. "
    "A **weekly** sweep recomputes top-100 aggregates and back-fills any missed videos. "
    "**Players**, **Federations**, **Other Clubs** and **Women's clubs** each have their own "
    "dedicated daily crons (running at ~01:00, ~01:30, ~01:45 and ~02:00 CET) so those "
    "features can be paused or killed independently of the main pipeline.\n\n"
    "**Players, Federations, Other Clubs and Women's clubs are isolated.** They live on their "
    "own pages and are deliberately excluded from every league/club view, leaderboard, and "
    "aggregate — they don't compete with the big-5 clubs in rankings, don't appear in Top "
    "Videos, Latest Videos, or Compare. Treat them as separate lenses.\n\n"
    "**Work in progress.** This is a research project. Expect it to evolve constantly — "
    "new metrics, new leagues, new views, occasional bugs, and the odd late-night experiment."
)
