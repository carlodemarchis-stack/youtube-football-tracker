"""🏈 NFL — All Channels (hidden v0).

Channel-level surface only — no videos, no per-video snapshots, no
trends. 33 channels (32 franchises + @NFL HQ).

Visual structure mirrors the Top-5 "All Channels" surface
(views/2_Clubs.py landing) using only the data the daily NFL channel
snapshot collects: subs, total views, video_count, long/shorts/live
counts, lifetime_long/short/live views, launched_at.

No aggregation by conference/division (kept as per-row columns only).
Single channel selector at the top filters all sections.

Hidden from the sidebar nav (see app.py — registered but the link is
CSS-hidden). Reached only by URL `/nfl`.

Forward-compatibility: see docs/NFL_V0.md. The file name `20_NFL.py`
reserves the `20*` numbering for WC2026-style future expansion.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv

from src import components_compat as _components
from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.charts import chart_title
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
nfl_all = [c for c in _cached_channels(db) if c.get("entity_type") == "NFL"]
if not nfl_all:
    st.info("No NFL channels in the DB yet — run "
            "`python3 scripts/import_nfl.py` to seed the roster.")
    st.stop()


def _nfl(c):
    return (c.get("competitions") or {}).get("nfl") or {}


def _is_hq(c) -> bool:
    return (_nfl(c).get("conference") or "—") == "—"


# ── Single channel filter ─────────────────────────────────────────
_options = ["All channels"] + (
    [c["name"] for c in nfl_all if _is_hq(c)]
    + sorted(c["name"] for c in nfl_all if not _is_hq(c))
)
_pick = st.selectbox("Channel", _options, index=0, key="nfl_channel_pick")
nfl = nfl_all if _pick == "All channels" else [
    c for c in nfl_all if c.get("name") == _pick
]
_scope_label = "33 channels" if _pick == "All channels" else _pick
IS_Z1 = (_pick == "All channels")

# Precompute ranks across all 33 channels — only used in Z2.
_N = len(nfl_all)
_rank_subs   = {c["youtube_channel_id"]: i + 1 for i, c in enumerate(
    sorted(nfl_all, key=lambda c: -(int(c.get("subscriber_count") or 0))))}
_rank_views  = {c["youtube_channel_id"]: i + 1 for i, c in enumerate(
    sorted(nfl_all, key=lambda c: -(int(c.get("total_views")      or 0))))}
_rank_videos = {c["youtube_channel_id"]: i + 1 for i, c in enumerate(
    sorted(nfl_all, key=lambda c: -(int(c.get("video_count")      or 0))))}


# ── KPIs ──────────────────────────────────────────────────────────
if IS_Z1:
    total_subs   = sum(int(c.get("subscriber_count") or 0) for c in nfl)
    total_views  = sum(int(c.get("total_views")      or 0) for c in nfl)
    total_videos = sum(int(c.get("video_count")      or 0) for c in nfl)
    avg_vps      = (total_views // total_subs)   if total_subs   else 0
    avg_vpv      = (total_views // total_videos) if total_videos else 0
    st.markdown(kpi_row([
        ("👥 Total Subscribers", fmt_num(total_subs)),
        ("👁️ Total Views",       fmt_num(total_views)),
        ("🎯 Views / Sub",       fmt_num(avg_vps)),
        ("🎬 Total Videos",      fmt_num(total_videos)),
        ("🎯 Avg Views / Video", fmt_num(avg_vpv)),
    ]), unsafe_allow_html=True)
else:
    # Z2 — channel detail. KPI tiles carry NFL rank chips ("3 / 33").
    _c = nfl[0]
    _yt = _c.get("youtube_channel_id", "")
    _subs   = int(_c.get("subscriber_count") or 0)
    _views  = int(_c.get("total_views")      or 0)
    _videos = int(_c.get("video_count")      or 0)
    _vpv    = (_views // _videos) if _videos else 0
    _vps    = (_views // _subs)   if _subs   else 0
    _launched = (_c.get("launched_at") or "")[:4] or "—"
    _handle = (_c.get("handle") or "").lstrip("@")
    _yt_url = (f"https://www.youtube.com/@{_handle}" if _handle
               else f"https://www.youtube.com/channel/{_yt}")
    _conf = _nfl(_c).get("conference") or "—"
    _div  = _nfl(_c).get("division")   or "—"
    _chip_color = {"AFC": "#CC0000", "NFC": "#013369",
                   "—":   "#D50A0A"}.get(_conf, _T.ACCENT)
    _conf_label = "NFL HQ" if _conf == "—" else f"{_conf} · {_div}"
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"margin:4px 0 12px 0'>"
        f"<span style='background:{_chip_color};color:#fff;"
        f"padding:2px 10px;border-radius:12px;font-size:12px;"
        f"font-weight:600'>{_conf_label}</span>"
        f"<a href='{_yt_url}' target='_blank' rel='noopener' "
        f"style='color:{_T.ACCENT};text-decoration:none;font-size:14px'>"
        f"Open on YouTube ↗</a></div>",
        unsafe_allow_html=True,
    )
    st.markdown(kpi_row([
        ("👥 Subscribers", fmt_num(_subs),
            f"#{_rank_subs.get(_yt, '?')} / {_N} in NFL"),
        ("👁️ Total Views", fmt_num(_views),
            f"#{_rank_views.get(_yt, '?')} / {_N} in NFL"),
        ("🎬 Videos", fmt_num(_videos),
            f"#{_rank_videos.get(_yt, '?')} / {_N} in NFL"),
        ("🎯 Views / Video", fmt_num(_vpv), ""),
        ("📅 Launched", _launched,
            f"Views/Sub: {fmt_num(_vps)}"),
    ]), unsafe_allow_html=True)


# ── 🥧 Three format pies — Views / Videos / Views-per-Video ───────
_t_long_v  = sum(int(c.get("lifetime_long_views")  or 0) for c in nfl)
_t_short_v = sum(int(c.get("lifetime_short_views") or 0) for c in nfl)
_t_live_v  = sum(int(c.get("lifetime_live_views")  or 0) for c in nfl)
_t_long_n  = sum(int(c.get("long_form_count")      or 0) for c in nfl)
_t_short_n = sum(int(c.get("shorts_count")         or 0) for c in nfl)
_t_live_n  = sum(int(c.get("live_count")           or 0) for c in nfl)
_t_long_vpv  = _t_long_v  // max(_t_long_n,  1)
_t_short_vpv = _t_short_v // max(_t_short_n, 1)
_t_live_vpv  = _t_live_v  // max(_t_live_n,  1)
_has_live = (_t_live_v + _t_live_n) > 0
_has_any_format = (_t_long_v + _t_short_v + _t_live_v
                   + _t_long_n + _t_short_n + _t_live_n) > 0
_pie_labels = ["Long", "Shorts", "Live"] if _has_live else ["Long", "Shorts"]
_pie_colors = ["#636EFA", "#00CC96", "#FFA15A"] if _has_live else ["#636EFA", "#00CC96"]


def _zv(l, s, v):
    return [l, s, v] if _has_live else [l, s]


def _make_pie(values, hover_suffix, title):
    chart_title(title)
    fig = go.Figure(go.Pie(
        labels=_pie_labels, values=values,
        marker=dict(colors=_pie_colors), hole=0.45,
        textinfo="percent+label", textposition="inside",
        hovertemplate="%{label}: %{value:,.0f} " + hover_suffix + "<extra></extra>",
    ))
    fig.update_layout(
        showlegend=False, height=260,
        margin=dict(t=10, b=10, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_T.TEXT),
    )
    return fig


if _has_any_format:
    _pcols = st.columns(3)
    with _pcols[0]:
        st.plotly_chart(_make_pie(_zv(_t_long_v, _t_short_v, _t_live_v),
                                  "views", "Lifetime Views by Format"),
                        width="stretch")
    with _pcols[1]:
        st.plotly_chart(_make_pie(_zv(_t_long_n, _t_short_n, _t_live_n),
                                  "videos", "Videos by Format"),
                        width="stretch")
    with _pcols[2]:
        st.plotly_chart(_make_pie(_zv(_t_long_vpv, _t_short_vpv, _t_live_vpv),
                                  "views/video", "Avg Views/Video by Format"),
                        width="stretch")


# ── 📊 Subscribers by channel ─────────────────────────────────────
# Per-row colour by conference (AFC red / NFC blue / NFL HQ shield).
# Same data-not-theme exception used in views/18_WC2026.py.
_CONF_COLOR = {"AFC": "#CC0000", "NFC": "#013369", "—": "#D50A0A"}


def _conf_color(c):
    return _CONF_COLOR.get(_nfl(c).get("conference") or "—", _T.ACCENT)


def _subs_bar(subset, title: str | None = None):
    if not subset:
        return
    subset = sorted(subset, key=lambda r: -(int(r.get("subscriber_count") or 0)))
    names = [c.get("name") or "?" for c in subset]
    subs  = [int(c.get("subscriber_count") or 0) for c in subset]
    cols  = [_conf_color(c) for c in subset]
    confs = [(_nfl(c).get("conference") or "—") for c in subset]
    divs  = [(_nfl(c).get("division") or "—") for c in subset]
    handles = [(c.get("handle") or "") for c in subset]
    customdata = list(zip(confs, divs, handles))
    if title:
        st.markdown(f"**{title}**")
    fig = go.Figure(go.Bar(
        x=names, y=subs, marker_color=cols, customdata=customdata,
        hovertemplate=("<b>%{x}</b><br>"
                       "%{customdata[0]} · %{customdata[1]} · @%{customdata[2]}<br>"
                       "Subs: %{y:,}<extra></extra>"),
    ))
    fig.update_layout(
        height=360,
        xaxis=dict(title="", tickangle=-45, automargin=True,
                   tickfont=dict(size=10)),
        yaxis=dict(title="", showgrid=True,
                   gridcolor="rgba(255,255,255,0.08)"),
        margin=dict(t=10, b=120, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_T.TEXT), showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")


if IS_Z1:
    _subs_rows = [c for c in nfl if int(c.get("subscriber_count") or 0) > 0]
    if _subs_rows:
        st.subheader(f"📊 Subscribers by channel — {_scope_label}")
        st.caption("All channels in scope, ranked by subscriber count. "
                   "Bar colour = conference (AFC red · NFC blue · NFL HQ shield). "
                   "Top row: ≥ 1M subs · bottom row: < 1M (the long tail).")
        # NFL HQ dwarfs every franchise (≈16M vs ≤1.5M), so split on 1M
        # like the Top-5 page does for the mega-channel vs long-tail mix.
        big   = [c for c in _subs_rows if int(c.get("subscriber_count") or 0) >= 1_000_000]
        small = [c for c in _subs_rows if int(c.get("subscriber_count") or 0) <  1_000_000]
        if big:
            _subs_bar(big, "≥ 1M subscribers")
        if small:
            _subs_bar(small, "< 1M subscribers")
        if not big and not small:
            _subs_bar(_subs_rows)
else:
    # Z2 — rank-highlight bar: this channel coloured, others greyed.
    _picked_yt = nfl[0].get("youtube_channel_id")
    ordered_all = sorted(
        [c for c in nfl_all if int(c.get("subscriber_count") or 0) > 0],
        key=lambda r: -(int(r.get("subscriber_count") or 0)),
    )
    st.subheader(f"📊 Subs rank in NFL — {_pick} highlighted")
    st.caption("Where this channel sits among the 33 NFL channels by "
               "subscriber count. Bar coloured = this channel.")
    names = [c.get("name") or "?" for c in ordered_all]
    subs_v = [int(c.get("subscriber_count") or 0) for c in ordered_all]
    cols   = [(_conf_color(c) if c.get("youtube_channel_id") == _picked_yt
               else "#3a3d46") for c in ordered_all]
    fig = go.Figure(go.Bar(
        x=names, y=subs_v, marker_color=cols,
        hovertemplate="<b>%{x}</b><br>Subs: %{y:,}<extra></extra>",
    ))
    fig.update_layout(
        height=360,
        xaxis=dict(title="", tickangle=-45, automargin=True,
                   tickfont=dict(size=10)),
        yaxis=dict(title="", showgrid=True,
                   gridcolor="rgba(255,255,255,0.08)"),
        margin=dict(t=10, b=120, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_T.TEXT), showlegend=False,
    )
    st.plotly_chart(fig, width="stretch")


# ── 📅 Channels launched per year (Z1 only — 1 dot is meaningless) ─
_yr_rows = []
if IS_Z1:
    for c in nfl:
        la = c.get("launched_at") or ""
        if len(la) < 4:
            continue
        try:
            _yr_rows.append({
                "year": int(la[:4]),
                "conf": _nfl(c).get("conference") or "—",
            })
        except Exception:
            continue

if _yr_rows:
    st.subheader(f"📅 Channels launched per year — {_scope_label}")
    st.caption("YouTube channel creation year. Stacked by conference.")
    df = pd.DataFrame(_yr_rows)
    yr_min, yr_max = int(df["year"].min()), int(df["year"].max())
    years = list(range(yr_min, yr_max + 1))
    fig = go.Figure()
    # Stable stack order so colours line up across reloads.
    for conf in ("—", "AFC", "NFC"):
        sub = df[df["conf"] == conf]
        if sub.empty:
            continue
        counts = sub.groupby("year").size().reindex(years, fill_value=0)
        label = "NFL HQ" if conf == "—" else conf
        fig.add_trace(go.Bar(
            name=label,
            x=list(counts.index), y=list(counts.values),
            marker_color=_CONF_COLOR.get(conf, _T.ACCENT),
            hovertemplate=f"<b>{label}</b><br>%{{x}}: %{{y}}<extra></extra>",
        ))
    fig.update_layout(
        barmode="stack",
        xaxis=dict(title="", dtick=1, tickangle=-30),
        yaxis=dict(title="Channels launched"),
        margin=dict(t=30, b=60),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_T.TEXT),
        legend=dict(orientation="h", yanchor="top", y=-0.18,
                    xanchor="center", x=0.5),
    )
    st.plotly_chart(fig, width="stretch")


# ── 📡 All Channels table (sortable HTML) ─────────────────────────
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


_CONF_DUAL = {
    "AFC": ("#CC0000", "#FFFFFF"),
    "NFC": ("#013369", "#FFFFFF"),
    "—":   ("#013369", "#D50A0A"),
}


def _conf_marker(name: str) -> str:
    c1, c2 = _CONF_DUAL.get(name, (_T.ACCENT, _T.WHITE))
    return dual_dot(c1, c2, 14, inline=True)


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


def _sort_js(table_id: str, default_col: int = 3) -> str:
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


if IS_Z1:
    st.subheader("📡 All Channels")
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
        "channel on YouTube. Conference + Division are shown per row "
        "(no aggregation in v0)."
    )
