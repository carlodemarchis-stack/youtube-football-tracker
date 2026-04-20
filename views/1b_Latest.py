"""Latest Videos — most recently published videos across tracked channels."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num, yt_popup_js
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    get_global_filter, get_league_for_channel, render_page_subtitle,
)

load_dotenv()
require_login()

st.title("Latest Videos")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

color_map = get_global_color_map()
dual = get_global_color_map_dual()

# ── Apply global filter ──────────────────────────────────────
g_league, g_club = get_global_filter()
_rss_updated = db.get_last_fetch_time("hourly_rss")
render_page_subtitle("Most recently published videos", updated_raw=_rss_updated)
if g_club:
    ch_ids = [g_club["id"]]
elif g_league:
    ch_ids = [c["id"] for c in all_channels if get_league_for_channel(c) == g_league]
else:
    ch_ids = None

# ── Format filter ────────────────────────────────────────────
_fc1, _fc2, _fc3 = st.columns([4, 1, 1])
with _fc1:
    fmt_options = ["All", "Long", "Shorts", "Live"]
    fmt_pick = st.segmented_control("Format", fmt_options, default="All")
with _fc2:
    show_scheduled = st.checkbox("Show scheduled", value=False)
with _fc3:
    mosaic_view = st.checkbox("Mosaic", value=False)

with st.spinner("Loading latest videos…"):
    latest_raw = db.get_recent_videos(limit=200, channel_ids=ch_ids)

# Filter out scheduled/premiere videos (future actual_start_time) unless checkbox is on
_now_utc = datetime.now(timezone.utc)
def _is_scheduled(v):
    _ast = v.get("actual_start_time") or ""
    if _ast:
        try:
            _ast_dt = datetime.fromisoformat(_ast.replace("Z", "+00:00"))
            return _ast_dt > _now_utc
        except Exception:
            return False
    # No actual_start_time: live with zero duration is likely scheduled
    if (v.get("format") or "").lower() == "live" and (v.get("duration_seconds") or 0) == 0:
        return True
    return False

# Detect "Live Now" — live format, 0 duration, actual_start_time in the past (within 6h)
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
        return 0 < delta.total_seconds() < 2 * 3600  # started within last 2h
    except Exception:
        return False

live_now = [v for v in latest_raw if _is_live_now(v)]

if not show_scheduled:
    latest_raw = [v for v in latest_raw if not _is_scheduled(v) or _is_live_now(v)]

# Apply format filter
if fmt_pick and fmt_pick != "All":
    _fmt_map = {"Long": "long", "Shorts": "short", "Live": "live"}
    _target = _fmt_map[fmt_pick]
    latest = []
    for v in latest_raw:
        vfmt = (v.get("format") or "").lower()
        if vfmt not in ("long", "short", "live"):
            vfmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        if vfmt == _target:
            latest.append(v)
    latest = latest[:100]
else:
    latest = latest_raw[:100]

if not latest:
    st.caption("No videos found.")
    st.stop()

ch_by_id = {c["id"]: c for c in all_channels}

_COUNTRY_FLAG = {"IT": "\U0001F1EE\U0001F1F9", "EN": "\U0001F1EC\U0001F1E7", "ES": "\U0001F1EA\U0001F1F8",
                 "DE": "\U0001F1E9\U0001F1EA", "FR": "\U0001F1EB\U0001F1F7", "US": "\U0001F1FA\U0001F1F8",
                 "GB": "\U0001F1EC\U0001F1E7"}

# ── Live Now banner ─────────────────────────────────────────
if live_now:
    _ln_cards = ""
    for v in live_now:
        yt_id = v.get("youtube_video_id", "") or ""
        url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else "#"
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;").replace('"', "&quot;")
        ch = ch_by_id.get(v.get("channel_id")) or {}
        ch_name = v.get("channel_name", "") or ch.get("name", "")
        thumb = v.get("thumbnail_url") or ""
        c1, c2 = dual.get(ch_name, (color_map.get(ch_name, "#636EFA"), "#FFFFFF"))
        # Time since start
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
        _ln_cards += f"""<a href="#" onclick="window.open('https://www.youtube.com/embed/{yt_id}?autoplay=1','ytplayer','width=960,height=540,menubar=no,toolbar=no,location=no');return false;" class="ln-card">
          <div class="ln-thumb">
            <img src="{thumb}" alt="">
            <span class="ln-badge">● LIVE</span>
            {'<span class="ln-dur">' + _live_label + '</span>' if _live_label else ''}
          </div>
          <div class="ln-info">
            <span class="ln-flag">{LEAGUE_FLAG.get(COUNTRY_TO_LEAGUE.get((ch.get("country") or "").strip(), ""), "")}</span>
            <span class="ln-dot" style="background:{c1};box-shadow:2px 0 0 {c2}"></span>
            <span class="ln-club">{ch_name}</span>
            {('<span class="ln-views">' + fmt_num(views) + ' views</span>') if views else ''}
          </div>
          <div class="ln-title" title="{title}">{title}</div>
        </a>"""

    st.markdown(f"""<style>
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
      .ln-dot {{ display:inline-block; width:10px; height:10px; border-radius:50%;
                 border:1px solid rgba(255,255,255,0.3); flex-shrink:0; }}
      .ln-club {{ font-size:11px; color:#999; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
      .ln-views {{ font-size:10px; color:#666; margin-left:auto; white-space:nowrap; }}
      .ln-title {{ padding:2px 8px 8px; font-size:12px; line-height:1.3;
                   display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
                   overflow:hidden; color:#ddd; }}
    </style>
    <div style="color:#FAFAFA;font-size:15px;font-weight:600;margin:0 0 10px 4px">🔴 Live Now</div>
    <div class="ln-grid">{_ln_cards}</div>""", unsafe_allow_html=True)

# ── Mosaic view ─────────────────────────────────────────────
if mosaic_view:
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
            <img src="{thumb}" alt="">
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

# ── Build HTML table rows ────────────────────────────────────
rows_html = ""
for v in latest:
    yt_id = v.get("youtube_video_id", "") or ""
    url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""
    title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&#39;")
    ch = ch_by_id.get(v.get("channel_id")) or {}
    ch_name = v.get("channel_name", "") or ch.get("name", "")
    c1, c2 = dual.get(ch_name, (color_map.get(ch_name, "#636EFA"), "#FFFFFF"))

    thumb = v.get("thumbnail_url") or ""
    thumb_html = f'<img src="{thumb}" style="width:120px;height:68px;object-fit:cover;border-radius:4px">' if thumb else ""

    dur = v.get("duration_seconds") or 0
    dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else "0:00"
    fmt_raw = (v.get("format") or "").lower()
    if fmt_raw not in ("long", "short", "live"):
        fmt_raw = "long" if dur >= 60 else "short"
    # Detect scheduled/premiere (future actual_start_time) for any format
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
    # Age calculation — use effective_date (actual air time for live videos)
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
                days = age_minutes // 1440
                age_label = f"{days}d"
        except Exception:
            age_label = ""

    row_click = f'onclick="window.open(\'{url}\',\'_blank\',\'noopener\')"' if url else ''
    _country = (ch.get("country") or "").strip()
    _league = COUNTRY_TO_LEAGUE.get(_country, _country)
    _flag = LEAGUE_FLAG.get(_league, "")
    dot = f'<span class="dot" style="background:{c1};box-shadow:3px 0 0 {c2}"></span>'

    rows_html += f"""<tr {row_click} style="cursor:pointer" data-views="{views}" data-likes="{likes}" data-comments="{comments}" data-dur="{dur}" data-age="{age_minutes}" data-ch="{ch_name}" data-fmt="{fmt_raw}" data-cat="{cat}">
        <td class="c-video">
          <div class="v-row">
            {thumb_html}
            <div class="v-info">
              <div class="v-meta">{_flag} {dot}<span style="color:#AAA">{ch_name}</span> · <span style="color:{fmt_color}">{fmt_label}</span>{(' · <span style="color:#666">' + cat + '</span>') if cat and cat != 'Other' else ''}</div>
              <a href="{url}" target="_blank" rel="noopener" class="v-title">{title}</a>
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
  .lt th .arrow {{ font-size:10px; margin-left:4px; opacity:0.5; }}
  .lt th.sorted .arrow {{ opacity:1; color:#58A6FF; }}
  .lt td {{ border-bottom:1px solid #262730; padding:6px 12px; vertical-align:middle; }}
  .lt tr:hover td {{ background:#1a1c24; }}
  .dot {{ display:inline-block; width:12px; height:12px; border-radius:50%;
          border:1px solid rgba(255,255,255,0.3); vertical-align:middle;
          margin-right:5px; }}
  .c-video {{ padding:6px 8px !important; }}
  .v-row {{ display:flex; align-items:center; gap:10px; }}
  .v-row img {{ width:120px; height:68px; object-fit:cover; border-radius:4px; flex-shrink:0; }}
  .v-info {{ min-width:0; }}
  .v-meta {{ font-size:12px; margin-bottom:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .v-title {{ color:#FAFAFA; text-decoration:none; font-size:13px; line-height:1.3;
              display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }}
  .c-dur, .c-views, .c-likes, .c-comments {{ text-align:right; }}
  .c-age {{ white-space:nowrap; }}
</style>
<table class="lt" id="ltTable">
  <thead><tr>
    <th>Video</th>
    <th data-col="dur" data-type="num" style="text-align:right">Duration <span class="arrow">▲</span></th>
    <th data-col="views" data-type="num" style="text-align:right">Views <span class="arrow">▲</span></th>
    <th data-col="likes" data-type="num" style="text-align:right">Likes <span class="arrow">▲</span></th>
    <th data-col="comments" data-type="num" style="text-align:right">Comments <span class="arrow">▲</span></th>
    <th data-col="age" data-type="num">Age <span class="arrow">▲</span></th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<script>
(function() {{
  const table = document.getElementById('ltTable');
  const headers = table.querySelectorAll('th[data-col]');
  let currentCol = null, asc = true;

  headers.forEach(th => {{
    th.addEventListener('click', () => {{
      const col = th.dataset.col;
      const type = th.dataset.type;
      if (currentCol === col) {{ asc = !asc; }}
      else {{ currentCol = col; asc = (type === 'num') ? false : true; }}

      headers.forEach(h => h.classList.remove('sorted'));
      th.classList.add('sorted');
      th.querySelector('.arrow').textContent = asc ? '▲' : '▼';

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
""", height=len(latest) * 92 + 80, scrolling=True)
