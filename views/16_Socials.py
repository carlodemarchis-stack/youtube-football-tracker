"""Socials — clubs' presence across other social platforms.

Respects the global league/club filter. One row per club, with a strip of
clickable platform badges built from the channels.socials JSONB column
(populated by scripts/backfill_socials.py from Wikidata).
"""
from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.auth import require_login
from src.filters import (
    get_global_filter, get_global_channels, get_channels_for_filter,
    get_global_color_map, get_global_color_map_dual,
    render_page_subtitle,
)

load_dotenv()
require_login()

st.title("Socials")
render_page_subtitle(
    "Other social platforms each tracked club operates on — "
    "data sourced from Wikidata"
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

# ── Apply global filter ──────────────────────────────────────
g_league, g_club = get_global_filter()
if g_club:
    rows = [g_club]
elif g_league:
    rows = [c for c in get_channels_for_filter(all_channels, g_league)
            if c.get("entity_type") == "Club"]
else:
    rows = [c for c in all_channels if c.get("entity_type") == "Club"]

# Sort by subscribers desc
rows.sort(key=lambda c: int(c.get("subscriber_count") or 0), reverse=True)

if not rows:
    st.info("No clubs match the current filter.")
    st.stop()

color_map = get_global_color_map() or {}
dual = get_global_color_map_dual() or {}

# ── Platform metadata ────────────────────────────────────────
# (key, label, brand color, text color) — matches keys written by
# scripts/backfill_socials.py.
PLATFORMS = [
    ("website",   "🌐", "#444444", "#FFFFFF"),
    ("instagram", "IG", "#E1306C", "#FFFFFF"),
    ("x",         "X",  "#000000", "#FFFFFF"),
    ("facebook",  "FB", "#1877F2", "#FFFFFF"),
    ("tiktok",    "TT", "#010101", "#FFFFFF"),
    # NB: YouTube is intentionally not rendered as a badge — the club name
    # is already a clickable link to the YT channel.
    ("threads",   "Th", "#000000", "#FFFFFF"),
    ("linkedin",  "LI", "#0A66C2", "#FFFFFF"),
    ("telegram",  "Tg", "#26A5E4", "#FFFFFF"),
    ("whatsapp",  "WA", "#25D366", "#FFFFFF"),
    ("snapchat",  "SC", "#FFFC00", "#000000"),
    ("twitch",    "Tw", "#9146FF", "#FFFFFF"),
    ("vk",        "VK", "#0077FF", "#FFFFFF"),
    ("weibo",     "Wb", "#E6162D", "#FFFFFF"),
    ("mastodon",  "Ms", "#6364FF", "#FFFFFF"),
    ("music",     "🎵", "#1DB954", "#FFFFFF"),
]


def _badges(socials: dict) -> str:
    if not socials:
        return '<span style="color:#666">—</span>'
    out = ""
    for key, label, bg, fg in PLATFORMS:
        url = socials.get(key)
        if not url:
            continue
        # Escape minimally to keep the HTML well-formed
        u = (url or "").replace('"', "&quot;")
        out += (
            f'<a href="{u}" target="_blank" rel="noopener" '
            f'title="{key}: {u}" '
            f'style="display:inline-flex;align-items:center;justify-content:center;'
            f'min-width:26px;height:22px;padding:0 7px;margin-right:4px;'
            f'border-radius:4px;background:{bg};color:{fg};font-size:11px;'
            f'font-weight:600;text-decoration:none;font-family:monospace">'
            f'{label}</a>'
        )
    return out or '<span style="color:#666">—</span>'


# ── Build table ──────────────────────────────────────────────
rows_html = ""
total_with_socials = 0
total_links = 0
for i, c in enumerate(rows, 1):
    name = c.get("name", "?")
    handle = c.get("handle", "") or ""
    yt_url = f"https://www.youtube.com/{handle}" if handle else ""
    c1, c2 = dual.get(name, (color_map.get(name, "#636EFA"), "#FFFFFF"))
    dot = (f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;'
           f'background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative">'
           f'<span style="display:block;width:7px;height:7px;border-radius:50%;'
           f'background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>')
    socials = c.get("socials") or {}
    if socials:
        total_with_socials += 1
        total_links += sum(1 for k, *_ in PLATFORMS if socials.get(k))
    name_html = (
        f'<a href="{yt_url}" target="_blank" rel="noopener" '
        f'style="color:#FAFAFA;text-decoration:none;border-bottom:1px dotted #555">'
        f'<b>{name}</b></a>'
    ) if yt_url else f"<b>{name}</b>"
    rows_html += f"""<tr>
        <td style="padding:8px 12px;text-align:right;color:#888">{i}</td>
        <td style="padding:8px 12px">{dot}</td>
        <td style="padding:8px 12px;white-space:nowrap">{name_html}</td>
        <td style="padding:8px 12px">{_badges(socials)}</td>
    </tr>"""

st.caption(
    f"**{total_with_socials}/{len(rows)}** clubs have linked socials · "
    f"{total_links} total platform link(s) tracked. "
    "If a club is missing — its Wikidata entry doesn't expose those properties yet. "
    "Re-run `scripts/backfill_socials.py` (admin) to refresh."
)

components.html(f"""
<style>
  .so {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .so th {{ padding:8px 12px; user-select:none; border-bottom:2px solid #444;
            text-align:left; color:#aaa; font-weight:600; }}
  .so td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
  .so tr:hover td {{ background:#1a1c24; }}
</style>
<div style="overflow-x:auto;width:100%">
<table class="so">
<thead><tr>
  <th style="text-align:right">#</th>
  <th></th>
  <th>Club</th>
  <th>Other platforms</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
""", height=len(rows) * 46 + 70, scrolling=False)

st.caption(
    "**How this is sourced.** Each club's Wikidata entry is queried for "
    "Twitter/X (P2002), Instagram (P2003), Facebook (P2013), TikTok (P7085), "
    "Threads (P11245), Telegram (P3789), Snapchat (P5263), Twitch (P6573), "
    "VK (P3185), Weibo (P8057), Mastodon (P4033) and Official website (P856). "
    "Click any badge to open the platform in a new tab."
)


# ── Followers leaderboard ──────────────────────────────────────────
st.markdown("---")
st.subheader("Followers across platforms")
st.caption(
    "Latest follower count per platform. YouTube comes from our daily cron; "
    "the rest from manual / browser-driven snapshots stored in "
    "`follower_snapshots`. Click any header to re-sort."
)

# Platforms to include as columns (skip non-follower-bearing ones)
_FCOLS = [
    ("youtube",   "YT", "#FF0000", "#FFFFFF"),
    ("instagram", "IG", "#E1306C", "#FFFFFF"),
    ("x",         "X",  "#000000", "#FFFFFF"),
    ("facebook",  "FB", "#1877F2", "#FFFFFF"),
    ("tiktok",    "TT", "#010101", "#FFFFFF"),
    ("threads",   "Th", "#000000", "#FFFFFF"),
    ("linkedin",  "LI", "#0A66C2", "#FFFFFF"),
    ("telegram",  "Tg", "#26A5E4", "#FFFFFF"),
    ("whatsapp",  "WA", "#25D366", "#FFFFFF"),
    ("snapchat",  "SC", "#FFFC00", "#000000"),
    ("twitch",    "Tw", "#9146FF", "#FFFFFF"),
    ("weibo",     "Wb", "#E6162D", "#FFFFFF"),
    ("vk",        "VK", "#0077FF", "#FFFFFF"),
]

_ch_ids = [c["id"] for c in rows]
_followers_map = db.get_latest_follower_snapshots_bulk(_ch_ids) if _ch_ids else {}

_lb_rows_html = ""
for i, c in enumerate(rows, 1):
    name = c.get("name", "?")
    handle = c.get("handle", "") or ""
    yt_url = f"https://www.youtube.com/{handle}" if handle else ""
    c1, c2 = dual.get(name, (color_map.get(name, "#636EFA"), "#FFFFFF"))
    dot = (f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;'
           f'background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative">'
           f'<span style="display:block;width:7px;height:7px;border-radius:50%;'
           f'background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>')
    name_html = (
        f'<a href="{yt_url}" target="_blank" rel="noopener" '
        f'style="color:#FAFAFA;text-decoration:none;border-bottom:1px dotted #555">'
        f'<b>{name}</b></a>'
    ) if yt_url else f"<b>{name}</b>"

    # Build the per-platform cells + accumulate total
    plat_cells = ""
    yt_subs = int(c.get("subscriber_count") or 0)
    snaps = _followers_map.get(c["id"], {})
    total = 0
    for key, _label, _bg, _fg in _FCOLS:
        if key == "youtube":
            n = yt_subs
        else:
            row = snaps.get(key)
            n = int(row["follower_count"]) if row else 0
        total += n
        if n > 0:
            plat_cells += (f'<td style="padding:6px 12px;text-align:right" '
                           f'data-val="{n}">{fmt_num(n)}</td>')
        else:
            plat_cells += '<td style="padding:6px 12px;text-align:right;color:#555" data-val="0">—</td>'

    total_cell = (f'<td style="padding:6px 12px;text-align:right;font-weight:600" '
                  f'data-val="{total}">{fmt_num(total)}</td>')

    _lb_rows_html += f"""<tr>
        <td style="padding:6px 12px;text-align:right;color:#888" data-val="{i}">{i}</td>
        <td style="padding:6px 12px">{dot}</td>
        <td style="padding:6px 12px;white-space:nowrap" data-val="{name}">{name_html}</td>
        {total_cell}
        {plat_cells}
    </tr>"""

# Header row — col 0=# / col 2=Club / col 3=Total / cols 4+ = platforms
_thead_extra = ""
for idx, (key, label, bg, fg) in enumerate(_FCOLS, start=4):
    title = key.capitalize()
    _thead_extra += (
        f'<th data-col="{idx}" data-type="num" '
        f'style="text-align:right;cursor:pointer" '
        f'title="{title}">'
        f'<span style="display:inline-block;min-width:22px;height:18px;'
        f'padding:0 5px;border-radius:3px;background:{bg};color:{fg};'
        f'font-size:10px;font-weight:600;line-height:18px">{label}</span></th>'
    )

_lb_h = max(len(rows), 1) * 38 + 80
components.html(f"""
<style>
  .fl {{ width:100%; border-collapse:collapse; font-size:13px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .fl th {{ padding:6px 12px; user-select:none; border-bottom:2px solid #444;
            text-align:right; color:#aaa; font-weight:600; }}
  .fl th[data-col] {{ cursor:pointer; }}
  .fl th[data-col]:hover {{ color:#636EFA; }}
  .fl td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
  .fl tr:hover td {{ background:#1a1c24; }}
  .fl .active {{ color:#636EFA; }}
</style>
<div style="overflow-x:auto;width:100%">
<table class="fl">
<thead><tr>
  <th data-col="0" data-type="num" style="text-align:right">#</th>
  <th></th>
  <th data-col="2" data-type="str" style="text-align:left">Club</th>
  <th data-col="3" data-type="num" style="text-align:right;color:#FAFAFA">Total</th>
  {_thead_extra}
</tr></thead>
<tbody>{_lb_rows_html}</tbody>
</table>
</div>
<script>
(function() {{
  const table = document.querySelector('.fl');
  const tbody = table.querySelector('tbody');
  const headers = table.querySelectorAll('th[data-col]');
  let currentCol = -1, currentAsc = false;  // overridden by initial sort() below
  function sort(colIdx, type) {{
    const trs = Array.from(tbody.rows);
    const isStr = type === 'str';
    if (colIdx === currentCol) currentAsc = !currentAsc;
    else {{ currentCol = colIdx; currentAsc = isStr; }}
    trs.sort((a, b) => {{
      const va = a.cells[colIdx].dataset.val || '';
      const vb = b.cells[colIdx].dataset.val || '';
      let cmp = isStr ? va.localeCompare(vb) : ((parseFloat(va)||0) - (parseFloat(vb)||0));
      return currentAsc ? cmp : -cmp;
    }});
    trs.forEach(r => tbody.appendChild(r));
    headers.forEach(h => h.classList.remove('active'));
    table.querySelector('th[data-col="' + colIdx + '"]').classList.add('active');
  }}
  headers.forEach(h => h.addEventListener('click',
    () => sort(parseInt(h.dataset.col), h.dataset.type || 'num')));
  // Initial sort: YouTube desc
  sort(3, 'num');
  function _fit() {{
    const h = document.documentElement.scrollHeight + 8;
    window.parent.postMessage({{type:'streamlit:setFrameHeight', height: h}}, '*');
  }}
  new ResizeObserver(_fit).observe(document.body);
  _fit();
}})();
</script>
""", height=_lb_h, scrolling=False)

st.caption(
    "Follower numbers shown are the most recent value in `follower_snapshots`. "
    "Re-run a benchmark on the **Data** page (admin) to refresh."
)
