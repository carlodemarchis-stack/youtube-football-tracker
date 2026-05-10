"""Digital Footprint — owned & operated estate per club.

Three zoom levels matched to the global header filter:

  Z1 (All Leagues)  : league-vs-league comparison + scatter
  Z2 (One League)   : ranking table + per-club bars
  Z3 (One Club)     : profile card with web + iOS + Wikipedia detail

Reads from data/digital_footprint.json (yearly snapshot built by
scripts/collect_digital_footprint.py). Performance trustability rules
followed: CrUX numbers are real-user (defensible), PSI Performance
score is excluded (synthetic noise), only PSI a11y/BP/SEO are shown.
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from src.analytics import kpi_row, fmt_num
from src.auth import require_login
from src.channels import LEAGUE_FLAG, league_with_flag
from src.filters import (
    get_global_filter, render_league_header, render_page_subtitle,
)

require_login()

st.title("Digital Footprint")

# ── Load snapshot ─────────────────────────────────────────────────
SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "digital_footprint.json"
if not SNAPSHOT_PATH.exists():
    st.warning("Digital Footprint snapshot not built yet. "
               "Run `python3 scripts/collect_digital_footprint.py`.")
    st.stop()

snap = json.loads(SNAPSHOT_PATH.read_text())
all_clubs = snap.get("clubs", [])

# Filter alignment with the global header filter. Match by club display
# name (loose match — the snapshot uses "Inter Milan" / "Bayern Munich"
# / etc., which mostly align with TEAM_COLORS keys). League is the
# trustworthy join key here.
g_league, g_club = get_global_filter()
g_club_name = (g_club or {}).get("name") if g_club else None


def _league_clubs(league: str) -> list[dict]:
    return [c for c in all_clubs if c.get("league") == league]


def _find_club(name: str) -> dict | None:
    if not name:
        return None
    name_l = name.lower().strip()
    for c in all_clubs:
        if c["name"].lower() == name_l:
            return c
    # Loose match: strip common prefixes
    for c in all_clubs:
        cn = c["name"].lower()
        if name_l in cn or cn in name_l:
            return c
    return None


# ── Helpers ───────────────────────────────────────────────────────
def _fmt_ms(v):
    if v is None:
        return "-"
    return f"{v/1000:.2f}s" if v >= 1000 else f"{int(v)} ms"


def _rate_lcp(v):
    if v is None:
        return "—"
    if v <= 2500:
        return "🟢"
    if v <= 4000:
        return "🟡"
    return "🔴"


def _rate_inp(v):
    if v is None:
        return "—"
    if v <= 200:
        return "🟢"
    if v <= 500:
        return "🟡"
    return "🔴"


def _safe(d: dict | None, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


# ── Z3 helpers ────────────────────────────────────────────────────
def render_z3(club: dict) -> None:
    name = club["name"]
    league = club["league"]
    flag = LEAGUE_FLAG.get(league, "")

    # Header line
    online_since = club.get("domain_first_year") or "—"
    cdn = _safe(club, "http", "cdn", default="?")
    st.markdown(
        f"<h3 style='margin:0'>{flag} {name}"
        f"<span style='color:#888;font-weight:400;font-size:0.95rem;"
        f"margin-left:14px'>{club['domain']} · online since {online_since} · {cdn} CDN</span></h3>",
        unsafe_allow_html=True,
    )
    st.caption(league)

    # Compute league rank for some metrics (subtitle context)
    peers = _league_clubs(league)

    def rank_of(metric_fn, higher_is_better: bool = True) -> str:
        vals = [(p["name"], metric_fn(p)) for p in peers]
        vals = [v for v in vals if v[1] is not None]
        if not vals:
            return ""
        vals.sort(key=lambda x: x[1], reverse=higher_is_better)
        for i, (n, _) in enumerate(vals, 1):
            if n == name:
                return f"#{i}/{len(vals)} in {league}"
        return ""

    # Web overview KPI row
    pages = _safe(club, "sitemap", "pages")
    locales = _safe(club, "sitemap", "locales")
    sec_p = _safe(club, "http", "security_headers_present")
    wiki_langs = _safe(club, "wikipedia", "langs")
    wiki_views = _safe(club, "wikipedia", "pageviews_12mo")

    st.markdown("**Web overview**")
    pairs = [
        ("Pages",  fmt_num(pages) if pages else "—",
            rank_of(lambda c: _safe(c, "sitemap", "pages"))),
        ("Locales", str(locales) if locales else "—", ""),
        ("Sec headers", f"{sec_p}/6" if sec_p is not None else "—", ""),
        ("Wiki langs", str(wiki_langs) if wiki_langs else "—",
            rank_of(lambda c: _safe(c, "wikipedia", "langs"))),
        ("Wiki views (12mo)", fmt_num(wiki_views) if wiki_views else "—",
            rank_of(lambda c: _safe(c, "wikipedia", "pageviews_12mo"))),
    ]
    st.markdown(kpi_row(pairs), unsafe_allow_html=True)

    # Real-user performance (CrUX) — phone is the headline
    st.markdown("**Real-user performance** "
                "<span style='color:#888;font-size:0.85rem'>"
                "(Chrome users on mobile, last 28 days)</span>",
                unsafe_allow_html=True)

    cx = club.get("crux_phone") or {}
    if "error" in cx or not cx:
        st.info("No real-user data available for this domain "
                "(insufficient Chrome traffic).")
    else:
        lcp = cx.get("lcp_p75"); fcp = cx.get("fcp_p75")
        inp = cx.get("inp_p75"); cls_ = cx.get("cls_p75")
        ttfb = cx.get("ttfb_p75")
        pairs = [
            (f"LCP {_rate_lcp(lcp)}", _fmt_ms(lcp),
                rank_of(lambda c: _safe(c, "crux_phone", "lcp_p75"),
                        higher_is_better=False)),
            (f"INP {_rate_inp(inp)}", _fmt_ms(inp),
                rank_of(lambda c: _safe(c, "crux_phone", "inp_p75"),
                        higher_is_better=False)),
            ("FCP", _fmt_ms(fcp), ""),
            ("CLS", f"{cls_:.2f}" if cls_ is not None else "—", ""),
            ("TTFB", _fmt_ms(ttfb), ""),
        ]
        st.markdown(kpi_row(pairs), unsafe_allow_html=True)

    # Lighthouse audit (a11y / BP / SEO only — Performance excluded by design)
    psi = club.get("psi") or {}
    if psi and "error" not in psi:
        st.markdown("**Lighthouse audit** "
                    "<span style='color:#888;font-size:0.85rem'>"
                    "(synthetic, indicative)</span>",
                    unsafe_allow_html=True)
        a11y = psi.get("a11y"); bp = psi.get("best_practices"); seo = psi.get("seo")
        pairs = [
            ("Accessibility", str(a11y) if a11y is not None else "—",
                rank_of(lambda c: _safe(c, "psi", "a11y"))),
            ("Best practices", str(bp) if bp is not None else "—", ""),
            ("SEO", str(seo) if seo is not None else "—", ""),
        ]
        st.markdown(kpi_row(pairs), unsafe_allow_html=True)

    # iOS app card
    app = club.get("ios_app") or {}
    if app.get("present"):
        st.markdown("**iOS app**")
        rating = app.get("rating")
        rating_s = f"{rating:.1f} ★" if rating else "—"
        rating_n = app.get("rating_count")
        last = app.get("last_update") or "—"
        size = app.get("size_mb")
        langs = app.get("languages")
        first = app.get("first_release") or "—"
        pairs = [
            ("Rating", rating_s,
                f"{fmt_num(rating_n)} ratings" if rating_n else ""),
            ("Last update", last, ""),
            ("Size", f"{size:.0f} MB" if size else "—", ""),
            ("Languages", str(langs) if langs else "—", ""),
            ("On store since", first, ""),
        ]
        st.markdown(kpi_row(pairs), unsafe_allow_html=True)
    else:
        st.caption("No official iOS app found in App Store search.")

    # Subtitle/footer with collection window
    cx_combined = club.get("crux_combined") or {}
    if cx_combined and "error" not in cx_combined:
        st.caption("CrUX values are p75 across real Chrome visits, "
                   "rolling 28-day window. Lower is better.")


# ── Z2 helpers ────────────────────────────────────────────────────
def render_z2(league: str) -> None:
    flag = LEAGUE_FLAG.get(league, "")
    clubs = _league_clubs(league)
    if not clubs:
        st.info(f"No clubs in the snapshot for {league}.")
        return

    render_league_header(league, extra_suffix=f"{len(clubs)} clubs in snapshot")

    # League aggregate KPIs
    def collect(fn):
        return [v for v in (fn(c) for c in clubs) if v is not None]

    pages_vals = collect(lambda c: _safe(c, "sitemap", "pages"))
    lcp_vals = collect(lambda c: _safe(c, "crux_phone", "lcp_p75"))
    apps_n = sum(1 for c in clubs if _safe(c, "ios_app", "present"))
    rating_vals = collect(lambda c: _safe(c, "ios_app", "rating"))
    wiki_vals = collect(lambda c: _safe(c, "wikipedia", "pageviews_12mo"))

    pairs = [
        ("Clubs", str(len(clubs)), ""),
        ("Total pages", fmt_num(sum(pages_vals)) if pages_vals else "—", ""),
        ("Median real LCP", _fmt_ms(sorted(lcp_vals)[len(lcp_vals)//2]) if lcp_vals else "—", ""),
        ("Clubs w/ iOS app", f"{apps_n}/{len(clubs)}", ""),
        ("Avg iOS rating", f"{sum(rating_vals)/len(rating_vals):.1f} ★" if rating_vals else "—", ""),
    ]
    st.markdown(kpi_row(pairs), unsafe_allow_html=True)

    # The ranking table — 12 columns, ranked by Wiki views (interest proxy)
    clubs_sorted = sorted(
        clubs,
        key=lambda c: _safe(c, "wikipedia", "pageviews_12mo") or 0,
        reverse=True,
    )

    rows_html = []
    from urllib.parse import quote_plus
    for i, c in enumerate(clubs_sorted, 1):
        pages = _safe(c, "sitemap", "pages")
        loc = _safe(c, "sitemap", "locales")
        cdn = _safe(c, "http", "cdn", default="?")
        cx = c.get("crux_phone") or {}
        lcp = cx.get("lcp_p75")
        a11y = _safe(c, "psi", "a11y")
        app = c.get("ios_app") or {}
        rating = app.get("rating")
        last = (app.get("last_update") or "")[:7] if app.get("present") else ""
        wl = _safe(c, "wikipedia", "langs")
        wv = _safe(c, "wikipedia", "pageviews_12mo")
        sec = _safe(c, "http", "security_headers_present")
        club_slug = quote_plus(c["name"])
        sec_s = f"{sec}/6" if sec is not None else "—"
        rating_s = f"{rating:.1f}★" if rating else "—"
        rows_html.append(
            "<tr>"
            f"<td style='text-align:right;color:#888'>{i}</td>"
            f"<td><a href='?league={league}&club={club_slug}' "
            f"style='color:#FAFAFA;text-decoration:none'>{c['name']}</a></td>"
            f"<td style='text-align:right'>{fmt_num(pages) if pages else '—'}</td>"
            f"<td style='text-align:right'>{loc if loc else '—'}</td>"
            f"<td style='text-align:right'>{cdn}</td>"
            f"<td style='text-align:right'>{sec_s}</td>"
            f"<td style='text-align:right'>{_rate_lcp(lcp)} {_fmt_ms(lcp)}</td>"
            f"<td style='text-align:right'>{a11y if a11y is not None else '—'}</td>"
            f"<td style='text-align:right'>{rating_s}</td>"
            f"<td style='text-align:right'>{last or '—'}</td>"
            f"<td style='text-align:right'>{wl if wl else '—'}</td>"
            f"<td style='text-align:right'>{fmt_num(wv) if wv else '—'}</td>"
            "</tr>"
        )

    table = (
        "<style>"
        ".df-table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}"
        ".df-table th{background:#1a1c24;color:#888;text-transform:uppercase;"
        "letter-spacing:.5px;font-size:11px;padding:8px 10px;text-align:right;"
        "font-weight:600;border-bottom:1px solid #2a2c34}"
        ".df-table th:nth-child(2){text-align:left}"
        ".df-table td{padding:8px 10px;border-bottom:1px solid #20222a;color:#FAFAFA}"
        ".df-table tr:hover td{background:#1a1c24}"
        "</style>"
        "<table class='df-table'>"
        "<thead><tr>"
        "<th>#</th><th style='text-align:left'>Club</th>"
        "<th>Pages</th><th>Loc</th><th>CDN</th><th>Sec</th>"
        "<th>Real LCP</th><th>A11y</th>"
        "<th>iOS ★</th><th>Updated</th>"
        "<th>Wiki L</th><th>Wiki views/12mo</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )
    st.markdown(table, unsafe_allow_html=True)

    st.caption(
        "Sorted by Wikipedia EN pageviews (12mo), used as a global-interest "
        "proxy. Real LCP is real-user p75 mobile (CrUX). "
        "Lighthouse Performance score is omitted on purpose — synthetic, noisy. "
        "A11y is from Lighthouse (synthetic but reliable)."
    )


# ── Z1 helpers ────────────────────────────────────────────────────
def render_z1() -> None:
    leagues = sorted({c["league"] for c in all_clubs})
    if not leagues:
        st.info("No data.")
        return

    def stats_for(league: str) -> dict:
        clubs = _league_clubs(league)
        pages = [_safe(c, "sitemap", "pages") for c in clubs]
        pages = [p for p in pages if p]
        lcp = [_safe(c, "crux_phone", "lcp_p75") for c in clubs]
        lcp = [v for v in lcp if v is not None]
        ratings = [_safe(c, "ios_app", "rating") for c in clubs]
        ratings = [r for r in ratings if r]
        apps = sum(1 for c in clubs if _safe(c, "ios_app", "present"))
        wiki = [_safe(c, "wikipedia", "pageviews_12mo") for c in clubs]
        wiki = [w for w in wiki if w]
        return {
            "clubs": len(clubs),
            "pages_sum": sum(pages) if pages else None,
            "median_lcp": sorted(lcp)[len(lcp)//2] if lcp else None,
            "avg_rating": sum(ratings)/len(ratings) if ratings else None,
            "apps_n": apps,
            "wiki_sum": sum(wiki) if wiki else None,
        }

    rows = [(lg, stats_for(lg)) for lg in leagues]

    # Top KPI row — totals across all leagues
    total_clubs = sum(s["clubs"] for _, s in rows)
    total_apps = sum(s["apps_n"] for _, s in rows)
    all_lcp = []
    for c in all_clubs:
        v = _safe(c, "crux_phone", "lcp_p75")
        if v is not None:
            all_lcp.append(v)
    all_pages = [p for p in (_safe(c, "sitemap", "pages") for c in all_clubs) if p]

    pairs = [
        ("Leagues", str(len(leagues)), ""),
        ("Clubs in snapshot", str(total_clubs), ""),
        ("Clubs w/ iOS app", f"{total_apps}/{total_clubs}", ""),
        ("Median real LCP", _fmt_ms(sorted(all_lcp)[len(all_lcp)//2]) if all_lcp else "—", ""),
        ("Total pages tracked", fmt_num(sum(all_pages)) if all_pages else "—", ""),
    ]
    st.markdown(kpi_row(pairs), unsafe_allow_html=True)

    # League comparison table
    rows_html = []
    rows_sorted = sorted(rows, key=lambda r: r[1]["wiki_sum"] or 0, reverse=True)
    for lg, s in rows_sorted:
        flag = LEAGUE_FLAG.get(lg, "")
        avg_rating = s["avg_rating"]
        rating_s = f"{avg_rating:.1f}★" if avg_rating else "—"
        rows_html.append(
            "<tr>"
            f"<td>{flag} {lg}</td>"
            f"<td style='text-align:right'>{s['clubs']}</td>"
            f"<td style='text-align:right'>{fmt_num(s['pages_sum']) if s['pages_sum'] else '—'}</td>"
            f"<td style='text-align:right'>{_fmt_ms(s['median_lcp']) if s['median_lcp'] else '—'}</td>"
            f"<td style='text-align:right'>{s['apps_n']}/{s['clubs']}</td>"
            f"<td style='text-align:right'>{rating_s}</td>"
            f"<td style='text-align:right'>{fmt_num(s['wiki_sum']) if s['wiki_sum'] else '—'}</td>"
            "</tr>"
        )
    table = (
        "<style>"
        ".df-l{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0}"
        ".df-l th{background:#1a1c24;color:#888;text-transform:uppercase;"
        "letter-spacing:.5px;font-size:11px;padding:10px;text-align:right;font-weight:600;"
        "border-bottom:1px solid #2a2c34}"
        ".df-l th:first-child{text-align:left}"
        ".df-l td{padding:10px;border-bottom:1px solid #20222a;color:#FAFAFA}"
        ".df-l tr:hover td{background:#1a1c24}"
        "</style>"
        "<table class='df-l'>"
        "<thead><tr>"
        "<th>League</th><th>Clubs</th><th>Total pages</th>"
        "<th>Median real LCP</th><th>iOS apps</th>"
        "<th>Avg iOS rating</th><th>Wiki views (sum 12mo)</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )
    st.markdown(table, unsafe_allow_html=True)
    st.caption(
        "Sample slate: top 5 clubs per league. Real LCP is real-user "
        "median across the slate (CrUX, mobile). Wiki views is 12-month "
        "EN Wikipedia traffic — proxy for global interest."
    )


# ── Subtitle + zoom routing ───────────────────────────────────────
render_page_subtitle(
    f"Owned & operated digital estate · {len(all_clubs)} clubs · yearly snapshot",
    updated_raw=snap.get("generated_at"),
    caveat="Trustable signals only — Lighthouse Performance score excluded "
           "(synthetic noise). CrUX = real Chrome users, last 28 days.",
)

if g_club_name:
    matched = _find_club(g_club_name)
    if matched:
        render_z3(matched)
    else:
        st.info(f"No digital footprint snapshot for **{g_club_name}** yet. "
                "The current sample slate covers the top 5 clubs of each league.")
        render_z2(g_league or matched.get("league") if matched else "Serie A")
elif g_league:
    render_z2(g_league)
else:
    render_z1()
