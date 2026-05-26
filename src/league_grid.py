"""League channel grid — a hidden, link-reached view (no menu entry).

Reached from the Season (Z2) page via `?view=league-grid` — same
query-param pattern as the Latest "feed" mode, so it never shows in the
sidebar nav. Renders the "Videos per day" bar chart for every channel in
the selected league, stacked in a list ordered by total season videos,
to eyeball each club's publishing rhythm side by side.
"""
from __future__ import annotations

import urllib.parse as _up
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from datetime import date as _date, datetime as _dt, timezone as _tz
from zoneinfo import ZoneInfo

from src.database import _fetch_all
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_COLOR_CHART, get_season_since
from src.dot import channel_badge, dual_dot
from src.heatmap import heatmap_figure, peak_label

_CET = ZoneInfo("Europe/Rome")


@st.cache_data(ttl=900, show_spinner=False)
def _load(_db, ids_tuple: tuple[str, ...], since: str) -> dict:
    """{channel_id: {date: count}} of videos published since season start,
    for the league's channels. One paginated scan, bucketed in Python."""
    if not ids_tuple:
        return {}
    rows = _fetch_all(
        _db.client.table("videos")
        .select("channel_id,published_at")
        .gte("published_at", since)
        .in_("channel_id", list(ids_tuple))
    )
    per: dict[str, Counter] = {}
    for r in rows:
        cid = r.get("channel_id")
        pa = r.get("published_at") or ""
        if not cid or not pa:
            continue
        try:
            d = pd.to_datetime(pa, utc=True).date()
        except Exception:
            continue
        per.setdefault(cid, Counter())[d] += 1
    return {cid: dict(c) for cid, c in per.items()}


def _chart(dates: list, vals: list[int], avg: float,
           y_max: int | None = None) -> go.Figure:
    """The per-channel videos-per-day bar (weekends orange + avg line) —
    same style as the Season page's Z3 club chart, compact for stacking.
    When y_max is set, the y-axis is pinned to [0, y_max*1.05] so multiple
    rows compare apples-to-apples; otherwise plotly auto-scales each."""
    colors = ["#FFA15A" if d.weekday() >= 5 else "#636EFA" for d in dates]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=dates, y=vals, marker_color=colors,
        hovertemplate="<b>%{x|%a %b %d, %Y}</b><br>"
                      "%{y} video(s)<extra></extra>",
    ))
    fig.add_hline(y=avg, line_dash="dash", line_color="rgba(255,255,255,0.35)",
                  annotation_text=f"avg {avg:.1f}",
                  annotation_position="top right",
                  annotation_font_color="rgba(255,255,255,0.55)")
    yaxis = dict(title="", showgrid=True, gridcolor="rgba(255,255,255,0.08)")
    if y_max is not None and y_max > 0:
        yaxis["range"] = [0, y_max * 1.05]
    fig.update_layout(
        height=240,
        xaxis=dict(title="", showgrid=False),
        yaxis=yaxis,
        margin=dict(t=10, b=30, l=50, r=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"), showlegend=False, bargap=0.15,
    )
    return fig


def render_league_grid(db, league: str, channels: list[dict],
                       color_map: dict | None = None,
                       dual_map: dict | None = None) -> None:
    st.title(f"📊 {league} — videos per day, all channels")
    st.markdown(
        f"<a href='?league={_up.quote(league)}' target='_self' "
        f"style='color:#58A6FF;text-decoration:none'>← Back to Season</a>",
        unsafe_allow_html=True,
    )

    league_ch = [
        c for c in channels
        if c.get("entity_type") in ("Club", "League")
        and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip()) == league
        and c.get("id")
    ]
    if not league_ch:
        st.info(f"No channels found for {league}.")
        return

    since = get_season_since(league=league)
    per = _load(db, tuple(sorted(c["id"] for c in league_ch)), since)
    name_of = {c["id"]: (c.get("name") or "?") for c in league_ch}
    totals = {c["id"]: sum((per.get(c["id"]) or {}).values()) for c in league_ch}
    ordered = sorted(league_ch, key=lambda c: -totals.get(c["id"], 0))

    # Calendar span (season start → today) so "0-video days" counts real
    # silent days, not just the gaps between uploads.
    season_start = _date.fromisoformat(since[:10])
    cal_days = max((_dt.now(_tz.utc).date() - season_start).days + 1, 1)

    st.caption(
        f"{len(ordered)} channels · videos published per day since "
        f"{since[:10]} · weekends in orange. Ordered by total season videos."
    )

    _mode = st.segmented_control(
        "Y-axis", ["Auto-scale", "Same scale"],
        default="Auto-scale", key="lg_z2_y",
        help="Same scale uses the busiest channel's max as the ceiling, "
             "so heights are comparable across rows.")
    _y_max = None
    if _mode == "Same scale":
        _all_vals = [v for cnt in per.values() for v in cnt.values()]
        _y_max = max(_all_vals) if _all_vals else None

    for c in ordered:
        cid = c["id"]
        cnt = per.get(cid) or {}
        total = totals.get(cid, 0)
        zero_days = max(cal_days - len(cnt), 0)
        avg_day = total / cal_days
        zero_pct = zero_days / cal_days * 100
        badge = channel_badge(c, color_map, dual_map, 16)
        st.markdown(
            f"<div style='font-size:16px;margin-top:2px'>{badge} "
            f"<b style='vertical-align:middle'>{name_of[cid]}</b>"
            f"<span style='color:#9aa0aa;font-size:13px;vertical-align:middle'>"
            f" — season videos {total:,} · avg {avg_day:.1f}/day · "
            f"{zero_days} days with no videos ({zero_pct:.0f}%)</span></div>",
            unsafe_allow_html=True,
        )
        if not cnt:
            st.caption("No videos this season.")
            continue
        dates = sorted(cnt.keys())
        vals = [cnt[d] for d in dates]
        avg = sum(vals) / len(vals) if vals else 0
        st.plotly_chart(_chart(dates, vals, avg, y_max=_y_max),
                        width="stretch", key=f"vpd_grid_{cid}")
        st.markdown("<hr style='border:none;border-top:1px solid #2a2c34;"
                    "margin:4px 0 14px 0'>", unsafe_allow_html=True)


