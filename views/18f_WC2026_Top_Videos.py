"""🏆 World Cup 2026 — All-Time Top.

Mirrors views/4_Top_Videos.py (the Top-5 page) section-for-section,
adapted to the WC2026 (Confederation → Team) hierarchy and using the
same canonical visual primitives:

  - Title + subtitle (render_page_subtitle when last-fetch is known)
  - Scope-aware top-100 candidate selection from db.get_top_videos_in_channels
  - KPI Row 1 (5 metric tiles: total / share / avg / cutoff / age)
  - Format + scope-mix donuts (Confederation mix at Z1, Team mix at Z2)
  - Sort selector (Views / Likes / Comments)
  - 🏆 Video table via the canonical render_top_season_videos_table
    renderer (single source of truth, shared with Season Top + Top-5)
  - 👁️ Views by Rank — Plotly bar, coloured per confederation / team
  - 📅 Rank vs Year — Plotly bars showing publication year per rank
  - 🎬 Top 100 by Publication Year — histogram
  - Theme Distribution pie
  - 📡 Per-channel Top 100 KPI row + channel-stats table (Z1 + Z2)

Caching:
  - Channel set, top-100 list, and last-fetch all live behind
    `@st.cache_data(ttl=…)` decorators.
  - Per-channel `top100_views` / `top100_avg_age_days` are populated
    nightly by `daily_wc2026.py` (step 4b: refresh_top100_stats +
    refresh_lifetime_format_views per channel). Without that step
    every Z1/Z2 KPI row would silently drift the day after the
    one-off popular backfill — covered by 1effc40.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src import components_compat as components  # noqa: F401  (used by renderer)
from src import theme as _T
from src.analytics import (
    build_category_pie, compute_theme_distribution, fmt_num, kpi_row,
)
from src.auth import require_login
from src.cached_db import (
    get_all_channels as _cached_channels,
    get_last_fetch_time as _cached_last_fetch,
)
from src.charts import chart_title, readable_hover
from src.database import Database
from src.dot import channel_badge  # noqa: F401  (used by renderer)
from src.filters import render_page_subtitle
from src.season_top import render_top_season_videos_table
from src.wc2026_badge import _CONF_DUAL, CONF_COLOR, wc2026_badge
from src.wc2026_filter import (
    get_wc2026_filter, scope_wc2026, scope_label as _wc_scope_label,
)


load_dotenv()
require_login()

st.title("🏆 World Cup 2026 — All-Time Top")


SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()
db = Database(SUPABASE_URL, SUPABASE_KEY)

# Confederation brand colours come from src/wc2026_badge.py so all
# WC2026 surfaces share one palette. _CONF_DUAL has UEFA correctly
# rendered as blue (primary) + red (secondary).
_CONF_COLOR = CONF_COLOR


def _wc_meta(c):
    return (c.get("competitions") or {}).get("wc2026") or {}


# ── Channel set + scope filter ────────────────────────────────────
all_channels = _cached_channels(db)
wc_all = [c for c in all_channels
          if (c.get("competitions") or {}).get("wc2026")]
if not wc_all:
    st.info("No WC2026 channels in the DB yet — run "
            "`python3 scripts/import_wc2026.py` first.")
    st.stop()

_confed, _team = get_wc2026_filter()
_scope_channels = scope_wc2026(wc_all, _confed, _team)
if not _scope_channels:
    st.info(f"No WC2026 channels for **{_wc_scope_label(_confed, _team)}**.")
    st.stop()

IS_Z1 = (_confed is None and _team is None)
IS_Z2 = (_confed is not None and _team is None)
IS_Z3 = (_team is not None)

_daily_updated = _cached_last_fetch(db, "daily_wc2026")
render_page_subtitle("Top 100 most viewed videos all time",
                     updated_raw=_daily_updated, show_filter=False)
st.caption(
    "📡 **Cohort:** the official YouTube channels of all 48 qualified "
    "teams + FIFA + the 6 confederations (UEFA · CONMEBOL · CONCACAF "
    "· CAF · AFC · OFC). **Every video they've ever published** is "
    "eligible for this ranking — not just WC2026-themed content."
)

# Scope context line.
if IS_Z3:
    _scope_text = f"📍 **{_team}** — top 100 for this team."
elif IS_Z2:
    _scope_text = (f"🌐 **{_confed}** — top 100 across "
                   f"{len(_scope_channels)} channel(s) in this confederation.")
else:
    _scope_text = (f"🌍 **All confederations** — top 100 across the "
                   f"full {len(_scope_channels)}-channel WC2026 cohort.")
st.caption(_scope_text)


# ── Fetch top-N candidate pool (200 to feed both ranked tables + KPIs) ─
@st.cache_data(ttl=300, show_spinner=False)
def _load_top_in_channels(channel_ids: tuple[str, ...], limit: int = 200):
    if not channel_ids:
        return []
    return db.get_top_videos_in_channels(list(channel_ids), limit=limit)


_ch_ids_tuple = tuple(c["id"] for c in _scope_channels)
videos = _load_top_in_channels(_ch_ids_tuple, limit=200)
if not videos:
    st.warning("No videos in the DB for this scope yet — run "
               "`scripts/refresh_popular_wc2026.py` or wait for the "
               "weekly cron.")
    st.stop()

# Hydrate channel meta (team, confed) onto each video row so charts can
# colour by group. The `channels` join field gives us name + handle from
# the supabase query; we look up the full channel for the rest.
_ch_by_id = {c["id"]: c for c in wc_all}
for v in videos:
    ch = _ch_by_id.get(v.get("channel_id")) or {}
    v["team_grp"] = _wc_meta(ch).get("team") or ch.get("name") or "?"
    v["confed_grp"] = _wc_meta(ch).get("confederation") or "—"
    v["channel_name"] = (v.get("channels") or {}).get("name") \
        or ch.get("name") or "?"

df = pd.DataFrame(videos)


# ── KPI Row 1: context Top 100 (one merged ranked list) ─────────────
# Same shape as views/4_Top_Videos.py row 1: total / share of lifetime
# / avg / cutoff / avg age. _lifetime_views denominator is the sum of
# `total_views` across in-scope channels — so "share of lifetime" reads
# as "what % of all views by these channels comes from the top 100".
_lifetime_views = sum(int(c.get("total_views") or 0)
                      for c in _scope_channels)

_kpi_top = df.sort_values("view_count", ascending=False).head(100)
_ctx_total = int(_kpi_top["view_count"].sum())
_ctx_avg = (int(_kpi_top["view_count"].mean())
            if len(_kpi_top) else 0)
_ctx_cutoff = (int(_kpi_top["view_count"].min())
               if len(_kpi_top) else 0)
_ctx_pct = (_ctx_total / _lifetime_views * 100) if _lifetime_views else 0.0
_now_utc_kpi = pd.Timestamp.now(tz="UTC")
_ctx_ages = (
    _now_utc_kpi - pd.to_datetime(
        _kpi_top["published_at"], utc=True, errors="coerce")
).dt.total_seconds() / 86400.0
_ctx_ages = _ctx_ages.dropna()
_ctx_avg_age = float(_ctx_ages.mean() / 365.25) if len(_ctx_ages) else 0.0

if IS_Z3:
    _ctx_label = f"Top 100 of {_team}"
elif IS_Z2:
    _ctx_label = (f"Top 100 across {_confed} "
                  f"(one ranked list)")
else:
    _ctx_label = "Top 100 across all confederations (one ranked list)"

# Z3 rank subtitles — where does this team sit within its confederation
# / overall cohort by precomputed top100_views?
_row1_subs: dict[str, str] = {}
if IS_Z3 and _scope_channels:
    _ch = _scope_channels[0]
    _ch_conf = _wc_meta(_ch).get("confederation")
    _peers_conf = [c for c in wc_all
                   if _wc_meta(c).get("confederation") == _ch_conf]
    _peers_all = wc_all

    def _r(chans, key_fn):
        scored = sorted(
            ((c.get("id"), key_fn(c)) for c in chans if c.get("id")),
            key=lambda kv: kv[1] or 0, reverse=True)
        for i, (cid, _v) in enumerate(scored, 1):
            if cid == _ch.get("id"):
                return i, len(scored)
        return None, len(scored)

    def _sub(key_fn, *, low_better: bool = False) -> str:
        kf = (lambda c: -(key_fn(c) or 0)) if low_better else key_fn
        lr, lt = _r(_peers_conf, kf)
        orr, ot = _r(_peers_all, kf)
        parts = []
        if lr and _ch_conf:
            parts.append(f"#{lr}/{lt} in {_ch_conf}")
        if orr:
            parts.append(f"#{orr}/{ot} overall")
        return " · ".join(parts)

    _row1_subs["total"] = _sub(lambda c: int(c.get("top100_views") or 0))
    _row1_subs["avg"]   = _row1_subs["total"]
    _row1_subs["share"] = _sub(
        lambda c: ((int(c.get("top100_views") or 0)
                    / max(int(c.get("total_views") or 0), 1)) * 100.0))
    _row1_subs["age"]   = _sub(
        lambda c: float(c.get("top100_avg_age_days") or 0),
        low_better=True)

st.markdown(
    f'<div style="color:#999;font-size:12px;margin:4px 0 6px 2px">'
    f'<b>{_ctx_label}.</b> One merged ranked list — the same 100 '
    f'videos shown in the table below.</div>',
    unsafe_allow_html=True,
)
st.markdown(kpi_row([
    ("👁️ Total views (top 100)", fmt_num(_ctx_total),
     _row1_subs.get("total", "")),
    ("🔥 Share of lifetime",     f"{_ctx_pct:.1f}%",
     _row1_subs.get("share", "")),
    ("🎯 Avg views / video",     fmt_num(_ctx_avg),
     _row1_subs.get("avg", "")),
    ("👁️ Cutoff (rank 100)",     fmt_num(_ctx_cutoff)),
    ("⏱️ Avg age",               f"{_ctx_avg_age:.1f}y",
     _row1_subs.get("age", "")),
], colors=["#58A6FF", "#00CC96", "#FFA15A", "#AB63FA", "#EF553B"]),
            unsafe_allow_html=True)

filtered = df.sort_values("view_count", ascending=False) \
             .head(100).reset_index(drop=True)


# ── Mix donuts ────────────────────────────────────────────────────
# Format mix (always) + scope-adaptive second/third donut:
#   Z1 → Confederation mix by count / by views
#   Z2 → Team mix (channels in the picked confed) by count / by views
#   Z3 → No mix row (single team — not meaningful)
_FMT_COLOR = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}
_FMT_LABEL = {"long": "Long", "short": "Shorts", "live": "Live"}

if not IS_Z3 and not filtered.empty:
    _fmt_n: dict[str, int] = {}
    _conf_n: dict[str, int] = {}
    _conf_v: dict[str, int] = {}
    _team_n: dict[str, int] = {}
    _team_v: dict[str, int] = {}
    for _row in filtered.itertuples(index=False):
        _f = (getattr(_row, "format", "") or "").lower()
        if _f not in ("long", "short", "live"):
            _f = ("long"
                  if (getattr(_row, "duration_seconds", 0) or 0) >= 60
                  else "short")
        _fmt_n[_f] = _fmt_n.get(_f, 0) + 1
        _vv = int(getattr(_row, "view_count", 0) or 0)
        _cn = getattr(_row, "confed_grp", "—") or "—"
        _tn = getattr(_row, "team_grp", "?") or "?"
        _conf_n[_cn] = _conf_n.get(_cn, 0) + 1
        _conf_v[_cn] = _conf_v.get(_cn, 0) + _vv
        _team_n[_tn] = _team_n.get(_tn, 0) + 1
        _team_v[_tn] = _team_v.get(_tn, 0) + _vv

    def _donut(labels, values, colors, hover_unit: str):
        f = go.Figure()
        f.add_trace(go.Pie(
            labels=labels, values=values,
            marker=dict(colors=colors, line=dict(color="#0E1117", width=2)),
            hole=0.45, textinfo="label+percent", sort=False,
            hovertemplate=("<b>%{label}</b><br>%{value:,} " + hover_unit
                           + " · %{percent}<extra></extra>"),
        ))
        f.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#FAFAFA"),
            margin=dict(t=10, b=10, l=10, r=10),
            height=260, showlegend=False,
        )
        return f

    def _sorted_with(d: dict, cmap: dict):
        items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        return ([k for k, _ in items], [v for _, v in items],
                [cmap.get(k, "#888") for k, _ in items])

    _n = len(filtered)
    _fmt_keys = [k for k in ("long", "short", "live") if _fmt_n.get(k)]
    _fmt_fig = _donut([_FMT_LABEL[k] for k in _fmt_keys],
                      [_fmt_n[k] for k in _fmt_keys],
                      [_FMT_COLOR[k] for k in _fmt_keys], "videos")

    def _three(title_n: str, pair_n, title_v: str, pair_v):
        _cf, _cn_col, _cv = st.columns(3)
        with _cf:
            chart_title(f"Format mix (top {_n})")
            st.plotly_chart(_fmt_fig, width="stretch")
        with _cn_col:
            chart_title(title_n)
            st.plotly_chart(_donut(*pair_n, "videos"), width="stretch")
        with _cv:
            chart_title(title_v)
            st.plotly_chart(_donut(*pair_v, "views"), width="stretch")

    if IS_Z2:
        # Team mix within the picked confederation. No standardised
        # team palette — fall back to a categorical sequence.
        _team_palette = px.colors.qualitative.Set3
        _team_cmap = {t: _team_palette[i % len(_team_palette)]
                      for i, t in enumerate(_team_n.keys())}
        _three(f"Team mix — by count (top {_n})",
                _sorted_with(_team_n, _team_cmap),
                f"Team mix — by views (top {_n})",
                _sorted_with(_team_v, _team_cmap))
    elif len([k for k, v in _conf_n.items() if v > 0]) >= 2:
        _three(f"Confederation mix — by count (top {_n})",
                _sorted_with(_conf_n, _CONF_COLOR),
                f"Confederation mix — by views (top {_n})",
                _sorted_with(_conf_v, _CONF_COLOR))
    else:
        # Single-confed at Z1 (degenerate). Format only.
        chart_title(f"Format mix (top {_n})")
        st.plotly_chart(_fmt_fig, width="stretch")


# ── Video table ───────────────────────────────────────────────────
if IS_Z3:
    _vl_label = _team
elif IS_Z2:
    _vl_label = _confed
else:
    _vl_label = "All Confederations"

_sort_label = st.radio(
    "Sort by",
    options=["Views", "Likes", "Comments"],
    horizontal=True, label_visibility="collapsed",
    key="wc_top_videos_sort",
)
_sort_field = {"Views": "view_count",
               "Likes": "like_count",
               "Comments": "comment_count"}[_sort_label]
_sort_order_by = _sort_label.lower()
filtered_sorted = filtered.sort_values(_sort_field, ascending=False)

_vids_list = []
for r in filtered_sorted.itertuples(index=False):
    _vids_list.append({
        "id": getattr(r, "id", None),
        "youtube_video_id": getattr(r, "youtube_video_id", "") or "",
        "title": getattr(r, "title", "") or "",
        "channel_id": getattr(r, "channel_id", None),
        "thumbnail_url": getattr(r, "thumbnail_url", "") or "",
        "duration_seconds": int(getattr(r, "duration_seconds", 0) or 0),
        "format": getattr(r, "format", "") or "",
        "published_at": getattr(r, "published_at", None),
        "view_count": int(getattr(r, "view_count", 0) or 0),
        "like_count": int(getattr(r, "like_count", 0) or 0),
        "comment_count": int(getattr(r, "comment_count", 0) or 0),
        "category": getattr(r, "category", "") or "",
        "has_paid_promotion": bool(getattr(r, "has_paid_promotion", False)),
    })
render_top_season_videos_table(
    _vids_list, _ch_by_id,
    header=f"🏆 Top {len(filtered_sorted)} All-Time Videos — {_vl_label}",
    order_by=_sort_order_by,
    max_height=1400,
    badge_resolver=lambda ch: wc2026_badge(ch, 14),
)


# ── 👁️ Views by Rank ──────────────────────────────────────────────
st.subheader("👁️ Views by Rank")
chart_df = filtered[["view_count", "title", "channel_name",
                     "team_grp", "confed_grp"]].copy()
chart_df["rank"] = range(1, len(chart_df) + 1)

# Z1 → colour by confederation. Z2 → colour by team. Z3 → single team
# (palette fallback — accent).
if IS_Z1:
    _color_field, _color_map = "confed_grp", _CONF_COLOR
elif IS_Z2:
    _team_palette_v = px.colors.qualitative.Set3
    _color_field = "team_grp"
    _color_map = {t: _team_palette_v[i % len(_team_palette_v)]
                  for i, t in enumerate(chart_df["team_grp"].unique())}
else:
    _color_field, _color_map = "team_grp", None

fig = px.bar(
    chart_df, x="rank", y="view_count",
    color=_color_field, color_discrete_map=_color_map,
    hover_data=["title", "channel_name"],
    labels={"rank": "Rank", "view_count": "Views"},
)
fig.update_layout(
    xaxis=dict(tickvals=[1] + list(range(5, 96, 5)) + [100],
                title="Rank"),
    yaxis_title="Total Views",
    margin=dict(t=20, b=40), bargap=0.1,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=_T.TEXT),
)
fig.update_yaxes(gridcolor=_T.BORDER)
st.plotly_chart(fig, width="stretch")


# ── 📅 Rank vs Publication Year ───────────────────────────────────
st.subheader("📅 Rank vs Publication Year")
from zoneinfo import ZoneInfo as _ZoneInfo
_current_year = datetime.now(_ZoneInfo("Europe/Rome")).year

scatter_df = filtered[["title", "channel_name", "view_count",
                       "published_at", _color_field]].copy()
scatter_df["rank"] = range(1, len(scatter_df) + 1)
scatter_df["year"] = pd.to_datetime(scatter_df["published_at"],
                                     utc=True).dt.year

fig_scatter = go.Figure()
baseline = _current_year
for group_name in scatter_df[_color_field].unique():
    grp = scatter_df[scatter_df[_color_field] == group_name]
    bar_color = (_color_map.get(group_name, None)
                 if _color_map else None)
    fig_scatter.add_trace(go.Bar(
        x=grp["rank"], y=grp["year"] - baseline, base=baseline,
        name=str(group_name), marker_color=bar_color,
        customdata=list(zip(grp["title"], grp["view_count"])),
        hovertemplate=("Rank %{x}<br>Year: %{y}<br>"
                       "%{customdata[0]}<br>"
                       "Views: %{customdata[1]:,}<extra></extra>"),
        width=0.8,
    ))
fig_scatter.update_layout(
    yaxis=dict(autorange="reversed", dtick=1, title="Year"),
    xaxis=dict(range=[0.5, 100.5],
                tickvals=[1] + list(range(5, 96, 5)) + [100],
                title="Rank"),
    margin=dict(t=20, b=40), bargap=0.1, barmode="overlay",
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=_T.TEXT),
)
fig_scatter.update_yaxes(gridcolor=_T.BORDER)
st.plotly_chart(fig_scatter, width="stretch")


# ── 🎬 Top 100 Videos by Publication Year ─────────────────────────
st.subheader("🎬 Top 100 Videos by Publication Year")
year_df = filtered.copy()
year_df["year"] = pd.to_datetime(year_df["published_at"],
                                  utc=True).dt.year
year_counts = (year_df.groupby("year").size()
               .reset_index(name="count").sort_values("year"))
fig_year = px.bar(year_counts, x="year", y="count",
                  labels={"year": "Year",
                          "count": "Videos in Top 100"},
                  color_discrete_sequence=["#636EFA"])
fig_year.update_layout(
    xaxis=dict(dtick=1, title="Year"),
    yaxis_title="Videos in Top 100", margin=dict(t=20, b=40),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=_T.TEXT),
)
fig_year.update_yaxes(gridcolor=_T.BORDER)
readable_hover(fig_year, x_date=False)
st.plotly_chart(fig_year, width="stretch")


# ── Theme Distribution ────────────────────────────────────────────
theme_df = compute_theme_distribution(filtered)
if not theme_df.empty:
    cat_counts = dict(zip(theme_df["category"], theme_df["count"]))
    st.plotly_chart(
        build_category_pie(cat_counts, "Theme Distribution", "videos"),
        width="stretch")


# ── 📡 Each channel's Top 100 (Z1 + Z2 only) ──────────────────────
if not IS_Z3:
    st.subheader("📡 Each channel's Top 100")
    try:
        _agg_total = sum(int(c.get("top100_views") or 0)
                         for c in _scope_channels)
        _agg_n = sum(1 for c in _scope_channels
                     if int(c.get("top100_views") or 0) > 0)
        _agg_vids = _agg_n * 100
        _agg_avg = (_agg_total // _agg_vids) if _agg_vids else 0
        _agg_per = (_agg_total // _agg_n) if _agg_n else 0
        _agg_pct = ((_agg_total / _lifetime_views * 100)
                    if _lifetime_views else 0.0)
        _w_age = 0.0
        _w_sum = 0.0
        for c in _scope_channels:
            v = int(c.get("top100_views") or 0)
            a = float(c.get("top100_avg_age_days") or 0)
            if v > 0 and a > 0:
                _w_age += a * v
                _w_sum += v
        _agg_avg_age = ((_w_age / _w_sum / 365.25)
                        if _w_sum else 0.0)

        _ent_label = "teams"
        st.markdown(
            f'<div style="color:#999;font-size:12px;margin:14px 0 6px 2px">'
            f'<b>Sum of each {_ent_label[:-1]}\'s own top 100.</b> '
            f'{_agg_n} {_ent_label} × up to 100 videos each — ties to '
            f'the channel-stats table below.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(kpi_row([
            ("Total views (sum of top 100s)", fmt_num(_agg_total)),
            ("Share of lifetime",             f"{_agg_pct:.1f}%"),
            ("Avg views / video",             fmt_num(_agg_avg)),
            (f"Avg per {_ent_label[:-1]}",    fmt_num(_agg_per)),
            ("Avg age",                       f"{_agg_avg_age:.1f}y"),
        ], colors=["#58A6FF", "#00CC96", "#FFA15A",
                   "#AB63FA", "#EF553B"]),
                    unsafe_allow_html=True)
    except Exception as _e:
        st.caption(f"(KPI row 2 unavailable: {_e})")


# ── Channel stats table (Z1 + Z2 only) ────────────────────────────
if not IS_Z3:
    # Per-channel rows pulled from the freshly-recomputed channel
    # aggregates (top100_views, top100_avg_age_days,
    # top100_long_pct …). These are written by daily_wc2026.py
    # step 4b — refresh_top100_stats() + refresh_lifetime_format_views().
    now_dt = datetime.now(timezone.utc)
    rows = []
    for c in _scope_channels:
        t100 = int(c.get("top100_views") or 0)
        if t100 <= 0:
            continue
        lv = int(c.get("total_views") or 0)
        avg = t100 // 100 if t100 else 0
        age_days = float(c.get("top100_avg_age_days") or 0)
        age_yrs = (age_days / 365.25) if age_days else 0.0
        long_pct = int(c.get("top100_long_pct") or 0)
        pct = (t100 / lv * 100) if lv else 0.0
        conf = _wc_meta(c).get("confederation") or "—"
        team = _wc_meta(c).get("team") or c.get("name") or "?"
        rows.append({
            "channel_id":  c.get("id"),
            "team":        team,
            "confed":      conf,
            "subs":        int(c.get("subscriber_count") or 0),
            "lifetime":    lv,
            "top100_v":    t100,
            "avg":         avg,
            "share_pct":   pct,
            "long_pct":    long_pct,
            "age_yrs":     age_yrs,
        })

    if rows:
        rows.sort(key=lambda r: r["top100_v"], reverse=True)
        tbl_html = ""
        for i, r in enumerate(rows, 1):
            ch = _ch_by_id.get(r["channel_id"]) or {}
            # Unified WC2026 marker: confederation dual-dot for FIFA /
            # UEFA / CONMEBOL / …, flag emoji for team channels.
            _dot = wc2026_badge(ch, 14)
            tbl_html += f"""<tr>
                <td style="padding:6px 10px;color:{_T.MUTED};text-align:right">{i}</td>
                <td style="padding:6px 10px">{_dot}</td>
                <td style="padding:6px 10px;font-weight:600">{r['team']}</td>
                <td style="padding:6px 10px;color:{_T.MUTED_2}">{r['confed']}</td>
                <td style="padding:6px 10px;text-align:right">{fmt_num(r['subs'])}</td>
                <td style="padding:6px 10px;text-align:right">{fmt_num(r['lifetime'])}</td>
                <td style="padding:6px 10px;text-align:right">{fmt_num(r['top100_v'])}</td>
                <td style="padding:6px 10px;text-align:right">{r['share_pct']:.1f}%</td>
                <td style="padding:6px 10px;text-align:right">{fmt_num(r['avg'])}</td>
                <td style="padding:6px 10px;text-align:right">{r['long_pct']}%</td>
                <td style="padding:6px 10px;text-align:right">{r['age_yrs']:.1f}y</td>
            </tr>"""
        components.html(f"""
        <style>
          .cstat {{ width:100%; border-collapse:collapse; font-size:13px;
                    color:{_T.TEXT};
                    font-family:"Source Sans Pro",sans-serif; }}
          .cstat th {{ padding:8px 10px;
                       border-bottom:2px solid {_T.BORDER_STRONG};
                       text-align:left; cursor:pointer; user-select:none; }}
          .cstat td {{ border-bottom:1px solid {_T.BORDER};
                       vertical-align:middle; }}
          .cstat tr:hover td {{ background:{_T.SURFACE}; }}
        </style>
        <table class="cstat"><thead><tr>
          <th style="text-align:right">#</th>
          <th></th>
          <th>Team</th>
          <th>Confederation</th>
          <th style="text-align:right">Subs</th>
          <th style="text-align:right">Lifetime views</th>
          <th style="text-align:right">Top-100 views</th>
          <th style="text-align:right">Share</th>
          <th style="text-align:right">Avg views/video</th>
          <th style="text-align:right">% long-form</th>
          <th style="text-align:right">Avg age</th>
        </tr></thead><tbody>{tbl_html}</tbody></table>
        """, height=len(rows) * 36 + 60, scrolling=True)
    else:
        st.caption("Per-channel `top100_views` not populated yet — the "
                   "daily WC2026 cron writes these in its 4b step. "
                   "First populated run will fill the table.")
