"""FIFA World Cup 2026 — Trends.

View growth and publishing output (videos by format) for every
official WC2026 channel — the 48 qualified national teams + FIFA +
6 confederations + per-country alt channels — on the road to the
tournament.

Data source: the `channel_snapshots` table, written nightly by
scripts/daily_wc2026.py (00:00 UTC ≈ 02:00 CET). This page is
read-only — no YouTube API calls, no writes. Deliberately isolated
from the global filter, same as the WC2026 All Channels page.

Window is anchored at 2026-05-14, the first night the dedicated cron
gave full coverage of every channel (earlier nights only captured the
handful that overlap daily_federations, which would draw a fake cliff).
"""
from __future__ import annotations

import os
from collections import defaultdict

import streamlit as st
import streamlit.components.v1 as _components
from dotenv import load_dotenv

from src.database import Database
from src.cached_db import get_all_channels as _cached_channels
from src.analytics import fmt_num, fmt_date, kpi_row
from src.auth import require_login
from src.dot import flag_span, dual_dot

load_dotenv()
require_login()

# First night the dedicated daily_wc2026 cron gave full coverage of
# every WC2026 channel. Everything on this page starts here.
TRACKING_START = "2026-05-14"

st.title("FIFA World Cup 2026 — Trends")
st.caption(
    "View gains and videos published (long / shorts / live) for every "
    "official WC2026 channel, night by night, on the road to the "
    "tournament. Snapshotted nightly (~02:00 CET). Alt channels are "
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
        team_color.setdefault(t, (c.get("color") or "#636EFA",
                                  c.get("color2") or "#FFFFFF"))
    team_conf.setdefault(t, _wc(c).get("confederation") or "")


@st.cache_data(ttl=1800, show_spinner=False)
def _load_wc_snapshots(cid_tuple: tuple[str, ...]) -> list[dict]:
    """Paginated read of channel_snapshots for the WC2026 channel ids.
    Cached 30 min — the data only changes once a night."""
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

dates = sorted(by_date.keys())


def _g(snap, k) -> int:
    return int((snap or {}).get(k) or 0)


# ── Not enough nights yet → show the latest snapshot + explain ────
if len(dates) < 2:
    _d0 = dates[-1] if dates else None
    if _d0 is None:
        st.info(
            "No snapshots captured yet for the WC2026 channels. The "
            "nightly **Daily WC2026** cron writes the first row at "
            "00:00 UTC."
        )
        st.stop()
    cur = by_date[_d0]
    _views = sum(_g(r, "total_views") for r in cur.values())
    _vids = sum(_g(r, "video_count") for r in cur.values())
    st.markdown(kpi_row([
        ("Latest snapshot", fmt_date(_d0), f"{len(cur)} channels"),
        ("Total views", fmt_num(_views), ""),
        ("Total videos", fmt_num(_vids), ""),
        ("Nights tracked", str(len(dates)), "need ≥ 2"),
    ]), unsafe_allow_html=True)
    st.info(
        f"Full nightly coverage began {fmt_date(TRACKING_START)} — the "
        "delta charts need at least two nights to compare. They'll "
        "appear after the next snapshot (~02:00 CET) and fill out as "
        "the tournament approaches."
    )
    st.stop()

first_d, last_d = dates[0], dates[-1]
import pandas as pd
import plotly.express as px

# Stable cohort = channels present on EVERY night in the window, so a
# late-added channel can never masquerade as a spike in growth.
cohort = set(by_date[dates[0]])
for d in dates[1:]:
    cohort &= set(by_date[d])
cohort = sorted(cohort)

# ── Per-night deltas (charts) ─────────────────────────────────────
# Δ views and net-new videos by format, summed over the cohort, for
# each consecutive night pair.
night_rows = []
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
    night_rows.append({"Date": d, "Δ Views": dv,
                        "Long": dl, "Shorts": ds, "Live": dli})
dfd = pd.DataFrame(night_rows)
dfd["Date"] = pd.to_datetime(dfd["Date"])

# ── Window totals (KPI + movers) — first vs last night ────────────
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
    return f"{'+' if x >= 0 else ''}{fmt_num(x)}"


_n_nights = len(dates)
st.markdown(kpi_row([
    ("Tracking since", fmt_date(first_d),
     f"{_n_nights} nights · {len(cohort)} channels"),
    ("Views gained", _sg(net_views), "across all channels"),
    ("Videos added", _sg(net_vids),
     f"{_sg(net_long)} long · {_sg(net_short)} short · {_sg(net_live)} live"),
    ("Subscribers", _sg(net_subs), "YouTube-rounded · directional"),
]), unsafe_allow_html=True)

# ── Charts ────────────────────────────────────────────────────────
_PLOT = dict(
    plot_bgcolor="#0E1117", paper_bgcolor="#0E1117",
    font=dict(color="#FAFAFA"), height=340,
    margin=dict(t=30, l=0, r=0, b=20),
)
gc1, gc2 = st.columns(2)
with gc1:
    st.subheader("👁️ Views gained per night")
    fv = px.bar(dfd, x="Date", y="Δ Views")
    fv.update_traces(marker_color="#636EFA")
    fv.update_layout(**_PLOT)
    fv.update_xaxes(gridcolor="#262730", tickformat="%b %d", title="")
    fv.update_yaxes(gridcolor="#262730", title="")
    st.plotly_chart(fv, use_container_width=True)
with gc2:
    st.subheader("🎬 Videos added per night")
    fl = px.bar(
        dfd, x="Date", y=["Long", "Shorts", "Live"],
        color_discrete_map={"Long": "#636EFA", "Shorts": "#00CC96",
                            "Live": "#EF553B"},
    )
    fl.update_layout(barmode="stack", **_PLOT)
    fl.update_layout(legend=dict(orientation="h", yanchor="bottom",
                                 y=1.02, xanchor="left", x=0,
                                 title=""))
    fl.update_xaxes(gridcolor="#262730", tickformat="%b %d", title="")
    fl.update_yaxes(gridcolor="#262730", title="")
    st.plotly_chart(fl, use_container_width=True)

st.caption(
    f"Per-night change across the {len(cohort)} channels present on every "
    f"night since {fmt_date(first_d)}. Video counts are channel-level "
    "totals, so a negative bar means videos were removed or made "
    "private. Subscriber numbers are YouTube-rounded — directional only."
)

# ── Biggest movers — one row per country, ranked by Δ views ───────
st.markdown("---")
st.subheader(f"🚀 Biggest movers · {fmt_date(first_d)} → {fmt_date(last_d)}")

_TBL_CSS = (
    "<style>"
    "body{margin:0;background:#0E1117;color:#FAFAFA;"
    "font-family:'Source Sans Pro',sans-serif}"
    ".mv{width:100%;border-collapse:collapse;font-size:14px;color:#FAFAFA}"
    ".mv th,.mv td{padding:6px 12px;white-space:nowrap}"
    ".mv th{border-bottom:2px solid #444;font-weight:600;text-align:right}"
    ".mv td{border-bottom:1px solid #262730;text-align:right}"
    ".mv th.l,.mv td.l{text-align:left}"
    ".mv tr:hover td{background:#1a1c24}"
    "</style>"
)


def _marker(team: str) -> str:
    flag = TEAM_FLAG.get(team, "")
    if flag:
        return flag_span(flag, 14)
    if team_is_gov.get(team):
        c1, c2 = team_color.get(team, ("#636EFA", "#FFFFFF"))
        return dual_dot(c1, c2, 14)
    return flag_span("", 14)


def _col(x: int) -> str:
    return "#00CC96" if x > 0 else ("#EF553B" if x < 0 else "#888")


ranked = sorted(team_dviews.items(), key=lambda kv: -kv[1])
body = ""
for i, (team, dv) in enumerate(ranked, 1):
    conf = team_conf.get(team, "") or "—"
    nv = team_dvids.get(team, 0)
    L, S, Li = team_dl[team], team_ds[team], team_dli[team]
    dsub = team_dsubs.get(team, 0)
    body += (
        f"<tr><td>{i}</td>"
        f"<td class='l'><div style='display:flex;align-items:center;"
        f"gap:8px'>{_marker(team)}<span>{team}</span></div></td>"
        f"<td class='l' style='color:#aaa'>{conf}</td>"
        f"<td style='color:{_col(dv)};font-weight:600'>{_sg(dv)}</td>"
        f"<td style='color:{_col(nv)}'>{_sg(nv)}</td>"
        f"<td style='color:#888'>{_sg(L)} / {_sg(S)} / {_sg(Li)}</td>"
        f"<td style='color:{_col(dsub)}'>{_sg(dsub)}</td>"
        f"</tr>"
    )

table_html = (
    _TBL_CSS
    + "<table class='mv'><thead><tr>"
    "<th>#</th><th class='l'>Team</th><th class='l'>Confederation</th>"
    "<th>Δ Views</th><th>Δ Videos</th>"
    "<th>Long / Short / Live</th><th>Δ Subs</th>"
    f"</tr></thead><tbody>{body}</tbody></table>"
)
_components.html(
    table_html,
    height=min(2000, 37 * len(ranked) + 70),
    scrolling=True,
)

st.caption(
    "One row per country, ranked by view gain over the tracked window. "
    "Δ Videos is the net change in channel video count split into "
    "long / shorts / live. Alt and federation channels are summed into "
    "their country's row, matching the All Channels table."
)