def render_all_leagues_grid(db, channels: list[dict],
                            color_map: dict | None = None,
                            dual_map: dict | None = None) -> None:
    """Z1 analog of render_league_grid: one "Videos per day" chart per
    LEAGUE (aggregated across that league's channels), ordered by total
    season videos. Reached from the Season Z1 page via
    ?view=all-leagues-grid. Row badge = the League HQ's country flag."""
    st.title("📊 All leagues — videos per day")
    st.markdown(
        "<a href='?' target='_self' "
        "style='color:#58A6FF;text-decoration:none'>← Back to Season</a>",
        unsafe_allow_html=True,
    )

    top5 = [c for c in channels
            if c.get("entity_type") in ("Club", "League")
            and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
            and c.get("id")]
    if not top5:
        st.info("No league channels found.")
        return
    ch_to_league = {
        c["id"]: COUNTRY_TO_LEAGUE[(c.get("country") or "").strip()]
        for c in top5
    }
    # One League HQ per league → its country flag is the row badge.
    hq_of: dict[str, dict] = {}
    for c in top5:
        if c.get("entity_type") == "League":
            hq_of.setdefault(ch_to_league[c["id"]], c)

    leagues = sorted(set(ch_to_league.values()))
    since_of = {lg: get_season_since(league=lg) for lg in leagues}
    per = _load(db, tuple(sorted(c["id"] for c in top5)),
                min(since_of.values()))
    # Aggregate per (league, day).
    league_day: dict[str, Counter] = {lg: Counter() for lg in leagues}
    for cid, cnt in per.items():
        lg = ch_to_league.get(cid)
        if not lg:
            continue
        for d, n in cnt.items():
            league_day[lg][d] += n
    totals = {lg: sum(c.values()) for lg, c in league_day.items()}
    ordered = sorted(leagues, key=lambda lg: -totals.get(lg, 0))

    st.caption(
        f"{len(ordered)} leagues · videos published per day across each "
        "league's channels · weekends in orange. Ordered by total season "
        "videos."
    )

    _mode = st.segmented_control(
        "Y-axis", ["Auto-scale", "Same scale"],
        default="Auto-scale", key="lg_z1_y",
        help="Same scale uses the busiest league's max as the ceiling, "
             "so heights are comparable across rows.")
    _y_max = None
    if _mode == "Same scale":
        _all_vals = [v for cnt in league_day.values() for v in cnt.values()]
        _y_max = max(_all_vals) if _all_vals else None

    today = _dt.now(_tz.utc).date()
    for lg in ordered:
        cnt = league_day[lg]
        total = totals.get(lg, 0)
        season_start = _date.fromisoformat(since_of[lg][:10])
        cal_days = max((today - season_start).days + 1, 1)
        zero_days = max(cal_days - len(cnt), 0)
        avg_day = total / cal_days
        zero_pct = zero_days / cal_days * 100
        hq = hq_of.get(lg)
        badge = (channel_badge(hq, color_map, dual_map, 16) if hq
                 else dual_dot(LEAGUE_COLOR_CHART.get(lg, "#636EFA"),
                               "#FFFFFF", 16))
        st.markdown(
            f"<div style='font-size:16px;margin-top:2px'>{badge} "
            f"<b style='vertical-align:middle'>{lg}</b>"
            f"<span style='color:#9aa0aa;font-size:13px;vertical-align:middle'>"
            f" — season videos {total:,} · avg {avg_day:.1f}/day · "
            f"{zero_days} days with no videos ({zero_pct:.0f}%)</span></div>",
            unsafe_allow_html=True,
        )
        if not cnt:
            st.caption("No videos this season.")
            continue
        dates = sorted(cnt.keys())
        vals = [cnt[d] for d in dates]
        avg = sum(vals) / len(vals) if vals else 0
        st.plotly_chart(_chart(dates, vals, avg, y_max=_y_max),
                        width="stretch", key=f"vpd_grid_lg_{lg}")
        st.markdown("<hr style='border:none;border-top:1px solid #2a2c34;"
                    "margin:4px 0 14px 0'>", unsafe_allow_html=True)


