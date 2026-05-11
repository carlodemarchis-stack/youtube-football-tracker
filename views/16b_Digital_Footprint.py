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
    tab_overview, tab_perf, tab_stack, tab_aud, tab_app, tab_gaps, tab_cmp = st.tabs([
        "Overview", "⚡ Performance", "🏗️ Stack", "🌍 Audience",
        "📱 Mobile app", "🔍 Gaps", "⚖️ Compare",
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
        _loc = (c.get("locales") or {}).get("count")
        _t = c.get("tech") or {}
        _vendor = (_t.get("vendor_chain") or [_t.get("vendor")] if _t.get("vendor") else [None])[0]

        st.markdown(kpi_row([
            (f"LCP {_rate_lcp(_lcp)}",
                _fmt_ms(_lcp) if _lcp else "—",
                f"#{_lcp_r[0]} of {_lcp_r[1]} in {league}" if _lcp_r[0] else ""),
            ("Wiki views (12mo)",
                fmt_num(_wv) if _wv else "—",
                f"#{_wv_r[0]} of {_wv_r[1]} in {league}" if _wv_r[0] else ""),
            ("iOS rating",
                f"{_r:.1f}★" if _r else "—",
                f"#{_r_r[0]} of {_r_r[1]} in {league}" if _r_r[0] else ""),
            ("Site locales", str(_loc) if _loc else "—", ""),
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
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px'>LCP (p75 mobile)</div>"
                    f"<div style='font-size:32px;font-weight:700'>{lcp_emoji} {lcp/1000:.2f}s</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"{'#' + str(lcp_rank[0]) + ' of ' + str(lcp_rank[1]) + ' in ' + league + ' · ' if lcp_rank[0] else ''}"
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
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px'>INP (tap latency p75)</div>"
                    f"<div style='font-size:32px;font-weight:700'>{inp_emoji} {int(inp)} ms</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"{'#' + str(inp_rank[0]) + ' of ' + str(inp_rank[1]) + ' in ' + league + ' · ' if inp_rank[0] else ''}"
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

            # Secondary CrUX metrics — small inline row
            st.markdown(
                f"<div style='color:#888;font-size:12px;margin-top:14px'>"
                f"FCP {_fmt_ms(fcp)} · CLS {(cx.get('cls_p75') or 0):.2f}"
                f" · TTFB {_fmt_ms(ttfb)}"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Lighthouse synthetic scores — labeled clearly
            psi = c.get("psi") or {}
            if psi and "error" not in psi:
                a11y = psi.get("a11y"); seo = psi.get("seo"); bp = psi.get("best_practices")
                a11y_r = _z3_rank(a11y, peers, lambda p: _safe(p, "psi", "a11y"))
                seo_r  = _z3_rank(seo,  peers, lambda p: _safe(p, "psi", "seo"))
                st.markdown(
                    f"<div style='display:flex;gap:24px;margin-top:6px;"
                    f"font-size:12px;color:#888'>"
                    f"<span>Lighthouse (synthetic): "
                    f"<b style='color:#FAFAFA'>A11y {a11y}</b>"
                    f"{'  #' + str(a11y_r[0]) + '/' + str(a11y_r[1]) if a11y_r[0] else ''}"
                    f" · <b style='color:#FAFAFA'>SEO {seo}</b>"
                    f"{'  #' + str(seo_r[0]) + '/' + str(seo_r[1]) if seo_r[0] else ''}"
                    f" · BP {bp}</span></div>",
                    unsafe_allow_html=True,
                )

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
            rank_label = (f"#{tot_rank[0]} of {tot_rank[1]} in {league}"
                           if tot_rank[0] else "")
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
                st.markdown(
                    f"<div style='font-size:11px;color:#888;text-transform:uppercase;"
                    f"letter-spacing:0.5px'>Rating</div>"
                    f"<div style='font-size:32px;font-weight:700'>{rating_disp}</div>"
                    f"<div style='color:#888;font-size:12px'>"
                    f"{fmt_num(rating_n) + ' ratings' if rating_n else ''}"
                    f"{' · #' + str(rating_r[0]) + ' of ' + str(rating_r[1]) + ' in ' + league if rating_r[0] else ''}"
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

            comparisons = [
                ("Real LCP (mobile)",  _fmt_lcp(c),        _fmt_lcp(peer)),
                ("Real INP (mobile)",  _fmt_inp(c),        _fmt_inp(peer)),
                ("Wiki views (12mo)",  fmt_num(_wiki_views(c) or 0),
                                       fmt_num(_wiki_views(peer) or 0)),
                ("Wiki languages",     str(_safe(c, "wikipedia", "langs") or "—"),
                                       str(_safe(peer, "wikipedia", "langs") or "—")),
                ("Site pages",         _fmt_pages(c),      _fmt_pages(peer)),
                ("Site locales",       str((c.get('locales') or {}).get('count') or "—"),
                                       str((peer.get('locales') or {}).get('count') or "—")),
                ("iOS rating",         _fmt_rating(c),     _fmt_rating(peer)),
                ("Lighthouse A11y",    str(_safe(c, "psi", "a11y") or "—"),
                                       str(_safe(peer, "psi", "a11y") or "—")),
            ]
            rows = "".join(
                f"<tr>"
                f"<td style='padding:5px 14px 5px 0;color:#888;font-size:12px'>{lbl}</td>"
                f"<td style='padding:5px 14px;color:#FAFAFA'>{va}</td>"
                f"<td style='padding:5px 0;color:#FAFAFA'>{vb}</td>"
                f"</tr>"
                for lbl, va, vb in comparisons
            )
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
# Always rendered at the bottom of the page (Z1/Z2/Z3) so the
# meaning of each metric is one scroll away, with the source +
# trust caveat called out per item.
st.markdown("---")
with st.expander("📖 What every column means", expanded=False):
    st.markdown(
        """
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
    )
