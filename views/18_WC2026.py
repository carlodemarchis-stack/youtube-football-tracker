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
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, fmt_date, kpi_row
from src.auth import require_login
from src.dot import dual_dot

load_dotenv()
require_login()

st.title("FIFA World Cup 2026")
st.caption("48 federation YouTube channels — one per qualified team. "
            "Tagged via competitions.wc2026 on each channel.")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = _cached_channels(db)
wc_all = [c for c in all_channels
          if (c.get("competitions") or {}).get("wc2026")]
# Split governing bodies (FIFA + 6 confederations) from team channels.
# Governing bodies have their own entity_type now ("GoverningBody")
# alongside Club / League / Federation / Player / Media / Other.
gov = [c for c in wc_all if c.get("entity_type") == "GoverningBody"]
wc  = [c for c in wc_all if c not in gov]

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


# ── KPI strip ─────────────────────────────────────────────────────
total_subs   = sum(int(c.get("subscriber_count") or 0) for c in wc)
total_views  = sum(int(c.get("total_views") or 0)      for c in wc)
total_videos = sum(int(c.get("video_count") or 0)      for c in wc)
confeds      = sorted({_wc(c).get("confederation") for c in wc
                       if _wc(c).get("confederation")})

st.markdown(kpi_row([
    ("Teams in snapshot", str(len(wc)), ""),
    ("Confederations",    str(len(confeds)), " · ".join(confeds)),
    ("Total subscribers", fmt_num(total_subs), ""),
    ("Total views",       fmt_num(total_views), ""),
    ("Total videos",      fmt_num(total_videos), ""),
]), unsafe_allow_html=True)


# ── Governing bodies (FIFA + 6 confederations) ───────────────────
if gov:
    st.markdown(
        "<div style='font-size:11px;color:#888;text-transform:uppercase;"
        "letter-spacing:0.5px;margin:24px 0 8px 0'>"
        "🏛️ Governing bodies</div>",
        unsafe_allow_html=True,
    )
    # Sort: FIFA first, then by subs desc
    gov_sorted = sorted(
        gov,
        key=lambda c: (
            0 if (c["competitions"]["wc2026"].get("team") == "FIFA") else 1,
            -(int(c.get("subscriber_count") or 0))
        ),
    )
    chips = []
    for c in gov_sorted:
        w = c["competitions"]["wc2026"]
        label = w.get("team") or c.get("name") or "—"
        subs = int(c.get("subscriber_count") or 0)
        handle = (c.get("handle") or "").lstrip("@")
        yt_id = c.get("youtube_channel_id") or ""
        yt_url = (f"https://www.youtube.com/@{handle}" if handle
                   else f"https://www.youtube.com/channel/{yt_id}")
        # Dual-dot from the channel's logo colors (set in TEAM_COLORS)
        c1 = c.get("color") or "#636EFA"
        c2 = c.get("color2") or "#FFFFFF"
        dot = dual_dot(c1, c2, 14)
        chips.append(
            f"<a href='{yt_url}' target='_blank' rel='noopener' "
            f"style='display:inline-block;background:#1a1c24;"
            f"border:1px solid #2a2c34;border-radius:6px;"
            f"padding:10px 14px;margin:0 8px 8px 0;"
            f"text-decoration:none;min-width:130px'>"
            f"<div style='display:flex;align-items:center;gap:8px'>"
            f"{dot}"
            f"<span style='color:#FAFAFA;font-weight:600;font-size:14px'>{label}</span>"
            f"</div>"
            f"<div style='color:#888;font-size:11px;margin-top:4px;margin-left:22px'>"
            f"{fmt_num(subs) + ' subs' if subs else '—'}</div>"
            f"</a>"
        )
    st.markdown("".join(chips), unsafe_allow_html=True)
    st.markdown(
        "<div style='font-size:11px;color:#888;margin-top:6px;margin-bottom:24px'>"
        "Click any tile to open the channel on YouTube.</div>",
        unsafe_allow_html=True,
    )