# ── Publishing-heatmap grids (Z2 per channel, Z1 per league) ──────────
@st.cache_data(ttl=900, show_spinner=False)
def _load_heat(_db, ids_tuple: tuple[str, ...], since: str) -> dict:
    """{channel_id: {"counts": 7x24, "sums": 7x24}} — publishing counts and
    summed views per CET (day-of-week × hour) slot, since season start."""
    if not ids_tuple:
        return {}
    rows = _fetch_all(
        _db.client.table("videos")
        .select("channel_id,published_at,view_count")
        .gte("published_at", since)
        .in_("channel_id", list(ids_tuple))
    )
    per: dict[str, dict] = {}
    for r in rows:
        cid = r.get("channel_id")
        pa = r.get("published_at") or ""
        if not cid or not pa:
            continue
        try:
            dt = pd.to_datetime(pa, utc=True).tz_convert(_CET)
        except Exception:
            continue
        dow, hr = dt.weekday(), dt.hour
        g = per.setdefault(cid, {"counts": [[0] * 24 for _ in range(7)],
                                 "sums": [[0] * 24 for _ in range(7)]})
        g["counts"][dow][hr] += 1
        g["sums"][dow][hr] += int(r.get("view_count") or 0)
    return per


def _avg_grid(counts, sums):
    return [[(sums[r][c] // counts[r][c]) if counts[r][c] else 0
             for c in range(24)] for r in range(7)]


def _grid_total(counts) -> int:
    return sum(counts[r][c] for r in range(7) for c in range(24))


def _heat_row(badge: str, name: str, counts, sums, key: str) -> None:
    total = _grid_total(counts)
    st.markdown(
        f"<div style='font-size:16px;margin-top:2px'>{badge} "
        f"<b style='vertical-align:middle'>{name}</b>"
        f"<span style='color:#9aa0aa;font-size:13px;vertical-align:middle'>"
        f" — season videos {total:,} · {peak_label(counts)}</span></div>",
        unsafe_allow_html=True,
    )
    if not total:
        st.caption("No videos this season.")
        return
    st.plotly_chart(heatmap_figure(counts, _avg_grid(counts, sums)),
                    width="stretch", key=key)
    st.markdown("<hr style='border:none;border-top:1px solid #2a2c34;"
                "margin:4px 0 14px 0'>", unsafe_allow_html=True)


def render_league_heatmaps(db, league: str, channels: list[dict],
                           color_map: dict | None = None,
                           dual_map: dict | None = None) -> None:
    """Z2 analog of render_league_grid for the publishing heatmap: one
    heatmap per channel in the league, ordered by total season videos."""
    st.title(f"📅 {league} — publishing heatmap, all channels")
    st.markdown(
        f"<a href='?league={_up.quote(league)}' target='_self' "
        f"style='color:#58A6FF;text-decoration:none'>← Back to Season</a>",
        unsafe_allow_html=True,
    )
    league_ch = [c for c in channels
                 if c.get("entity_type") in ("Club", "League")
                 and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip()) == league
                 and c.get("id")]
    if not league_ch:
        st.info(f"No channels found for {league}.")
        return
    since = get_season_since(league=league)
    per = _load_heat(db, tuple(sorted(c["id"] for c in league_ch)), since)
    name_of = {c["id"]: (c.get("name") or "?") for c in league_ch}
    totals = {c["id"]: _grid_total((per.get(c["id"]) or {}).get(
        "counts", [[0] * 24 for _ in range(7)])) for c in league_ch}
    ordered = sorted(league_ch, key=lambda c: -totals.get(c["id"], 0))
    st.caption(
        f"{len(ordered)} channels · day-of-week × hour (CET) since "
        f"{since[:10]} · brighter = more videos. Ordered by total season "
        "videos."
    )
    _empty = {"counts": [[0] * 24 for _ in range(7)],
              "sums": [[0] * 24 for _ in range(7)]}
    for c in ordered:
        g = per.get(c["id"]) or _empty
        _heat_row(channel_badge(c, color_map, dual_map, 16),
                  name_of[c["id"]], g["counts"], g["sums"],
                  key=f"heat_grid_{c['id']}")


