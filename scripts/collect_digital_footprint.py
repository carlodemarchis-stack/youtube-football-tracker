"""Yearly Digital Footprint collector.

Reads its slate from Supabase: every core channel (Club + League,
excluding Players / Federations / Other Clubs / Women) that has a
non-empty `website` set. Smaller clubs without a website mapped yet
are skipped — fill them in via Admin → Channel Management.

For each channel, pulls the trustable signals validated upstream:

  - Wayback CDX            : domain first-seen year (proxy for "online since")
  - HTTP headers           : CDN, security headers, server fingerprint
  - sitemap.xml            : page count + locale count
  - Wikipedia API          : language editions, article size, EN pageviews 12mo
  - iTunes Search/Lookup   : iOS app rating, last update, size, languages
  - Google CrUX            : real-user mobile + desktop performance
  - Google PSI             : Lighthouse a11y / best-practices / SEO
                             (Performance score deliberately ignored — too noisy)

Writes `data/digital_footprint.json` keyed by youtube_channel_id.
The view at views/16b_Digital_Footprint.py reads that file.

Usage:
  GOOGLE_PSI_API_KEY=AIza... python3 scripts/collect_digital_footprint.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.channels import COUNTRY_TO_LEAGUE
from src.database import Database

UA = "ytft-lab/1.0 (https://ytft.aguywithascarf.com)"
HEADERS = {"User-Agent": UA}

PSI_KEY = os.environ.get("GOOGLE_PSI_API_KEY", "").strip()


# ── Per-source helpers ────────────────────────────────────────────
def wayback_first_year(domain: str) -> int | None:
    try:
        r = requests.get(
            "http://web.archive.org/cdx/search/cdx",
            params={"url": domain, "limit": 1, "output": "json"},
            timeout=20, headers=HEADERS,
        )
        r.raise_for_status()
        rows = r.json()
        if len(rows) < 2:
            return None
        ts = rows[1][1]
        return int(ts[:4]) if ts and len(ts) >= 4 else None
    except Exception:
        return None


# ── Tech-stack fingerprints (CMS / sport-tech vendor / framework) ─
# Patterns validated against a sample of known cases:
#   Premier League → Pulselive (well-known)
#   Bundesliga → Deltatre (deltatreId embedded in page JSON)
#   Man United → Stadion (DAZN-owned, adopted 2024)
#   Real Madrid / BVB → Adobe Experience Manager
#   AC Milan → Kentico Kontent (headless CMS)
#   Bayer 04 → Angular framework
# Each entry: (label, regex, category). category is one of
#   "vendor" (sport-tech specialists),
#   "cms"    (general-purpose content systems), or
#   "framework" (frontend rendering tech).
import re as _re

TECH_PATTERNS = [
    # ── Sport-tech vendors / platform substrates (highest signal) ─
    # Pulselive, Deltatre, Stadion are sport-specialist platforms.
    # Contentful is a general-purpose headless CMS but is a
    # meaningful platform choice in football (Stadion sits on top of
    # it; several clubs run native Contentful). Elevated to vendor
    # so the table surfaces it next to the specialists.
    ("Pulselive",     _re.compile(r"pulselive\.com|pulse\.football|cdn\.pulselive|data-pulse-|aws\.pulselive", _re.I), "vendor"),
    # Deltatre footprints: their CMS is Forge, their video player is
    # Diva. Both appear as siteconfig:forge* keys, forgelibpath URLs,
    # divavideos paths or DIVA_* feature flags in embedded JSON.
    ("Deltatre",      _re.compile(r"deltatre|deltatreId|forgelibpath|siteconfig:forge|dltforge|divavideos|diva[._-]video|DIVA_VIDEO|/forge/", _re.I), "vendor"),
    ("Stadion",       _re.compile(r"stadion\.io|contentfulproxy\.stadion|dazn-stadion|stadion-app", _re.I),            "vendor"),
    # InCrowd Sports — UK sport-tech (Crystal Palace, etc.). Merged
    # with Cortex (data-feed sister company); grouping under same
    # vendor since they're one entity now.
    ("InCrowd",       _re.compile(r"incrowdsports\.com|incrowd\.io|cortextech\.io|cortex\.io", _re.I), "vendor"),
    # Hiway Media — Italian sport-tech (SS Lazio uses
    # mediaverse.sslazio.hiway.media). Accept both Hyway/Hiway
    # spellings to be safe against future rebrands.
    ("Hiway Media",   _re.compile(r"hiway\.media|hyway\.media|hiwaymedia|hywaymedia", _re.I), "vendor"),
    ("Contentful",    _re.compile(r"contentful\.com|images\.ctfassets\.net|videos\.ctfassets\.net|assets\.ctfassets\.net|downloads\.ctfassets\.net", _re.I), "vendor"),
    # ── CMS / DXP ──────────────────────────────────────────
    ("AEM",           _re.compile(r"data-sly-|/etc\.clientlibs/|/content/dam/", _re.I),                       "cms"),
    ("Sitecore",      _re.compile(r"/sitecore/|SC_ANALYTICS_GLOBAL_COOKIE|data-sc-item-id", _re.I),           "cms"),
    ("Drupal",        _re.compile(r'generator"\s*content="Drupal|data-drupal-|/sites/default/files/', _re.I), "cms"),
    ("WordPress",     _re.compile(r'wp-content/|wp-includes/|generator"\s*content="WordPress', _re.I),         "cms"),
    ("Wagtail",       _re.compile(r'generator"\s*content="Wagtail', _re.I),                                    "cms"),
    ("Kentico Kontent",_re.compile(r"kc-usercontent\.com|kontent\.ai", _re.I),                                 "cms"),
    ("Storyblok",     _re.compile(r"storyblok\.com|a\.storyblok\.com", _re.I),                                "cms"),
    ("Strapi",        _re.compile(r"strapi\.io", _re.I),                                                       "cms"),
    ("Magnolia",      _re.compile(r"magnolia-cms|magnolia\.info", _re.I),                                      "cms"),
    ("Umbraco",       _re.compile(r"umbraco\b", _re.I),                                                        "cms"),
    # ── Frontend frameworks ────────────────────────────────
    ("Next.js",       _re.compile(r"__NEXT_DATA__|/_next/static", _re.I),                                      "framework"),
    ("Nuxt",          _re.compile(r"__NUXT__|nuxt-link|data-n-head", _re.I),                                   "framework"),
    ("Angular",       _re.compile(r"_nghost-ng-|_ngcontent-|ng-version=", _re.I),                              "framework"),
    ("React",         _re.compile(r'data-reactroot|"react"\s*:\s*"', _re.I),                                   "framework"),
    # ── Adjacent identifiers (non-categorized but worth flagging) ──
    ("Cloudinary",    _re.compile(r"res\.cloudinary\.com|cloudinary-core", _re.I),                            "image"),
    ("Magento",       _re.compile(r"Mage\.Cookies|/static/version\d+/frontend/Magento_", _re.I),               "shop"),
    ("Shopify",       _re.compile(r"cdn\.shopify\.com|shopify\.com/s/", _re.I),                               "shop"),
]


def tech_stack(url: str) -> dict:
    """Fingerprint the homepage HTML for CMS / sport-tech vendor /
    frontend framework. Returns first match per category.

    Reliability: high-precision (false positives unlikely — patterns
    require vendor-specific markers) but not exhaustive (clubs with
    in-house custom platforms return empty)."""
    if not url:
        return {}
    try:
        r = requests.get(url, timeout=20, allow_redirects=True,
                         headers=HEADERS)
        html = r.text[:300_000]  # 300KB is plenty; anything below is
                                  # already past the <head>+nav region
                                  # where vendor markers live.
    except Exception:
        return {}

    out: dict = {"vendor": None, "cms": None, "framework": None,
                 "extras": [], "evidence": []}

    # Meta generator is the most authoritative signal when present
    mg = _re.search(
        r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']',
        html, _re.I)
    if mg:
        out["evidence"].append(f"<meta generator>: {mg.group(1)[:50]}")

    # Allow multiple vendor matches (Stadion + Contentful is the
    # common case — both are real, complementary signals).
    # Single match for cms and framework (first wins).
    vendor_hits: list[str] = []
    seen_single = set()
    for label, rx, category in TECH_PATTERNS:
        if category in seen_single:
            continue
        m = rx.search(html)
        if not m:
            continue
        if category == "vendor":
            vendor_hits.append(label)
        elif category in ("cms", "framework"):
            seen_single.add(category)
            out[category] = label
        else:
            seen_single.add(category)
            out["extras"].append(label)
        ev = html[max(0, m.start()-5):m.end()+15].replace("\n", " ")[:55]
        out["evidence"].append(f"{label}: …{ev}…")

    if vendor_hits:
        # Primary vendor = first hit; full chain stored too so the UI
        # can render "Stadion · Contentful" for stacked layers.
        out["vendor"] = vendor_hits[0]
        out["vendor_chain"] = vendor_hits

    return out


def http_headers(url: str) -> dict:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20,
                         allow_redirects=True, stream=True)
        h = {k.lower(): v for k, v in r.headers.items()}
        r.close()
        cdn = ""
        srv = h.get("server", "").lower()
        if "akamai" in srv or any("akamai" in k for k in h):
            cdn = "Akamai"
        elif "cloudflare" in srv or "cf-ray" in h:
            cdn = "Cloudflare"
        elif "cloudfront" in h.get("via", "").lower():
            cdn = "CloudFront"
        elif "fastly" in srv or "fastly" in h.get("via", "").lower():
            cdn = "Fastly"
        elif "vercel" in srv or "x-vercel-id" in h:
            cdn = "Vercel"
        elif "netlify" in srv:
            cdn = "Netlify"
        sec = sum(1 for k in (
            "strict-transport-security", "content-security-policy",
            "x-frame-options", "x-content-type-options",
            "referrer-policy", "permissions-policy",
        ) if k in h)
        return {"cdn": cdn or "Unknown",
                "security_headers_present": sec,
                "security_headers_max": 6,
                "hsts": "strict-transport-security" in h,
                "final_url": r.url}
    except Exception:
        return {"cdn": "?", "security_headers_present": None,
                "security_headers_max": 6}


# Country code → primary language code. Used as the lowest-trust
# fallback when a site is a JS-only shell that exposes no HTML
# locale signal at all (very common in modern Premier League sites).
_COUNTRY_TO_LANG = {
    "IT": "it", "GB": "en", "EN": "en", "UK": "en",
    "ES": "es", "DE": "de", "FR": "fr", "US": "en",
    "PT": "pt", "NL": "nl", "BE": "nl",
}


def detect_locales(website: str, sitemap_locales: list[str] | None = None,
                   country: str | None = None) -> dict:
    """Return {count, langs[], source} for a site's language editions.

    Layered detector — uses the most reliable signal that returns ≥ 2:
      1. <link rel="alternate" hreflang="xx"> in the homepage <head>
         (Google/Bing standard, machine-readable).
      2. Sitemap path regex /xx/ — only when ≤ 12 distinct values
         (anything higher tends to be false positives — URL slugs
         that happen to match the 2-letter pattern).
      3. <html lang="xx"> on the homepage — single-locale fallback.
      4. "—" when none of the above worked.

    Two-letter codes are normalized so en-GB + en-US collapse to en.
    Returns an empty dict on fetch failure so the caller can show "—".
    """
    if not website:
        return {}
    try:
        r = requests.get(website, headers=HEADERS, timeout=20,
                         allow_redirects=True)
        html = r.text[:300_000]
        hdrs = {k.lower(): v for k, v in r.headers.items()}
    except Exception:
        return {}

    # 1. hreflang — strongest signal
    raw = set(_re.findall(
        r'hreflang=["\']([a-z]{2}(?:-[A-Z]{2})?)["\']', html))
    raw.discard("x-default")
    hreflang = sorted({l.split("-")[0] for l in raw})
    if len(hreflang) >= 2:
        return {"count": len(hreflang), "langs": hreflang,
                "source": "hreflang"}

    # 2. Sitemap path regex — moderate signal
    if sitemap_locales and 2 <= len(sitemap_locales) <= 12:
        return {"count": len(sitemap_locales), "langs": list(sitemap_locales),
                "source": "sitemap"}

    # 3. html lang attribute — single-locale fallback
    m = _re.search(r'<html[^>]+lang=["\']([a-z]{2})', html, _re.I)
    if m:
        return {"count": 1, "langs": [m.group(1).lower()],
                "source": "html-lang"}

    # 4. og:locale — Facebook Open Graph standard, used by some
    #    sites that skip html lang (e.g. Lega Serie A homepage).
    m = _re.search(
        r'property=["\']og:locale["\'][^>]+content=["\']([a-z]{2})',
        html, _re.I)
    if m:
        return {"count": 1, "langs": [m.group(1).lower()],
                "source": "og:locale"}

    # 5. Content-Language HTTP header
    if hdrs.get("content-language"):
        v = hdrs["content-language"][:2].lower()
        if v.isalpha():
            return {"count": 1, "langs": [v], "source": "http-header"}

    # 6. Country fallback — last-resort assumption for JS-only sites
    #    that ship an empty HTML shell (common in Premier League).
    #    Source label is "country" so consumers know it's lower trust.
    if country:
        cc = (country or "").upper()
        lang = _COUNTRY_TO_LANG.get(cc)
        if lang:
            return {"count": 1, "langs": [lang], "source": "country"}

    return {"count": None, "langs": [], "source": "unknown"}


def parse_sitemap(domain: str) -> dict:
    import re
    candidates = [
        f"https://www.{domain}/sitemap.xml",
        f"https://{domain}/sitemap.xml",
        f"https://www.{domain}/robots.txt",
    ]
    sitemap_urls: list[str] = []
    for url in candidates:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            text = r.text
            if url.endswith("robots.txt"):
                for line in text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemap_urls.append(line.split(":", 1)[1].strip())
                if sitemap_urls:
                    break
            else:
                sitemap_urls.append(url)
                break
        except Exception:
            continue

    if not sitemap_urls:
        return {"pages": None, "locales": None, "locale_list": []}

    total = 0
    locales: set[str] = set()

    def fetch(sm_url):
        try:
            r = requests.get(sm_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return []
            return re.findall(r"<loc>([^<]+)</loc>", r.text)
        except Exception:
            return []

    for sm in sitemap_urls:
        locs = fetch(sm)
        if locs and all(loc.endswith(".xml") for loc in locs[:3]):
            for sub in locs[:30]:
                sublocs = fetch(sub)
                total += len(sublocs)
                for loc in sublocs:
                    m = re.search(r"//[^/]+/([a-z]{2})(?:/|$)", loc)
                    if m:
                        locales.add(m.group(1))
        else:
            total += len(locs)
            for loc in locs:
                m = re.search(r"//[^/]+/([a-z]{2})(?:/|$)", loc)
                if m:
                    locales.add(m.group(1))

    return {"pages": total or None,
            "locales": len(locales) or None,
            "locale_list": sorted(locales)}


def wikipedia(slug: str) -> dict:
    out = {"langs": None, "article_kb": None, "pageviews_12mo": None}
    if not slug:
        return out
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "langlinks|info",
                    "titles": slug, "lllimit": 500,
                    "format": "json", "redirects": 1},
            headers=HEADERS, timeout=20,
        )
        d = r.json()
        pages = list((d.get("query") or {}).get("pages", {}).values())
        if pages and "missing" not in pages[0]:
            p = pages[0]
            out["langs"] = len(p.get("langlinks", [])) + 1
            out["article_kb"] = round((p.get("length") or 0) / 1024)
    except Exception:
        pass
    try:
        end = datetime.now(timezone.utc).replace(day=1)
        start = end - timedelta(days=370)
        r = requests.get(
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/all-agents/{slug}/monthly/"
            f"{start.strftime('%Y%m%d')}00/{end.strftime('%Y%m%d')}00",
            headers=HEADERS, timeout=20,
        )
        d = r.json()
        items = d.get("items", []) if isinstance(d, dict) else []
        out["pageviews_12mo"] = sum(i.get("views", 0) for i in items)
    except Exception:
        pass
    return out


def ios_app(query: str, country: str) -> dict:
    out = {"present": False}
    try:
        r = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "entity": "software",
                    "country": country, "limit": 5},
            headers=HEADERS, timeout=20,
        )
        results = r.json().get("results", [])
        token = (query.split() or [""])[0].lower()
        chosen = None
        for app in results:
            name = (app.get("trackName") or "").lower()
            artist = (app.get("artistName") or "").lower()
            if token and token in (name + " " + artist):
                chosen = app
                break
        if not chosen and results:
            chosen = results[0]
        if not chosen:
            return out
        out.update({
            "present": True,
            "name": chosen.get("trackName"),
            "developer": chosen.get("artistName"),
            "version": chosen.get("version"),
            "first_release": (chosen.get("releaseDate") or "")[:10],
            "last_update": (chosen.get("currentVersionReleaseDate") or "")[:10],
            "rating": chosen.get("averageUserRating"),
            "rating_count": chosen.get("userRatingCount"),
            "size_mb": round(int(chosen.get("fileSizeBytes", 0)) / 1024 / 1024, 1),
            "languages": len(chosen.get("languageCodesISO2A") or []),
            "language_list": chosen.get("languageCodesISO2A") or [],
            "min_os": chosen.get("minimumOsVersion"),
            "price": chosen.get("formattedPrice"),
            "store_url": chosen.get("trackViewUrl"),
        })
    except Exception:
        pass
    return out


def crux(origin: str, form_factor: str | None = None) -> dict:
    if not PSI_KEY:
        return {}
    body: dict = {"origin": origin}
    if form_factor:
        body["formFactor"] = form_factor
    try:
        r = requests.post(
            "https://chromeuxreport.googleapis.com/v1/records:queryRecord",
            params={"key": PSI_KEY},
            json=body, timeout=30,
            headers={"Content-Type": "application/json"},
        )
        d = r.json()
        if "error" in d:
            return {"error": d["error"].get("message", "")[:80]}
        m = d.get("record", {}).get("metrics", {})

        def p75(k):
            v = m.get(k, {}).get("percentiles", {}).get("p75")
            return float(v) if v is not None else None

        cls_v = p75("cumulative_layout_shift")
        if cls_v is not None:
            cls_v = cls_v / 100
        return {"lcp_p75": p75("largest_contentful_paint"),
                "fcp_p75": p75("first_contentful_paint"),
                "inp_p75": p75("interaction_to_next_paint"),
                "cls_p75": cls_v,
                "ttfb_p75": p75("experimental_time_to_first_byte")}
    except Exception as e:
        return {"error": str(e)[:80]}


def psi(url: str) -> dict:
    if not PSI_KEY:
        return {}
    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile",
                    "category": ["accessibility", "best-practices", "seo"],
                    "key": PSI_KEY},
            timeout=120,
        )
        d = r.json()
        if "error" in d:
            return {"error": d["error"].get("message", "")[:80]}
        cats = d.get("lighthouseResult", {}).get("categories", {})

        def pct(k):
            s = cats.get(k, {}).get("score")
            return round(s * 100) if s is not None else None

        return {"a11y": pct("accessibility"),
                "best_practices": pct("best-practices"),
                "seo": pct("seo")}
    except Exception as e:
        return {"error": str(e)[:80]}


# ── Helpers ───────────────────────────────────────────────────────
def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _origin_of(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc.lower()
        if not host.startswith("www."):
            host = "www." + host
        return f"{p.scheme}://{host}"
    except Exception:
        return ""


def _country_to_app_country(country: str) -> str:
    """Map our 2-letter / full country names to iTunes country codes."""
    c = (country or "").strip().lower()
    mapping = {
        "it": "it", "italy": "it",
        "gb": "gb", "uk": "gb", "england": "gb",
        "es": "es", "spain": "es",
        "de": "de", "germany": "de",
        "fr": "fr", "france": "fr",
        "us": "us", "united states": "us",
    }
    return mapping.get(c, "us")


# ── Per-channel orchestrator ──────────────────────────────────────
def collect_one(ch: dict) -> dict:
    name = ch.get("name", "")
    website = (ch.get("website") or "").strip()
    wiki_slug = (ch.get("wikipedia_slug") or "").strip()
    domain = _domain_of(website)
    origin = _origin_of(website)
    print(f"  → {name:<32} ({domain})", flush=True)

    # Use country → league name for both clubs and league channels.
    # ("LALIGA EA SPORTS" the YouTube channel sits under La Liga, etc.)
    country = (ch.get("country") or "").upper()
    league = COUNTRY_TO_LEAGUE.get(country, ch.get("country") or "—")
    app_country = _country_to_app_country(ch.get("country") or "us")
    app_query = name  # use the channel name as the App Store search term

    sm = parse_sitemap(domain) if domain else {}
    return {
        # Identity (joins back to the channels table)
        "youtube_channel_id": ch.get("youtube_channel_id"),
        "name":               name,
        "handle":             ch.get("handle"),
        "league":             league,
        "country":            ch.get("country"),
        "entity_type":        ch.get("entity_type"),
        "website":            website,
        "domain":             domain,
        "wikipedia_slug":     wiki_slug,

        # Signals
        "domain_first_year":  wayback_first_year(domain) if domain else None,
        "http":               http_headers(website) if website else {},
        "tech":               tech_stack(website) if website else {},
        "sitemap":            sm,
        "locales":            detect_locales(website, sm.get("locale_list"), ch.get("country")) if website else {},
        "wikipedia":          wikipedia(wiki_slug),
        "ios_app":            ios_app(app_query, app_country),
        "crux_phone":         crux(origin, "PHONE") if origin else {},
        "crux_desktop":       crux(origin, "DESKTOP") if origin else {},
        "crux_combined":      crux(origin) if origin else {},
        "psi":                psi(website) if website else {},
    }


def main() -> int:
    if not PSI_KEY:
        print("WARN: GOOGLE_PSI_API_KEY not set — CrUX + PSI columns empty",
              file=sys.stderr)

    url = os.environ.get("SUPABASE_URL", "")
    # Read-only — anon key is fine; service key is also accepted.
    key = (os.environ.get("SUPABASE_KEY", "")
           or os.environ.get("SUPABASE_SERVICE_KEY", ""))
    if not (url and key):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set", file=sys.stderr)
        return 1

    db = Database(url, key)
    chs = db.get_all_channels()
    core = [c for c in chs if c.get("entity_type") not in
            ("Player", "Federation", "OtherClub", "WomenClub")]
    slate = [c for c in core if (c.get("website") or "").strip()]
    skipped = [c for c in core if not (c.get("website") or "").strip()]

    print(f"Slate: {len(slate)} core channels with website set "
          f"({len(skipped)} skipped — fill via Admin → Channel Management)")
    if not slate:
        print("Nothing to collect. Run scripts/seed_channel_websites.py first.")
        return 1

    t0 = time.time()
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(collect_one, slate):
            out.append(r)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 2,
        "covered": len(out),
        "skipped_no_website": [c["name"] for c in skipped],
        "channels": out,
    }

    out_path = Path(__file__).resolve().parent.parent / "data" / "digital_footprint.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\n✓ Wrote {out_path} — {len(out)} channels in {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
