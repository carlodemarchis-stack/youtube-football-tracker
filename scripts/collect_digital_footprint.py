"""Yearly Digital Footprint collector.

Pulls a fixed slate of trustable, free signals about each club's owned
& operated digital estate (website + iOS app + external validators):

  - Wayback CDX            : domain first-seen year (proxy for "online since")
  - HTTP headers           : CDN, security headers, server fingerprint
  - sitemap.xml            : page count + section split + locale count
  - Wikipedia API          : language editions, article size, EN pageviews 12mo
  - iTunes Search/Lookup   : iOS app rating, last update, size, languages
  - Google CrUX            : real-user mobile + desktop performance
  - Google PSI             : Lighthouse a11y / best-practices / SEO scores
                             (Performance score deliberately ignored — too noisy)

Reads PSI/CrUX key from $GOOGLE_PSI_API_KEY (Railway env or local .env).

Output: writes `data/digital_footprint.json` — a single snapshot read by
        views/16b_Digital_Footprint.py. Yearly cadence; idempotent.

Usage:
  GOOGLE_PSI_API_KEY=AIza... python3 scripts/collect_digital_footprint.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# Polite UA — required by Wikipedia, harmless elsewhere
UA = "ytft-lab/1.0 (https://ytft.aguywithascarf.com)"
HEADERS = {"User-Agent": UA}

PSI_KEY = os.environ.get("GOOGLE_PSI_API_KEY", "").strip()

# ── Slate: top 5 clubs per league ─────────────────────────────────
# Hardcoded for the proof-of-concept. Once leagues are fully modeled
# in the DB this can be derived from channels.country + sub_count desc.
CLUBS: list[dict] = [
    # Serie A
    {"name": "Juventus",     "league": "Serie A",        "domain": "juventus.com",            "site": "https://www.juventus.com/en/",     "wiki": "Juventus_FC",                "app_query": "juventus",                  "app_country": "it"},
    {"name": "Inter Milan",  "league": "Serie A",        "domain": "inter.it",                "site": "https://www.inter.it/en",          "wiki": "Inter_Milan",                "app_query": "inter milan official",      "app_country": "it"},
    {"name": "AC Milan",     "league": "Serie A",        "domain": "acmilan.com",             "site": "https://www.acmilan.com/en",       "wiki": "AC_Milan",                   "app_query": "ac milan official",         "app_country": "it"},
    {"name": "SSC Napoli",   "league": "Serie A",        "domain": "sscnapoli.it",            "site": "https://sscnapoli.it/en/",         "wiki": "SSC_Napoli",                 "app_query": "ssc napoli",                "app_country": "it"},
    {"name": "AS Roma",      "league": "Serie A",        "domain": "asroma.com",              "site": "https://www.asroma.com/en",        "wiki": "AS_Roma",                    "app_query": "as roma official",          "app_country": "it"},
    # Premier League
    {"name": "Manchester United", "league": "Premier League", "domain": "manutd.com",         "site": "https://www.manutd.com/",          "wiki": "Manchester_United_F.C.",     "app_query": "manchester united",         "app_country": "gb"},
    {"name": "Manchester City",   "league": "Premier League", "domain": "mancity.com",        "site": "https://www.mancity.com/",         "wiki": "Manchester_City_F.C.",       "app_query": "manchester city official",  "app_country": "gb"},
    {"name": "Liverpool FC",      "league": "Premier League", "domain": "liverpoolfc.com",    "site": "https://www.liverpoolfc.com/",     "wiki": "Liverpool_F.C.",             "app_query": "liverpool fc official",     "app_country": "gb"},
    {"name": "Chelsea FC",        "league": "Premier League", "domain": "chelseafc.com",      "site": "https://www.chelseafc.com/en",     "wiki": "Chelsea_F.C.",               "app_query": "chelsea fc official",       "app_country": "gb"},
    {"name": "Arsenal",           "league": "Premier League", "domain": "arsenal.com",        "site": "https://www.arsenal.com/",         "wiki": "Arsenal_F.C.",               "app_query": "arsenal official",          "app_country": "gb"},
    # La Liga
    {"name": "Real Madrid",  "league": "La Liga",        "domain": "realmadrid.com",          "site": "https://www.realmadrid.com/en",    "wiki": "Real_Madrid_CF",             "app_query": "real madrid",               "app_country": "es"},
    {"name": "FC Barcelona", "league": "La Liga",        "domain": "fcbarcelona.com",         "site": "https://www.fcbarcelona.com/en/", "wiki": "FC_Barcelona",               "app_query": "fc barcelona",              "app_country": "es"},
    {"name": "Atletico Madrid","league": "La Liga",      "domain": "atleticodemadrid.com",    "site": "https://en.atleticodemadrid.com/", "wiki": "Atlético_Madrid",            "app_query": "atletico de madrid",        "app_country": "es"},
    {"name": "Sevilla FC",   "league": "La Liga",        "domain": "sevillafc.es",            "site": "https://www.sevillafc.es/en",      "wiki": "Sevilla_FC",                 "app_query": "sevilla fc",                "app_country": "es"},
    {"name": "Real Betis",   "league": "La Liga",        "domain": "realbetisbalompie.es",    "site": "https://www.realbetisbalompie.es/en", "wiki": "Real_Betis",              "app_query": "real betis",                "app_country": "es"},
    # Bundesliga
    {"name": "Bayern Munich","league": "Bundesliga",     "domain": "fcbayern.com",            "site": "https://fcbayern.com/en",          "wiki": "FC_Bayern_Munich",           "app_query": "fc bayern",                 "app_country": "de"},
    {"name": "Borussia Dortmund","league": "Bundesliga", "domain": "bvb.de",                  "site": "https://www.bvb.de/eng",           "wiki": "Borussia_Dortmund",          "app_query": "bvb",                       "app_country": "de"},
    {"name": "RB Leipzig",   "league": "Bundesliga",     "domain": "rbleipzig.com",           "site": "https://rbleipzig.com/en/",        "wiki": "RB_Leipzig",                 "app_query": "rb leipzig",                "app_country": "de"},
    {"name": "Bayer Leverkusen","league": "Bundesliga",  "domain": "bayer04.de",              "site": "https://www.bayer04.de/en",        "wiki": "Bayer_04_Leverkusen",        "app_query": "bayer 04",                  "app_country": "de"},
    {"name": "Eintracht Frankfurt","league": "Bundesliga","domain":"eintracht.de",            "site": "https://www.eintracht.de/en/",     "wiki": "Eintracht_Frankfurt",        "app_query": "eintracht frankfurt",       "app_country": "de"},
    # Ligue 1
    {"name": "Paris Saint-Germain","league": "Ligue 1",  "domain": "psg.fr",                  "site": "https://en.psg.fr/",               "wiki": "Paris_Saint-Germain_F.C.",   "app_query": "psg official",              "app_country": "fr"},
    {"name": "Olympique de Marseille","league":"Ligue 1","domain": "om.fr",                   "site": "https://www.om.fr/en",             "wiki": "Olympique_de_Marseille",     "app_query": "olympique marseille",       "app_country": "fr"},
    {"name": "Olympique Lyonnais","league": "Ligue 1",   "domain": "ol.fr",                   "site": "https://www.ol.fr/en",             "wiki": "Olympique_Lyonnais",         "app_query": "ol lyon",                   "app_country": "fr"},
    {"name": "AS Monaco",    "league": "Ligue 1",        "domain": "asmonaco.com",            "site": "https://www.asmonaco.com/en/",     "wiki": "AS_Monaco_FC",               "app_query": "as monaco",                 "app_country": "fr"},
    {"name": "Lille OSC",    "league": "Ligue 1",        "domain": "losc.fr",                 "site": "https://www.losc.fr/en/",          "wiki": "LOSC_Lille",                 "app_query": "losc lille",                "app_country": "fr"},
]


# ── Source 1: Wayback CDX (first-seen year) ───────────────────────
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
        ts = rows[1][1]  # YYYYMMDDhhmmss
        return int(ts[:4]) if ts and len(ts) >= 4 else None
    except Exception:
        return None


# ── Source 2: HTTP response headers ───────────────────────────────
def http_headers(url: str) -> dict:
    try:
        # Use GET + stream so akamai doesn't drop our HEAD
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True, stream=True)
        h = {k.lower(): v for k, v in r.headers.items()}
        r.close()
        cdn = ""
        if "akamai" in h.get("server", "").lower() or any("akamai" in k for k in h):
            cdn = "Akamai"
        elif "cloudflare" in h.get("server", "").lower() or "cf-ray" in h:
            cdn = "Cloudflare"
        elif "cloudfront" in h.get("via", "").lower():
            cdn = "CloudFront"
        elif "fastly" in h.get("server", "").lower() or "fastly" in h.get("via", "").lower():
            cdn = "Fastly"
        elif "vercel" in h.get("server", "").lower() or "x-vercel-id" in h:
            cdn = "Vercel"
        elif "netlify" in h.get("server", "").lower():
            cdn = "Netlify"
        sec_headers = sum(1 for k in (
            "strict-transport-security", "content-security-policy",
            "x-frame-options", "x-content-type-options",
            "referrer-policy", "permissions-policy",
        ) if k in h)
        return {
            "cdn": cdn or "Unknown",
            "security_headers_present": sec_headers,
            "security_headers_max": 6,
            "hsts": "strict-transport-security" in h,
            "final_url": r.url,
        }
    except Exception:
        return {"cdn": "?", "security_headers_present": None, "security_headers_max": 6}


# ── Source 3: Sitemap (page count + locales) ──────────────────────
def parse_sitemap(domain: str) -> dict:
    """Try a few common sitemap locations; expand sitemap-indexes one level."""
    import re

    candidates = [
        f"https://www.{domain}/sitemap.xml",
        f"https://{domain}/sitemap.xml",
        f"https://www.{domain}/robots.txt",  # last resort: parse Sitemap: lines
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

    total_pages = 0
    sub_sitemaps = 0
    locale_paths: set[str] = set()

    def fetch_sm(sm_url: str) -> tuple[int, list[str]]:
        try:
            r = requests.get(sm_url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                return 0, []
            txt = r.text
            locs = re.findall(r"<loc>([^<]+)</loc>", txt)
            return len(locs), locs
        except Exception:
            return 0, []

    for sm in sitemap_urls:
        n, locs = fetch_sm(sm)
        # Heuristic: if the doc looks like a sitemap-index, recurse one level
        if locs and all(loc.endswith(".xml") for loc in locs[:3]):
            sub_sitemaps += len(locs)
            for sub in locs[:30]:  # safety cap
                m, sublocs = fetch_sm(sub)
                total_pages += m
                for loc in sublocs:
                    # crude locale guess from /xx/ path segment
                    m2 = re.search(rf"//[^/]+/([a-z]{{2}})(?:/|$)", loc)
                    if m2:
                        locale_paths.add(m2.group(1))
        else:
            total_pages += n
            for loc in locs:
                m2 = re.search(rf"//[^/]+/([a-z]{{2}})(?:/|$)", loc)
                if m2:
                    locale_paths.add(m2.group(1))

    return {
        "pages": total_pages or None,
        "locales": len(locale_paths) or None,
        "locale_list": sorted(locale_paths),
        "sub_sitemaps": sub_sitemaps or None,
    }


# ── Source 4: Wikipedia (langs + size + pageviews) ────────────────
def wikipedia(title: str) -> dict:
    out = {"langs": None, "article_kb": None, "pageviews_12mo": None}
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "langlinks|info",
                    "titles": title, "lllimit": 500, "format": "json", "redirects": 1},
            headers=HEADERS, timeout=20,
        )
        d = r.json()
        pages = list((d.get("query") or {}).get("pages", {}).values())
        if pages and "missing" not in pages[0]:
            p = pages[0]
            out["langs"] = len(p.get("langlinks", [])) + 1  # +1 for English
            out["article_kb"] = round((p.get("length") or 0) / 1024)
    except Exception:
        pass
    try:
        from datetime import datetime as _dt, timedelta
        end = _dt.now(timezone.utc).replace(day=1)
        start = end - timedelta(days=370)
        r = requests.get(
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/all-agents/{title}/monthly/"
            f"{start.strftime('%Y%m%d')}00/{end.strftime('%Y%m%d')}00",
            headers=HEADERS, timeout=20,
        )
        d = r.json()
        items = d.get("items", []) if isinstance(d, dict) else []
        out["pageviews_12mo"] = sum(i.get("views", 0) for i in items)
    except Exception:
        pass
    return out


# ── Source 5: iTunes Search / Lookup (iOS app) ────────────────────
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
        # Pick the first result whose artistName looks like an official club
        # publisher (heuristic: name contains the search term token).
        token = query.split()[0].lower()
        chosen = None
        for app in results:
            name = (app.get("trackName") or "").lower()
            artist = (app.get("artistName") or "").lower()
            if token in name and ("fc" in artist or "calcio" in artist
                                   or "official" in artist or "club" in artist
                                   or "spa" in artist or "ag" in artist
                                   or "fútbol" in artist or "futbol" in artist
                                   or query.split()[0].lower() in artist):
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


# ── Source 6: CrUX (real-user web vitals) ─────────────────────────
def crux(origin: str, form_factor: str | None = None) -> dict:
    if not PSI_KEY:
        return {}
    body = {"origin": origin}
    if form_factor:
        body["formFactor"] = form_factor
    try:
        r = requests.post(
            "https://chromeuxreport.googleapis.com/v1/records:queryRecord",
            params={"key": PSI_KEY},
            json=body, timeout=30, headers={"Content-Type": "application/json"},
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
            cls_v = cls_v / 100  # CrUX returns CLS×100
        return {
            "lcp_p75": p75("largest_contentful_paint"),
            "fcp_p75": p75("first_contentful_paint"),
            "inp_p75": p75("interaction_to_next_paint"),
            "cls_p75": cls_v,
            "ttfb_p75": p75("experimental_time_to_first_byte"),
        }
    except Exception as e:
        return {"error": str(e)[:80]}


# ── Source 7: PSI (Lighthouse a11y/BP/SEO only) ───────────────────
def psi(url: str) -> dict:
    if not PSI_KEY:
        return {}
    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile",
                    "category": ["accessibility", "best-practices", "seo"],
                    "key": PSI_KEY},
            timeout=90,
        )
        d = r.json()
        if "error" in d:
            return {"error": d["error"].get("message", "")[:80]}
        cats = d.get("lighthouseResult", {}).get("categories", {})

        def pct(k):
            s = cats.get(k, {}).get("score")
            return round(s * 100) if s is not None else None

        return {
            "a11y": pct("accessibility"),
            "best_practices": pct("best-practices"),
            "seo": pct("seo"),
        }
    except Exception as e:
        return {"error": str(e)[:80]}


# ── Orchestrator ──────────────────────────────────────────────────
def collect_one(club: dict) -> dict:
    print(f"  → {club['name']:<28} ({club['domain']})", flush=True)
    origin = f"https://www.{club['domain']}"
    return {
        **club,
        "domain_first_year":   wayback_first_year(club["domain"]),
        "http":                http_headers(club["site"]),
        "sitemap":             parse_sitemap(club["domain"]),
        "wikipedia":           wikipedia(club["wiki"]),
        "ios_app":             ios_app(club["app_query"], club["app_country"]),
        "crux_phone":          crux(origin, "PHONE"),
        "crux_desktop":        crux(origin, "DESKTOP"),
        "crux_combined":       crux(origin),
        "psi":                 psi(club["site"]),
    }


def main() -> int:
    if not PSI_KEY:
        print("WARN: GOOGLE_PSI_API_KEY not set — CrUX + PSI columns will be empty",
              file=sys.stderr)

    print(f"Collecting digital footprint for {len(CLUBS)} clubs…", flush=True)
    t0 = time.time()
    out = []
    # Run with modest concurrency — Wikipedia + iTunes are happy in parallel,
    # the slow ones are PSI (≈40s each). 6 workers keeps total under ~5 min.
    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(collect_one, CLUBS):
            out.append(r)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "clubs": out,
    }

    out_path = Path(__file__).resolve().parent.parent / "data" / "digital_footprint.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    dt = time.time() - t0
    print(f"\n✓ Wrote {out_path} — {len(out)} clubs in {dt:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
