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
from src.analytics import fmt_num, yt_popup_js, CATEGORY_COLORS
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
render_page_subtitle("Most recently published WC2026 videos · "
                     f"{len(wc)} channels", updated_raw=_latest_pub)

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

# ── Live Now banner (verbatim from core Latest) ──────────────
if live_now:
    _ln_cards = ""
    for v in live_now:
        yt_id = v.get("youtube_video_id", "") or ""
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
        ch = ch_by_id.get(v.get("channel_id")) or {}
        ch_name = v.get("channel_name", "") or ch.get("name", "")
        thumb = v.get("thumbnail_url") or ""
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
        _ln_cards += f"""<a href="https://www.youtube.com/watch?v={yt_id}" class="ln-card">
          <div class="ln-thumb">
            <img src="{thumb}" alt="">
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
      body {{ margin:0; font-family:"Source Sans Pro",sans-serif; }}
      .ln-grid {{ display:flex; gap:12px; overflow-x:auto; padding:4px 0 8px 0; }}
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
    """, height=310, scrolling=False)

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
# Confederations are governing bodies → §7 marker is a dual-dot (two
# brand colours). Pairs lifted from src.channels' governing-body brand
# map, keyed by the confederation code the data uses. Brand colours
# are the §1 data-not-theme exception.
_CONF_DUAL = {
    "UEFA": ("#C8102E", "#003F87"), "CONMEBOL": ("#003F87", "#F4C300"),
    "CONCACAF": ("#F26522", "#1E73BE"), "CAF": ("#006B3F", "#FCD116"),
    "AFC": ("#F0A91A", "#005A36"), "OFC": ("#0073CF", "#FFFFFF"),
    "FIFA": ("#326295", "#FFFFFF"),
}


def _conf_badge(v) -> str:
    k = _conf_of(v)
    c1, c2 = _CONF_DUAL.get(k, (_CONF_COLOR.get(k, "#888"), "#888"))
    return dual_dot(c1, c2, 14, inline=True)


def _conf_of(v):
    ch = ch_by_id.get(v.get("channel_id")) or {}
    return (((ch.get("competitions") or {}).get("wc2026") or {})
            .get("confederation") or "Other")


def _team_of(v):
    ch = ch_by_id.get(v.get("channel_id")) or {}
    w = (ch.get("competitions") or {}).get("wc2026") or {}
    return w.get("team") or ch.get("country") or ch.get("name") or "—"


_team_color: dict[str, str] = {}
# Teams are clubs/federations → §7 marker is a dual-dot (the channel's
# two brand colours). Same source as the WC2026 main table's per-row
# marker (channel color / color2); brand colours are the §1
# data-not-theme exception.
_team_dual: dict[str, tuple[str, str]] = {}
for _c in wc:
    _w = (_c.get("competitions") or {}).get("wc2026") or {}
    _t = _w.get("team") or _c.get("country") or _c.get("name") or "—"
    _team_color.setdefault(_t, _c.get("color") or "#636EFA")
    _team_dual.setdefault(_t, (_c.get("color") or "#636EFA",
                               _c.get("color2") or "#FFFFFF"))


def _team_badge(v) -> str:
    c1, c2 = _team_dual.get(_team_of(v), ("#636EFA", "#FFFFFF"))
    return dual_dot(c1, c2, 14, inline=True)

try:
    if _wc_team:
        # Z3 — single team: rich thumbnail strip (core Z3 analog).
        from src.timeline import render_48h_timeline
        render_48h_timeline(timeline_unscheduled)
    elif _wc_confed:
        # Z2 — one confederation: dots grouped by team within it.
        from src.timeline import render_48h_dots
        render_48h_dots(
            timeline_unscheduled,
            channel_resolver=_team_of,
            color_resolver=lambda v: _team_color.get(_team_of(v), "#888"),
            badge_resolver=_team_badge,
            group_resolver=_team_of,
            row_label="team",
        )
    else:
        # Z1 — all WC2026: dots grouped by confederation.
        from src.timeline import render_48h_dots
        render_48h_dots(
            timeline_unscheduled,
            channel_resolver=_conf_of,
            color_resolver=lambda v: _CONF_COLOR.get(_conf_of(v), "#888"),
            badge_resolver=_conf_badge,
            group_resolver=_conf_of,
            row_label="confederation",
        )
except Exception as _e:
    st.caption(f"(24h timeline unavailable: {_e})")

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
