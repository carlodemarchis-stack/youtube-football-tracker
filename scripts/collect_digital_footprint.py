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
        "sitemap":            parse_sitemap(domain) if domain else {},
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
