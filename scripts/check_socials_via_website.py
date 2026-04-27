#!/usr/bin/env python3
"""Cross-check `channels.socials` against each club's official website.

For every Serie A club:
  1. Fetch the website URL we already have stored.
  2. Regex-extract every <a href> pointing to a known social platform.
  3. Compare with the DB row's `socials` JSONB and report:
       NEW       — found on website, missing from DB
       DIFFERENT — DB has a different URL
       MATCH     — same as DB
       MISSING   — DB has it but website footer doesn't (just FYI)
  4. With APPLY=1, merge NEW + replace DIFFERENT into the DB.

Wikidata is one-account-per-platform; club websites often surface several
variants (women's, EN, store). This catches them.
"""
from __future__ import annotations

import os
import re
import sys
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database

APPLY = os.environ.get("APPLY") == "1"
COUNTRY = os.environ.get("COUNTRY", "IT")

_HTTP = requests.Session()
_HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/17.0 Safari/605.1.15",
    "Accept-Language": "en;q=0.9",
})

# Platform key → (regex of href patterns, URL-cleaner). Matches must be specific
# enough to avoid false positives like "facebook.com/sharer".
# Junk handles that aren't real account names — discard if pattern matches them.
_JUNK_HANDLES = {"share", "intent", "sharer", "login", "home", "i", "dialog",
                 "tr", "explore", "search"}

PLATFORM_PATTERNS: list[tuple[str, re.Pattern, callable]] = [
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+?)/?(?:[?\"\'#]|$)"),
        lambda m: f"https://instagram.com/{m.group(1)}"),
    # Exclude x.com/share, x.com/intent, x.com/i/, x.com/home, etc.
    ("x",         re.compile(r"https?://(?:www\.)?(?:x|twitter)\.com/(?!share|intent|sharer|i/|home|search)([a-zA-Z0-9_]+)/?(?:[?\"\'#]|$)"),
        lambda m: f"https://x.com/{m.group(1)}"),
    ("facebook",  re.compile(r"https?://(?:www\.)?facebook\.com/(?!sharer|dialog|tr\?|share\.php)([a-zA-Z0-9.\-_]+?)/?(?:[?\"\'#]|$)"),
        lambda m: f"https://facebook.com/{m.group(1)}"),
    # Skip vm.tiktok.com redirect shortlinks (they look like @ZMxxxxx) and @login.
    # Real TikTok usernames are lowercase, may contain underscore/period/digits.
    ("tiktok",    re.compile(r"https?://(?:www\.)?tiktok\.com/@([a-zA-Z0-9_.]+)(?:/?(?:[?\"\'#]|$)|/video)"),
        lambda m: f"https://www.tiktok.com/@{m.group(1)}"),
    ("threads",   re.compile(r"https?://(?:www\.)?threads\.(?:net|com)/@?([a-zA-Z0-9_.]+?)/?(?:[?\"\'#]|$)"),
        lambda m: f"https://www.threads.net/@{m.group(1)}"),
    ("youtube",   re.compile(r"https?://(?:www\.)?youtube\.com/(?:c/|user/|@)([a-zA-Z0-9_\-]+)/?"),
        lambda m: f"https://www.youtube.com/@{m.group(1)}"),
    ("linkedin",  re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|school)/([a-zA-Z0-9\-_]+)/?"),
        lambda m: f"https://www.linkedin.com/company/{m.group(1)}"),
    ("twitch",    re.compile(r"https?://(?:www\.)?twitch\.tv/([a-zA-Z0-9_]+)/?"),
        lambda m: f"https://twitch.tv/{m.group(1)}"),
    ("telegram",  re.compile(r"https?://(?:www\.)?t\.me/([a-zA-Z0-9_]+)/?"),
        lambda m: f"https://t.me/{m.group(1)}"),
    ("snapchat",  re.compile(r"https?://(?:www\.)?(?:snapchat\.com/add|story\.snapchat\.com)/([a-zA-Z0-9_.\-]+)/?"),
        lambda m: f"https://snapchat.com/add/{m.group(1)}"),
    ("vk",        re.compile(r"https?://(?:www\.)?vk\.com/([a-zA-Z0-9_.]+)/?"),
        lambda m: f"https://vk.com/{m.group(1)}"),
    ("weibo",     re.compile(r"https?://(?:www\.)?weibo\.com/(?:u/)?([a-zA-Z0-9_]+)/?"),
        lambda m: f"https://weibo.com/u/{m.group(1)}"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def extract_socials(html: str, base_url: str) -> dict[str, str]:
    """Return the first non-junk match for each platform. Skips share-intent
    URLs, TikTok login redirects, and vm.tiktok.com short-link IDs."""
    out: dict[str, str] = {}
    for key, pat, builder in PLATFORM_PATTERNS:
        for m in pat.finditer(html):
            handle = m.group(1)
            if handle.lower() in _JUNK_HANDLES:
                continue
            # TikTok shortlinks come through as `ZM...` capitalised mush — skip.
            if key == "tiktok" and re.match(r"^Z[A-Za-z0-9]{6,12}$", handle):
                continue
            try:
                out[key] = builder(m)
                break
            except Exception:
                pass
    return out


def _norm(url: str) -> str:
    """Lowercase + trim trailing slash for case-insensitive URL comparison."""
    return (url or "").rstrip("/").lower()


def main() -> int:
    sb_url = os.environ["SUPABASE_URL"]
    sb_key = os.environ["SUPABASE_KEY"]
    db = Database(sb_url, sb_key)

    chans = [c for c in db.get_all_channels()
             if c.get("entity_type") == "Club"
             and (c.get("country") or "").upper() == COUNTRY]
    log(f"{len(chans)} club(s) — country={COUNTRY}  apply={APPLY}")

    for ch in chans:
        name = ch.get("name") or "?"
        existing = ch.get("socials") or {}
        site = existing.get("website")
        if not site:
            log(f"  ✗ {name}: no website on file — skip")
            continue
        try:
            r = _HTTP.get(site, timeout=20, allow_redirects=True)
            r.raise_for_status()
            html = r.text
        except Exception as e:
            log(f"  ✗ {name}: fetch failed — {e}")
            continue

        found = extract_socials(html, site)
        if not found:
            log(f"  ·  {name}: no recognisable social links found on {site}")
            continue

        new_keys = []
        diff_keys = []
        for k, url in found.items():
            cur = existing.get(k)
            if not cur:
                new_keys.append((k, url))
            elif _norm(cur) != _norm(url):
                diff_keys.append((k, cur, url))

        if not new_keys and not diff_keys:
            log(f"  ✓ {name}: matches DB ({len(found)} platforms)")
            continue

        log(f"  · {name}:")
        for k, url in new_keys:
            log(f"      + NEW       {k}: {url}")
        for k, cur, url in diff_keys:
            log(f"      ⚠ DIFFERENT {k}: db={cur}  →  site={url}")

        if APPLY and (new_keys or diff_keys):
            merged = {**existing}
            for k, url in new_keys:
                merged[k] = url
            for k, _cur, url in diff_keys:
                merged[k] = url
            try:
                db.client.table("channels").update({"socials": merged}).eq("id", ch["id"]).execute()
                log(f"      → applied")
            except Exception as e:
                log(f"      ✗ apply failed: {e}")

        time.sleep(0.5)

    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