def render_all_leagues_heatmaps(db, channels: list[dict],
                                color_map: dict | None = None,
                                dual_map: dict | None = None) -> None:
    """Z1 analog: one publishing heatmap per league (channels combined),
    ordered by total season videos. Row badge = League HQ country flag."""
    st.title("📅 All leagues — publishing heatmap")
    st.markdown(
        "<a href='?' target='_self' "
        "style='color:#58A6FF;text-decoration:none'>← Back to Season</a>",
        unsafe_allow_html=True,
    )
    top5 = [c for c in channels
            if c.get("entity_type") in ("Club", "League")
            and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
            and c.get("id")]
    if not top5:
        st.info("No league channels found.")
        return
    ch_to_league = {c["id"]: COUNTRY_TO_LEAGUE[(c.get("country") or "").strip()]
                    for c in top5}
    hq_of: dict[str, dict] = {}
    for c in top5:
        if c.get("entity_type") == "League":
            hq_of.setdefault(ch_to_league[c["id"]], c)
    leagues = sorted(set(ch_to_league.values()))
    since_of = {lg: get_season_since(league=lg) for lg in leagues}
    per = _load_heat(db, tuple(sorted(c["id"] for c in top5)),
                     min(since_of.values()))
    # Aggregate per-channel grids into per-league grids.
    agg = {lg: {"counts": [[0] * 24 for _ in range(7)],
                "sums": [[0] * 24 for _ in range(7)]} for lg in leagues}
    for cid, g in per.items():
        lg = ch_to_league.get(cid)
        if not lg:
            continue
        for r in range(7):
            for c in range(24):
                agg[lg]["counts"][r][c] += g["counts"][r][c]
                agg[lg]["sums"][r][c] += g["sums"][r][c]
    totals = {lg: _grid_total(agg[lg]["counts"]) for lg in leagues}
    ordered = sorted(leagues, key=lambda lg: -totals.get(lg, 0))
    st.caption(
        f"{len(ordered)} leagues · day-of-week × hour (CET) across each "
        "league's channels · brighter = more videos. Ordered by total "
        "season videos."
    )
    for lg in ordered:
        hq = hq_of.get(lg)
        badge = (channel_badge(hq, color_map, dual_map, 16) if hq
                 else dual_dot(LEAGUE_COLOR_CHART.get(lg, "#636EFA"),
                               "#FFFFFF", 16))
        _heat_row(badge, lg, agg[lg]["counts"], agg[lg]["sums"],
                  key=f"heat_grid_lg_{lg}")
