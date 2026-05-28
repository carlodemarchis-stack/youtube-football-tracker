"""🏈 NFL — All Channels (hidden v0).

Channel-level surface only — no videos, no per-video snapshots, no
trends. 33 channels (32 franchises + @NFL HQ).

Visual structure mirrors views/18_WC2026.py:
  - KPI strip
  - 🏟 By conference summary table (AFC / NFC / NFL HQ)
  - 📋 All channels detail table (HTML + JS sorting)

Hidden from the sidebar nav (see app.py — registered but the link is
CSS-hidden). Reached only by URL `/nfl`.

Forward-compatibility: see docs/NFL_V0.md. The file name `20_NFL.py`
reserves the `20*` numbering for WC2026-style future expansion
(`20a_NFL_Daily_Recap.py`, etc.).
"""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from src import components_compat as _components
from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.dot import dual_dot
from src import theme as _T

load_dotenv()
require_login()

st.title("🏈 NFL — All Channels")
st.markdown(
    f'<div style="background:{_T.SURFACE};border-left:3px solid {_T.WARN};'
    'padding:10px 14px;margin:6px 0 14px 0;border-radius:4px;'
    f'font-size:13px;line-height:1.55;color:{_T.TEXT}">'
    f'<span style="color:{_T.WARN};font-weight:600">🚧 NFL preview · pre-alpha</span>'
    ' &nbsp;—&nbsp; channel-level snapshot only. No videos / no trends '
    'yet. Hidden from the sidebar; reachable by direct URL.'
    '</div>', unsafe_allow_html=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
nfl = [c for c in _cached_channels(db) if c.get("entity_type") == "NFL"]
if not nfl:
    st.info("No NFL channels in the DB yet — run "
            "`python3 scripts/import_nfl.py` to seed the roster.")
    st.stop()


def _nfl(c):
    return (c.get("competitions") or {}).get("nfl") or {}


def _is_hq(c) -> bool:
    """The @NFL HQ row — has no conference/division (set to "—" at
    import time). Treated as its own bucket so it doesn't skew the
    AFC/NFC summary."""
    return (_nfl(c).get("conference") or "—") == "—"


# ── KPIs ──────────────────────────────────────────────────────────
total_subs   = sum(int(c.get("subscriber_count") or 0) for c in nfl)
total_views  = sum(int(c.get("total_views")      or 0) for c in nfl)
total_videos = sum(int(c.get("video_count")      or 0) for c in nfl)
n_teams      = sum(1 for c in nfl if not _is_hq(c))
n_hq         = sum(1 for c in nfl if _is_hq(c))
avg_vpv      = (total_views // total_videos) if total_videos else 0
ch_sub = f"{n_teams} franchises" + (f" + {n_hq} HQ" if n_hq else "")

st.markdown(kpi_row([
    ("📡 Channels",      str(len(nfl)),       ch_sub),
    ("👥 Subscribers",   fmt_num(total_subs), ""),
    ("👁️ Views",         fmt_num(total_views), ""),
    ("🎬 Videos",        fmt_num(total_videos), ""),
    ("🎯 Views / video", fmt_num(avg_vpv),    ""),
]), unsafe_allow_html=True)


# ── Shared table helpers (lifted from views/18_WC2026.py) ─────────
def td(val_sort, content, *, align="right"):
    v = "" if val_sort is None else str(val_sort)
    return f"<td style='text-align:{align}' data-val=\"{v}\">{content}</td>"


_TABLE_CSS = (
    "<style>"
    f"body{{margin:0;background:{_T.BG};color:{_T.TEXT};"
    "font-family:'Source Sans Pro',sans-serif}"
    ".nfl-wrap{overflow-x:auto;width:100%}"
    ".nfl-tbl{width:100%;border-collapse:collapse;border:0;font-size:14px;"
    f"color:{_T.TEXT};background:transparent}}"
    ".nfl-tbl th,.nfl-tbl td{border-left:0;border-right:0;border-top:0;"
    "padding:6px 12px;white-space:nowrap}"
    f".nfl-tbl th{{user-select:none;font-weight:600;color:{_T.TEXT};border-bottom:0}}"
    f".nfl-tbl th[data-col]:hover{{color:{_T.ACCENT}}}"
    f".nfl-tbl th.active{{color:{_T.ACCENT}}}"
    f".nfl-tbl td{{border-bottom:1px solid {_T.BORDER}}}"
    f".nfl-tbl tr:hover td{{background:{_T.SURFACE}}}"
    ".nfl-tbl a{color:inherit;text-decoration:none}"
    "</style>"
)


def _sort_js(table_id: str, default_col: int = 2) -> str:
    return (
        "<script>(function(){"
        f"const t=document.getElementById('{table_id}');"
        "const tb=t.querySelector('tbody');"
        "const hs=t.querySelectorAll('th[data-col]');"
        f"let cur={default_col},asc=false;"
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


# ── Conference / HQ markers ───────────────────────────────────────
# Brand colours — §1 data-not-theme exception (matches the
# _CONF_DUAL pattern in views/18_WC2026.py).
_CONF_DUAL = {
    "AFC": ("#CC0000", "#FFFFFF"),  # AFC red
    "NFC": ("#013369", "#FFFFFF"),  # NFC blue
    "—":   ("#013369", "#D50A0A"),  # NFL shield colours (HQ)
}


def _conf_marker(name: str) -> str:
    c1, c2 = _CONF_DUAL.get(name, (_T.ACCENT, _T.WHITE))
    return dual_dot(c1, c2, 14, inline=True)


# ── 🏟 By conference summary table ────────────────────────────────
_CF_COLS = [
    ("Conference",    "str", "left"),
    ("Channels",      "num", "right"),
    ("Subscribers",   "num", "right"),
    ("Total views",   "num", "right"),
    ("Views / Sub",   "num", "right"),
    ("Videos",        "num", "right"),
    ("Long",          "num", "right"),
    ("Shorts",        "num", "right"),
    ("Live",          "num", "right"),
    ("Views / Video", "num", "right"),
]
_cf_th = "".join(
    f"<th data-col='{i}' data-type='{tp}' "
    f"style='text-align:{al};cursor:pointer'>{lbl}</th>"
    for i, (lbl, tp, al) in enumerate(_CF_COLS)
)

_cf: dict[str, dict] = {}
for _c in nfl:
    _k = (_nfl(_c).get("conference") or "—")
    # Show HQ as "NFL HQ" in the summary so it reads naturally.
    _label = "NFL HQ" if _k == "—" else _k
    _s = _cf.setdefault(_label, {
        "key": _k, "ch": 0, "subs": 0, "v": 0, "vid": 0,
        "lo": 0, "sh": 0, "li": 0,
    })
    _s["ch"]   += 1
    _s["subs"] += int(_c.get("subscriber_count") or 0)
    _s["v"]    += int(_c.get("total_views")      or 0)
    _s["vid"]  += int(_c.get("video_count")      or 0)
    _s["lo"]   += int(_c.get("long_form_count")  or 0)
    _s["sh"]   += int(_c.get("shorts_count")     or 0)
    _s["li"]   += int(_c.get("live_count")       or 0)

st.subheader("🏟 By conference")
_cf_rows = []
for _name, _s in sorted(_cf.items(), key=lambda kv: -kv[1]["subs"]):
    _vps = (_s["v"] // _s["subs"]) if _s["subs"] else 0
    _vpv = (_s["v"] // _s["vid"])  if _s["vid"]  else 0
    _cf_rows.append(
        "<tr>"
        + td(_name,
              f"<div style='display:flex;align-items:center;gap:8px'>"
              f"{_conf_marker(_s['key'])}<span>{_name}</span></div>",
              align="left")
        + td(_s["ch"],   fmt_num(_s["ch"]))
        + td(_s["subs"], fmt_num(_s["subs"]))
        + td(_s["v"],    fmt_num(_s["v"]))
        + td(_vps,       fmt_num(_vps))
        + td(_s["vid"],  fmt_num(_s["vid"]))
        + td(_s["lo"],   fmt_num(_s["lo"]))
        + td(_s["sh"],   fmt_num(_s["sh"]))
        + td(_s["li"],   fmt_num(_s["li"]))
        + td(_vpv,       fmt_num(_vpv))
        + "</tr>"
    )
_cf_h = min(700, 36 * len(_cf_rows) + 52)
_components.html(
    _TABLE_CSS
    + "<div class='nfl-wrap'><table class='nfl-tbl' id='nfl-tbl-conf'>"
    f"<thead><tr style='border-bottom:2px solid {_T.BORDER_STRONG}'>"
    + _cf_th + "</tr></thead>"
    f"<tbody>{''.join(_cf_rows)}</tbody></table></div>"
    + _sort_js("nfl-tbl-conf", default_col=2),
    height=_cf_h, scrolling=True,
)
st.caption(
    "Aggregated from the NFL channel stats we collect "
    "(subscribers / views / videos / format split). Click a header to sort."
)


# ── 📋 All channels detail table ──────────────────────────────────
def _row_html(c) -> str:
    n = _nfl(c)
    yt_id = c.get("youtube_channel_id") or ""
    handle = (c.get("handle") or "").lstrip("@")
    yt_url = (f"https://www.youtube.com/@{handle}" if handle
              else f"https://www.youtube.com/channel/{yt_id}")
    team = c.get("name") or "—"
    conf = n.get("conference") or "—"
    div  = n.get("division")   or "—"
    subs   = int(c.get("subscriber_count") or 0)
    views  = int(c.get("total_views")      or 0)
    videos = int(c.get("video_count")      or 0)
    longs  = int(c.get("long_form_count")  or 0)
    shorts = int(c.get("shorts_count")     or 0)
    lives  = int(c.get("live_count")       or 0)
    vpv    = (views / videos) if videos else 0

    team_cell = (
        f"<td style='text-align:left' data-val=\"{team.lower()}\">"
        f"<div style='display:flex;align-items:center;gap:8px'>"
        f"{_conf_marker(conf)}"
        f"<a href='{yt_url}' target='_blank' rel='noopener' "
        f"style='color:{_T.TEXT};text-decoration:none'>{team}</a>"
        f"</div></td>"
    )

    return (
        "<tr>"
        + team_cell
        + td(conf,   conf, align="left")
        + td(div,    div,  align="left")
        + td(subs,   fmt_num(subs))
        + td(views,  fmt_num(views))
        + td(videos, fmt_num(videos))
        + td(longs,  fmt_num(longs))
        + td(shorts, fmt_num(shorts))
        + td(lives,  fmt_num(lives))
        + td(int(vpv), fmt_num(int(vpv)))
        + "</tr>"
    )


_COLS = [
    ("Team",          "str", "left"),
    ("Conference",    "str", "left"),
    ("Division",      "str", "left"),
    ("Subscribers",   "num", "right"),
    ("Total views",   "num", "right"),
    ("Videos",        "num", "right"),
    ("Long-form",     "num", "right"),
    ("Shorts",        "num", "right"),
    ("Live",          "num", "right"),
    ("Views / Video", "num", "right"),
]
_th_html = "".join(
    f"<th data-col='{i}' data-type='{tp}' "
    f"style='text-align:{al};cursor:pointer'>{lbl}</th>"
    for i, (lbl, tp, al) in enumerate(_COLS)
)

st.subheader("📋 All channels")
ordered = sorted(nfl, key=lambda c: -(int(c.get("subscriber_count") or 0)))
rows_html = [_row_html(c) for c in ordered]
iframe_h = min(2400, 31 * len(ordered) + 44)
_components.html(
    _TABLE_CSS
    + "<div class='nfl-wrap'><table class='nfl-tbl' id='nfl-tbl-main'>"
    f"<thead><tr style='border-bottom:2px solid {_T.BORDER_STRONG}'>"
    + _th_html + "</tr></thead>"
    f"<tbody>{''.join(rows_html)}</tbody></table></div>"
    + _sort_js("nfl-tbl-main", default_col=3),
    height=iframe_h, scrolling=True,
)

st.caption(
    "Click any column header to sort. Click a team name to open the "
    "channel on YouTube. Conference + Division are stored in the "
    "database for future grouping; v0 keeps the flat list."
)
