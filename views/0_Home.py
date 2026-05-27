from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
from src import components_compat as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels, get_recent_videos as _cached_recent, read_dashboard_cache as _cached_dc_read
from src.analytics import fmt_num, yt_popup_js
from src.growth import group_by_channel, delta
from src.filters import get_global_color_map, get_global_color_map_dual
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG
from src.dot import dual_dot, channel_badge
from src import theme as _T
from src.auth import is_logged_in

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
    all_channels = get_global_channels() or _cached_channels(db)
    # Players are isolated — never show in the public feed
    all_channels = [c for c in all_channels if c.get("entity_type") not in ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub")]
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

    latest_raw = _cached_recent(db, limit=200, channel_ids=tuple(ch_ids))

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

        _data_fmt_h = (v.get("format") or "").lower()
        if _data_fmt_h not in ("long", "short", "live"):
            _data_fmt_h = "long" if dur >= 60 else "short"
        cards_html += f"""<a href="{url}" target="_blank" rel="noopener" data-fmt="{_data_fmt_h}" class="card">
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

# Tagline — sits right under the title so the page opens with a
# one-line "what is this".
st.markdown(
    "Track YouTube performance for 100+ football clubs across Europe's top "
    "leagues — plus the road to the **FIFA World Cup 2026**."
)

# ── Leagues covered ─────────────────────────────────────────────
try:
    _chs = st.session_state.get("_global_channels") or []
    if not _chs:
        SUPABASE_URL = os.getenv("SUPABASE_URL", "")
        SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
        if SUPABASE_URL and SUPABASE_KEY:
            _chs = _cached_channels(Database(SUPABASE_URL, SUPABASE_KEY))
    _league_counts: dict[str, int] = {}
    _league_has_channel: dict[str, bool] = {}
    for _c in _chs:
        if _c.get("entity_type") in ("Player", "Federation", "GoverningBody",
                                      "OtherClub", "WomenClub"):
            continue  # tangential entities live on their own pages
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
        st.caption(f"Covering {len(_league_counts)} leagues "
                    "(Clubs and League Channels): " + " · ".join(_parts))

    # WC2026 coverage — its own prominent line (dedicated sidebar group,
    # tournament ~1 month out). Counts every channel tagged
    # competitions.wc2026 (teams + FIFA + confederations + alt channels).
    _wc2026 = [c for c in _chs if (c.get("competitions") or {}).get("wc2026")]
    if _wc2026:
        from src.analytics import fmt_num as _fmt_wc
        _wc_subs = sum(int(c.get("subscriber_count") or 0) for c in _wc2026)
        st.caption(
            f"🏆 **FIFA World Cup 2026** — tracking **{len(_wc2026)} official "
            f"channels** ({_fmt_wc(_wc_subs)} subs): the 48 qualified national "
            "teams plus FIFA and the 6 confederations. See the dedicated page."
        )

    # Also-tracking line: Players + Other Clubs + Women (isolated).
    # Federations parked until after WC2026 — intentionally omitted here.
    _players = [c for c in _chs if c.get("entity_type") == "Player"]
    _others = [c for c in _chs if c.get("entity_type") == "OtherClub"]
    _women = [c for c in _chs if c.get("entity_type") == "WomenClub"]
    if _players or _others or _women:
        from src.analytics import fmt_num as _fmt
        _bits = []
        if _players:
            _ps = sum(int(c.get("subscriber_count") or 0) for c in _players)
            _bits.append(f"⚽ **{len(_players)} players** ({_fmt(_ps)} subs)")
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

# Logged-out visitors only: an elegant, low-pressure nudge to sign in.
# Honest about the mechanism (free, Google / email code, no password)
# so it reads as an invitation, not a paywall. Hidden once signed in.
if not is_logged_in():
    st.markdown(
        f"""<div style="background:{_T.SURFACE};border-left:3px solid {_T.ACCENT};
        padding:14px 18px;margin:16px 0 4px 0;border-radius:6px;
        line-height:1.6;color:{_T.TEXT};font-size:15px">
        🔓 <b>Most of this lives behind a quick — and free — sign-in.</b>
        The daily recaps, season cadence, all-time leaderboards, every
        club's single best video and the road-to-WC2026 tracker are all
        one step away. Sign in with Google or a one-time email code
        <span style="color:{_T.MUTED}">(no password, no spam, nothing to
        install)</span> — the <b>Sign&nbsp;in</b> panel is in the
        left&nbsp;sidebar&nbsp;👈 &nbsp;It takes seconds, and whatever
        you're looking at follows you from page to page.
        </div>""",
        unsafe_allow_html=True,
    )

# Canonical link blue (_T.LINK = #58A6FF) so name links read like
# links, not as muted body text. Underline only on hover.
_link = f"color:{_T.LINK};text-decoration:none"

st.markdown(
    f"""<div style="line-height:1.6">
    Hi, I'm <a href="https://linkedin.com/in/carlodemarchis" target="_blank" style="{_link}"><b>Carlo De Marchis</b></a>
    — <a href="https://www.linkedin.com/newsletters/a-guy-with-a-scarf-6998145822441775104/" target="_blank" style="{_link}"><i>A Guy With A Scarf</i></a>
    — and this is a project born from my passion for data, stats and
    visualisation in sports and media. It'll keep changing based on what
    I learn and what you tell me.
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

# ── Pre-beta banner — sits right above the daily AI note so it
# reads as part of the same "what's going on" block at the top of
# the page. Lightweight: orange left-border, mailto link.
st.markdown(
    '<div style="background:#1a1c24;border-left:3px solid #FFA15A;'
    'padding:10px 14px;margin:6px 0 12px 0;border-radius:4px;'
    'font-size:13px;line-height:1.55;color:#FAFAFA">'
    '<span style="color:#FFA15A;font-weight:600">🚧 Work in progress · pre-beta</span>'
    ' &nbsp;—&nbsp; this is an early build that will keep evolving. '
    'Bumps and rough edges are expected. '
    '<a href="mailto:carlodemarchis@gmail.com?subject=YTFT%20feedback" '
    'style="color:#58A6FF;text-decoration:none">Let me know</a> '
    'if you find any issue.'
    '</div>',
    unsafe_allow_html=True,
)

# ── Strong sign-in CTA ──────────────────────────────────────────────
# Pairs concrete value props with a real Google OAuth button, placed
# right after the reader has consumed the intro — the moment they're
# most primed to act. Reinforces (rather than replaces) the elegant
# "see the sidebar" nudge near the top: that one is for the already-
# convinced, this one is for the curious-but-undecided. Hidden once
# signed in.
if not is_logged_in():
    st.markdown(
        f"""<div style="background:linear-gradient(135deg,#1a2230,#1a1c24);
        border:1px solid {_T.ACCENT}55;border-radius:8px;
        padding:18px 22px;margin:14px 0 8px 0;line-height:1.6">
        <div style="font-size:18px;font-weight:700;color:{_T.TEXT};
        margin-bottom:6px">🔓 Unlock the full tracker — free, ~10 seconds</div>
        <div style="color:{_T.MUTED_2};font-size:14px;margin-bottom:8px">
        Sign in to get:
        </div>
        <ul style="color:{_T.TEXT};font-size:14px;margin:0 0 4px 0;
        padding-left:22px">
          <li>📊 <b>Daily Recap</b> with AI commentary on yesterday's biggest moves</li>
          <li>🚀 <b>Viral videos</b> + <b>One-hit wonders</b> — videos that carry a club's whole season</li>
          <li>🏆 <b>Season + all-time leaderboards</b> across 100+ clubs · per-club deep-dives</li>
          <li>🌍 <b>FIFA World Cup 2026</b> — 62 official channels, every video tracked</li>
        </ul>
        </div>""",
        unsafe_allow_html=True,
    )
    _cta_c1, _cta_c2 = st.columns([2, 3])
    with _cta_c1:
        if st.button("🔓 Sign in free with Google",
                     type="primary", width="stretch",
                     key="hero_cta_signin"):
            st.login("google")
    with _cta_c2:
        st.markdown(
            f"<div style='color:{_T.MUTED_2};font-size:13px;"
            f"padding-top:8px'>…or use a one-time email code from "
            f"the <b>Sign in</b> panel in the left sidebar. No password, "
            f"no spam, no install.</div>",
            unsafe_allow_html=True,
        )
    st.markdown("<div style='margin-bottom:14px'></div>",
                unsafe_allow_html=True)

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
        _note_row = _cached_dc_read(_db_note, "daily_note", _y.isoformat())
        if _note_row and _note_row.get("payload"):
            _p = _note_row["payload"]
            # Prefer pre-decorated HTML (with badge injection); fall back
            # to raw text for older cache rows that pre-date decoration.
            _note_body = (_p.get("html") or _p.get("text") or "").strip()
            # Streamlit's markdown processor collapses bare newlines, so
            # convert them to explicit <br> for the per-sentence layout.
            _note_body = _note_body.replace("\n", "<br>")
            if _note_body:
                st.markdown(
                    f'<div style="font-style:italic;color:#cccccc;line-height:1.7;'
                    f'border-left:3px solid #636EFA;padding:8px 14px;margin:18px 0">'
                    f'<div style="font-size:11px;color:#888;text-transform:uppercase;'
                    f'letter-spacing:0.5px;margin-bottom:6px;font-style:normal">'
                    f'Yesterday in football YouTube · {_y.strftime("%a %b %d")}</div>'
                    f'{_note_body}</div>',
                    unsafe_allow_html=True,
                )
except Exception:
    pass  # never block the page on a missing/broken note

# ── Biggest gainers this week (cached: dashboard_cache.home_top) ─────────────
try:
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    if SUPABASE_URL and SUPABASE_KEY:
        _db = Database(SUPABASE_URL, SUPABASE_KEY)
        # Reuse the global channel list when available — saves a duplicate
        # fetch on every Home page load.
        _channels = (st.session_state.get("_global_channels")
                     or _cached_channels(_db))
        _ch_by_id = {c["id"]: c for c in _channels}

        # Read pre-computed leaderboards. Cache is refreshed nightly by
        # daily_refresh and on every new-video pull by hourly_rss, so the
        # numbers are at most 1 hour stale.
        from src import dashboard_cache as _dc_home
        _home = _cached_dc_read(_db, "home_top", _dc_home.scope_all())
        _home_payload = (_home or {}).get("payload") or {}
        top5_views = _home_payload.get("top5_views") or []
        top5_pubs = _home_payload.get("top5_pubs") or []

        # Re-attach the live channel dict (for badge color lookups) — the
        # cache only stores ids/names so we don't bloat the payload with
        # the full channel record.
        for _r in top5_views:
            _r["_ch"] = _ch_by_id.get(_r.get("channel_id")) or {}
        for _r in top5_pubs:
            _r["_ch"] = _ch_by_id.get(_r.get("channel_id")) or {}

        if top5_views or top5_pubs:
            color_map = get_global_color_map()
            dual = get_global_color_map_dual()

            def _yt_url(ch: dict) -> str:
                """YouTube channel URL — prefers @handle, falls back to
                /channel/<id>. Returns '' when neither is available so
                the caller can render the name without an <a>."""
                handle = (ch.get("handle") or "").lstrip("@").strip()
                yt_id = (ch.get("youtube_channel_id") or "").strip()
                if handle:
                    return f"https://www.youtube.com/@{handle}"
                if yt_id:
                    return f"https://www.youtube.com/channel/{yt_id}"
                return ""

            def _name_link(name: str, ch: dict) -> str:
                """Wrap a channel name in an <a> that opens its YouTube
                page in a new tab. No underline by default; hover
                underline + cursor:pointer rules live in the table's
                CSS block."""
                url = _yt_url(ch or {})
                if not url:
                    return name
                return (f'<a href="{url}" target="_blank" rel="noopener" '
                        f'class="ch">{name}</a>')

            # ── Two side-by-side tables ─────────────────────────────
            _gcol1, _gcol2 = st.columns(2)
            with _gcol1:
                st.subheader("👁️ Biggest view gains (last 7 days)")
                rows = ""
                for i, g in enumerate(top5_views, 1):
                    ch = g.get("_ch") or {}
                    dot = channel_badge(ch, color_map, dual, 14)
                    col = _T.POS if g["d7_views"] > 0 else (_T.NEG if g["d7_views"] < 0 else _T.MUTED)
                    sgn = "+" if g["d7_views"] >= 0 else ""
                    rows += f"""<tr>
                        <td style="padding:6px 12px;color:{_T.MUTED}">{i}</td>
                        <td style="padding:6px 12px">{dot}</td>
                        <td style="padding:6px 12px">{_name_link(g['name'], ch)}</td>
                        <td style="padding:6px 12px;text-align:right">{fmt_num(g['views'])}</td>
                        <td style="padding:6px 12px;text-align:right;color:{col}">{sgn}{fmt_num(g['d7_views'])}</td>
                    </tr>"""
                components.html(f"""
                <style>
                  .home-g {{ width:100%; border-collapse:collapse; font-size:14px; color:{_T.TEXT};
                             font-family:"Source Sans Pro",sans-serif; }}
                  .home-g th {{ padding:6px 12px; border-bottom:2px solid {_T.BORDER_STRONG}; text-align:left; }}
                  .home-g td {{ border-bottom:1px solid {_T.BORDER}; }}
                  .home-g a.ch {{ color:inherit; text-decoration:none; cursor:pointer; }}
                  .home-g a.ch:hover {{ text-decoration:underline; }}
                </style>
                <table class="home-g"><thead><tr>
                  <th>#</th><th></th><th>Channel</th>
                  <th style="text-align:right">Total Views</th>
                  <th style="text-align:right">Δ last 7 days</th>
                </tr></thead><tbody>{rows}</tbody></table>
                """, height=len(top5_views) * 37 + 80, scrolling=False)

            with _gcol2:
                st.subheader("🎬 Most videos published (last 7 days)")
                if top5_pubs:
                    rows2 = ""
                    for i, r in enumerate(top5_pubs, 1):
                        ch = r.get("_ch") or {}
                        dot = channel_badge(ch, color_map, dual, 14)
                        lsl = f'{r["long"]} / {r["short"]} / {r["live"]}'
                        rows2 += f"""<tr>
                            <td style="padding:6px 12px;color:{_T.MUTED}">{i}</td>
                            <td style="padding:6px 12px">{dot}</td>
                            <td style="padding:6px 12px">{_name_link(r['name'], ch)}</td>
                            <td style="padding:6px 12px;text-align:center;color:{_T.MUTED_2}">{lsl}</td>
                            <td style="padding:6px 12px;text-align:right;font-weight:600">{r['count']}</td>
                        </tr>"""
                    components.html(f"""
                    <style>
                      .home-p {{ width:100%; border-collapse:collapse; font-size:14px; color:{_T.TEXT};
                                 font-family:"Source Sans Pro",sans-serif; }}
                      .home-p th {{ padding:6px 12px; border-bottom:2px solid {_T.BORDER_STRONG}; text-align:left; }}
                      .home-p td {{ border-bottom:1px solid {_T.BORDER}; }}
                      .home-p a.ch {{ color:inherit; text-decoration:none; cursor:pointer; }}
                      .home-p a.ch:hover {{ text-decoration:underline; }}
                    </style>
                    <table class="home-p"><thead><tr>
                      <th>#</th><th></th><th>Channel</th>
                      <th style="text-align:center">Long / Shorts / Live</th>
                      <th style="text-align:right">Videos</th>
                    </tr></thead><tbody>{rows2}</tbody></table>
                    """, height=len(top5_pubs) * 37 + 80, scrolling=False)
                else:
                    st.caption("No videos published in the last 7 days.")
except Exception:
    pass  # Snapshot data may not exist yet; degrade silently
from src.channels import current_season_label_safe as _csl_home
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

# YouTube API latency — bright-white boxed callout under the fine print.
# Same visual treatment as the season-scope disclaimer on the Season page.
# Flagged loudly because users keep asking why a chart bar reads "0" on a
# given day — it's almost always YouTube's lazy lifetime-aggregate update,
# not our pipeline. We retro-fit the data once their API catches up.
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

# ── Footer: release-notes link + current version ──────────────────────
from src.releases import current_version as _cv
st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
st.page_link("views/99_Release_Notes.py",
             label=f"📋 Release notes · v{_cv()}")
