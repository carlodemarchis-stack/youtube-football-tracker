"""FIFA World Cup 2026 — Trends.

View growth and publishing output (videos by format) for every
official WC2026 channel — the 48 qualified national teams + FIFA +
6 confederations + per-country alt channels — on the road to the
tournament.

Data source: the `channel_snapshots` table, written daily by
scripts/daily_wc2026.py (00:00 UTC ≈ 02:00 CET). This page is
read-only — no YouTube API calls, no writes. Deliberately isolated
from the global filter, same as the WC2026 All Channels page.

Window is anchored at 2026-05-14, the first day the dedicated cron
gave full coverage of every channel (earlier days only captured the
handful that overlap daily_federations, which would draw a fake cliff).
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import date as _date, datetime as _datetime
from zoneinfo import ZoneInfo as _ZoneInfo

import streamlit as st
from src import components_compat as _components
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, fmt_date, kpi_row
from src.charts import readable_hover
from src.auth import require_login
from src.dot import flag_span, dual_dot
from src import theme as _T
from src.wc_table import td as _td, render_sortable_table as _render_tbl

load_dotenv()
require_login()

# First day the dedicated daily_wc2026 cron gave full coverage of
# every WC2026 channel. Everything on this page starts here.
TRACKING_START = "2026-05-14"


def _absd(iso: str) -> str:
    """Absolute, unambiguous date (e.g. 'May 14, 2026') — used where a
    caption must be specific about the window. Deliberately NOT
    analytics.fmt_date, which is fuzzy/relative ('3d ago')."""
    try:
        return _date.fromisoformat(str(iso)[:10]).strftime("%b %d, %Y")
    except Exception:
        return str(iso)

st.title("FIFA World Cup 2026 — Trends")
st.caption(
    "View gains and videos published (long / shorts / live) for every "
    "official WC2026 channel, day by day, on the road to the "
    "tournament. Snapshotted daily (~02:00 CET). Alt channels are "
    "rolled into their country's totals — same convention as the "
    "All Channels table."
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)

# Live channel list (cached 30 min) — we need the DB `id` to join against
# channel_snapshots, plus competitions.wc2026 for team / role / flag.
channels = _cached_channels(db)
wc = [c for c in channels if (c.get("competitions") or {}).get("wc2026")]
if not wc:
    st.warning(
        "No channels with `competitions.wc2026` set yet — see the "
        "**All Channels** page for setup steps."
    )
    st.stop()

# WC2026 sub-app filter (confederation → team), shared across the
# WC2026 pages. Scoping `wc` here cascades through ch_by_id →
# channel_snapshots query → cohort → biggest movers automatically.
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


def _wc(c):
    return c.get("competitions", {}).get("wc2026", {}) or {}


def _team_of(c):
    """Grouping key — alts roll into their country, governing bodies
    keep their own name."""
    return _wc(c).get("team") or c.get("country") or c.get("name") or "—"


# Team → flag emoji, keyed by the WC2026 `team` value. Kept in sync with
# views/18_WC2026.py (England / Scotland use the ISO 3166-2 subdivision
# tag sequence rather than the UK flag — they qualify separately).
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

# id → channel record (snapshots key on the DB id)
ch_by_id = {c["id"]: c for c in wc if c.get("id")}
team_of_id = {cid: _team_of(c) for cid, c in ch_by_id.items()}
# Per team: is it a governing body (FIFA / confederation) — used for the
# marker (two-color dot) when there's no national flag.
team_is_gov: dict[str, bool] = {}
team_color: dict[str, tuple] = {}
team_conf: dict[str, str] = {}
for c in wc:
    t = _team_of(c)
    if c.get("entity_type") == "GoverningBody":
        team_is_gov[t] = True
        team_color.setdefault(t, (c.get("color") or _T.ACCENT,
                                  c.get("color2") or _T.WHITE))
    team_conf.setdefault(t, _wc(c).get("confederation") or "")


def _ch_url(c) -> str:
    """Same URL resolution as the WC2026 All Channels page: explicit
    competitions.wc2026.youtube_url override → @handle → /channel/<id>."""
    w = _wc(c)
    yt_id = c.get("youtube_channel_id") or ""
    handle = (c.get("handle") or "").lstrip("@")
    return (
        w.get("youtube_url")
        or (f"https://www.youtube.com/@{handle}" if handle
            else f"https://www.youtube.com/channel/{yt_id}")
    )


# Team → canonical channel URL. Alts fold into the team row, so link
# the primary (team/governing_body) channel with the most subs; fall
# back to the biggest alt if a team has no primary.
def _is_primary(c) -> bool:
    return (_wc(c).get("role") or "team") in ("team", "governing_body")


team_url: dict[str, str] = {}
for c in sorted(
    wc,
    key=lambda x: (0 if _is_primary(x) else 1,
                   -(int(x.get("subscriber_count") or 0))),
):
    team_url.setdefault(_team_of(c), _ch_url(c))


@st.cache_data(ttl=1800, show_spinner=False)
def _load_wc_snapshots(cid_tuple: tuple[str, ...]) -> list[dict]:
    """Paginated read of channel_snapshots for the WC2026 channel ids.
    Cached 30 min — the data only changes once a day."""
    ids = list(cid_tuple)
    if not ids:
        return []
    out: list[dict] = []
    page = 1000
    offset = 0
    while True:
        resp = (
            db.client.table("channel_snapshots")
            .select("channel_id,captured_date,subscriber_count,"
                    "total_views,video_count,long_form_count,"
                    "shorts_count,live_count")
            .in_("channel_id", ids)
            .gte("captured_date", TRACKING_START)
            .order("captured_date")
            .range(offset, offset + page - 1)
            .execute()
        )
        rows = resp.data or []
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


snaps = _load_wc_snapshots(tuple(sorted(ch_by_id.keys())))

# date -> {channel_id -> snapshot row}
by_date: dict[str, dict[str, dict]] = defaultdict(dict)
for s in snaps:
    by_date[s["captured_date"]][s["channel_id"]] = s

# Drop today (CET): the daily_wc2026 cron writes at 00:00 UTC ≈ 02:00 CET,
# which is "today's first row" — but it represents only a few hours of
# growth at best, and routinely hits YouTube's frozen-aggregate window
# so the cohort Δ for today comes back ~0. Either way, today is partial
# and would distort the chart's right edge. Tomorrow's row (May 22 at
# 00:00 UTC) becomes the new "last complete day" automatically.
_today_cet_iso = _datetime.now(_ZoneInfo("Europe/Rome")).date().isoformat()
by_date = {d: v for d, v in by_date.items() if d < _today_cet_iso}

dates = sorted(by_date.keys())


def _g(snap, k) -> int:
    return int((snap or {}).get(k) or 0)


# ── Not enough days yet → show the latest snapshot + explain ──────
if len(dates) < 2:
    _d0 = dates[-1] if dates else None
    if _d0 is None:
        st.info(
            "No snapshots captured yet for the WC2026 channels. The "
            "**Daily WC2026** cron writes the first row at 00:00 UTC."
        )
        st.stop()
    cur = by_date[_d0]
    _views = sum(_g(r, "total_views") for r in cur.values())
    _vids = sum(_g(r, "video_count") for r in cur.values())
    st.markdown(kpi_row([
        ("Latest snapshot", fmt_date(_d0), f"{len(cur)} channels"),
        ("Total views", fmt_num(_views), ""),
        ("Total videos", fmt_num(_vids), ""),
        ("Days tracked", str(len(dates)), "need ≥ 2"),
    ]), unsafe_allow_html=True)
    st.info(
        f"Full daily coverage began {fmt_date(TRACKING_START)} — the "
        "delta charts need at least two days to compare. They'll "
        "appear after the next snapshot (~02:00 CET) and fill out as "
        "the tournament approaches."
    )
    st.stop()

first_d, last_d = dates[0], dates[-1]
import pandas as pd
import plotly.express as px

# Stable cohort = channels present on EVERY day in the window, so a
# late-added channel can never masquerade as a spike in growth.
cohort = set(by_date[dates[0]])
for d in dates[1:]:
    cohort &= set(by_date[d])
cohort = sorted(cohort)

# ── Per-day deltas (charts) ───────────────────────────────────────
# Δ views and net-new videos by format, summed over the cohort, for
# each consecutive day pair.
day_rows = []
for i in range(1, len(dates)):
    d, dp = dates[i], dates[i - 1]
    cur, prev = by_date[d], by_date[dp]
    dv = dl = ds = dli = 0
    for c in cohort:
        cc, pp = cur.get(c), prev.get(c)
        dv += _g(cc, "total_views") - _g(pp, "total_views")
        dl += _g(cc, "long_form_count") - _g(pp, "long_form_count")
        ds += _g(cc, "shorts_count") - _g(pp, "shorts_count")
        dli += _g(cc, "live_count") - _g(pp, "live_count")
    day_rows.append({"Date": d, "Δ Views": dv,
                     "Long": dl, "Shorts": ds, "Live": dli})
dfd = pd.DataFrame(day_rows)
dfd["Date"] = pd.to_datetime(dfd["Date"])

# ── Window totals (KPI + movers) — first vs last day ──────────────
f, l = by_date[first_d], by_date[last_d]


def _net(key: str) -> int:
    return sum(_g(l.get(c), key) - _g(f.get(c), key) for c in cohort)


net_views = _net("total_views")
net_subs = _net("subscriber_count")
net_long = _net("long_form_count")
net_short = _net("shorts_count")
net_live = _net("live_count")
net_vids = net_long + net_short + net_live

# Per-team rollup (alts + governing bodies fold into their country).
team_dviews: dict[str, int] = defaultdict(int)
team_dsubs: dict[str, int] = defaultdict(int)
team_dl: dict[str, int] = defaultdict(int)
team_ds: dict[str, int] = defaultdict(int)
team_dli: dict[str, int] = defaultdict(int)
for c in cohort:
    t = team_of_id.get(c, "—")
    team_dviews[t] += _g(l.get(c), "total_views") - _g(f.get(c), "total_views")
    team_dsubs[t] += _g(l.get(c), "subscriber_count") - _g(f.get(c), "subscriber_count")
    team_dl[t] += _g(l.get(c), "long_form_count") - _g(f.get(c), "long_form_count")
    team_ds[t] += _g(l.get(c), "shorts_count") - _g(f.get(c), "shorts_count")
    team_dli[t] += _g(l.get(c), "live_count") - _g(f.get(c), "live_count")
team_dvids = {t: team_dl[t] + team_ds[t] + team_dli[t] for t in team_dviews}


def _sg(x: int) -> str:
    # No leading "+" — a gain is obvious; negatives keep their "-".
    return fmt_num(x)


_n_days = len(dates)
st.markdown(kpi_row([
    ("Tracking since", fmt_date(first_d),
     f"{_n_days} days · {len(cohort)} channels"),
    ("Views gained", _sg(net_views), "across all channels"),
    ("Videos added", _sg(net_vids),
     f"{_sg(net_long)} long · {_sg(net_short)} short · {_sg(net_live)} live"),
    ("Subscribers", _sg(net_subs), "YouTube-rounded · directional"),
]), unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────
_PLOT = dict(
    plot_bgcolor=_T.BG, paper_bgcolor=_T.BG,
    font=dict(color=_T.TEXT), height=340,
    margin=dict(t=30, l=0, r=0, b=20),
)
# The first per-day row is the partial bootstrap day (tracking only
# just began that day), so its delta is noise — drop it from the
# charts only. KPIs/movers above still use the full window.
dfc = dfd.iloc[1:] if len(dfd) > 1 else dfd
gc1, gc2 = st.columns(2)
with gc1:
    st.subheader("👁️ Views gained per day")
    fv = px.line(dfc, x="Date", y="Δ Views", markers=True)
    fv.update_traces(line_color=_T.ACCENT, marker_color=_T.ACCENT)
    fv.update_layout(**_PLOT)
    fv.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
    fv.update_yaxes(gridcolor=_T.BORDER, title="")
    readable_hover(fv, x_date=True)
    st.plotly_chart(fv, width="stretch")
with gc2:
    st.subheader("🎬 Videos added per day")
    fl = px.bar(
        dfc, x="Date", y=["Long", "Shorts", "Live"],
        color_discrete_map={"Long": _T.ACCENT, "Shorts": _T.POS,
                            "Live": _T.WARN},
    )
    fl.update_layout(barmode="stack", **_PLOT)
    fl.update_layout(legend=dict(orientation="h", yanchor="bottom",
                                 y=1.02, xanchor="left", x=0,
                                 title=""))
    fl.update_xaxes(gridcolor=_T.BORDER, tickformat="%b %d", title="")
    fl.update_yaxes(gridcolor=_T.BORDER, title="")
    readable_hover(fl, x_date=True)
    st.plotly_chart(fl, width="stretch")

st.caption(
    f"Per-day change across the {len(cohort)} channels present every "
    f"day since {fmt_date(first_d)}. Video counts are channel-level "
    "totals, so a negative bar means videos were removed or made "
    "private. Subscriber numbers are YouTube-rounded — directional only."
)

# ── Biggest movers — one row per country ──────────────────────────
st.markdown("---")
st.subheader("🚀 Biggest movers")
st.caption(
    f"Cumulative change from {_absd(first_d)} to {_absd(last_d)} "
    f"({_n_days} daily snapshots) — each team's {_absd(last_d)} channel "
    f"totals minus its {_absd(first_d)} totals, across the "
    f"{len(cohort)} channels tracked every day in that window. "
    "Not a per-day or last-24h figure."
)

def _marker(team: str) -> str:
    flag = TEAM_FLAG.get(team, "")
    if flag:
        return flag_span(flag, 14)
    if team_is_gov.get(team):
        c1, c2 = team_color.get(team, (_T.ACCENT, _T.WHITE))
        return dual_dot(c1, c2, 14)
    return flag_span("", 14)


def _col(x: int) -> str:
    return _T.POS if x > 0 else (_T.NEG if x < 0 else _T.MUTED)


# Same look + sort behaviour as the WC2026 All Channels table (shared
# src.wc_table). "Long / Short / Live" is a composite cell → not
# sortable (4th tuple element False).
_MOVER_COLS = [
    ("#",                   "num", "right"),
    ("Team",                "str", "left"),
    ("Confederation",       "str", "left"),
    ("Δ Views",             "num", "right"),
    ("Δ Videos",            "num", "right"),
    ("Long / Short / Live", "str", "right", False),
    ("Δ Subs",              "num", "right"),
]

ranked = sorted(team_dviews.items(), key=lambda kv: -kv[1])
mover_rows = []
for i, (team, dv) in enumerate(ranked, 1):
    conf = team_conf.get(team, "") or "—"
    nv = team_dvids.get(team, 0)
    L, S, Li = team_dl[team], team_ds[team], team_dli[team]
    dsub = team_dsubs.get(team, 0)
    url = team_url.get(team, "")
    name_html = (
        f"<a href='{url}' target='_blank' rel='noopener'>{team}</a>"
        if url else f"<span>{team}</span>"
    )
    mover_rows.append(
        "<tr>"
        + _td(i, str(i))
        + _td(team.lower(),
              f"<div style='display:flex;align-items:center;gap:8px'>"
              f"{_marker(team)}{name_html}</div>", align="left")
        + _td(conf.lower(),
              f"<span style='color:{_T.MUTED_2}'>{conf}</span>",
              align="left")
        + _td(dv, f"<span style='color:{_col(dv)};font-weight:600'>"
                  f"{_sg(dv)}</span>")
        + _td(nv, f"<span style='color:{_col(nv)}'>{_sg(nv)}</span>")
        + _td(L, f"<span style='color:{_T.MUTED}'>"
                 f"{_sg(L)} / {_sg(S)} / {_sg(Li)}</span>")
        + _td(dsub, f"<span style='color:{_col(dsub)}'>{_sg(dsub)}</span>")
        + "</tr>"
    )

_components.html(
    _render_tbl(_MOVER_COLS, mover_rows, "wc-tbl-movers",
                default_col=3, default_asc=False),
    height=min(2000, 36 * len(ranked) + 80),
    scrolling=True,
)

st.caption(
    "One row per country. Click any column header to sort; click a team "
    "to open its channel on YouTube. Δ Videos is the net change in "
    "channel video count split into long / shorts / live. Alt and "
    "federation channels are summed into their country's row, matching "
    "the All Channels table."
)
