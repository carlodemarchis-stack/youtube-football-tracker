"""Latest Videos — most recently published videos across tracked channels."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    get_global_filter, get_league_for_channel,
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
if g_club:
    ch_ids = [g_club["id"]]
elif g_league:
    ch_ids = [c["id"] for c in all_channels if get_league_for_channel(c) == g_league]
else:
    ch_ids = None

with st.spinner("Loading latest videos…"):
    latest = db.get_recent_videos(limit=50, channel_ids=ch_ids)

if not latest:
    st.caption("No videos found.")
    st.stop()

if g_club:
    _scope = f"**{g_club['name']}** only"
elif g_league:
    _scope = f"all clubs in **{g_league}**"
else:
    _scope = "**all tracked channels**"
st.caption(f"Showing **{len(latest)}** most recent videos · {_scope}")

ch_by_id = {c["id"]: c for c in all_channels}

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
    fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[fmt_raw]
    fmt_color = {"long": "#636EFA", "short": "#EF553B", "live": "#FFA15A"}[fmt_raw]

    cat = (v.get("category") or "").replace("<", "&lt;")
    views = int(v.get("view_count") or 0)
    views_str = fmt_num(views)
    likes = int(v.get("like_count") or 0)
    likes_str = fmt_num(likes)
    comments = int(v.get("comment_count") or 0)
    comments_str = fmt_num(comments)
    # Age calculation
    pub_str = v.get("published_at") or ""
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

    rows_html += f"""<tr {row_click} style="cursor:pointer" data-views="{views}" data-likes="{likes}" data-comments="{comments}" data-dur="{dur}" data-age="{age_minutes}" data-ch="{ch_name}" data-fmt="{fmt_raw}" data-cat="{cat}">
        <td class="c-thumb">{thumb_html}</td>
        <td class="c-club">
            <span class="dot" style="background:{c1};box-shadow:3px 0 0 {c2}"></span>
            <span style="color:#AAA;font-size:13px">{ch_name}</span>
        </td>
        <td class="c-title"><a href="{url}" target="_blank" rel="noopener" style="color:#FAFAFA;text-decoration:none">{title}</a></td>
        <td class="c-fmt" style="color:{fmt_color}">{fmt_label}</td>
        <td class="c-dur" style="text-align:right">{dur_s}</td>
        <td class="c-views" style="text-align:right">{views_str}</td>
        <td class="c-likes" style="text-align:right">{likes_str}</td>
        <td class="c-comments" style="text-align:right">{comments_str}</td>
        <td class="c-cat">{cat}</td>
        <td class="c-age" style="color:#888">{age_label}</td>
    </tr>"""

components.html(f"""
<style>
  .lt {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .lt th {{ padding:8px 12px; border-bottom:2px solid #444; text-align:left;
            cursor:pointer; user-select:none; white-space:nowrap; }}
  .lt th:hover {{ color:#58A6FF; }}
  .lt th .arrow {{ font-size:10px; margin-left:4px; opacity:0.5; }}
  .lt th.sorted .arrow {{ opacity:1; color:#58A6FF; }}
  .lt td {{ border-bottom:1px solid #262730; padding:6px 12px; }}
  .lt tr:hover td {{ background:#1a1c24; }}
  .dot {{ display:inline-block; width:12px; height:12px; border-radius:50%;
          border:1px solid rgba(255,255,255,0.3); vertical-align:middle;
          margin-right:6px; }}
  .c-thumb {{ padding:6px 8px !important; }}
  .c-dur, .c-views, .c-likes, .c-comments {{ text-align:right; }}
  .c-age {{ white-space:nowrap; }}
</style>
<table class="lt" id="ltTable">
  <thead><tr>
    <th></th>
    <th data-col="ch" data-type="str">Club <span class="arrow">▲</span></th>
    <th data-col="title" data-type="str">Title <span class="arrow">▲</span></th>
    <th data-col="fmt" data-type="str">Format <span class="arrow">▲</span></th>
    <th data-col="dur" data-type="num" style="text-align:right">Duration <span class="arrow">▲</span></th>
    <th data-col="views" data-type="num" style="text-align:right">Views <span class="arrow">▲</span></th>
    <th data-col="likes" data-type="num" style="text-align:right">Likes <span class="arrow">▲</span></th>
    <th data-col="comments" data-type="num" style="text-align:right">Comments <span class="arrow">▲</span></th>
    <th data-col="cat" data-type="str">Theme <span class="arrow">▲</span></th>
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
""", height=len(latest) * 92 + 80, scrolling=True)