st.markdown(
    "<div style='font-size:11px;color:#888;text-transform:uppercase;"
    "letter-spacing:0.5px;margin:8px 0 8px 0'>"
    "🏆 48 qualified teams</div>",
    unsafe_allow_html=True,
)

# ── Table — one row per team, sortable like the All Channels view ─
# Each cell carries data-val so the JS sort uses raw numbers, not
# the formatted display text.
rows_html = []
# Sort default: by group then team
def _sort_key(c):
    w = _wc(c)
    return (w.get("group") or "Z", w.get("team") or c.get("name") or "")
wc_sorted = sorted(wc, key=_sort_key)

for c in wc_sorted:
    w = _wc(c)
    yt_id = c.get("youtube_channel_id") or ""
    handle = (c.get("handle") or "").lstrip("@")
    yt_url = (f"https://www.youtube.com/@{handle}" if handle
               else f"https://www.youtube.com/channel/{yt_id}")
    team = w.get("team") or c.get("country") or c.get("name") or "—"
    name = c.get("name") or "—"
    grp = w.get("group") or "—"
    cf = w.get("confederation") or "—"
    subs = int(c.get("subscriber_count") or 0)
    views = int(c.get("total_views") or 0)
    videos = int(c.get("video_count") or 0)
    longs = int(c.get("long_form_count") or 0)
    shorts = int(c.get("shorts_count") or 0)
    lives = int(c.get("live_count") or 0)
    last_fetched = c.get("last_fetched") or ""

    def td(val_sort, content, *, align="right"):
        v = "" if val_sort is None else str(val_sort)
        return f"<td style='text-align:{align}' data-val=\"{v}\">{content}</td>"

    flag = TEAM_FLAG.get(team, "")
    team_with_flag = (f"<span style='display:inline-block;width:24px;"
                      f"text-align:center;margin-right:8px'>{flag}</span>"
                      f"{team}") if flag else team

    rows_html.append(
        "<tr>"
        + td(team, team_with_flag, align="left")
        + td(grp, grp, align="center")
        + td(cf, cf, align="left")
        + (f"<td style='text-align:left' data-val=\"{name.lower()}\">"
            f"<a href='{yt_url}' target='_blank' rel='noopener' "
            f"style='color:#FAFAFA;text-decoration:none'>{name}</a></td>")
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

# Column metadata for the sort JS — (label, type, align)
COLS = [
    ("Team",          "str", "left"),
    ("Group",         "str", "center"),
    ("Confederation", "str", "left"),
    ("Channel",       "str", "left"),
    ("Subs",          "num", "right"),
    ("Views",         "num", "right"),
    ("Videos",        "num", "right"),
    ("Long",          "num", "right"),
    ("Shorts",        "num", "right"),
    ("Live",          "num", "right"),
    ("Updated",       "str", "right"),
]
th_html = "".join(
    f"<th data-col='{i}' data-type='{tp}' "
    f"style='text-align:{al};cursor:pointer'>{lbl}</th>"
    for i, (lbl, tp, al) in enumerate(COLS)
)

table_html = (
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
    "<div class='wc-wrap'>"
    "<table class='wc-tbl'>"
    "<thead><tr style='border-bottom:2px solid #444'>"
    + th_html +
    "</tr></thead>"
    f"<tbody>{''.join(rows_html)}</tbody></table></div>"
    "<script>(function(){"
    "const t=document.querySelector('.wc-tbl');"
    "const tb=t.querySelector('tbody');"
    "const hs=t.querySelectorAll('th[data-col]');"
    "let cur=4,asc=false;"
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
_components.html(table_html, height=iframe_h, scrolling=True)

st.caption(
    "Click any column header to sort. Subscriber / video stats are "
    "pulled by the standard daily_federations cron — last refresh "
    "shown in the Updated column. "
    "Source CSV: data/wc2026_federation_youtube.csv."
)
