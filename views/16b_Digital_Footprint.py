"""Digital Footprint — owned & operated estate per channel.

Z1 (All Leagues) : flat per-channel table for the whole snapshot,
                   sortable by Wiki views (interest proxy).
Z2 (One League)  : same table filtered to the league.
Z3 (One Club)    : profile card with web + iOS + Wikipedia detail.

Reads data/digital_footprint.json — yearly snapshot built by
scripts/collect_digital_footprint.py against channels in the DB
that have `website` set.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, quote_plus

import streamlit as st

from src.analytics import kpi_row, fmt_num
from src.auth import require_login
from src.channels import LEAGUE_FLAG
from src.dot import channel_badge
from src.filters import (
    get_global_color_map, get_global_color_map_dual,
    get_global_filter, render_league_header, render_page_subtitle,
)

require_login()

st.title("Digital Footprint")

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "digital_footprint.json"
if not SNAPSHOT_PATH.exists():
    st.warning("Digital Footprint snapshot not built yet. "
               "Run `python3 scripts/collect_digital_footprint.py`.")
    st.stop()

snap = json.loads(SNAPSHOT_PATH.read_text())
# v1 used "clubs", v2 uses "channels". Accept both.
all_channels = snap.get("channels") or snap.get("clubs") or []

g_league, g_club = get_global_filter()
g_club_name = (g_club or {}).get("name") if g_club else None


# ── Helpers ───────────────────────────────────────────────────────
def _safe(d, *keys, default=None):
    cur = d or {}
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _fmt_ms(v):
    if v is None:
        return "-"
    return f"{v/1000:.2f}s" if v >= 1000 else f"{int(v)} ms"


def _rate_lcp(v):
    if v is None:
        return ""
    return "🟢" if v <= 2500 else ("🟡" if v <= 4000 else "🔴")


def _rate_inp(v):
    if v is None:
        return ""
    return "🟢" if v <= 200 else ("🟡" if v <= 500 else "🔴")


def _league_channels(league):
    return [c for c in all_channels if c.get("league") == league]


def _find_channel(name):
    if not name:
        return None
    nl = name.lower().strip()
    for c in all_channels:
        if c["name"].lower() == nl:
            return c
    for c in all_channels:
        cn = c["name"].lower()
        if nl in cn or cn in nl:
            return c
    return None


def _tech_cell(c):
    """Render a compact stack summary: vendor chain (sport-tech) > cms > framework.
    Color-coded so vendors stand out as the highest signal. Stadion-on-
    Contentful clubs render as 'Stadion · Contentful · ...' so the
    layered platform choice is visible."""
    t = c.get("tech") or {}
    chain = t.get("vendor_chain") or ([t["vendor"]] if t.get("vendor") else [])
    cms = t.get("cms")
    fw = t.get("framework")
    if chain:
        chain_html = " · ".join(
            f"<span style='color:#FF6B6B;font-weight:600'>{v}</span>"
            for v in chain
        )
        return (chain_html
                + (f"<span style='color:#666'> · {cms}</span>" if cms else "")
                + (f"<span style='color:#666'> · {fw}</span>" if fw else ""))
    if cms:
        return (f"<span style='color:#58A6FF'>{cms}</span>"
                + (f"<span style='color:#666'> · {fw}</span>" if fw else ""))
    if fw:
        return f"<span style='color:#888'>{fw}</span>"
    return "<span style='color:#444'>—</span>"


def _website_link(c):
    """Render the website cell — clickable, displays the bare host."""
    url = c.get("website") or ""
    if not url:
        return "—"
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
    except Exception:
        host = url
    return (f"<a href='{url}' target='_blank' rel='noopener' "
            f"style='color:#58A6FF;text-decoration:none'>{host}</a>")


# ── Flat per-channel table ────────────────────────────────────────
def _flat_table(channels: list[dict], header: str = "") -> None:
    if not channels:
        st.info("No channels in this scope.")
        return

    # Sort by Wiki EN pageviews 12mo (interest proxy); fallback to name
    channels_sorted = sorted(
        channels,
        key=lambda c: (_safe(c, "wikipedia", "pageviews_12mo") or 0,
                       c.get("name", "")),
        reverse=True,
    )

    color_map = get_global_color_map() or {}
    dual_map = get_global_color_map_dual() or {}

    rows_html = []
    for c in channels_sorted:
        # Snapshot dicts don't carry color; channel_badge only needs
        # name + entity_type + country, which the snapshot has.
        badge = channel_badge(c, color_map, dual_map, 12)
        web = _website_link(c)
        cdn = _safe(c, "http", "cdn", default="?")
        sec = _safe(c, "http", "security_headers_present")
        sec_s = f"{sec}/6" if sec is not None else "—"
        pages = _safe(c, "sitemap", "pages")
        loc = _safe(c, "sitemap", "locales")
        cx = c.get("crux_phone") or {}
        lcp = cx.get("lcp_p75")
        inp = cx.get("inp_p75")
        a11y = _safe(c, "psi", "a11y")
        seo = _safe(c, "psi", "seo")
        app = c.get("ios_app") or {}
        rating = app.get("rating")
        rating_n = app.get("rating_count")
        last = (app.get("last_update") or "")[:7]
        wl = _safe(c, "wikipedia", "langs")
        wv = _safe(c, "wikipedia", "pageviews_12mo")
        domain_yr = c.get("domain_first_year")

        slug = quote_plus(c["name"])
        league = c.get("league", "")
        name_link = (f"<a href='?league={quote_plus(league)}&club={slug}' "
                     f"style='color:#FAFAFA;text-decoration:none'>"
                     f"{c['name']}</a>")
        # Badge + name on the same flex row so the dot/flag aligns
        # vertically with the text — matches the convention used in
        # render_club_header / Top Season tables.
        channel_cell = (
            "<div style='display:flex;align-items:center;gap:8px'>"
            f"{badge}<span>{name_link}</span></div>"
        )

        tech = _tech_cell(c)

        rows_html.append(
            "<tr>"
            f"<td>{channel_cell}</td>"
            f"<td>{web}</td>"
            f"<td>{tech}</td>"
            f"<td style='text-align:right'>{domain_yr or '—'}</td>"
            f"<td style='text-align:right'>{cdn}</td>"
            f"<td style='text-align:right'>{sec_s}</td>"
            f"<td style='text-align:right'>{fmt_num(pages) if pages else '—'}</td>"
            f"<td style='text-align:right'>{loc if loc else '—'}</td>"
            f"<td style='text-align:right'>{_rate_lcp(lcp)} {_fmt_ms(lcp)}</td>"
            f"<td style='text-align:right'>{_rate_inp(inp)} {_fmt_ms(inp)}</td>"
            f"<td style='text-align:right'>{a11y if a11y is not None else '—'}</td>"
            f"<td style='text-align:right'>{seo if seo is not None else '—'}</td>"
            f"<td style='text-align:right'>"
            f"{f'{rating:.1f}★' if rating else '—'}"
            + (f" <span style='color:#666'>({fmt_num(rating_n)})</span>"
               if rating_n else "") + "</td>"
            f"<td style='text-align:right'>{last or '—'}</td>"
            f"<td style='text-align:right'>{wl if wl else '—'}</td>"
            f"<td style='text-align:right'>{fmt_num(wv) if wv else '—'}</td>"
            "</tr>"
        )

    if header:
        st.markdown(f"**{header}**")

    table = (
        "<style>"
        ".df-tbl{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}"
        ".df-tbl th{background:#1a1c24;color:#888;text-transform:uppercase;"
        "letter-spacing:.4px;font-size:10.5px;padding:8px 8px;text-align:right;"
        "font-weight:600;border-bottom:1px solid #2a2c34;white-space:nowrap}"
        ".df-tbl th:nth-child(1),.df-tbl th:nth-child(2),.df-tbl th:nth-child(3){text-align:left}"
        ".df-tbl td{padding:7px 8px;border-bottom:1px solid #20222a;color:#FAFAFA}"
        ".df-tbl tr:hover td{background:#1a1c24}"
        "</style>"
        "<div style='overflow-x:auto'>"
        "<table class='df-tbl'>"
        # Two-row thead: top row groups columns with colored
        # underlines (mirrors the All Channels — Season layout);
        # bottom row holds the individual column labels.
        "<thead>"
        "<tr>"
        "<th colspan='2' style='border-bottom:0'></th>"
        "<th colspan='4' style='text-align:center;border-bottom:2px solid #FF6B6B;color:#FF6B6B'>Stack</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #19D3F3;color:#19D3F3'>Content</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #00CC96;color:#00CC96'>Real users</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #58A6FF;color:#58A6FF'>Lighthouse</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A'>iOS app</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA'>Wikipedia</th>"
        "</tr>"
        "<tr>"
        "<th>Channel</th>"
        "<th>Website</th>"
        "<th>Tech</th>"
        "<th>Since</th><th>CDN</th><th>Sec</th>"
        "<th>Pages</th><th>Loc</th>"
        "<th>Real LCP</th><th>Real INP</th>"
        "<th>A11y</th><th>SEO</th>"
        "<th>iOS ★</th><th>Updated</th>"
        "<th>Wiki L</th><th>Views/12mo</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
    )
    st.markdown(table, unsafe_allow_html=True)
    st.caption(
        "Sorted by Wikipedia EN pageviews (12mo) — global interest proxy. "
        "Real LCP / INP = real Chrome users on mobile, p75, last 28 days "
        "(CrUX). A11y / SEO from Lighthouse — Performance score is "
        "deliberately omitted (synthetic, noisy on content-heavy sites)."
    )


# ── Z3 — single channel profile ───────────────────────────────────
def render_z3(c: dict) -> None:
    name = c["name"]
    league = c.get("league", "")
    flag = LEAGUE_FLAG.get(league, "")
    online_since = c.get("domain_first_year") or "—"
    cdn = _safe(c, "http", "cdn", default="?")
    web_url = c.get("website") or ""
    web_host = urlparse(web_url).netloc if web_url else c.get("domain", "—")
    web_link = (f"<a href='{web_url}' target='_blank' "
                f"style='color:#58A6FF;text-decoration:none'>{web_host}</a>")

    st.markdown(
        f"<h3 style='margin:0'>{flag} {name}"
        f"<span style='color:#888;font-weight:400;font-size:0.95rem;"
        f"margin-left:14px'>{web_link} · online since {online_since} · {cdn} CDN</span></h3>",
        unsafe_allow_html=True,
    )
    st.caption(league or "—")

    peers = _league_channels(league)

    def rank_of(metric_fn, higher_is_better=True):
        vals = [(p["name"], metric_fn(p)) for p in peers]
        vals = [v for v in vals if v[1] is not None]
        if not vals:
            return ""
        vals.sort(key=lambda x: x[1], reverse=higher_is_better)
        for i, (n, _) in enumerate(vals, 1):
            if n == name:
                return f"#{i}/{len(vals)} in {league}"
        return ""

    pages = _safe(c, "sitemap", "pages")
    locales = _safe(c, "sitemap", "locales")
    sec_p = _safe(c, "http", "security_headers_present")
    wiki_langs = _safe(c, "wikipedia", "langs")
    wiki_views = _safe(c, "wikipedia", "pageviews_12mo")

    st.markdown("**Web overview**")
    st.markdown(kpi_row([
        ("Pages",  fmt_num(pages) if pages else "—",
            rank_of(lambda c: _safe(c, "sitemap", "pages"))),
        ("Locales", str(locales) if locales else "—", ""),
        ("Sec headers", f"{sec_p}/6" if sec_p is not None else "—", ""),
        ("Wiki langs", str(wiki_langs) if wiki_langs else "—",
            rank_of(lambda c: _safe(c, "wikipedia", "langs"))),
        ("Wiki views (12mo)", fmt_num(wiki_views) if wiki_views else "—",
            rank_of(lambda c: _safe(c, "wikipedia", "pageviews_12mo"))),
    ]), unsafe_allow_html=True)

    st.markdown("**Real-user performance** "
                "<span style='color:#888;font-size:0.85rem'>"
                "(Chrome on mobile, last 28 days)</span>",
                unsafe_allow_html=True)
    cx = c.get("crux_phone") or {}
    if not cx or "error" in cx:
        st.info("No real-user data available (insufficient Chrome traffic).")
    else:
        lcp = cx.get("lcp_p75"); fcp = cx.get("fcp_p75")
        inp = cx.get("inp_p75"); cls_ = cx.get("cls_p75")
        ttfb = cx.get("ttfb_p75")
        st.markdown(kpi_row([
            (f"LCP {_rate_lcp(lcp)}", _fmt_ms(lcp),
                rank_of(lambda c: _safe(c, "crux_phone", "lcp_p75"),
                        higher_is_better=False)),
            (f"INP {_rate_inp(inp)}", _fmt_ms(inp),
                rank_of(lambda c: _safe(c, "crux_phone", "inp_p75"),
                        higher_is_better=False)),
            ("FCP", _fmt_ms(fcp), ""),
            ("CLS", f"{cls_:.2f}" if cls_ is not None else "—", ""),
            ("TTFB", _fmt_ms(ttfb), ""),
        ]), unsafe_allow_html=True)

    # Tech stack — sport-tech vendor / CMS / framework with evidence
    t = c.get("tech") or {}
    if t.get("vendor") or t.get("cms") or t.get("framework"):
        st.markdown("**Tech stack** "
                    "<span style='color:#888;font-size:0.85rem'>"
                    "(fingerprinted from homepage HTML)</span>",
                    unsafe_allow_html=True)
        chain = t.get("vendor_chain") or (
            [t["vendor"]] if t.get("vendor") else [])
        vendor_str = " · ".join(chain) if chain else "—"
        st.markdown(kpi_row([
            ("Sport-tech / platform", vendor_str,
                "Stacked layers shown left-to-right (delivery → substrate)"),
            ("CMS / DXP",            t.get("cms") or "—", ""),
            ("Framework",            t.get("framework") or "—", ""),
        ]), unsafe_allow_html=True)
        ev = t.get("evidence") or []
        if ev:
            ev_html = "<br>".join(
                f"<code style='color:#888;font-size:11px'>{e}</code>"
                for e in ev[:4])
            st.markdown(
                f"<div style='color:#888;font-size:11px;margin:-6px 0 14px 0'>"
                f"Evidence: {ev_html}</div>",
                unsafe_allow_html=True,
            )

    psi = c.get("psi") or {}
    if psi and "error" not in psi:
        st.markdown("**Lighthouse audit** "
                    "<span style='color:#888;font-size:0.85rem'>"
                    "(synthetic, indicative — Performance score omitted "
                    "by design)</span>", unsafe_allow_html=True)
        a11y = psi.get("a11y"); bp = psi.get("best_practices"); seo = psi.get("seo")
        st.markdown(kpi_row([
            ("Accessibility", str(a11y) if a11y is not None else "—",
                rank_of(lambda c: _safe(c, "psi", "a11y"))),
            ("Best practices", str(bp) if bp is not None else "—", ""),
            ("SEO", str(seo) if seo is not None else "—", ""),
        ]), unsafe_allow_html=True)

    app = c.get("ios_app") or {}
    if app.get("present"):
        st.markdown("**iOS app**")
        rating = app.get("rating")
        rating_s = f"{rating:.1f} ★" if rating else "—"
        rating_n = app.get("rating_count")
        last = app.get("last_update") or "—"
        size = app.get("size_mb")
        langs = app.get("languages")
        first = app.get("first_release") or "—"
        st.markdown(kpi_row([
            ("Rating", rating_s,
                f"{fmt_num(rating_n)} ratings" if rating_n else ""),
            ("Last update", last, ""),
            ("Size", f"{size:.0f} MB" if size else "—", ""),
            ("Languages", str(langs) if langs else "—", ""),
            ("On store since", first, ""),
        ]), unsafe_allow_html=True)
    else:
        st.caption("No official iOS app matched in App Store search.")


# ── Subtitle + zoom routing ───────────────────────────────────────
covered = snap.get("covered", len(all_channels))
skipped = snap.get("skipped_no_website", []) or []
skipped_msg = (f" · {len(skipped)} more channels not yet mapped"
               if skipped else "")

render_page_subtitle(
    f"Owned & operated digital estate · {covered} channels in snapshot"
    f"{skipped_msg}",
    updated_raw=snap.get("generated_at"),
    caveat="Trustable signals only — Lighthouse Performance score omitted. "
           "CrUX = real Chrome users (last 28 days). "
           "Add more channels via Admin → Channel Management (set website + Wikipedia slug).",
)

if g_club_name:
    matched = _find_channel(g_club_name)
    if matched:
        render_z3(matched)
    else:
        st.info(
            f"**{g_club_name}** is not in the digital footprint snapshot yet. "
            "Set its `website` + `wikipedia_slug` via Admin → Channel "
            "Management, then run `python3 scripts/collect_digital_footprint.py` "
            "to refresh the data."
        )
        if g_league:
            _flat_table(_league_channels(g_league),
                        header=f"{LEAGUE_FLAG.get(g_league,'')} {g_league}")
elif g_league:
    render_league_header(g_league,
                         extra_suffix=f"{len(_league_channels(g_league))} channels")
    _flat_table(_league_channels(g_league))
else:
    # Z1 — flat table across every league
    leagues = sorted({c.get("league") for c in all_channels if c.get("league")})
    n_leagues = len(leagues)
    apps_n = sum(1 for c in all_channels if _safe(c, "ios_app", "present"))
    all_lcp = [v for v in (_safe(c, "crux_phone", "lcp_p75")
                            for c in all_channels) if v is not None]
    all_pages = [p for p in (_safe(c, "sitemap", "pages")
                              for c in all_channels) if p]
    st.markdown(kpi_row([
        ("Leagues", str(n_leagues), ""),
        ("Channels in snapshot", str(covered), ""),
        ("With iOS app", f"{apps_n}/{covered}", ""),
        ("Median real LCP", _fmt_ms(sorted(all_lcp)[len(all_lcp)//2])
            if all_lcp else "—", ""),
        ("Total pages", fmt_num(sum(all_pages)) if all_pages else "—", ""),
    ]), unsafe_allow_html=True)
    _flat_table(all_channels)
