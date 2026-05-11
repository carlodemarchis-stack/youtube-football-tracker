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
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, quote_plus

import streamlit as st
import streamlit.components.v1 as _components

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


def _rate_fcp(v):
    if v is None:
        return ""
    return "🟢" if v <= 1800 else ("🟡" if v <= 3000 else "🔴")


def _rate_cls(v):
    if v is None:
        return ""
    return "🟢" if v <= 0.10 else ("🟡" if v <= 0.25 else "🔴")


def _rate_ttfb(v):
    if v is None:
        return ""
    return "🟢" if v <= 800 else ("🟡" if v <= 1800 else "🔴")


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


def _unique_pages(c):
    """Pages, locale-normalized when we have a trustable locale count.
    Multi-locale sites that translate the same content shouldn't be
    counted N× — divide by the locale count when source is high-trust.
    Returns (unique, raw, divisor, source) — None for missing data."""
    raw = _safe(c, "sitemap", "pages")
    if not raw:
        return None, None, None, None
    loc = c.get("locales") or {}
    src = loc.get("source") or ""
    count = loc.get("count") or 1
    # Only normalize when locale signal is reliable AND count > 1.
    # hreflang/sitemap = trust the count. html-lang/og:locale/
    # http-header always = 1. country/manual = don't divide
    # (manual could be many langs but isn't necessarily mirrored
    # 1:1 in the sitemap).
    if src in ("hreflang", "sitemap") and count > 1:
        return raw // count, raw, count, src
    return raw, raw, 1, src


