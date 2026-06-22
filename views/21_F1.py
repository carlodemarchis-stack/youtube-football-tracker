"""🏎️ F1 — All Channels (hidden v0).

Channel-level surface only — no videos, no per-video snapshots, no
trends. 12 channels (11 constructor teams + @Formula1 HQ).

Visual structure mirrors the Top-5 "All Channels" surface
(views/2_Clubs.py landing) using only the data the daily F1 channel
snapshot collects: subs, total views, video_count, long/shorts/live
counts, lifetime_long/short/live views, launched_at.

No aggregation by conference/division (kept as per-row columns only).
Single channel selector at the top filters all sections.

Hidden from the sidebar nav (see app.py — registered but the link is
CSS-hidden). Reached only by URL `/f1`.

Forward-compatibility: see docs/F1_V0.md. The file name `21_F1.py`
reserves the `21*` numbering for WC2026-style future expansion.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import plotly.graph_objects as go
from dotenv import load_dotenv

from src import components_compat as _components
from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.charts import chart_title
from src import theme as _T

load_dotenv()
require_login()

st.title("🏎️ F1 — All Channels")
st.markdown(
    f'<div style="background:{_T.SURFACE};border-left:3px solid {_T.WARN};'
    'padding:10px 14px;margin:6px 0 14px 0;border-radius:4px;'
    f'font-size:13px;line-height:1.55;color:{_T.TEXT}">'
    f'<span style="color:{_T.WARN};font-weight:600">🚧 F1 preview · pre-alpha</span>'
    ' &nbsp;—&nbsp; channel-level snapshot only. No videos / no trends '
    'yet. Hidden from the sidebar; reachable by direct URL.'
    '</div>', unsafe_allow_html=True)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
f1_all = [c for c in _cached_channels(db) if c.get("entity_type") == "F1"]
if not f1_all:
    st.info("No F1 channels in the DB yet — run "
            "`python3 scripts/import_f1.py` to seed the roster.")
    st.stop()


def _f1(c):
    return (c.get("competitions") or {}).get("f1") or {}


def _is_hq(c) -> bool:
    return _f1(c).get("role") == "hq"


# ── Channel filter ────────────────────────────────────────────────
# The dropdown is rendered above the page title by app.py (same slot
# as the global / WC2026 filters). Here we just read the selection.
from src.f1_filter import get_f1_filter, is_f1_teams_only
_pick = get_f1_filter()  # None for "All channels", else the team name
_teams_only = is_f1_teams_only()

# "Teams only" = Z1 multi-channel scope minus the F1 HQ channel.
# HQ is identified by competitions.f1.role == "hq" on the seed record.
def _is_f1_hq(c) -> bool:
    return ((c.get("competitions") or {}).get("f1") or {})\
        .get("role") == "hq"

if _pick is not None:
    f1 = [c for c in f1_all if c.get("name") == _pick]
elif _teams_only:
    f1 = [c for c in f1_all if not _is_f1_hq(c)]
else:
    f1 = f1_all
if _pick is not None:
    _scope_label = _pick
elif _teams_only:
    _scope_label = f"{len(f1)} teams (HQ excluded)"
else:
    _scope_label = f"{len(f1_all)} channels"
IS_Z1 = (_pick is None)

# Precompute ranks across all 12 channels — only used in Z2.
_N = len(f1_all)
_rank_subs   = {c["youtube_channel_id"]: i + 1 for i, c in enumerate(
    sorted(f1_all, key=lambda c: -(int(c.get("subscriber_count") or 0))))}
_rank_views  = {c["youtube_channel_id"]: i + 1 for i, c in enumerate(
    sorted(f1_all, key=lambda c: -(int(c.get("total_views")      or 0))))}
_rank_videos = {c["youtube_channel_id"]: i + 1 for i, c in enumerate(
    sorted(f1_all, key=lambda c: -(int(c.get("video_count")      or 0))))}


# ── KPIs ──────────────────────────────────────────────────────────
if IS_Z1:
    total_subs   = sum(int(c.get("subscriber_count") or 0) for c in f1)
    total_views  = sum(int(c.get("total_views")      or 0) for c in f1)
    total_videos = sum(int(c.get("video_count")      or 0) for c in f1)
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
    # Z2 — channel detail. KPI tiles carry F1 rank chips ("3 / 33").
    _c = f1[0]
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
    # F1 has no conference/division grouping — show 'F1 HQ' for the
    # main channel, 'Constructor' for the 11 team channels.
    _role = _f1(_c).get("role") or "team"
    _is_hq_chip = (_role == "hq")
    _chip_color = "#D50A0A" if _is_hq_chip else _T.ACCENT
    _conf_label = "F1 HQ" if _is_hq_chip else "Constructor"
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
            f"#{_rank_subs.get(_yt, '?')} / {_N} in F1"),
        ("👁️ Total Views", fmt_num(_views),
            f"#{_rank_views.get(_yt, '?')} / {_N} in F1"),
        ("🎬 Videos", fmt_num(_videos),
            f"#{_rank_videos.get(_yt, '?')} / {_N} in F1"),
        ("🎯 Views / Video", fmt_num(_vpv), ""),
        ("📅 Launched", _launched,
            f"Views/Sub: {fmt_num(_vps)}"),
    ]), unsafe_allow_html=True)


# ── 👁️ Views gained per day (since tracking began) ────────────────
# Mirrors the NFL per-day Δ chart: reads channel_snapshots for the
# scoped channels, computes Δ total_views between consecutive days
# (only channels with snapshots on BOTH days of a pair contribute, so
# a late-onboarded channel never reads as a spike on its first day).
# Drops the first row (bootstrap noise). Renders in Z1 (cohort sum)
# and Z2 (single channel) — same code path.
try:
    import pandas as _pd
    import plotly.express as _px
    from collections import defaultdict as _dd
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    _ids = [c["id"] for c in f1]
    _snaps = []
    for _i in range(0, len(_ids), 50):
        _chunk = _ids[_i:_i + 50]
        _resp = (db.client.table("channel_snapshots")
                 .select("channel_id,captured_date,total_views")
                 .in_("channel_id", _chunk)
                 .order("captured_date", desc=False)
                 .range(0, 50 * 400 - 1)
                 .execute().data) or []
        _snaps.extend(_resp)

    _by_date: dict[str, dict[str, int]] = _dd(dict)
    for _r in _snaps:
        _by_date[_r["captured_date"]][_r["channel_id"]] = \
            int(_r.get("total_views") or 0)

    # Drop today (UTC) — the F1 cron writes at 04:45 UTC, so before it
    # runs today's row is missing and after it's complete; dropping it
    # is the conservative consistency choice.
    _today = _dt.now(_tz.utc).date().isoformat()
    _by_date = {d: v for d, v in _by_date.items() if d < _today}
    _dates = sorted(_by_date.keys())

    if len(_dates) >= 2:
        _day_rows = []
        for _i2 in range(1, len(_dates)):
            _d, _dp = _dates[_i2], _dates[_i2 - 1]
            _cur, _prev = _by_date[_d], _by_date[_dp]
            _dv = sum(_cur[cid] - _prev[cid]
                      for cid in _cur if cid in _prev)
            # A missed snapshot day would otherwise dump the whole
            # multi-day delta onto the later date as a fake spike. Spread
            # it evenly across the gap so a skipped cron self-heals.
            _gap = (_dt.fromisoformat(_d) - _dt.fromisoformat(_dp)).days or 1
            if _gap <= 1:
                _day_rows.append({"Date": _d, "Δ Views": _dv})
            else:
                for _k in range(1, _gap + 1):
                    _fd = (_dt.fromisoformat(_dp)
                           + _td(days=_k)).date().isoformat()
                    _day_rows.append({"Date": _fd, "Δ Views": _dv // _gap})
        _dfd = _pd.DataFrame(_day_rows)
        _dfd["Date"] = _pd.to_datetime(_dfd["Date"])
        # Drop first row (partial bootstrap — first day has no prior).
        _dfc = _dfd.iloc[1:] if len(_dfd) > 1 else _dfd

        chart_title("Views gained per day")
        if IS_Z1:
            st.caption(
                f"Daily Δ total_views summed across the {len(f1)} "
                f"channels in scope, since the first snapshot on "
                f"{_dates[0]} ({len(_dates)} days). Only channels "
                "with snapshots on both days of a pair contribute, "
                "so a late-onboarded channel never reads as a spike."
            )
        else:
            st.caption(
                f"Daily Δ total_views for **{_pick}** since the first "
                f"snapshot on {_dates[0]} ({len(_dates)} days)."
            )
        _fv = _px.line(_dfc, x="Date", y="Δ Views", markers=True)
        _fv.update_traces(line_color=_T.ACCENT,
                           marker_color=_T.ACCENT)
        _fv.update_layout(
            plot_bgcolor=_T.BG, paper_bgcolor=_T.BG,
            font=dict(color=_T.TEXT), height=340,
            margin=dict(t=10, l=0, r=0, b=20),
            yaxis=dict(title="", showgrid=True,
                       gridcolor="rgba(255,255,255,0.08)"),
            xaxis=dict(title=""),
        )
        st.plotly_chart(_fv, width="stretch")
    else:
        st.caption("📈 Views-per-day chart needs ≥ 2 daily snapshots — "
                   "first one will appear after tomorrow's cron run.")
except Exception as _e:
    st.caption(f"(Views-per-day chart unavailable: {_e})")


# ── 🥧 Videos by Format ───────────────────────────────────────────
# Only this pie is meaningful in v0 — the channels API gives us
# long_form_count / shorts_count / live_count directly. Lifetime
# views-by-format pies depend on the per-video snapshot pipeline,
# which F1 v0 doesn't run, so they'd render as all zeros.
_t_long_n  = sum(int(c.get("long_form_count") or 0) for c in f1)
_t_short_n = sum(int(c.get("shorts_count")    or 0) for c in f1)
_t_live_n  = sum(int(c.get("live_count")      or 0) for c in f1)
_has_live = _t_live_n > 0
_pie_labels = ["Long", "Shorts", "Live"] if _has_live else ["Long", "Shorts"]
_pie_colors = ["#636EFA", "#00CC96", "#FFA15A"] if _has_live else ["#636EFA", "#00CC96"]
_pie_values = ([_t_long_n, _t_short_n, _t_live_n] if _has_live
               else [_t_long_n, _t_short_n])

if sum(_pie_values) > 0:
    chart_title("Videos by Format")
    fig_pie = go.Figure(go.Pie(
        labels=_pie_labels, values=_pie_values,
        marker=dict(colors=_pie_colors), hole=0.45,
        textinfo="percent+label", textposition="inside",
        hovertemplate="%{label}: %{value:,.0f} videos<extra></extra>",
    ))
    fig_pie.update_layout(
        showlegend=False, height=300,
        margin=dict(t=10, b=10, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_T.TEXT),
    )
    # Constrain width so the donut doesn't sprawl across the page —
    # ~1/3 width feels right (matches the look of three side-by-side
    # donuts in the Top-5 page without the empty companion donuts).
    _pcols = st.columns([1, 1, 1])
    with _pcols[1]:
        st.plotly_chart(fig_pie, width="stretch")


# ── 📡 All Channels table (sortable HTML) ─────────────────────────
# Order: KPI → pie → table → all other charts.
def td(val_sort, content, *, align="right"):
    v = "" if val_sort is None else str(val_sort)
    return f"<td style='text-align:{align}' data-val=\"{v}\">{content}</td>"


_TABLE_CSS = (
    "<style>"
    f"body{{margin:0;background:{_T.BG};color:{_T.TEXT};"
    "font-family:'Source Sans Pro',sans-serif}"
    ".f1-wrap{overflow-x:auto;width:100%}"
    ".f1-tbl{width:100%;border-collapse:collapse;border:0;font-size:14px;"
    f"color:{_T.TEXT};background:transparent}}"
    ".f1-tbl th,.f1-tbl td{border-left:0;border-right:0;border-top:0;"
    "padding:6px 12px;white-space:nowrap}"
    f".f1-tbl th{{user-select:none;font-weight:600;color:{_T.TEXT};border-bottom:0}}"
    f".f1-tbl th[data-col]:hover{{color:{_T.ACCENT}}}"
    f".f1-tbl th.active{{color:{_T.ACCENT}}}"
    f".f1-tbl td{{border-bottom:1px solid {_T.BORDER}}}"
    f".f1-tbl tr:hover td{{background:{_T.SURFACE}}}"
    ".f1-tbl a{color:inherit;text-decoration:none}"
    "</style>"
)


def _row_html(c) -> str:
    yt_id = c.get("youtube_channel_id") or ""
    handle = (c.get("handle") or "").lstrip("@")
    yt_url = (f"https://www.youtube.com/@{handle}" if handle
              else f"https://www.youtube.com/channel/{yt_id}")
    team = c.get("name") or "—"
    subs   = int(c.get("subscriber_count") or 0)
    views  = int(c.get("total_views")      or 0)
    videos = int(c.get("video_count")      or 0)
    longs  = int(c.get("long_form_count")  or 0)
    shorts = int(c.get("shorts_count")     or 0)
    lives  = int(c.get("live_count")       or 0)
    vpv    = (views / videos) if videos else 0
    team_cell = (
        f"<td style='text-align:left' data-val=\"{team.lower()}\">"
        f"<a href='{yt_url}' target='_blank' rel='noopener' "
        f"style='color:{_T.TEXT};text-decoration:none'>{team}</a>"
        f"</td>"
    )
    return (
        "<tr>"
        + team_cell
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
    ordered = sorted(f1, key=lambda c: -(int(c.get("subscriber_count") or 0)))
    rows_html = [_row_html(c) for c in ordered]
    iframe_h = min(2400, 31 * len(ordered) + 44)
    _components.html(
        _TABLE_CSS
        + "<div class='f1-wrap'><table class='f1-tbl' id='f1-tbl-main'>"
        f"<thead><tr style='border-bottom:2px solid {_T.BORDER_STRONG}'>"
        + _th_html + "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
        + _sort_js("f1-tbl-main", default_col=3),
        height=iframe_h, scrolling=True,
    )
    st.caption("Click any column header to sort. Click a team name to "
               "open the channel on YouTube.")


# ── 📊 Subscribers by channel (neutral colour — no aggregation) ───
if IS_Z1:
    _subs_rows = sorted(
        [c for c in f1 if int(c.get("subscriber_count") or 0) > 0],
        key=lambda r: -(int(r.get("subscriber_count") or 0)),
    )
else:
    _subs_rows = sorted(
        [c for c in f1_all if int(c.get("subscriber_count") or 0) > 0],
        key=lambda r: -(int(r.get("subscriber_count") or 0)),
    )


def _subs_bar(subset, title: str | None = None,
               highlight_yt: str | None = None):
    if not subset:
        return
    names = [c.get("name") or "?" for c in subset]
    subs  = [int(c.get("subscriber_count") or 0) for c in subset]
    if highlight_yt:
        cols = [(_T.ACCENT if c.get("youtube_channel_id") == highlight_yt
                 else "#3a3d46") for c in subset]
    else:
        cols = _T.ACCENT
    if title:
        st.markdown(f"**{title}**")
    fig = go.Figure(go.Bar(
        x=names, y=subs, marker_color=cols,
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


if _subs_rows:
    if IS_Z1:
        st.subheader(f"📊 Subscribers by channel — {_scope_label}")
        if _teams_only:
            st.caption("All 11 F1 constructor channels, ranked by "
                       "subscriber count. Log scale so the long "
                       "tail (~100k subs) stays readable next to "
                       "Red Bull at ~2.9M.")
        else:
            st.caption("All channels in scope, ranked by subscriber "
                       "count. Log scale so the long tail (clubs "
                       "in the ~100k range) stays readable "
                       "alongside F1's ~14.7M-subscriber main "
                       "channel.")
        # Single chart, log Y so the F1's 14.7M doesn't flatten every
        # team into invisible nubs (the old split into ≥1M / <1M
        # rows worked around the same problem but hid the relative
        # gap between groups).
        _names = [c.get("name") or "?" for c in _subs_rows]
        _subs  = [int(c.get("subscriber_count") or 0) for c in _subs_rows]
        fig_subs = go.Figure(go.Bar(
            x=_names, y=_subs, marker_color=_T.ACCENT,
            hovertemplate="<b>%{x}</b><br>Subs: %{y:,}<extra></extra>",
        ))
        fig_subs.update_layout(
            height=480,
            xaxis=dict(title="", tickangle=-45, automargin=True,
                       tickfont=dict(size=10)),
            yaxis=dict(title="Subscribers (log)", type="log",
                       showgrid=True,
                       gridcolor="rgba(255,255,255,0.08)"),
            margin=dict(t=10, b=120, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=_T.TEXT), showlegend=False,
        )
        st.plotly_chart(fig_subs, width="stretch")
    else:
        st.subheader(f"📊 Subs rank in F1 — {_pick} highlighted")
        st.caption("Where this channel sits among the 33 F1 channels.")
        _subs_bar(_subs_rows,
                  highlight_yt=f1[0].get("youtube_channel_id"))


# ── 📅 Channels launched per year (Z1 only — single-colour bars) ──
if IS_Z1:
    _yr_counts: dict[int, int] = {}
    for c in f1:
        la = c.get("launched_at") or ""
        if len(la) < 4:
            continue
        try:
            y = int(la[:4])
        except Exception:
            continue
        _yr_counts[y] = _yr_counts.get(y, 0) + 1

    if _yr_counts:
        st.subheader(f"📅 Channels launched per year — {_scope_label}")
        st.caption(f"YouTube channel creation year across the "
                   f"{len(f1)} channel(s) in scope.")
        yr_min, yr_max = min(_yr_counts), max(_yr_counts)
        years = list(range(yr_min, yr_max + 1))
        values = [_yr_counts.get(y, 0) for y in years]
        fig_yr = go.Figure(go.Bar(
            x=years, y=values, marker_color=_T.ACCENT,
            hovertemplate="<b>%{x}</b><br>%{y} channel(s)<extra></extra>",
        ))
        fig_yr.update_layout(
            xaxis=dict(title="", dtick=1, tickangle=-30),
            yaxis=dict(title="Channels launched"),
            margin=dict(t=30, b=60),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color=_T.TEXT), showlegend=False,
        )
        st.plotly_chart(fig_yr, width="stretch")
