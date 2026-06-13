"""FIFA World Cup 2026 — Latest videos.

Standalone, isolated WC2026 page (own menu group, §10 — killable with
zero impact elsewhere). Mirrors the core Latest Videos feed look (Live
Now banner + format/scheduled/mosaic controls + sortable table /
mosaic, identical CSS) but scoped to `competitions.wc2026` channels.

Deliberately dropped vs the core page:
  - the LLM "vibe check" note  — core-only dashboard_cache, needs its
    own cron; out of WC2026's lightweight scope.
  - the core global league/club filter — WC2026 has its own separate
    Confederation→Team filter instead (src.wc2026_filter), so the
    core one stays in app.py `_no_filter_url_paths`.

The 24h published-timeline is scope-adaptive (mirrors core Z1/Z2/Z3):
Z1 all → grouped by CONFEDERATION (league-grouping would collapse to
one meaningless "Other" row); Z2 one confederation → grouped by TEAM;
Z3 one team → the rich thumbnail strip.

Data path is the exact proven one the core page uses
(`get_recent_videos` with an explicit channel-id scope).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
from src import components_compat as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num, yt_popup_js, CATEGORY_COLORS, kpi_row
try:
    from src.analytics import video_table_height
except ImportError:
    def video_table_height(n_rows: int, header_buffer: int = 50) -> int:
        return max(0, int(n_rows)) * 75 + header_buffer
from src.filters import render_page_subtitle
from src.dot import channel_badge, dual_dot  # noqa: F401  (kept for fallback)
from src.wc2026_badge import wc2026_badge
from src.auth import require_login

load_dotenv()
require_login()

st.title("FIFA World Cup 2026 — Latest")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
from src.cached_db import (
    get_all_channels as _cached_channels,
    get_recent_videos as _cached_recent,
    read_dashboard_cache as _cached_dc_read,
)
from src.filters import get_global_color_map, get_global_color_map_dual

all_channels = _cached_channels(db)
wc = [c for c in all_channels
      if (c.get("competitions") or {}).get("wc2026")]
if not wc:
    st.warning("No channels with `competitions.wc2026` set yet.")
    st.stop()

# WC2026 sub-app filter (confederation → team), shared across pages.
# Scoping `wc` here cascades through ch_ids → the recent-videos query.
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)
_wc_confed, _wc_team = get_wc2026_filter()
wc = scope_wc2026(wc, _wc_confed, _wc_team)
if not wc:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_wc_confed, _wc_team)}**.")
    st.stop()
if _wc_confed or _wc_team:
    st.caption(f"Filtered: {_wc_scope_label(_wc_confed, _wc_team)} · "
               f"{len(wc)} channel(s)")

color_map = get_global_color_map() or {}
dual = get_global_color_map_dual() or {}
ch_by_id = {c["id"]: c for c in wc}
ch_ids = [c["id"] for c in wc]

# "Updated" indicator = newest WC2026 video's published_at (scoped, so
# it reflects this feed — not the global newest video).
_latest_pub = None
try:
    _r = (db.client.table("videos")
          .select("published_at")
          .in_("channel_id", ch_ids)
          .order("published_at", desc=True)
          .limit(1).execute().data or [])
    _latest_pub = _r[0]["published_at"] if _r else None
except Exception:
    pass
render_page_subtitle(
    "Most recent uploads from the WC2026 cohort · "
    f"{len(wc)} channels", updated_raw=_latest_pub, show_filter=False)
st.caption(
    "📡 **Cohort:** the official YouTube channels of all 48 qualified "
    "teams + FIFA + the 6 confederations (UEFA · CONMEBOL · CONCACAF "
    "· CAF · AFC · OFC). We surface **every video they publish** "
    "below, including content unrelated to WC2026."
)

# 🤖 WC2026 Latest vibe — cohort-wide AI note (refreshed hourly by the
# WC2026 cron). Stored under its own dimension so it never collides
# with the Top-5 latest_vibe.
try:
    from src import dashboard_cache as _dc_lv
    _wc_vibe_row = _cached_dc_read(db, "wc2026_latest_vibe", _dc_lv.scope_all())
    _wc_vibe_html = (_wc_vibe_row or {}).get("payload", {}).get("html") or ""
except Exception:
    _wc_vibe_html = ""
if _wc_vibe_html:
    st.markdown(
        '<div style="background:#1a1c24;border-left:3px solid #58A6FF;'
        'padding:12px 16px;margin:8px 0 18px 0;border-radius:4px;'
        'font-size:14px;line-height:1.6;color:#FAFAFA">'
        '<span style="color:#888;font-size:11px;font-weight:600;'
        'letter-spacing:0.5px;text-transform:uppercase">'
        '🤖 WC2026 vibe check · updated hourly</span>'
        f'<div style="margin-top:6px">{_wc_vibe_html}</div></div>',
        unsafe_allow_html=True,
    )

with st.spinner("Loading latest videos…"):
    try:
        latest_raw = _cached_recent(db, limit=300, channel_ids=tuple(ch_ids))
    except Exception as _e:
        st.error(f"Could not load videos: {type(_e).__name__}: {_e}")
        st.stop()

# ── Scheduled / live-now detection (verbatim from core Latest) ──
_now_utc = datetime.now(timezone.utc)


def _is_scheduled(v):
    _ast = v.get("actual_start_time") or ""
    if _ast:
        try:
            _ast_dt = datetime.fromisoformat(_ast.replace("Z", "+00:00"))
            return _ast_dt > _now_utc
        except Exception:
            return False
    if (v.get("format") or "").lower() == "live" and (v.get("duration_seconds") or 0) == 0:
        return True
    return False


def _is_live_now(v):
    if (v.get("format") or "").lower() != "live":
        return False
    if (v.get("duration_seconds") or 0) > 0:
        return False
    _ast = v.get("actual_start_time") or ""
    if not _ast:
        return False
    try:
        _ast_dt = datetime.fromisoformat(_ast.replace("Z", "+00:00"))
        delta = _now_utc - _ast_dt
        return 0 < delta.total_seconds() < 2 * 3600
    except Exception:
        return False


live_now = [v for v in latest_raw if _is_live_now(v)]
latest_raw_unscheduled = [v for v in latest_raw
                          if not _is_scheduled(v) or _is_live_now(v)]

# Timeline needs EVERY video in the window (not the most-recent N) so a
# high-volume scope can't lose its oldest hours to the row cap. Own
# complete, paginated, time-windowed fetch (49h covers 24/48h + buffer).
try:
    _timeline_raw = _cached_recent(db, limit=12000,
                                   channel_ids=tuple(ch_ids),
                                   since_hours=49)
except Exception:
    _timeline_raw = latest_raw
timeline_unscheduled = [v for v in _timeline_raw
                        if not _is_scheduled(v) or _is_live_now(v)]

# Live Now banner is rendered later — right above the video display
# block (Format controls → mosaic / table) — so it sits adjacent to
# whichever view the user picks rather than hovering near the
# subtitle.

# ── 24h published timeline — scope-adaptive (mirrors core Z1/Z2/Z3)
# Core Latest groups by Top-5 league; WC2026 has no league dimension,
# so this adapts to the active WC2026 filter:
#   Z1 (all)            → dots grouped by CONFEDERATION (~7 rows;
#                          league-grouping would collapse to one
#                          meaningless "Other" row)
#   Z2 (one confed)     → dots grouped by TEAM within it (mirrors
#                          core Z2 "by club")
#   Z3 (one team)       → rich thumbnail strip (mirrors core Z3
#                          single-club)
# Confederation brand colours are data, not theme (§1 exception);
# team rows use each team's own channel brand colour.
_CONF_COLOR = {
    "UEFA": "#C8102E", "CONMEBOL": "#003F87", "AFC": "#F0A91A",
    "CAF": "#006B3F", "CONCACAF": "#F26522", "OFC": "#0073CF",
    "FIFA": "#326295",
}
# Confederation + team markers come from src/wc2026_badge.py so the
# timeline strip uses the same dual-dot + flag vocabulary as every
# other WC2026 page (UEFA red+blue, England with its own flag, etc.).
_team_color: dict[str, str] = {}
for _c in wc:
    _w = (_c.get("competitions") or {}).get("wc2026") or {}
    _t = _w.get("team") or _c.get("country") or _c.get("name") or "—"
    # Keep _team_color for chart colour lookups (used by the
    # timeline group → row colour); the row marker itself uses the
    # shared resolver below.
    _team_color.setdefault(_t, _c.get("color") or "#636EFA")


def _conf_of(v):
    ch = ch_by_id.get(v.get("channel_id")) or {}
    return (((ch.get("competitions") or {}).get("wc2026") or {})
            .get("confederation") or "Other")


def _team_of(v):
    ch = ch_by_id.get(v.get("channel_id")) or {}
    w = (ch.get("competitions") or {}).get("wc2026") or {}
    return w.get("team") or ch.get("country") or ch.get("name") or "—"


def _conf_badge(v) -> str:
    """Confederation row-marker for the Z1 timeline. Looks up any
    channel under that confederation and renders its governing-body
    dual-dot via the shared resolver."""
    k = _conf_of(v)
    # Synthesize a GoverningBody channel-dict so wc2026_badge picks
    # the right branch — k IS the body name (FIFA / UEFA / …).
    return wc2026_badge(
        {"entity_type": "GoverningBody", "name": k}, 14)


def _team_badge(v) -> str:
    """Team row-marker for the Z2 timeline — flag emoji via the
    shared resolver."""
    ch = ch_by_id.get(v.get("channel_id")) or {}
    return wc2026_badge(ch, 14)


# ── KPI bar — last-24h stats (same style as other pages) ──────────
def _kpi_fmt(v) -> str:
    f = (v.get("format") or "").lower()
    if f in ("long", "short", "live"):
        return f
    return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"

# timeline_unscheduled is fetched on a wide (49h) window so the dot
# timeline never loses its oldest hours to the row cap — but the KPI
# tile says "24h", so count ONLY the last 24h by published_at.
from datetime import datetime as _kdt, timezone as _ktz, timedelta as _ktd
_k_cut = (_kdt.now(_ktz.utc) - _ktd(hours=24)).isoformat()
_k_vids = [v for v in timeline_unscheduled
           if (v.get("published_at") or "") >= _k_cut]
_k_n = len(_k_vids)
if _k_n:
    _k_long = sum(1 for v in _k_vids if _kpi_fmt(v) == "long")
    _k_short = sum(1 for v in _k_vids if _kpi_fmt(v) == "short")
    _k_live = sum(1 for v in _k_vids if _kpi_fmt(v) == "live")
    _k_chans = len({v.get("channel_id") for v in _k_vids
                    if v.get("channel_id")})
    _k_confeds = len({_conf_of(v) for v in _k_vids
                      if _conf_of(v)})
    _k_views = sum(int(v.get("view_count") or 0)
                   for v in _k_vids)
    st.markdown(kpi_row([
        ("🎬 Videos · 24h", fmt_num(_k_n),
         f"{_k_chans} channels · {_k_confeds} confederations"),
        ("▶️ Long", fmt_num(_k_long)),
        ("📱 Shorts", fmt_num(_k_short)),
        ("🔴 Live", fmt_num(_k_live)),
        ("👁️ Views so far", fmt_num(_k_views), "on these uploads"),
    ]), unsafe_allow_html=True)

try:
    # Use the SAME last-24h set as the KPI bar so the caption count
    # matches the tile (and the dots) — timeline_unscheduled spans 49h
    # for row-cap headroom, but this section is "the last 24h".
    _tl_n = len(_k_vids)
    _tl_chans = len({v.get("channel_id") for v in _k_vids
                     if v.get("channel_id")})
    if _wc_team:
        # Z3 — single team: rich thumbnail strip (core Z3 analog). Keep
        # the wider set — it's a "latest videos" strip, not the 24h dots.
        from src.timeline import render_48h_timeline
        render_48h_timeline(timeline_unscheduled)
    elif _wc_confed:
        # Z2 — one confederation: dots grouped by team within it.
        from src.timeline import render_48h_dots
        # Every team in scope gets a row, even if it had 0 uploads,
        # so the "who's quiet" view is preserved.
        _all_teams_in_scope = sorted({
            (((c.get("competitions") or {}).get("wc2026") or {})
             .get("team")
             or c.get("country") or c.get("name") or "—")
            for c in wc
        })
        _team_groups = [
            {"key": t, "label": t,
             # Synthesize a channel-dict so the shared badge resolver
             # picks the right flag for the team.
             "badge_html": wc2026_badge(
                 {"competitions": {"wc2026": {"team": t}},
                  "name": t, "country": t}, 14)}
            for t in _all_teams_in_scope
        ]
        _n_teams = len(_all_teams_in_scope)
        render_48h_dots(
            _k_vids,
            channel_resolver=_team_of,
            color_resolver=lambda v: _team_color.get(_team_of(v), "#888"),
            badge_resolver=_team_badge,
            group_resolver=_team_of,
            row_label="team",
            all_groups=_team_groups,
            caption=(f"{_tl_n} video(s) across {_n_teams} team(s) "
                     f"from {_tl_chans} channel(s) in the last 24h. "
                     "Quiet teams shown as empty rows. "
                     "Click any dot to open the video."),
        )
    else:
        # Z1 — all WC2026: dots grouped by confederation.
        from src.timeline import render_48h_dots
        # Every confederation present in the cohort gets a row, even
        # if it published 0 videos in the last 24h.
        _all_confeds_in_scope = sorted({
            (((c.get("competitions") or {}).get("wc2026") or {})
             .get("confederation") or "Other")
            for c in wc
        })
        _confed_groups = [
            {"key": k, "label": k,
             "badge_html": wc2026_badge(
                 {"entity_type": "GoverningBody", "name": k}, 14)}
            for k in _all_confeds_in_scope
        ]
        _n_confeds = len(_all_confeds_in_scope)
        render_48h_dots(
            _k_vids,
            channel_resolver=_conf_of,
            color_resolver=lambda v: _CONF_COLOR.get(_conf_of(v), "#888"),
            badge_resolver=_conf_badge,
            group_resolver=_conf_of,
            row_label="confederation",
            all_groups=_confed_groups,
            caption=(f"{_tl_n} video(s) across {_n_confeds} "
                     f"confederation(s) from {_tl_chans} channel(s) "
                     "in the last 24h. Quiet confederations shown "
                     "as empty rows. Click any dot to open the video."),
        )
except Exception as _e:
    st.caption(f"(24h timeline unavailable: {_e})")

# ── Last-day Δ views per channel (scoped bar chart) ──────────────
# One bar per channel in scope, sorted by Δ views desc. Picks the two
# most-recent captured_dates in channel_snapshots so we always have
# data — robust against missing/partial days. Coloured by
# confederation brand colour. Cohort + sub-scope filter cascade
# automatically via `wc` / `ch_ids` above.
try:
    import pandas as _pd
    import altair as _alt
    from collections import defaultdict as _dd
    from src.wc2026_badge import CONF_COLOR as _CC

    _snap_rows = []
    if ch_ids:
        _resp = (db.client.table("channel_snapshots")
                 .select("channel_id,captured_date,total_views")
                 .in_("channel_id", ch_ids)
                 .order("captured_date", desc=True)
                 .range(0, 2 * len(ch_ids) - 1)
                 .execute().data) or []
        _snap_rows = _resp

    _by_date = _dd(dict)
    for _r in _snap_rows:
        _by_date[_r["captured_date"]][_r["channel_id"]] = \
            int(_r.get("total_views") or 0)
    _dates_desc = sorted(_by_date.keys(), reverse=True)
    if len(_dates_desc) >= 2:
        _cur_d, _prev_d = _dates_desc[0], _dates_desc[1]
        _cur, _prev = _by_date[_cur_d], _by_date[_prev_d]
        _name_of = {c["id"]: (((c.get("competitions") or {})
                                 .get("wc2026") or {}).get("team")
                                or c.get("name") or "?")
                     for c in wc}
        _conf_of_id = {c["id"]: (((c.get("competitions") or {})
                                     .get("wc2026") or {})
                                    .get("confederation") or "Other")
                        for c in wc}
        _rows = []
        for cid, cv in _cur.items():
            pv = _prev.get(cid)
            if pv is None:
                continue
            _rows.append({
                "Channel": _name_of.get(cid, "?"),
                "Δ Views": int(cv - pv),
                "Confederation": _conf_of_id.get(cid, "Other"),
            })
        if _rows:
            _df_full = _pd.DataFrame(_rows)\
                .sort_values("Δ Views", ascending=False)
            # Cap at top-20 by Δ views. Beyond that the y-axis labels
            # collide and the chart stops being readable on a normal
            # screen — better to show fewer rows clearly than 50+
            # with auto-skipped labels.
            _MAX_BARS = 20
            _df = _df_full.head(_MAX_BARS)
            _total = len(_df_full)
            st.subheader("👁️ Δ Views per channel — last full day")
            _suffix = (f" Top {len(_df)} of {_total} shown."
                       if _total > _MAX_BARS else "")
            st.caption(
                f"Each WC2026 channel's view-count change between "
                f"{_prev_d} and {_cur_d}, ordered by most growth. "
                f"Bar colour = confederation. Inherits the filter "
                f"above.{_suffix}"
            )
            _conf_seen = list(dict.fromkeys(_df["Confederation"]))
            _bar = (
                _alt.Chart(_df)
                .mark_bar()
                .encode(
                    y=_alt.Y(
                        "Channel:N",
                        sort=_df["Channel"].tolist(),
                        title=None,
                        # Force every label to render — no
                        # auto-skip / overlap suppression — and
                        # widen the gutter so long team names
                        # (e.g. 'Bosnia and Herzegovina') don't
                        # get truncated.
                        axis=_alt.Axis(labelLimit=240,
                                        labelOverlap=False,
                                        labelPadding=4)),
                    x=_alt.X("Δ Views:Q", title="Δ Views",
                              axis=_alt.Axis(format="~s")),
                    color=_alt.Color(
                        "Confederation:N",
                        scale=_alt.Scale(
                            domain=_conf_seen,
                            range=[_CC.get(c, "#888")
                                   for c in _conf_seen]),
                        legend=_alt.Legend(orient="bottom",
                                            title=None)),
                    tooltip=[
                        _alt.Tooltip("Channel:N"),
                        _alt.Tooltip("Confederation:N"),
                        _alt.Tooltip("Δ Views:Q", format=","),
                    ],
                )
                # 28px per row + 60px header/legend padding keeps
                # every label readable.
                .properties(height=28 * len(_df) + 60)
            )
            st.altair_chart(_bar, width="stretch")
except Exception as _e:
    st.caption(f"(Δ views chart unavailable: {_e})")

# ── Live Now banner (verbatim from core Latest) ──────────────
# Sits right above the Format controls + video display so it stays
# adjacent to whichever view (mosaic or table) the user picks.
if live_now:
    _ln_cards = ""
    for v in live_now:
        yt_id = v.get("youtube_video_id", "") or ""
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
        ch = ch_by_id.get(v.get("channel_id")) or {}
        ch_name = v.get("channel_name", "") or ch.get("name", "")
        # YouTube serves a `_live.jpg` placeholder while a stream is
        # in progress, but it 404s in iframe contexts roughly half the
        # time (and ages out the second the stream ends). Fall back
        # to the always-present hqdefault.jpg for the same video so
        # the card never shows a broken-image icon.
        thumb = v.get("thumbnail_url") or ""
        if (not thumb or "_live.jpg" in thumb) and yt_id:
            thumb = f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg"
        _ast = v.get("actual_start_time") or ""
        _live_label = ""
        if _ast:
            try:
                _ast_dt = datetime.fromisoformat(_ast.replace("Z", "+00:00"))
                _mins = int((_now_utc - _ast_dt).total_seconds() / 60)
                _live_label = f"{_mins}m" if _mins < 60 else f"{_mins // 60}h{_mins % 60:02d}m"
            except Exception:
                pass
        views = int(v.get("view_count") or 0)
        _ln_fallback = (f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg"
                        if yt_id else "")
        _ln_onerror = (f"this.onerror=null;this.src='{_ln_fallback}'"
                       if _ln_fallback and thumb != _ln_fallback else "")
        _ln_cards += f"""<a href="https://www.youtube.com/watch?v={yt_id}" target="_blank" rel="noopener" class="ln-card">
          <div class="ln-thumb">
            <img src="{thumb}" alt="" onerror="{_ln_onerror}">
            <span class="ln-badge">● LIVE</span>
            {'<span class="ln-dur">' + _live_label + '</span>' if _live_label else ''}
          </div>
          <div class="ln-info">
            {wc2026_badge(ch, 12)}
            <span class="ln-club">{ch_name}</span>
            {('<span class="ln-views">' + fmt_num(views) + ' views</span>') if views else ''}
          </div>
          <div class="ln-title" title="{title}">{title}</div>
        </a>"""

    components.html(f"""
    <style>
      * {{ box-sizing:border-box; }}
      html, body {{ margin:0; padding:0; background:#0E1117;
                    font-family:"Source Sans Pro",sans-serif; }}
      .ln-grid {{ display:flex; gap:12px; overflow-x:auto;
                  padding:4px 0 8px 0;
                  background:#0E1117; }}
      .ln-card {{ display:block; text-decoration:none; color:#FAFAFA; border-radius:8px;
                  background:#1a1c24; overflow:hidden; min-width:220px; max-width:260px;
                  flex-shrink:0; border:1px solid #EF553B44; transition:transform 0.15s; }}
      .ln-card:hover {{ transform:scale(1.03); background:#22252e; border-color:#EF553B88; }}
      .ln-thumb {{ position:relative; width:100%; aspect-ratio:16/9; overflow:hidden; }}
      .ln-thumb img {{ width:100%; height:100%; object-fit:cover; display:block; }}
      .ln-badge {{ position:absolute; top:6px; left:6px; background:#EF553B;
                   color:#fff; font-size:10px; font-weight:700; padding:2px 7px;
                   border-radius:3px; letter-spacing:0.5px; }}
      .ln-dur {{ position:absolute; bottom:4px; right:4px; background:rgba(0,0,0,0.8);
                 color:#fff; font-size:11px; padding:1px 5px; border-radius:3px; }}
      .ln-info {{ padding:6px 8px 2px; display:flex; align-items:center; gap:5px; }}
      .ln-club {{ font-size:11px; color:#999; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .ln-views {{ font-size:10px; color:#666; margin-left:auto; white-space:nowrap; }}
      .ln-title {{ padding:2px 8px 8px; font-size:12px; line-height:1.3;
                   display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
                   overflow:hidden; color:#ddd; }}
    </style>
    <div style="color:#FAFAFA;font-size:15px;font-weight:600;margin:0 0 10px 4px">🔴 Live Now</div>
    <div class="ln-grid">{_ln_cards}</div>
    {yt_popup_js()}
    """, height=240, scrolling=False)

# ── Format / scheduled / mosaic controls ─────────────────────
_fc1, _fc2, _fc3 = st.columns([4, 1, 1])
with _fc1:
    fmt_options = ["All", "Long", "Shorts", "Live"]
    fmt_pick = st.segmented_control("Format", fmt_options, default="All")
with _fc2:
    show_scheduled = st.checkbox("Show scheduled", value=False)
with _fc3:
    mosaic_view = st.checkbox("Mosaic", value=False)

_src = latest_raw if show_scheduled else latest_raw_unscheduled

if fmt_pick and fmt_pick != "All":
    _fmt_map = {"Long": "long", "Shorts": "short", "Live": "live"}
    _target = _fmt_map[fmt_pick]
    latest = []
    for v in _src:
        vfmt = (v.get("format") or "").lower()
        if vfmt not in ("long", "short", "live"):
            vfmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        if vfmt == _target:
            latest.append(v)
    latest = latest[:100]
else:
    latest = _src[:100]

if not latest:
    st.caption("No videos found.")
    st.stop()

# ── Mosaic view (verbatim from core Latest) ──────────────────
if mosaic_view:
    cards_html = ""
    for v in latest:
        yt_id = v.get("youtube_video_id", "") or ""
        url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "#"
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
        ch = ch_by_id.get(v.get("channel_id")) or {}
        ch_name = v.get("channel_name", "") or ch.get("name", "")
        thumb = v.get("thumbnail_url") or ""
        dur = v.get("duration_seconds") or 0
        dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else ""

        cards_html += f"""<a href="{url}" target="_blank" rel="noopener" class="card">
          <div class="card-thumb">
            <img src="{thumb}" alt="">
            {'<span class="card-dur">' + dur_s + '</span>' if dur_s else ''}
          </div>
          <div class="card-info">
            {wc2026_badge(ch, 12)}
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
      .card-club {{ font-size:11px; color:#999; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .card-title {{ padding:2px 8px 8px; font-size:12px; line-height:1.3;
                     display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
                     overflow:hidden; color:#ddd; }}
    </style>
    <div class="mosaic">{cards_html}</div>
    {yt_popup_js()}
    """, height=max(280, (len(latest) // 5 + 1) * 195), scrolling=True)
    st.stop()

# ── Sortable table (verbatim from core Latest) ───────────────
rows_html = ""
for v in latest:
    yt_id = v.get("youtube_video_id", "") or ""
    url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""
    title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;")
    ch = ch_by_id.get(v.get("channel_id")) or {}
    ch_name = v.get("channel_name", "") or ch.get("name", "")

    thumb = v.get("thumbnail_url") or ""
    thumb_html = f'<img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px">' if thumb else ""

    dur = v.get("duration_seconds") or 0
    dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else "0:00"
    fmt_raw = (v.get("format") or "").lower()
    if fmt_raw not in ("long", "short", "live"):
        fmt_raw = "long" if dur >= 60 else "short"
    _sched = _is_scheduled(v)
    if _sched:
        fmt_label = "Scheduled"
        fmt_color = "#AB63FA"
    else:
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[fmt_raw]
        fmt_color = {"long": "#636EFA", "short": "#EF553B", "live": "#FFA15A"}[fmt_raw]

    cat = (v.get("category") or "").replace("<", "&lt;")
    views = int(v.get("view_count") or 0)
    views_str = fmt_num(views)
    likes = int(v.get("like_count") or 0)
    likes_str = fmt_num(likes)
    comments = int(v.get("comment_count") or 0)
    comments_str = fmt_num(comments)
    pub_str = v.get("effective_date") or v.get("published_at") or ""
    age_minutes = 0
    age_label = ""
    if pub_str:
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - pub_dt
            age_minutes = int(delta.total_seconds() / 60)
            if age_minutes < 60:
                age_label = f"{age_minutes}m"
            elif age_minutes < 1440:
                age_label = f"{age_minutes // 60}h"
            else:
                age_label = f"{age_minutes // 1440}d"
        except Exception:
            age_label = ""

    row_click = f'onclick="window.open(\'{url}\',\'_blank\',\'noopener\')"' if url else ''
    _badge = wc2026_badge(ch, 14)
    _cat_color = CATEGORY_COLORS.get(cat, "#888")
    _cat_only = (f'<span style="color:{_cat_color}">{cat}</span>'
                 if cat and cat != 'Other' else '')
    _fmt_only = f'<span style="color:{fmt_color}">{fmt_label}</span>'
    _tags_line = ' · '.join([_fmt_only] + [p for p in (_cat_only,) if p])

    rows_html += f"""<tr {row_click} style="cursor:pointer" data-views="{views}" data-likes="{likes}" data-comments="{comments}" data-dur="{dur}" data-age="{age_minutes}" data-ch="{ch_name}" data-fmt="{fmt_raw}" data-cat="{cat}">
        <td class="c-video">
          <div class="v-row">
            {thumb_html}
            <div class="v-info">
              <div class="v-channel">{_badge} <span style="color:#AAA">{ch_name}</span></div>
              <a href="{url}" target="_blank" rel="noopener" class="v-title">{title}</a>
              <div class="v-meta">{_tags_line}</div>
            </div>
          </div>
        </td>
        <td class="c-dur" style="text-align:right">{dur_s}</td>
        <td class="c-views" style="text-align:right">{views_str}</td>
        <td class="c-likes" style="text-align:right">{likes_str}</td>
        <td class="c-comments" style="text-align:right">{comments_str}</td>
        <td class="c-age" style="color:#888">{age_label}</td>
    </tr>"""

components.html(f"""
<style>
  .lt {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .lt th {{ padding:8px 12px; border-bottom:2px solid #444; text-align:left;
            user-select:none; white-space:nowrap; }}
  .lt th[data-col] {{ cursor:pointer; }}
  .lt th[data-col]:hover {{ color:#58A6FF; }}
  .lt th.active {{ color:#58A6FF; }}
  .lt td {{ border-bottom:1px solid #262730; padding:6px 12px; vertical-align:middle; }}
  .lt tr:hover td {{ background:#1a1c24; }}
  .c-video {{ padding:6px 8px !important; }}
  .v-row {{ display:flex; align-items:flex-start; gap:10px; }}
  .v-row img {{ width:110px; height:62px; object-fit:cover; border-radius:4px; flex-shrink:0; }}
  .v-info {{ min-width:0; display:flex; flex-direction:column;
             justify-content:space-between; height:62px; flex:1; }}
  .v-channel {{ font-size:12px; display:flex; align-items:center; gap:6px;
                white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .v-meta {{ font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .v-title {{ color:#FAFAFA; text-decoration:none; font-size:13px; line-height:1.25; font-weight:700;
              display:-webkit-box; -webkit-line-clamp:1; -webkit-box-orient:vertical; overflow:hidden;
              text-overflow:ellipsis; }}
  .c-dur, .c-views, .c-likes, .c-comments {{ text-align:right; }}
  .c-age {{ white-space:nowrap; }}
</style>
<table class="lt" id="ltTable">
  <thead><tr>
    <th>Video</th>
    <th data-col="dur" data-type="num" style="text-align:right">Duration</th>
    <th data-col="views" data-type="num" style="text-align:right">Views</th>
    <th data-col="likes" data-type="num" style="text-align:right">Likes</th>
    <th data-col="comments" data-type="num" style="text-align:right">Comments</th>
    <th data-col="age" data-type="num" class="active">Age ▲</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<script>
(function() {{
  const table = document.getElementById('ltTable');
  const headers = table.querySelectorAll('th[data-col]');
  let currentCol = 'age', asc = true;
  headers.forEach(th => {{
    th.addEventListener('click', () => {{
      const col = th.dataset.col;
      const type = th.dataset.type;
      if (currentCol === col) {{ asc = !asc; }}
      else {{ currentCol = col; asc = (type === 'num') ? false : true; }}
      headers.forEach(h => {{
        h.classList.remove('active');
        h.textContent = h.textContent.replace(/ [▲▼]/g, '');
      }});
      th.classList.add('active');
      th.textContent += asc ? ' ▲' : ' ▼';
      const tbody = table.querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      rows.sort((a, b) => {{
        let va, vb;
        if (type === 'num') {{
          va = parseFloat(a.dataset[col]) || 0;
          vb = parseFloat(b.dataset[col]) || 0;
        }} else {{
          va = (a.dataset[col] || '').toLowerCase();
          vb = (b.dataset[col] || '').toLowerCase();
        }}
        if (va < vb) return asc ? -1 : 1;
        if (va > vb) return asc ? 1 : -1;
        return 0;
      }});
      rows.forEach(r => tbody.appendChild(r));
    }});
  }});
}})();
</script>
{yt_popup_js()}
""", height=video_table_height(len(latest)), scrolling=True)