def _wiki_views(c):
    """Total Wikipedia pageviews across major language editions when
    we have it; fall back to EN-only otherwise."""
    w = c.get("wikipedia") or {}
    tot = w.get("pageviews_12mo_total")
    if tot:
        return tot
    return w.get("pageviews_12mo")


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

    # Sort by Wiki total pageviews 12mo (interest proxy across all
    # language editions, not just English); fallback to name.
    channels_sorted = sorted(
        channels,
        key=lambda c: (_wiki_views(c) or 0, c.get("name", "")),
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
        # Pages → locale-normalized "unique" count, with the raw +
        # multiplier surfaced on hover.
        unique_pages, raw_pages, divisor, _psrc = _unique_pages(c)
        # Prefer the layered locales detector over the raw sitemap
        # regex — falls back through hreflang → sitemap → html lang.
        loc_data = c.get("locales") or {}
        loc = loc_data.get("count")
        loc_langs = loc_data.get("langs") or []
        loc_src = loc_data.get("source") or ""
        # Wikipedia: prefer multi-lang sum, fall back to EN-only
        wv = _wiki_views(c)
        wv_en = _safe(c, "wikipedia", "pageviews_12mo")
        wv_by_lang = _safe(c, "wikipedia", "pageviews_by_lang") or {}
        cx = c.get("crux_phone") or {}
        lcp = cx.get("lcp_p75")
        inp = cx.get("inp_p75")
        a11y = _safe(c, "psi", "a11y")
        seo = _safe(c, "psi", "seo")
        app = c.get("ios_app") or {}
        rating = app.get("rating")
        rating_n = app.get("rating_count")
        store_url = (app.get("store_url") or "").strip()
        last = (app.get("last_update") or "")[:7]
        wl = _safe(c, "wikipedia", "langs")
        # wv already set above to total-or-EN by _wiki_views(c)
        domain_yr = c.get("domain_first_year")

        slug = quote_plus(c["name"])
        league = c.get("league", "")
        # target="_top" so click navigates the Streamlit parent page
        # (the table itself renders inside a components.html iframe).
        name_link = (f"<a href='?league={quote_plus(league)}&club={slug}' "
                     f"target='_top' "
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

        # Build cells with data-val attributes (used by the sort JS).
        # Numeric cells store the raw number; string cells store a
        # lowercase comparison key.
        def _td(val_sort, content, *, align="right", extra_attr=""):
            v = "" if val_sort is None else str(val_sort)
            a = f"style='text-align:{align}'" if align != "left" else "style='text-align:left'"
            return f"<td {a} data-val=\"{v}\" {extra_attr}>{content}</td>"

        # Pages cell — unique-content count
        if unique_pages and divisor > 1:
            pages_cell = _td(unique_pages, fmt_num(unique_pages),
                              extra_attr=f"title='Locale-normalized: "
                                          f"{fmt_num(raw_pages)} raw URLs ÷ {divisor} locales "
                                          f"= {fmt_num(unique_pages)} unique pages'")
        elif unique_pages:
            pages_cell = _td(unique_pages, fmt_num(unique_pages))
        else:
            pages_cell = _td(0, "—")

        # Locales cell
        if loc and loc_langs:
            loc_cell = _td(loc, str(loc),
                            extra_attr=f"title='{', '.join(loc_langs)} (via {loc_src})'")
        else:
            loc_cell = _td(loc or 0, str(loc) if loc else "—")

        # iOS rating cell — content includes link + count
        if rating and store_url:
            rating_html = (f"<a href='{store_url}' target='_top' rel='noopener' "
                            f"style='color:#58A6FF;text-decoration:none'>"
                            f"{rating:.1f}★</a>")
        elif rating:
            rating_html = f"{rating:.1f}★"
        else:
            rating_html = "—"
        if rating_n:
            rating_html += f" <span style='color:#666'>({fmt_num(rating_n)})</span>"
        ios_cell = _td(rating or 0, rating_html)

        # Wiki views cell with per-language tooltip
        if wv and wv_by_lang:
            wv_tip = ", ".join(f"{k}: {fmt_num(v)}" for k, v
                                in sorted(wv_by_lang.items(),
                                          key=lambda x: -x[1])[:8])
            wv_cell = _td(wv, fmt_num(wv), extra_attr=f"title='{wv_tip}'")
        else:
            wv_cell = _td(wv or 0, fmt_num(wv) if wv else "—")

        rows_html.append(
            "<tr>"
            f"<td style='text-align:left' data-val=\"{c['name'].lower()}\">{channel_cell}</td>"
            f"<td style='text-align:left' data-val=\"{(c.get('domain') or '').lower()}\">{web}</td>"
            f"<td style='text-align:left' data-val=\"{((c.get('tech') or {}).get('vendor') or (c.get('tech') or {}).get('cms') or (c.get('tech') or {}).get('framework') or '').lower()}\">{tech}</td>"
            f"{_td(domain_yr or 0, domain_yr or '—')}"
            f"{_td(cdn, cdn)}"
            f"{_td(sec if sec is not None else -1, sec_s)}"
            f"{pages_cell}"
            f"{loc_cell}"
            f"{_td(lcp if lcp else 99999, f'{_rate_lcp(lcp)} {_fmt_ms(lcp)}')}"
            f"{_td(inp if inp else 99999, f'{_rate_inp(inp)} {_fmt_ms(inp)}')}"
            f"{_td(a11y if a11y is not None else -1, str(a11y) if a11y is not None else '—')}"
            f"{_td(seo if seo is not None else -1, str(seo) if seo is not None else '—')}"
            f"{ios_cell}"
            f"{_td(last or '0000-00', last or '—')}"
            f"{_td(wl or 0, str(wl) if wl else '—')}"
            f"{wv_cell}"
            "</tr>"
        )

    if header:
        st.markdown(f"**{header}**")

    # Column metadata for the sort JS — (data-col index, type).
    # For columns where "lower is better" (LCP, INP), the JS still
    # toggles asc/desc on click; users can pick the direction they want.
    # Default sort = column 15 (Wiki views) desc.
    ths = [
        # (idx, type, align, label, tooltip)
        (0,  "str", "left",  "Channel",     ""),
        (1,  "str", "left",  "Website",     ""),
        (2,  "str", "left",  "Tech",
            "Detected tech stack — vendor (red), CMS (blue), framework (grey)."),
        (3,  "num", "right", "Since",
            "First year the domain was archived on Wayback Machine."),
        (4,  "str", "right", "CDN",
            "CDN from HTTP response headers."),
        (5,  "num", "right", "Sec",
            "Security headers present (out of 6): HSTS, CSP, X-Frame, X-Content, Referrer, Permissions."),
        (6,  "num", "right", "Pages",
            "Locale-normalized unique pages. Hover a cell to see raw + divisor."),
        (7,  "num", "right", "Loc",
            "Language editions offered. Hover a cell for the language list + source."),
        (8,  "num", "right", "Real LCP",
            "LCP p75 mobile (CrUX). 🟢 ≤2.5s · 🟡 ≤4s · 🔴 >4s"),
        (9,  "num", "right", "Real INP",
            "INP p75 mobile (CrUX). 🟢 ≤200ms · 🟡 ≤500ms · 🔴 >500ms"),
        (10, "num", "right", "A11y",
            "Lighthouse Accessibility 0–100."),
        (11, "num", "right", "SEO",
            "Lighthouse SEO 0–100."),
        (12, "num", "right", "iOS ★",
            "App Store rating. Click the cell to open the App Store page."),
        (13, "str", "right", "Updated",
            "iOS app current-version release date."),
        (14, "num", "right", "Wiki L",
            "Wikipedia language editions (global brand width)."),
        (15, "num", "right", "Views/12mo",
            "Wiki pageviews summed across major language editions. Hover for breakdown."),
    ]
    th_html = "".join(
        f"<th data-col='{idx}' data-type='{tp}' "
        f"style='text-align:{al};cursor:pointer'"
        + (f" title=\"{tip}\"" if tip else "")
        + f">{lbl}</th>"
        for idx, tp, al, lbl, tip in ths
    )

    table_html = (
        "<style>"
        "body{margin:0;background:#0E1117;color:#FAFAFA;"
        "font-family:'Source Sans Pro',sans-serif}"
        ".df-wrap{overflow-x:auto;width:100%}"
        ".df-tbl{width:100%;border-collapse:collapse;border:0;font-size:14px;"
        "color:#FAFAFA;background:transparent}"
        ".df-tbl th,.df-tbl td{border-left:0;border-right:0;border-top:0;"
        "padding:6px 12px;white-space:nowrap}"
        ".df-tbl th{user-select:none;font-weight:600;color:#FAFAFA;border-bottom:0}"
        ".df-tbl th[data-col]:hover{color:#636EFA}"
        ".df-tbl th.active{color:#636EFA}"
        ".df-tbl td{border-bottom:1px solid #262730}"
        ".df-tbl tr:hover td{background:#1a1c24}"
        ".df-tbl tr.outlier-top td:first-child{box-shadow:inset 3px 0 0 #00CC96}"
        ".df-tbl tr.outlier-bottom td:first-child{box-shadow:inset 3px 0 0 #EF553B}"
        ".df-tbl a{color:inherit;text-decoration:none}"
        "</style>"
        "<div class='df-wrap'>"
        "<table class='df-tbl'>"
        "<thead>"
        "<tr>"
        "<th colspan='2'></th>"
        "<th colspan='4' style='text-align:center;border-bottom:2px solid #FF6B6B;color:#FF6B6B'>Stack</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #19D3F3;color:#19D3F3'>Content</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #00CC96;color:#00CC96'>Real users</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #58A6FF;color:#58A6FF'>Lighthouse</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A'>iOS app</th>"
        "<th colspan='2' style='text-align:center;border-bottom:2px solid #AB63FA;color:#AB63FA'>Wikipedia</th>"
        "</tr>"
        f"<tr style='border-bottom:2px solid #444'>{th_html}</tr>"
        "</thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
        # Sortable + outlier-highlighting JS. Highlights top-3 and
        # bottom-3 rows of the current sort with a small left-edge
        # color marker (green for top, red for bottom).
        "<script>(function(){"
        "const t=document.querySelector('.df-tbl');"
        "const tb=t.querySelector('tbody');"
        "const hs=t.querySelectorAll('th[data-col]');"
        "let cur=15,asc=false;"
        "function refresh(){"
            "const rows=Array.from(tb.rows);"
            "const isStr=hs[cur].dataset.type==='str';"
            "rows.sort((a,b)=>{"
                "const va=a.cells[cur].dataset.val||'';"
                "const vb=b.cells[cur].dataset.val||'';"
                "let c;"
                "if(isStr)c=va.localeCompare(vb,undefined,{sensitivity:'base'});"
                "else c=(parseFloat(va)||0)-(parseFloat(vb)||0);"
                "return asc?c:-c;"
            "});"
            # Clear classes and re-attach in new order
            "rows.forEach(r=>{r.classList.remove('outlier-top','outlier-bottom');tb.appendChild(r);});"
            # Outliers: top 3 of current sort, bottom 3
            "const n=rows.length;"
            "for(let i=0;i<3&&i<n;i++)rows[i].classList.add('outlier-top');"
            "for(let i=0;i<3&&i<n;i++)rows[n-1-i].classList.add('outlier-bottom');"
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
    # Iframe height: 32px/row × N rows + ~80px for 2 header rows
    iframe_h = min(900, 32 * len(channels_sorted) + 90)
    _components.html(table_html, height=iframe_h, scrolling=True)
    st.caption(
        "Click any column header to sort. Top-3 of current sort marked "
        "green, bottom-3 red. Default sort = Wikipedia views (12mo, "
        "multi-language total) — global interest proxy. "
        "Real LCP / INP = real Chrome users (CrUX). A11y / SEO from "
        "Lighthouse — Performance score deliberately omitted."
    )


# ── Z3 — single-club narrative deep-dive ──────────────────────────
# Departs from Z1/Z2 in shape: Z1/Z2 are comparison tables;
# Z3 tells the *story* of one club's digital estate, with each
# metric contextualized (rank in league, gap to median, percentile)
# and grouped into thematic sections rather than a metric dump.

def _z3_rank(value, peers: list[dict], extractor, *, higher_better: bool = True) -> tuple[int, int]:
    """Return (rank, total) of this value within peer values
    extracted by extractor(c). 0/0 when not rankable."""
    vals = [(p["name"], extractor(p)) for p in peers]
    vals = [(n, v) for n, v in vals if v is not None]
    if not vals or value is None:
        return 0, 0
    vals.sort(key=lambda x: x[1], reverse=higher_better)
    for i, (_, v) in enumerate(vals, 1):
        if v == value:
            return i, len(vals)
    return 0, len(vals)


def _rank_pill(rank: int, total: int, league: str = "") -> str:
    """Format a rank subtitle. Rank 1 gets green + 🏆; other ranks
    stay grey/default. Used everywhere ranks are shown on Z3."""
    if not rank or not total:
        return ""
    in_lg = f" in {league}" if league else ""
    if rank == 1:
        return (f"<span style='color:#00CC96;font-weight:600'>"
                f"🏆 #{rank} of {total}{in_lg}</span>")
    return f"#{rank} of {total}{in_lg}"


def _z3_median(peers: list[dict], extractor) -> float | None:
    vals = [extractor(p) for p in peers]
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return vals[len(vals) // 2]


def _z3_summary_narrative(c: dict, peers: list[dict]) -> str:
    """Auto-generate a 2-3 sentence framing from the data. No GPT —
    deterministic, fast, free."""
    name = c["name"]
    league = c.get("league", "—")
    sentences = []

    # Sentence 1 — stack + delivery
    t = c.get("tech") or {}
    cdn = _safe(c, "http", "cdn", default="")
    vendor_chain = t.get("vendor_chain") or (
        [t["vendor"]] if t.get("vendor") else [])
    cms = t.get("cms")
    if vendor_chain:
        bits = " · ".join(vendor_chain)
        if cms and cms not in vendor_chain:
            bits += f" with {cms} underneath"
        s1 = f"{name} runs on {bits}"
    elif cms:
        s1 = f"{name} runs on {cms}"
    else:
        s1 = f"{name}'s site appears to be a custom in-house build"
    if cdn and cdn != "?":
        s1 += f", served via {cdn}"
    sentences.append(s1 + ".")

    # Sentence 2 — real-user performance + rank
    cx = c.get("crux_phone") or {}
    lcp = cx.get("lcp_p75")
    if lcp:
        r, total = _z3_rank(lcp, peers,
                             lambda p: _safe(p, "crux_phone", "lcp_p75"),
                             higher_better=False)
        rate = "fast" if lcp <= 2500 else ("moderate" if lcp <= 4000 else "slow")
        if r and total:
            sentences.append(
                f"Real-user mobile experience is {rate} (LCP p75 {lcp/1000:.1f}s) — "
                f"#{r} of {total} in {league}.")
        else:
            sentences.append(
                f"Real-user mobile LCP p75 is {lcp/1000:.1f}s ({rate}).")
    else:
        sentences.append("No real-user performance data available "
                         "(insufficient Chrome traffic).")

    # Sentence 3 — audience reach + iOS
    wv = _wiki_views(c)
    bits3 = []
    if wv:
        r, total = _z3_rank(wv, peers, _wiki_views)
        if r and total:
            bits3.append(f"global Wikipedia interest is #{r} of {total} in {league} "
                          f"({fmt_num(wv)} 12mo views across major languages)")
        else:
            bits3.append(f"Wikipedia draws {fmt_num(wv)} 12mo views")
    app = c.get("ios_app") or {}
    if app.get("present") and app.get("rating"):
        r = app["rating"]
        last = (app.get("last_update") or "")[:7]
        bits3.append(f"iOS app rated {r:.1f}★" +
                      (f", last updated {last}" if last else ""))
    elif not app.get("present"):
        bits3.append("no official iOS app")
    if bits3:
        sentences.append(("; ".join(bits3) + ".").capitalize())

    return " ".join(sentences)


def _bar_for(value, lo, hi, *, ok=None, warn=None, lower_better=False) -> str:
    """Render a small horizontal bar from lo..hi with the value marked.
    Color-banded if ok/warn thresholds provided (CWV style)."""
    if value is None or hi == lo:
        return ""
    pct = max(0, min(100, (value - lo) / (hi - lo) * 100))
    # Color the value marker
    if ok is not None and warn is not None:
        if lower_better:
            color = "#00CC96" if value <= ok else ("#FFA15A" if value <= warn else "#EF553B")
        else:
            color = "#00CC96" if value >= warn else ("#FFA15A" if value >= ok else "#EF553B")
    else:
        color = "#58A6FF"
    return (
        f"<div style='position:relative;height:6px;background:#1a1c24;"
        f"border-radius:3px;margin:6px 0'>"
        f"<div style='position:absolute;left:{pct}%;top:-2px;"
        f"width:10px;height:10px;background:{color};border-radius:50%;"
        f"transform:translateX(-50%)'></div>"
        f"</div>"
    )


def _section_header(title: str, sub: str = "") -> None:
    sub_html = (f"<span style='color:#888;font-weight:400;font-size:0.85rem;"
                f"margin-left:10px'>{sub}</span>" if sub else "")
    st.markdown(
        f"<div style='margin:18px 0 8px 0;border-bottom:1px solid #262730;"
        f"padding-bottom:6px'>"
        f"<span style='font-weight:600;font-size:14px;color:#FAFAFA'>{title}</span>"
        f"{sub_html}</div>",
        unsafe_allow_html=True,
    )


def render_z3(c: dict) -> None:
    name = c["name"]
    league = c.get("league", "")
    flag = LEAGUE_FLAG.get(league, "")
    peers = _league_channels(league)

    # ── Header: badge + name + clickable links ───────────────────
    web_url = c.get("website") or ""
    web_host = urlparse(web_url).netloc if web_url else c.get("domain", "—")
    web_link = (f"<a href='{web_url}' target='_blank' rel='noopener' "
                f"style='color:#58A6FF;text-decoration:none'>{web_host}</a>"
                if web_url else web_host)
    online_since = c.get("domain_first_year")
    age_yrs = (datetime.now().year - online_since) if online_since else None
    age_bit = f"online since {online_since} ({age_yrs} yrs old)" if age_yrs else ""
    wiki_slug = c.get("wikipedia_slug")
    wiki_link = (f"<a href='https://en.wikipedia.org/wiki/{wiki_slug}' target='_blank' "
                  f"style='color:#888;font-size:0.9rem;margin-left:14px'>📖 Wikipedia</a>"
                  if wiki_slug else "")
    app = c.get("ios_app") or {}
    store_url = (app.get("store_url") or "").strip() if app.get("present") else ""
    ios_link = (f"<a href='{store_url}' target='_blank' "
                 f"style='color:#888;font-size:0.9rem;margin-left:8px'>📱 App Store</a>"
                 if store_url else "")
    wayback_link = (f"<a href='https://web.archive.org/web/*/{c.get('domain')}' "
                    f"target='_blank' style='color:#888;font-size:0.9rem;margin-left:8px'>"
                    f"📦 Wayback</a>" if c.get("domain") else "")

    st.markdown(
        f"<h3 style='margin:0 0 4px 0'>{flag} {name}</h3>"
        f"<div style='color:#888;font-size:0.95rem;font-weight:400'>"
        f"{web_link} · {age_bit}{wiki_link}{ios_link}{wayback_link}"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.caption(league or "—")

    # ── Tabs: Overview (summary) + one tab per section ───────────
    (tab_overview, tab_perf, tab_stack, tab_aud, tab_content,
     tab_app, tab_gaps, tab_cmp) = st.tabs([
        "Overview", "⚡ Performance", "🏗️ Stack", "🌍 Audience",
        "📚 Content", "📱 Mobile app", "🔍 Gaps", "⚖️ Compare",
    ])

    # ── Overview tab — narrative card + 5-card highlight strip ───
    with tab_overview:
        st.markdown(
            f"<div style='background:#1a1c24;border-left:3px solid #58A6FF;"
            f"border-radius:4px;padding:12px 16px;margin:12px 0;font-size:14px;"
            f"line-height:1.6;color:#FAFAFA'>"
            f"{_z3_summary_narrative(c, peers)}"
            f"</div>",
            unsafe_allow_html=True,
        )
        # Quick-glance KPI strip with ranks
        _cx = c.get("crux_phone") or {}
        _lcp = _cx.get("lcp_p75")
        _lcp_r = _z3_rank(_lcp, peers,
                          lambda p: _safe(p, "crux_phone", "lcp_p75"),
                          higher_better=False)
        _wv = _wiki_views(c)
        _wv_r = _z3_rank(_wv, peers, _wiki_views)
        _app = c.get("ios_app") or {}
        _r = _app.get("rating")
        _r_r = _z3_rank(_r, peers, lambda p: _safe(p, "ios_app", "rating"))
        _loc_data = c.get("locales") or {}
        _loc = _loc_data.get("count")
        _loc_langs = _loc_data.get("langs") or []
        # Compact 2-letter list — uppercase reads well in the
        # subtitle, e.g. "EN · ES · FR · CA · JA".
        _langs_str = " · ".join(l.upper() for l in _loc_langs[:8])
        if len(_loc_langs) > 8:
            _langs_str += f" +{len(_loc_langs)-8}"
        _t = c.get("tech") or {}
        _vendor = (_t.get("vendor_chain") or [_t.get("vendor")] if _t.get("vendor") else [None])[0]

        st.markdown(kpi_row([
            (f"LCP {_rate_lcp(_lcp)}",
                _fmt_ms(_lcp) if _lcp else "—",
                _rank_pill(_lcp_r[0], _lcp_r[1], league)),
            ("Wiki views (12mo)",
                fmt_num(_wv) if _wv else "—",
                _rank_pill(_wv_r[0], _wv_r[1], league)),
            ("iOS rating",
                f"{_r:.1f}★" if _r else "—",
                _rank_pill(_r_r[0], _r_r[1], league)),
            ("Language versions",
                str(_loc) if _loc else "—",
                _langs_str),
            ("Primary vendor", _vendor or "—",
                "Sport-tech / platform"),
        ]), unsafe_allow_html=True)
        st.caption("Switch to any tab above for the deep dive on that area.")

    # ── Performance tab ───────────────────────────────────────────
    with tab_perf:
        cx = c.get("crux_phone") or {}
        if not cx or "error" in cx:
            st.info("No real-user CrUX data — this site doesn't get enough "
                    "Chrome traffic to be measured. That's itself a signal "
                    "about audience size.")
        else:
            lcp = cx.get("lcp_p75"); inp = cx.get("inp_p75")
            fcp = cx.get("fcp_p75"); ttfb = cx.get("ttfb_p75")
            # Build league-wide LCP / INP context
            lcp_med = _z3_median(peers, lambda p: _safe(p, "crux_phone", "lcp_p75"))
            inp_med = _z3_median(peers, lambda p: _safe(p, "crux_phone", "inp_p75"))
            lcp_rank = _z3_rank(lcp, peers,
                                 lambda p: _safe(p, "crux_phone", "lcp_p75"),
                                 higher_better=False)
            inp_rank = _z3_rank(inp, peers,
                                 lambda p: _safe(p, "crux_phone", "inp_p75"),
                                 higher_better=False)

            col1, col2 = st.columns(2)
            with col1:
                lcp_emoji = _rate_lcp(lcp)
                lcp_gap = ((lcp - lcp_med) / 1000) if lcp_med else None
                gap_text = (f"{abs(lcp_gap):.1f}s "
                             + ("slower" if lcp_gap > 0 else "faster")
                             + f" than {league} median"
                             ) if lcp_gap is not None else ""
                st.markdown(
                    f"<div title='Largest Contentful Paint — when the biggest visible element finishes loading. p75 = the 75th-percentile experience across real Chrome users on mobile (CrUX, last 28 days). Lower is better.' "
                    f"style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;cursor:help'>LCP (p75 mobile)</div>"
                    f"<div style='font-size:32px;font-weight:700'>{lcp_emoji} {lcp/1000:.2f}s</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"{_rank_pill(lcp_rank[0], lcp_rank[1], league)}"
                    f"{' · ' if lcp_rank[0] and gap_text else ''}"
                    f"{gap_text}</div>",
                    unsafe_allow_html=True,
                )
                # Distribution bar relative to league min/max
                league_lcps = [v for v in (_safe(p, "crux_phone", "lcp_p75")
                                           for p in peers) if v is not None]
                if league_lcps:
                    lo, hi = min(league_lcps), max(league_lcps)
                    st.markdown(_bar_for(lcp, lo, hi, ok=2500, warn=4000,
                                           lower_better=True),
                                unsafe_allow_html=True)
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"color:#666;font-size:10px;margin-top:-4px'>"
                        f"<span>{lo/1000:.1f}s (fastest)</span>"
                        f"<span>{hi/1000:.1f}s (slowest)</span></div>",
                        unsafe_allow_html=True,
                    )

            with col2:
                inp_emoji = _rate_inp(inp)
                inp_gap = (inp - inp_med) if inp_med else None
                gap_text = (f"{abs(int(inp_gap))}ms "
                             + ("slower" if inp_gap > 0 else "faster")
                             + f" than {league} median"
                             ) if inp_gap is not None else ""
                st.markdown(
                    f"<div title='Interaction to Next Paint — how long the page takes to react to a tap or click. p75 = the 75th-percentile lag across real Chrome users on mobile (CrUX, last 28 days). Lower is better.' "
                    f"style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;cursor:help'>INP (tap latency p75)</div>"
                    f"<div style='font-size:32px;font-weight:700'>{inp_emoji} {int(inp)} ms</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"{_rank_pill(inp_rank[0], inp_rank[1], league)}"
                    f"{' · ' if inp_rank[0] and gap_text else ''}"
                    f"{gap_text}</div>",
                    unsafe_allow_html=True,
                )
                league_inps = [v for v in (_safe(p, "crux_phone", "inp_p75")
                                           for p in peers) if v is not None]
                if league_inps:
                    lo, hi = min(league_inps), max(league_inps)
                    st.markdown(_bar_for(inp, lo, hi, ok=200, warn=500,
                                           lower_better=True),
                                unsafe_allow_html=True)
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"color:#666;font-size:10px;margin-top:-4px'>"
                        f"<span>{int(lo)}ms (fastest)</span>"
                        f"<span>{int(hi)}ms (slowest)</span></div>",
                        unsafe_allow_html=True,
                    )

            # Secondary CrUX metrics — same visual pattern as LCP/INP
            # above, in a 3-column row with rating emoji + rank + gap
            # + min/max distribution bar.
            cls_v = cx.get("cls_p75")
            fcp_med = _z3_median(peers, lambda p: _safe(p, "crux_phone", "fcp_p75"))
            cls_med = _z3_median(peers, lambda p: _safe(p, "crux_phone", "cls_p75"))
            ttfb_med = _z3_median(peers, lambda p: _safe(p, "crux_phone", "ttfb_p75"))
            fcp_rank = _z3_rank(fcp, peers,
                                 lambda p: _safe(p, "crux_phone", "fcp_p75"),
                                 higher_better=False)
            cls_rank = _z3_rank(cls_v, peers,
                                 lambda p: _safe(p, "crux_phone", "cls_p75"),
                                 higher_better=False)
            ttfb_rank = _z3_rank(ttfb, peers,
                                  lambda p: _safe(p, "crux_phone", "ttfb_p75"),
                                  higher_better=False)

            def _gap_text(value, median, fmt_v, unit_label):
                if value is None or median is None:
                    return ""
                gap = value - median
                if gap == 0:
                    return f"matches {league} median"
                direction = "slower" if gap > 0 else "faster"
                return f"{fmt_v(abs(gap))} {direction} than {league} median"

            ccol1, ccol2, ccol3 = st.columns(3)
            with ccol1:
                rank_html = _rank_pill(fcp_rank[0], fcp_rank[1], league)
                gap_text = _gap_text(fcp, fcp_med,
                                       lambda v: f"{v/1000:.1f}s", "s")
                sep = " · " if rank_html and gap_text else ""
                st.markdown(
                    f"<div title='First Contentful Paint — when ANY content first appears on screen. p75 across real Chrome users on mobile (CrUX). 🟢 ≤1.8s · 🟡 ≤3s · 🔴 >3s. Lower is better.' "
                    f"style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;cursor:help'>FCP (p75 mobile)</div>"
                    f"<div style='font-size:24px;font-weight:700'>{_rate_fcp(fcp)} {_fmt_ms(fcp)}</div>"
                    f"<div style='color:#888;font-size:12px'>{rank_html}{sep}{gap_text}</div>",
                    unsafe_allow_html=True,
                )
                league_fcps = [v for v in (_safe(p, "crux_phone", "fcp_p75")
                                            for p in peers) if v is not None]
                if league_fcps and fcp:
                    lo, hi = min(league_fcps), max(league_fcps)
                    st.markdown(_bar_for(fcp, lo, hi, ok=1800, warn=3000,
                                           lower_better=True),
                                unsafe_allow_html=True)
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"color:#666;font-size:10px;margin-top:-4px'>"
                        f"<span>{lo/1000:.1f}s (fastest)</span>"
                        f"<span>{hi/1000:.1f}s (slowest)</span></div>",
                        unsafe_allow_html=True,
                    )

            with ccol2:
                rank_html = _rank_pill(cls_rank[0], cls_rank[1], league)
                gap_text = ""
                if cls_v is not None and cls_med is not None:
                    gap = cls_v - cls_med
                    if gap == 0:
                        gap_text = f"matches {league} median"
                    else:
                        gap_text = (f"{abs(gap):.2f} "
                                     + ("worse" if gap > 0 else "better")
                                     + f" than {league} median")
                sep = " · " if rank_html and gap_text else ""
                cls_disp = f"{cls_v:.2f}" if cls_v is not None else "—"
                st.markdown(
                    f"<div title='Cumulative Layout Shift — how much the page jumps around as it loads. 0 = perfectly stable. p75 across real Chrome users on mobile (CrUX). 🟢 ≤0.10 · 🟡 ≤0.25 · 🔴 >0.25. Lower is better.' "
                    f"style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;cursor:help'>CLS (layout stability p75)</div>"
                    f"<div style='font-size:24px;font-weight:700'>{_rate_cls(cls_v)} {cls_disp}</div>"
                    f"<div style='color:#888;font-size:12px'>{rank_html}{sep}{gap_text}</div>",
                    unsafe_allow_html=True,
                )
                league_clss = [v for v in (_safe(p, "crux_phone", "cls_p75")
                                            for p in peers) if v is not None]
                if league_clss and cls_v is not None:
                    lo, hi = min(league_clss), max(league_clss)
                    if hi > lo:
                        st.markdown(_bar_for(cls_v, lo, hi, ok=0.10, warn=0.25,
                                               lower_better=True),
                                    unsafe_allow_html=True)
                        st.markdown(
                            f"<div style='display:flex;justify-content:space-between;"
                            f"color:#666;font-size:10px;margin-top:-4px'>"
                            f"<span>{lo:.2f} (best)</span>"
                            f"<span>{hi:.2f} (worst)</span></div>",
                            unsafe_allow_html=True,
                        )

            with ccol3:
                rank_html = _rank_pill(ttfb_rank[0], ttfb_rank[1], league)
                gap_text = _gap_text(ttfb, ttfb_med,
                                       lambda v: f"{int(v)}ms", "ms")
                sep = " · " if rank_html and gap_text else ""
                st.markdown(
                    f"<div title='Time to First Byte — how fast the server responds to the request. p75 across real Chrome users on mobile (CrUX). Reflects backend / CDN speed. 🟢 ≤800ms · 🟡 ≤1800ms · 🔴 >1800ms. Lower is better.' "
                    f"style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;cursor:help'>TTFB (server response p75)</div>"
                    f"<div style='font-size:24px;font-weight:700'>{_rate_ttfb(ttfb)} {_fmt_ms(ttfb)}</div>"
                    f"<div style='color:#888;font-size:12px'>{rank_html}{sep}{gap_text}</div>",
                    unsafe_allow_html=True,
                )
                league_ttfbs = [v for v in (_safe(p, "crux_phone", "ttfb_p75")
                                             for p in peers) if v is not None]
                if league_ttfbs and ttfb:
                    lo, hi = min(league_ttfbs), max(league_ttfbs)
                    st.markdown(_bar_for(ttfb, lo, hi, ok=800, warn=1800,
                                           lower_better=True),
                                unsafe_allow_html=True)
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;"
                        f"color:#666;font-size:10px;margin-top:-4px'>"
                        f"<span>{int(lo)}ms (fastest)</span>"
                        f"<span>{int(hi)}ms (slowest)</span></div>",
                        unsafe_allow_html=True,
                    )

            # Lighthouse synthetic scores — labeled clearly
            psi = c.get("psi") or {}
            if psi and "error" not in psi:
                a11y = psi.get("a11y"); seo = psi.get("seo"); bp = psi.get("best_practices")
                a11y_r = _z3_rank(a11y, peers, lambda p: _safe(p, "psi", "a11y"))
                seo_r  = _z3_rank(seo,  peers, lambda p: _safe(p, "psi", "seo"))
                bp_r   = _z3_rank(bp,   peers, lambda p: _safe(p, "psi", "best_practices"))
                st.markdown(
                    "<div style='color:#888;font-size:12px;margin:14px 0 4px 0'>"
                    "Lighthouse (synthetic mobile audit)</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(kpi_row([
                    ("<span title='Lighthouse Accessibility score 0–100. Audits things like color contrast, alt text on images, ARIA labels, keyboard navigation, form labels. Synthetic but reliable — same checklist Google runs.' style='cursor:help'>Accessibility</span>",
                        str(a11y) if a11y is not None else "—",
                        _rank_pill(a11y_r[0], a11y_r[1], league)),
                    ("<span title='Lighthouse SEO score 0–100. Audits crawlability, meta tags, structured data, mobile friendliness, link practices. Synthetic but reliable signal for search-engine fitness.' style='cursor:help'>SEO</span>",
                        str(seo) if seo is not None else "—",
                        _rank_pill(seo_r[0], seo_r[1], league)),
                    ("<span title='Lighthouse Best Practices score 0–100. Audits HTTPS, deprecated APIs, console errors, image aspect ratios, security headers. Generic web-hygiene checklist.' style='cursor:help'>Best practices</span>",
                        str(bp) if bp is not None else "—",
                        _rank_pill(bp_r[0], bp_r[1], league)),
                ]), unsafe_allow_html=True)

        # ── Stack story ───────────────────────────────────────────────
    with tab_stack:
        t = c.get("tech") or {}
        cdn = _safe(c, "http", "cdn", default="?")
        chain = t.get("vendor_chain") or (
            [t["vendor"]] if t.get("vendor") else [])
        layers = []
        layers.append(("Delivery (CDN)", cdn, "#FF6B6B" if cdn != "?" else "#444"))
        if chain:
            for v in chain:
                layers.append(("Sport-tech / platform", v, "#FF6B6B"))
        if t.get("cms"):
            layers.append(("CMS / DXP", t["cms"], "#58A6FF"))
        if t.get("framework"):
            layers.append(("Frontend framework", t["framework"], "#888"))
        if not chain and not t.get("cms") and not t.get("framework"):
            layers.append(("Platform", "Custom / in-house (no fingerprint matched)",
                            "#888"))

        stack_rows = "".join(
            f"<tr><td style='padding:6px 14px 6px 0;color:#888;font-size:12px;"
            f"vertical-align:top'>{label}</td>"
            f"<td style='padding:6px 0;color:{color};font-weight:600'>{value}</td></tr>"
            for label, value, color in layers
        )
        st.markdown(
            f"<table style='border-collapse:collapse;margin:6px 0'>{stack_rows}</table>",
            unsafe_allow_html=True,
        )

        # Peers on same primary vendor
        primary = chain[0] if chain else (t.get("cms") or t.get("framework"))
        if primary:
            def _primary_of(p):
                pt = p.get("tech") or {}
                pc = pt.get("vendor_chain") or ([pt["vendor"]] if pt.get("vendor") else [])
                return pc[0] if pc else (pt.get("cms") or pt.get("framework"))
            same_stack = [p["name"] for p in peers
                           if _primary_of(p) == primary and p["name"] != name]
            if same_stack:
                sample = ", ".join(same_stack[:5])
                more = f" (+{len(same_stack)-5} more)" if len(same_stack) > 5 else ""
                st.caption(f"Same {primary} stack as: {sample}{more}")

        # ── Audience story ────────────────────────────────────────────
    with tab_aud:
        w = c.get("wikipedia") or {}
        by_lang = w.get("pageviews_by_lang") or {}
        total = _wiki_views(c)
        wl = w.get("langs")
        if total and by_lang:
            # Horizontal bars per language, top 8
            sorted_langs = sorted(by_lang.items(), key=lambda x: -x[1])[:8]
            max_v = sorted_langs[0][1] if sorted_langs else 1
            rows = []
            for lc, v in sorted_langs:
                pct = int(v / max_v * 100)
                share = v / total * 100
                rows.append(
                    f"<tr>"
                    f"<td style='padding:3px 12px 3px 0;color:#888;font-size:12px;width:40px'>{lc}</td>"
                    f"<td style='padding:3px 0'>"
                    f"<div style='background:#58A6FF;height:14px;width:{pct}%;"
                    f"border-radius:2px'></div></td>"
                    f"<td style='padding:3px 0 3px 12px;font-size:12px;width:90px;text-align:right'>"
                    f"{fmt_num(v)}</td>"
                    f"<td style='padding:3px 0 3px 8px;color:#888;font-size:11px;width:50px;text-align:right'>"
                    f"{share:.0f}%</td>"
                    f"</tr>"
                )
            tot_rank = _z3_rank(total, peers, _wiki_views)
            rank_label = _rank_pill(tot_rank[0], tot_rank[1], league)
            st.markdown(
                f"<div style='display:flex;align-items:baseline;gap:14px;"
                f"margin-bottom:6px'>"
                f"<div style='font-size:24px;font-weight:700'>{fmt_num(total)}</div>"
                f"<div style='color:#888;font-size:12px'>Wikipedia pageviews "
                f"(12mo) across {len(by_lang)} major languages · {rank_label}</div></div>"
                f"<table style='border-collapse:collapse;width:100%;max-width:520px'>"
                f"{''.join(rows)}</table>",
                unsafe_allow_html=True,
            )
            if wl:
                st.caption(f"Article exists in {wl} language editions total")

        # Site locales (separate concept — what languages the SITE offers)
        loc = c.get("locales") or {}
        loc_count = loc.get("count")
        loc_langs = loc.get("langs") or []
        loc_src = loc.get("source") or ""
        if loc_count:
            langs_str = "/".join(loc_langs[:8])
            if len(loc_langs) > 8:
                langs_str += f" +{len(loc_langs)-8}"
            st.markdown(
                f"<div style='margin-top:14px;color:#888;font-size:13px'>"
                f"<b style='color:#FAFAFA'>Site offers {loc_count} locale"
                f"{'s' if loc_count != 1 else ''}</b> ({langs_str}) "
                f"<span style='font-size:11px'>· via {loc_src}</span></div>",
                unsafe_allow_html=True,
            )

    # ── Content & features tab ────────────────────────────────────
    with tab_content:
        ct = c.get("content") or {}
        owned = ct.get("owned_subdomains") or []
        feats = ct.get("features") or {}
        feat_loc = ct.get("feature_locations") or {}
        partners = ct.get("external_partners") or []
        sections = ct.get("sections") or []

        # Feature catalog — display order + label + emoji
        FEATURE_CATALOG = [
            ("shop",              "🛒",  "Shop / e-commerce"),
            ("ticketing",         "🎟️",  "Ticketing"),
            ("streaming",         "📺",  "Streaming / club TV"),
            ("membership",        "🏆",  "Membership / fan club"),
            ("museum_tour",       "🏛️",  "Museum / stadium tour"),
            ("academy",           "🎓",  "Academy / youth"),
            ("newsletter",        "📧",  "Newsletter"),
            ("press_media",       "📰",  "Press / media centre"),
            ("podcast",           "🎙️",  "Podcast"),
            ("radio",             "📻",  "Club radio"),
            ("magazine",          "📖",  "Magazine / programme"),
            ("foundation",        "🤝",  "Foundation / CSR"),
            ("investor_relations","📈",  "Investor relations"),
            ("careers",           "💼",  "Careers"),
        ]

        # Sanity check: empty content (e.g. WAF-blocked site)
        if not (owned or feats or partners):
            st.info(
                f"No content fingerprint available for {name}'s site "
                "(probe couldn't load the page, or homepage HTML is "
                "JS-only with no link signals)."
            )
        else:
            # 1) Feature catalog — 13 rows, 2 columns
            present_n = sum(1 for f, _, _ in FEATURE_CATALOG if feats.get(f))
            st.markdown(
                f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                f"letter-spacing:0.5px;margin-bottom:6px'>"
                f"Features detected · {present_n}/{len(FEATURE_CATALOG)}</div>",
                unsafe_allow_html=True,
            )
            # Compute "how many peers also have it" for context
            peer_features = [(p.get("content") or {}).get("features") or {} for p in peers]
            def _peer_share(key: str) -> str:
                if not peer_features: return ""
                hits = sum(1 for pf in peer_features if pf.get(key))
                return f"{hits}/{len(peers)} in {league}"
            rows = []
            for key, emoji, label in FEATURE_CATALOG:
                present = feats.get(key, False)
                loc = feat_loc.get(key)
                if present:
                    icon = "<span style='color:#00CC96;font-weight:600'>✓</span>"
                    label_color = "#FAFAFA"
                else:
                    icon = "<span style='color:#444'>—</span>"
                    label_color = "#666"
                # Linkify location if it looks like a subdomain
                if loc:
                    loc_html = (f" · <a href='https://{loc}' target='_blank' "
                                 f"rel='noopener' style='color:#58A6FF;"
                                 f"text-decoration:none'>{loc}</a>")
                else:
                    loc_html = ""
                share = _peer_share(key)
                share_html = (f"<span style='color:#666;font-size:11px;"
                               f"margin-left:8px'>{share}</span>" if share else "")
                rows.append(
                    "<tr>"
                    f"<td style='padding:4px 10px 4px 0;width:24px;text-align:center'>{icon}</td>"
                    f"<td style='padding:4px 10px 4px 0;width:24px;font-size:16px'>{emoji}</td>"
                    f"<td style='padding:4px 14px 4px 0;color:{label_color}'>"
                    f"<b>{label}</b>{loc_html}</td>"
                    f"<td style='padding:4px 0;text-align:right'>{share_html}</td>"
                    "</tr>"
                )
            st.markdown(
                "<table style='border-collapse:collapse;width:100%;"
                "max-width:680px;font-size:13px;margin:6px 0 18px 0'>"
                + "".join(rows) +
                "</table>",
                unsafe_allow_html=True,
            )

            # 2) Owned digital estate — subdomain inventory
            if owned:
                st.markdown(
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;margin:8px 0 6px 0'>"
                    f"Owned digital estate · {len(owned)} {'subdomain' if len(owned)==1 else 'subdomains'} "
                    f"found on homepage</div>",
                    unsafe_allow_html=True,
                )
                # Try to label each subdomain via reverse-lookup in feat_loc
                loc_to_feat = {v: (lbl, emo) for k, v, in feat_loc.items()
                               for fk, emo, lbl in FEATURE_CATALOG if fk == k}
                chips = []
                for s in owned:
                    info = loc_to_feat.get(s)
                    chip_label = (f"<b style='color:#FAFAFA'>{s}</b>"
                                   + (f" <span style='color:#888;font-size:11px'>"
                                      f"{info[1]} {info[0]}</span>" if info else ""))
                    chips.append(
                        f"<a href='https://{s}' target='_blank' rel='noopener' "
                        f"style='display:inline-block;background:#1a1c24;"
                        f"border:1px solid #2a2c34;border-radius:4px;"
                        f"padding:6px 10px;margin:2px 4px 2px 0;"
                        f"text-decoration:none;font-size:12px'>{chip_label}</a>"
                    )
                st.markdown("".join(chips), unsafe_allow_html=True)

            # 3) Partner / sponsor surface
            if partners:
                st.markdown(
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;margin:18px 0 6px 0'>"
                    f"Partner / sponsor surface · {len(partners)} external "
                    f"{'site' if len(partners)==1 else 'sites'} linked from homepage</div>",
                    unsafe_allow_html=True,
                )
                chips = []
                for p in partners:
                    chips.append(
                        f"<a href='https://{p}' target='_blank' rel='noopener' "
                        f"style='display:inline-block;background:#1a1c24;"
                        f"border:1px solid #2a2c34;border-radius:4px;"
                        f"padding:5px 10px;margin:2px 4px 2px 0;color:#888;"
                        f"text-decoration:none;font-size:12px'>{p}</a>"
                    )
                st.markdown("".join(chips), unsafe_allow_html=True)
                st.caption("Includes kit-maker, shirt sponsors, technical "
                            "partners, museum/tour partners, etc. — anyone "
                            "with an outbound link on the homepage.")

            # 4) Sitemap-index sections (when available)
            if sections:
                st.markdown(
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px;margin:18px 0 6px 0'>"
                    f"Sitemap sections · {len(sections)}</div>",
                    unsafe_allow_html=True,
                )
                section_chips = "".join(
                    f"<span style='display:inline-block;background:#1a1c24;"
                    f"border-radius:4px;padding:4px 10px;margin:2px 4px 2px 0;"
                    f"color:#888;font-size:12px'>{s}</span>"
                    for s in sections
                )
                st.markdown(section_chips, unsafe_allow_html=True)
                st.caption("From the homepage's sitemap-index — proxy for "
                            "how the site organizes its content.")

        # ── App story ─────────────────────────────────────────────────
    with tab_app:
        if not app.get("present"):
            st.info(f"No official iOS app for {name}.")
        else:
            rating = app.get("rating")
            rating_n = app.get("rating_count")
            last = app.get("last_update") or ""
            first = app.get("first_release") or ""
            size = app.get("size_mb")
            langs = app.get("languages")
            store_url = (app.get("store_url") or "").strip()

            # Maintenance freshness: how many months since last update?
            from datetime import date
            months_since = None
            if last:
                try:
                    ld = datetime.strptime(last, "%Y-%m-%d").date()
                    today = date.today()
                    months_since = (today.year - ld.year) * 12 + (today.month - ld.month)
                except Exception:
                    pass
            freshness_color = "#00CC96" if months_since is not None and months_since <= 6 else (
                "#FFA15A" if months_since is not None and months_since <= 24 else "#EF553B"
            )
            freshness_label = "actively maintained" if months_since is not None and months_since <= 6 else (
                "occasionally maintained" if months_since is not None and months_since <= 24 else "appears stale"
            )

            rating_r = _z3_rank(rating, peers, lambda p: _safe(p, "ios_app", "rating"))
            rating_disp = (f"<a href='{store_url}' target='_blank' "
                            f"style='color:#58A6FF;text-decoration:none'>{rating:.1f}★</a>"
                            if rating and store_url else
                            (f"{rating:.1f}★" if rating else "—"))

            col1, col2 = st.columns(2)
            with col1:
                rating_rank_html = _rank_pill(rating_r[0], rating_r[1], league)
                st.markdown(
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px'>Rating</div>"
                    f"<div style='font-size:32px;font-weight:700'>{rating_disp}</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"{fmt_num(rating_n) + ' ratings' if rating_n else ''}"
                    f"{(' · ' + rating_rank_html) if rating_rank_html else ''}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with col2:
                st.markdown(
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px'>Maintenance</div>"
                    f"<div style='font-size:18px;font-weight:600;color:{freshness_color}'>"
                    f"{freshness_label}</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"Last update {last or '—'}"
                    + (f" · {months_since} mo ago" if months_since is not None else "")
                    + f" · on store since {first[:4] if first else '—'}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            # Footer line with size + langs
            st.markdown(
                f"<div style='color:#888;font-size:12px;margin-top:10px'>"
                f"{f'{size:.0f} MB' if size else ''}"
                + (f" · {langs} languages" if langs else "")
                + f"</div>",
                unsafe_allow_html=True,
            )

        # ── What's missing ────────────────────────────────────────────
    with tab_gaps:
        gaps = []
        if not app.get("present"):
            gaps.append("No official iOS app")
        if (loc_count or 0) <= 1:
            gaps.append("Single-locale site (no international audience push)")
        elif loc_src == "country":
            gaps.append("No structured hreflang declarations (locale detected by fallback)")
        sec_p = _safe(c, "http", "security_headers_present")
        if sec_p is not None and sec_p < 3:
            gaps.append(f"Weak security headers ({sec_p}/6 present)")
        if not (t.get("vendor") or t.get("cms") or t.get("framework")):
            gaps.append("No detectable platform fingerprint (likely custom in-house)")
        if not cx or "error" in cx:
            gaps.append("Not enough Chrome traffic for CrUX real-user data")
        if not w.get("pageviews_12mo_total"):
            gaps.append("No Wikipedia interest data (article missing or unmapped)")

        if not gaps:
            st.markdown("<div style='color:#00CC96'>None obvious — this is a "
                         "well-rounded digital footprint.</div>",
                         unsafe_allow_html=True)
        else:
            st.markdown(
                "<ul style='margin:6px 0 0 0;padding-left:20px;color:#FAFAFA;"
                "line-height:1.7;font-size:14px'>"
                + "".join(f"<li>{g}</li>" for g in gaps)
                + "</ul>",
                unsafe_allow_html=True,
            )

        # ── Compare to a peer ─────────────────────────────────────────
    with tab_cmp:
        peer_names = [p["name"] for p in peers if p["name"] != name]
        if peer_names:
            # Pre-pick the closest peer in Wiki views (most natural comparison)
            my_wv = _wiki_views(c) or 0
            peers_by_wv = sorted(
                ((abs((_wiki_views(p) or 0) - my_wv), p["name"]) for p in peers
                 if p["name"] != name and _wiki_views(p))
            )
            default = peers_by_wv[0][1] if peers_by_wv else peer_names[0]
            choice = st.selectbox("Peer", peer_names,
                                    index=peer_names.index(default) if default in peer_names else 0,
                                    key=f"_z3_peer_{name}",
                                    label_visibility="collapsed")
            peer = next(p for p in peers if p["name"] == choice)

            def _fmt_lcp(p):
                v = _safe(p, "crux_phone", "lcp_p75")
                return f"{v/1000:.2f}s" if v else "—"
            def _fmt_inp(p):
                v = _safe(p, "crux_phone", "inp_p75")
                return f"{int(v)} ms" if v else "—"
            def _fmt_rating(p):
                v = _safe(p, "ios_app", "rating")
                return f"{v:.1f}★" if v else "—"
            def _fmt_pages(p):
                v, _, _, _ = _unique_pages(p)
                return fmt_num(v) if v else "—"

            # Each row: (label, formatter→displayed, raw_extractor, higher_better).
            # The "winning" side gets a green text color; the loser
            # stays white. Ties = both white.
            def _pages_raw(p):
                v, _, _, _ = _unique_pages(p)
                return v
            comparisons = [
                ("Real LCP (mobile)",  _fmt_lcp,
                    lambda p: _safe(p, "crux_phone", "lcp_p75"), False),
                ("Real INP (mobile)",  _fmt_inp,
                    lambda p: _safe(p, "crux_phone", "inp_p75"), False),
                ("Wiki views (12mo)",  lambda p: fmt_num(_wiki_views(p) or 0),
                    lambda p: _wiki_views(p), True),
                ("Wiki languages",     lambda p: str(_safe(p, "wikipedia", "langs") or "—"),
                    lambda p: _safe(p, "wikipedia", "langs"), True),
                ("Site pages",         _fmt_pages,
                    _pages_raw, True),
                ("Site locales",       lambda p: str((p.get('locales') or {}).get('count') or "—"),
                    lambda p: (p.get('locales') or {}).get('count'), True),
                ("iOS rating",         _fmt_rating,
                    lambda p: _safe(p, "ios_app", "rating"), True),
                ("Lighthouse A11y",    lambda p: str(_safe(p, "psi", "a11y") or "—"),
                    lambda p: _safe(p, "psi", "a11y"), True),
            ]

            def _winner(va, vb, higher_better):
                """Return ('a', 'b', 'tie', or 'none') for who wins."""
                if va is None and vb is None: return "none"
                if va is None: return "b"
                if vb is None: return "a"
                if va == vb: return "tie"
                if higher_better:
                    return "a" if va > vb else "b"
                return "a" if va < vb else "b"

            GREEN = "#00CC96"
            rows_html = []
            for lbl, fmt, raw, higher in comparisons:
                disp_a = fmt(c)
                disp_b = fmt(peer)
                ra = raw(c); rb = raw(peer)
                w = _winner(ra, rb, higher)
                color_a = GREEN if w == "a" else "#FAFAFA"
                color_b = GREEN if w == "b" else "#FAFAFA"
                weight_a = 600 if w == "a" else 400
                weight_b = 600 if w == "b" else 400
                rows_html.append(
                    f"<tr>"
                    f"<td style='padding:5px 14px 5px 0;color:#888;font-size:12px'>{lbl}</td>"
                    f"<td style='padding:5px 14px;color:{color_a};font-weight:{weight_a}'>{disp_a}</td>"
                    f"<td style='padding:5px 0;color:{color_b};font-weight:{weight_b}'>{disp_b}</td>"
                    f"</tr>"
                )
            rows = "".join(rows_html)
            st.markdown(
                f"<table style='border-collapse:collapse;margin:6px 0;font-size:14px'>"
                f"<thead><tr>"
                f"<th></th>"
                f"<th style='text-align:left;padding:0 14px 8px 0;color:#888;"
                f"font-weight:600;font-size:12px'>{name}</th>"
                f"<th style='text-align:left;padding:0 0 8px 0;color:#888;"
                f"font-weight:600;font-size:12px'>{choice}</th>"
                f"</tr></thead>"
                f"<tbody>{rows}</tbody></table>",
                unsafe_allow_html=True,
            )


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


# ── Glossary ──────────────────────────────────────────────────────
# Content adapts to which zoom level the page is rendering: at Z1/Z2
# it's a column-by-column glossary of the comparison table; at Z3
# it's a tab-by-tab guide to the deep-dive page. Trust caveats and
# data sources are called out per item so users can audit anything.
st.markdown("---")

_is_z3 = bool(g_club_name and _find_channel(g_club_name))

if _is_z3:
    _expander_title = "📖 What every section means"
    _glossary_md = """
### Header
- **Flag + name** — country flag for the league channels (entity_type
  League), dual-color dot for clubs.
- **www.club.com** — the canonical owned-&-operated URL we use as
  the source of truth for every web-side signal. Click to open.
- **Online since YYYY (X yrs old)** — first year the domain appears
  in the Wayback Machine archive. A "site age" proxy — earlier than
  the actual launch sometimes, never later.
- **📖 Wikipedia · 📱 App Store · 📦 Wayback** — direct links to the
  raw sources for verification.

---

### Overview tab

The auto-generated paragraph synthesizes the rest of the page in
2–3 sentences using snapshot data — no AI, deterministic. Below it
a 5-card strip with the headline KPIs (LCP rank · Wiki views rank ·
iOS rating rank · Language versions · Primary vendor).

A **🏆 #1 in green** anywhere on the page means this club is the
league leader on that metric.

---

### ⚡ Performance tab — real user data

Real Chrome users on **mobile**, p75 across the last 28 days, from
Google's Chrome User Experience Report (CrUX). p75 means "75% of
visits were at or better than this number". Lower is better;
thresholds follow Google's Core Web Vitals.

- **LCP** Largest Contentful Paint — when the biggest visible
  element finishes loading. 🟢 ≤2.5s · 🟡 ≤4s · 🔴 >4s.
- **INP** Interaction to Next Paint — how long the page takes to
  react to a tap/click. 🟢 ≤200ms · 🟡 ≤500ms · 🔴 >500ms.
- **FCP** First Contentful Paint — when ANY content first appears.
- **CLS** Cumulative Layout Shift — how much the page jumps as it
  loads. 0 = perfectly stable. 🟢 ≤0.10 · 🟡 ≤0.25 · 🔴 >0.25.
- **TTFB** Time to First Byte — server-response speed (reflects
  backend / CDN). 🟢 ≤800ms · 🟡 ≤1800ms · 🔴 >1800ms.

Each card shows the value, league rank, gap to league median, and
a mini-bar between the fastest and slowest peer.

The **Lighthouse** row at the bottom (Accessibility / SEO / Best
practices) is a separate **synthetic** audit by Google PageSpeed
Insights — not real users. Reliable for non-performance categories;
the Performance score is deliberately omitted (synthetic mobile
emulation punishes content-heavy sites way more than real users
feel it — we use real-user CrUX above instead).

---

### 🏗️ Stack tab

The layered table shows what powers the site, from delivery surface
to content layer:
- **Delivery (CDN)** — detected via HTTP response headers (Akamai,
  Cloudflare, CloudFront, Fastly, Vercel, Netlify).
- **Sport-tech / platform** — specialist sports platforms detected
  in the homepage HTML: Pulselive, Deltatre, Stadion, InCrowd,
  Hiway Media, Contentful.
- **CMS / DXP** — general-purpose content systems: AEM, Sitecore,
  Drupal, WordPress, Wagtail, Kentico Kontent, Storyblok, Umbraco.
- **Frontend framework** — Next.js, Nuxt, Angular, React.

The "Same X stack as…" line below lists peer clubs running on the
same primary vendor — useful for spotting platform patterns within
a league.

Some clubs show "Custom / in-house" — no fingerprint matched
because the site is built bespoke (common for Real Madrid, Bayern,
PSG, etc.). False positives (e.g. an embedded Pulselive widget
that triggered the regex) are cleared manually.

---

### 🌍 Audience tab

**Wikipedia pageviews** are our proxy for global interest — they're
the closest available signal to "how many people search for and
read about this club worldwide". Summed across major language
editions (en, es, de, fr, it, pt, ja, ru, zh, ar, ko, tr, nl, pl)
rather than English-only — fairer to clubs with non-English
fanbases (PSG's English article gets 464k views; their French
article 4M).

The bars are top-8 languages by views with %-of-total. The total
+ rank shows the absolute scale within the league.

**Wiki language editions** below is the number of language editions
the article *exists* in (proxy for global brand width — Juventus
has 119, Lecce has 68).

**Site language versions** is a separate signal — how many language
*editions of the website* the club operates. Detected via a
layered probe (most reliable signal wins):
1. **Manual override** — hand-curated list for WAF-blocked sites.
2. **hreflang** declarations `<link rel="alternate" hreflang="xx">`.
3. **Sitemap path codes** `/xx/` — when between 2 and 12 distinct.
4. **`<html lang>`** — single-locale fallback.
5. **`og:locale`** — Open Graph meta tag.
6. **`Content-Language`** HTTP header.
7. **Country fallback** — last-resort assumption (UK → `en`).

---

### 📚 Content tab

What the site offers beyond just having a homepage. Three layers:

- **Features detected** — a 13-row matrix (shop, ticketing,
  streaming, membership, museum, academy, newsletter, press,
  podcast, magazine, foundation, investor relations, careers).
  ✓ in green when present; the right column shows
  "{N}/{league_size} in {league}" so you can see whether the
  club is ahead of or behind league norms on each feature.
  Where a feature lives on its own subdomain (e.g.
  `store.juventus.com` for the shop), the URL is shown next to
  the row and click-through goes to the site.
- **Owned digital estate** — the list of subdomains found on
  the homepage. Each chip links to the property. The number is
  itself a signal — Juventus runs 5–6 distinct owned properties;
  smaller clubs run 1.
- **Partner / sponsor surface** — external sites linked from
  the homepage (kit makers, shirt sponsors, technical partners,
  tour partners). Higher count usually means a more developed
  commercial portfolio.
- **Sitemap sections** — the sub-sitemap names from the
  sitemap-index (e.g. `first-team-men`, `first-team-women`,
  `jtv-first-team-men`, `jofc`). Proxy for how the editorial
  team organizes content.

Method: features are fingerprinted from homepage HTML (keyword
patterns + own-subdomain prefix matching). Same caveats as the
Stack tab — clubs whose homepage is a JS-only shell may return
empty results.

### 📱 Mobile app tab — iOS only

Data from Apple's iTunes Search/Lookup API. Android is not tracked
(no official free API; scraping is brittle).

- **Rating** — App Store average rating (out of 5), with total
  rating count below. Click the star to open the store page.
- **Maintenance** — months since last update, with a color label:
  🟢 actively maintained (≤6 months) · 🟠 occasionally (≤24
  months) · 🔴 appears stale. Some clubs have apps that were
  delisted from the App Store entirely (Lazio, Union Berlin,
  Sunderland) — these show "No official iOS app".

Below: size in MB and number of languages the app ships with.

---

### 🔍 Gaps tab

Explicit list of notable absences. Negative space is insight —
"no iOS app" / "single locale" / "no fingerprint detected" tells
you something about the club's digital posture.

---

### ⚖️ Compare tab

Side-by-side overlay against a peer club from the same league.
Defaults to the closest peer in Wikipedia views (most natural
matchup) but you can pick any other club. The **green +bold**
side wins on each metric — lower for LCP/INP (latency), higher
for everything else.

---

### What we don't track (and why)
- **Absolute web traffic numbers** — third-party estimators
  (Similarweb) are ±30–50% accurate; not defensible enough to put
  in the data we publish.
- **Google Trends** — the free API path is rate-limited; reliable
  access is paid.
- **Lighthouse Performance score** — synthetic mobile emulation
  punishes content-heavy sites way more than real users feel it.
  We use real-user CrUX numbers instead.
- **Android app data** — no official free API; scraping is brittle
  enough that we leave it out rather than publish unreliable
  numbers.
"""
else:
    _expander_title = "📖 What every column means"
    _glossary_md = """
### Identity
- **Channel** — the YouTube channel name; the dual-color dot uses
  the club's brand colors, league channels get their country flag.
- **Website** — the canonical owned-&-operated landing page (locale-
  specific where it exists, e.g. `/en/`). Click to open.

### Stack (red header)
- **Tech** — detected platform layers. Sport-tech vendors (Pulselive,
  Deltatre, Stadion, InCrowd, Hiway Media, Contentful) render in
  red; CMS / DXP (AEM, Sitecore, Drupal, WordPress, Wagtail,
  Kentico Kontent, Storyblok, Umbraco) in blue; frontend frameworks
  (Next.js, Nuxt, Angular, React) in grey. Fingerprinted from the
  homepage HTML — evidence snippets visible on the Z3 club page.
- **Since** — first year the domain shows up in the Wayback Machine
  archive. A "site age" proxy.
- **CDN** — Content Delivery Network detected from HTTP response
  headers (Akamai, Cloudflare, CloudFront, Fastly, Vercel, Netlify).
- **Sec** — count of security headers present out of 6 standard
  ones: HSTS, CSP, X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, Permissions-Policy.

### Content (cyan header)
- **Pages** — **locale-normalized** unique-page count. When the
  locale signal is trustable (hreflang or sitemap), the raw URL
  total is divided by the locale count so a club with 600 EN +
  600 IT translated articles is reported as 600 unique pages, not
  1,200. Hover any cell to see the raw + divisor. Some clubs use
  non-standard sitemap locations and return "—".
- **Loc** — number of language editions the site offers. Detected
  via a layered probe (most reliable signal wins):
  1. **Manual override** — hand-curated list for sites whose WAF
     blocks our IP (e.g. FC Bayern's Akamai bot manager).
  2. **hreflang** declarations `<link rel="alternate" hreflang="xx">`
     in the homepage `<head>` — Google's structured signal. Used
     when ≥ 2 codes are found.
  3. **Sitemap path codes** `/xx/` — only when between 2 and 12
     distinct codes (above that = false positives from URL slugs).
  4. **`<html lang="xx">`** — fallback for single-locale sites; count = 1.
  5. **`og:locale`** — Facebook Open Graph meta tag.
  6. **Content-Language** HTTP header.
  7. **Country fallback** — last-resort assumption based on the
     club's country (e.g. UK club → `en`). Lower trust; labeled
     `via country` in the cell tooltip.
  Hover any cell to see the **language list and which source layer
  was used**.

### Real users (green header) — from CrUX
Real Chrome users on **mobile**, p75 across the last 28 days. p75
means "75 % of visits were at or better than this number". Lower is
better. Color thresholds follow Google's Core Web Vitals.
- **Real LCP** — Largest Contentful Paint: how long until the
  biggest visible element finishes loading. 🟢 ≤ 2.5s · 🟡 ≤ 4s ·
  🔴 > 4s.
- **Real INP** — Interaction to Next Paint: how long until the
  page responds to a tap or click. 🟢 ≤ 200 ms · 🟡 ≤ 500 ms ·
  🔴 > 500 ms.
- Missing values = the site didn't get enough Chrome traffic to be
  measured.

### Lighthouse (blue header) — synthetic audit
Google PageSpeed Insights audit, mobile profile. Synthetic but
reliable for non-performance categories.
- **A11y** — Accessibility score 0–100.
- **SEO** — Search-engine fitness score 0–100.
- **Performance score is deliberately omitted** — it's noisy on
  content-heavy sites; we use the real-user CrUX numbers above
  instead.

### iOS app (orange header) — from iTunes Search/Lookup API
- **iOS ★** — average rating out of 5, with total rating count in
  parentheses. **Click the rating to open the App Store page.**
- **Updated** — release date of the current version (maintenance
  signal).

### Wikipedia (purple header)
- **Wiki L** — number of language editions of the article (proxy
  for global brand width).
- **Views/12mo** — total Wikipedia pageviews over the trailing 12
  months, **summed across major language editions** (en, es, de,
  fr, it, pt, ja, ru, zh, ar, ko, tr, nl, pl). Earlier versions
  used English-only views, which under-counted clubs with primarily
  Italian / Spanish / French / German fan bases (e.g. PSG's EN
  article gets 464k views; their FR article 4M — total ≈ 5.2M).
  Hover any cell to see the per-language breakdown.

### What we don't track (and why)
- **Absolute traffic numbers** — third-party estimators (Similarweb)
  are ±30–50 %; not defensible enough.
- **Google Trends** — the free path is rate-limited; reliable
  access is paid.
- **PSI Performance score** — synthetic mobile emulation punishes
  content-heavy sites way more than real users feel it.
"""

with st.expander(_expander_title, expanded=False):
    st.markdown(_glossary_md)
