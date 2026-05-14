"""FIFA World Cup 2026 — federation YouTube channels of the 48 teams.

Reads channels tagged with `competitions.wc2026` (added via
scripts/import_wc2026.py after migration_v21.sql). Groups them by
confederation; shows the basic YouTube stats we already cache.

Deliberately isolated from the rest of the app:
- Doesn't appear in the global League/Club filter
- No per-team detail page — the channel cell links to YouTube
"""
from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as _components
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import (
    get_all_channels as _cached_channels,
    read_dashboard_cache as _cached_dc_read,
)
from src.analytics import fmt_num, fmt_date, kpi_row
from src.auth import require_login
from src.dot import dual_dot

load_dotenv()
require_login()

st.title("FIFA World Cup 2026")
st.caption(
    "Official YouTube channels of the 48 qualified national teams, "
    "plus FIFA and the 6 confederations. When a country runs more than "
    "one official channel (e.g. a federation channel and a team-brand "
    "channel), the stats are summed into the country's row — the +N alt "
    "chip shows how many extra channels were rolled in, and each one is "
    "listed individually in the expander below the table."
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
# Cache-first read. dashboard_cache.wc2026 is rebuilt at write-time
# (rebuild_all + the standalone scripts/refresh_wc2026.py) — the page
# just paints. Live-channel fallback below covers cold-cache state.
from src.dashboard_cache import scope_all as _scope_all
_cached = _cached_dc_read(db, "wc2026", _scope_all())
if _cached and (_cached.get("payload") or {}).get("rows"):
    wc = (_cached["payload"] or {}).get("rows") or []
    _cache_age = _cached.get("computed_at") or ""
else:
    all_channels = _cached_channels(db)
    wc = [c for c in all_channels
          if (c.get("competitions") or {}).get("wc2026")]
    _cache_age = ""

if not wc:
    st.warning(
        "No channels with `competitions.wc2026` set yet.\n\n"
        "**Steps to populate:**\n"
        "1. Run `migration_v21.sql` in Supabase (adds the `competitions` JSONB column).\n"
        "2. Run `python3 scripts/import_wc2026.py` to import the CSV.\n"
        "3. Refresh this page."
    )
    st.stop()


def _wc(c):
    return c.get("competitions", {}).get("wc2026", {}) or {}


# Team → flag emoji. Keyed by the `team` value in the WC2026 CSV.
# England + Scotland use the ISO 3166-2 subdivision tag sequence
# (rather than the UK flag) — they qualify separately.
TEAM_FLAG = {
    # CONCACAF
    "Mexico": "🇲🇽", "United States": "🇺🇸", "Canada": "🇨🇦",
    "Haiti": "🇭🇹", "Panama": "🇵🇦", "Curaçao": "🇨🇼",
    # CONMEBOL
    "Argentina": "🇦🇷", "Brazil": "🇧🇷", "Uruguay": "🇺🇾",
    "Ecuador": "🇪🇨", "Colombia": "🇨🇴", "Paraguay": "🇵🇾",
    # UEFA
    "England": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "France": "🇫🇷", "Germany": "🇩🇪", "Spain": "🇪🇸",
    "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "Belgium": "🇧🇪",
    "Croatia": "🇭🇷", "Switzerland": "🇨🇭", "Norway": "🇳🇴",
    "Scotland": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
    "Austria": "🇦🇹", "Czechia": "🇨🇿",
    "Bosnia and Herzegovina": "🇧🇦", "Sweden": "🇸🇪", "Türkiye": "🇹🇷",
    # CAF
    "Morocco": "🇲🇦", "Egypt": "🇪🇬", "Algeria": "🇩🇿",
    "Ghana": "🇬🇭", "Ivory Coast": "🇨🇮", "Tunisia": "🇹🇳",
    "Senegal": "🇸🇳", "South Africa": "🇿🇦",
    "DR Congo": "🇨🇩", "Cape Verde": "🇨🇻",
    # AFC
    "Iran": "🇮🇷", "Iraq": "🇮🇶", "Saudi Arabia": "🇸🇦",
    "Jordan": "🇯🇴", "Qatar": "🇶🇦", "Uzbekistan": "🇺🇿",
    "Japan": "🇯🇵", "South Korea": "🇰🇷",
    # OFC
    "Australia": "🇦🇺", "New Zealand": "🇳🇿",
}


# ── Split channels by role ────────────────────────────────────────
# - primary  : team + governing_body channels — appear in the main table
# - alts     : federation channels for countries that already have a
#              team-brand primary — surfaced via a "+N alt" chip and
#              an expander below the table
def _role(c):
    return (_wc(c) or {}).get("role") or "team"

primary    = [c for c in wc if _role(c) in ("team", "governing_body")]
alts       = [c for c in wc if _role(c) not in ("team", "governing_body")]
# Index alts by team name so a row can show its alt channels.
from collections import defaultdict
alts_by_team: dict[str, list] = defaultdict(list)
for c in alts:
    team = _wc(c).get("team")
    if team:
        alts_by_team[team].append(c)

# ── KPI strip ─────────────────────────────────────────────────────
# Include alts so the global totals match the per-row sums shown
# in the table (alts roll into the primary row).
total_subs   = sum(int(c.get("subscriber_count") or 0) for c in wc)
total_views  = sum(int(c.get("total_views") or 0)      for c in wc)
total_videos = sum(int(c.get("video_count") or 0)      for c in wc)
n_teams      = sum(1 for c in primary if c.get("entity_type") != "GoverningBody")
n_gov        = sum(1 for c in primary if c.get("entity_type") == "GoverningBody")
avg_vpv      = (total_views // total_videos) if total_videos else 0
ch_sub = (f"{n_teams} teams + FIFA + {max(n_gov - 1, 0)} confederations"
          if n_gov else f"{n_teams} teams")
if alts:
    ch_sub += f" · +{len(alts)} alt"

st.markdown(kpi_row([
    # Total = primary (teams + FIFA + 6 confederations) + alt channels.
    # Subscribers / Views / Videos already sum over `wc` (primary + alts).
    ("Channels", str(len(wc)), ch_sub),
    ("Subscribers", fmt_num(total_subs), ""),
    ("Views",       fmt_num(total_views), ""),
    ("Videos",      fmt_num(total_videos), ""),
    ("Views / video", fmt_num(avg_vpv), ""),
]), unsafe_allow_html=True)


# ── Table — one row per channel (48 teams + 7 governing bodies),
#    sortable like the All Channels view ─
# ── Table — one row per team, sortable like the All Channels view ─
# Each cell carries data-val so the JS sort uses raw numbers, not
# the formatted display text.
def td(val_sort, content, *, align="right"):
    v = "" if val_sort is None else str(val_sort)
    return f"<td style='text-align:{align}' data-val=\"{v}\">{content}</td>"


def _channel_row_html(c, *, show_alt_chip=True):
    """Render a single <tr> for a channel — shared between primary and alts."""
    w = _wc(c)
    yt_id = c.get("youtube_channel_id") or ""
    handle = (c.get("handle") or "").lstrip("@")
    # Explicit override in competitions.wc2026.youtube_url wins
    # (used for channels where the @handle URL doesn't resolve
    # to the right destination — e.g. legacy /TFF style).
    yt_url = (
        w.get("youtube_url")
        or (f"https://www.youtube.com/@{handle}" if handle
            else f"https://www.youtube.com/channel/{yt_id}")
    )
    team = w.get("team") or c.get("country") or c.get("name") or "—"
    is_gov = c.get("entity_type") == "GoverningBody"
    cf = ("" if is_gov else (w.get("confederation") or "—"))
    subs = int(c.get("subscriber_count") or 0)
    views = int(c.get("total_views") or 0)
    videos = int(c.get("video_count") or 0)
    longs = int(c.get("long_form_count") or 0)
    shorts = int(c.get("shorts_count") or 0)
    lives = int(c.get("live_count") or 0)
    last_fetched = c.get("last_fetched") or ""

    # Roll alt-channel stats into the primary row so the table shows
    # combined reach per country. Only on primary rows (the alts
    # expander still shows each channel individually).
    role = w.get("role") or "team"
    if show_alt_chip and role in ("team", "governing_body"):
        for a in alts_by_team.get(team, []):
            subs   += int(a.get("subscriber_count") or 0)
            views  += int(a.get("total_views")      or 0)
            videos += int(a.get("video_count")      or 0)
            longs  += int(a.get("long_form_count")  or 0)
            shorts += int(a.get("shorts_count")     or 0)
            lives  += int(a.get("live_count")       or 0)

    if is_gov:
        c1 = c.get("color") or "#636EFA"
        c2 = c.get("color2") or "#FFFFFF"
        marker = dual_dot(c1, c2, 14)
    else:
        flag = TEAM_FLAG.get(team, "")
        marker = (f"<span style='display:inline-block;width:14px;"
                   f"text-align:center'>{flag}</span>") if flag else ""

    # Display label: for alts (role=federation) use the actual channel
    # name so users can tell them apart from the primary row.
    display_label = team if role in ("team", "governing_body") else (
        c.get("name") or team)

    n_alt = 0 if (is_gov or not show_alt_chip) else len(alts_by_team.get(team, []))
    alt_chip = (f" <span title='{n_alt} alternate official channel"
                 f"{'' if n_alt == 1 else 's'} for {team} — see expander below' "
                 f"style='background:#1a1c24;border:1px solid #2a2c34;"
                 f"border-radius:3px;padding:1px 6px;color:#888;"
                 f"font-size:11px;margin-left:6px'>+{n_alt} alt</span>"
                ) if n_alt else ""

    team_cell = (
        f"<td style='text-align:left' data-val=\"{display_label.lower()}\">"
        f"<div style='display:flex;align-items:center;gap:8px'>"
        f"{marker}"
        f"<a href='{yt_url}' target='_blank' rel='noopener' "
        f"style='color:#FAFAFA;text-decoration:none'>{display_label}</a>"
        f"{alt_chip}"
        f"</div></td>"
    )

    return (
        "<tr>"
        + team_cell
        + td(cf, cf, align="left")
        + td(subs,   fmt_num(subs))
        + td(views,  fmt_num(views))
        + td(videos, fmt_num(videos))
        + td(longs,  fmt_num(longs))
        + td(shorts, fmt_num(shorts))
        + td(lives,  fmt_num(lives))
        + td(last_fetched, fmt_date(last_fetched) if last_fetched else "—",
              align="right")
        + "</tr>"
    )


# Initial order: subscribers desc — JS-side default sort lands on
# the same column once it renders, so this just prevents a flash
# of unsorted rows.
wc_sorted = sorted(primary,
                    key=lambda c: -(int(c.get("subscriber_count") or 0)))
rows_html = [_channel_row_html(c) for c in wc_sorted]

# Column metadata for the sort JS — (label, type, align).
# Team column carries both the dot/flag and the clickable channel
# name (no separate "YouTube channel" column).
COLS = [
    ("Team",            "str", "left"),
    ("Confederation",   "str", "left"),
    ("Subscribers",     "num", "right"),
    ("Total views",     "num", "right"),
    ("Videos",          "num", "right"),
    ("Long-form",       "num", "right"),
    ("Shorts",          "num", "right"),
    ("Live streams",    "num", "right"),
    ("Last updated",    "str", "right"),
]
th_html = "".join(
    f"<th data-col='{i}' data-type='{tp}' "
    f"style='text-align:{al};cursor:pointer'>{lbl}</th>"
    for i, (lbl, tp, al) in enumerate(COLS)
)

_TABLE_CSS = (
    "<style>"
    "body{margin:0;background:#0E1117;color:#FAFAFA;"
    "font-family:'Source Sans Pro',sans-serif}"
    ".wc-wrap{overflow-x:auto;width:100%}"
    ".wc-tbl{width:100%;border-collapse:collapse;border:0;font-size:14px;"
    "color:#FAFAFA;background:transparent}"
    ".wc-tbl th,.wc-tbl td{border-left:0;border-right:0;border-top:0;"
    "padding:6px 12px;white-space:nowrap}"
    ".wc-tbl th{user-select:none;font-weight:600;color:#FAFAFA;border-bottom:0}"
    ".wc-tbl th[data-col]:hover{color:#636EFA}"
    ".wc-tbl th.active{color:#636EFA}"
    ".wc-tbl td{border-bottom:1px solid #262730}"
    ".wc-tbl tr:hover td{background:#1a1c24}"
    ".wc-tbl a{color:inherit;text-decoration:none}"
    "</style>"
)


def _render_wc_table(rows_html: list[str], table_id: str) -> str:
    return (
        _TABLE_CSS
        + f"<div class='wc-wrap'><table class='wc-tbl' id='{table_id}'>"
        "<thead><tr style='border-bottom:2px solid #444'>"
        + th_html +
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
        "<script>(function(){"
        f"const t=document.getElementById('{table_id}');"
        "const tb=t.querySelector('tbody');"
        "const hs=t.querySelectorAll('th[data-col]');"
        "let cur=2,asc=false;"
        "function refresh(){"
            "const rows=Array.from(tb.rows);"
            "const isStr=hs[cur].dataset.type==='str';"
            "rows.sort((a,b)=>{"
                "const va=a.cells[cur].dataset.val||'';"
                "const vb=b.cells[cur].dataset.val||'';"
                "let c=isStr?va.localeCompare(vb,undefined,{sensitivity:'base'})"
                         ":(parseFloat(va)||0)-(parseFloat(vb)||0);"
                "return asc?c:-c;"
            "});"
            "rows.forEach(r=>tb.appendChild(r));"
            "hs.forEach(h=>{h.classList.remove('active');h.textContent=h.textContent.replace(/ [▲▼]/g,'');});"
            "hs[cur].classList.add('active');"
            "hs[cur].textContent+=asc?' ▲':' ▼';"
        "}"
        "hs.forEach((h,i)=>{h.addEventListener('click',()=>{"
            "if(i===cur)asc=!asc;else{cur=i;asc=hs[i].dataset.type==='str';}"
            "refresh();"
        "});});"
        "refresh();"
        "})();</script>"
    )


iframe_h = min(2000, 36 * len(wc_sorted) + 80)
_components.html(_render_wc_table(rows_html, "wc-tbl-primary"),
                  height=iframe_h, scrolling=True)

st.caption(
    "Click any column header to sort. Click a channel name to open "
    "it on YouTube."
)

# ── Alternate channels (federation + team where both exist) ──────
if alts:
    with st.expander(f"+{len(alts)} alternate official channels "
                      f"({', '.join(sorted(alts_by_team.keys()))})",
                      expanded=False):
        st.caption(
            "Some federations run two channels — one for the organization "
            "(federation news, all national teams) and one for the senior "
            "men's team. The main table above shows the team-brand channel; "
            "the federation channel for the same country is listed here."
        )
        alts_sorted = sorted(
            alts, key=lambda x: -(int(x.get("subscriber_count") or 0)))
        alt_rows_html = [_channel_row_html(c, show_alt_chip=False)
                         for c in alts_sorted]
        alt_h = min(2000, 36 * len(alts_sorted) + 80)
        _components.html(_render_wc_table(alt_rows_html, "wc-tbl-alts"),
                          height=alt_h, scrolling=True)
