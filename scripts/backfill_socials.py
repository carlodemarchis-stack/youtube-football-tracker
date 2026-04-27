#!/usr/bin/env python3
"""Backfill `channels.socials` (JSONB) from Wikidata.

For each club channel: search Wikidata by name, pick the first hit with
a football-related description, then fetch every social platform claim
we know about and write a {platform_key: url} dict.

Defaults to Serie A clubs (country='IT', entity_type='Club').
Override with COUNTRY=GB or LEAGUE=all to widen.

Idempotent — skips channels that already have a non-empty `socials`
unless FORCE=1 is set.
"""
from __future__ import annotations

import json
import os
import sys
import time
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database

FORCE = os.environ.get("FORCE") == "1"
COUNTRY = os.environ.get("COUNTRY", "IT")          # default Serie A
LEAGUE_ALL = os.environ.get("LEAGUE") == "all"     # bypass country filter

_WIKI = requests.Session()
_WIKI.headers.update({
    "User-Agent": "YTFT-SocialsBackfill/1.0 (https://github.com/carlodemarchis-stack/youtube-football-tracker)",
    "Accept": "application/json",
})

# Wikidata property → (display key, URL template). `{}` is the formatter slot.
# Properties confirmed on Wikidata as of 2026-04. Skipped any uncertain ones.
PROPS: dict[str, tuple[str, str]] = {
    "P856":   ("website",   "{}"),                        # already a full URL
    "P2002":  ("x",         "https://x.com/{}"),
    "P2003":  ("instagram", "https://instagram.com/{}"),
    "P2013":  ("facebook",  "https://facebook.com/{}"),
    "P7085":  ("tiktok",    "https://www.tiktok.com/@{}"),
    "P11245": ("threads",   "https://www.threads.net/@{}"),
    "P3789":  ("telegram",  "https://t.me/{}"),
    "P5263":  ("snapchat",  "https://snapchat.com/add/{}"),
    "P6573":  ("twitch",    "https://twitch.tv/{}"),
    "P3185":  ("vk",        "https://vk.com/{}"),
    "P8057":  ("weibo",     "https://weibo.com/u/{}"),
    "P4033":  ("mastodon",  "https://{}"),                # full address
}

FOOTBALL_KEYWORDS = ("football", "soccer", "calcio", "fútbol")


def log(msg: str) -> None:
    print(msg, flush=True)


def search_qid(name: str) -> str | None:
    """Return the best Wikidata QID for a club named `name`."""
    try:
        r = _WIKI.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbsearchentities", "search": name,
                    "language": "en", "format": "json", "type": "item",
                    "limit": 10},
            timeout=15,
        )
        r.raise_for_status()
        hits = r.json().get("search", []) or []
    except Exception as e:
        log(f"  search error: {e}")
        return None
    # Prefer hits whose description mentions football
    for h in hits:
        desc = (h.get("description") or "").lower()
        if any(k in desc for k in FOOTBALL_KEYWORDS):
            return h.get("id")
    return hits[0]["id"] if hits else None


def fetch_socials(qid: str) -> dict[str, str]:
    """Return {platform_key: url} for whichever PROPS the entity has."""
    try:
        r = _WIKI.get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetentities", "ids": qid,
                    "props": "claims", "format": "json"},
            timeout=20,
        )
        r.raise_for_status()
        claims = r.json()["entities"][qid]["claims"]
    except Exception as e:
        log(f"  claims fetch error for {qid}: {e}")
        return {}

    out: dict[str, str] = {}
    for prop, (key, tpl) in PROPS.items():
        rows = claims.get(prop) or []
        if not rows:
            continue
        # Pick the first preferred-rank or normal claim
        rows_sorted = sorted(rows, key=lambda r: 0 if r.get("rank") == "preferred" else 1)
        snak = rows_sorted[0].get("mainsnak", {})
        dv = (snak.get("datavalue") or {}).get("value")
        if not dv:
            continue
        # P856 returns a URL string; identifiers return strings; some return dicts
        if isinstance(dv, dict):
            val = dv.get("text") or dv.get("id") or ""
        else:
            val = str(dv)
        if not val:
            continue
        try:
            out[key] = tpl.format(val) if "{}" in tpl else val
        except Exception:
            out[key] = val
    return out


def main() -> int:
    sb_url = os.environ["SUPABASE_URL"]
    sb_key = os.environ["SUPABASE_KEY"]
    db = Database(sb_url, sb_key)

    chans = db.get_all_channels()
    # Limit to club channels (entity_type='Club') and optionally a country
    targets = [c for c in chans if c.get("entity_type") == "Club"]
    if not LEAGUE_ALL:
        targets = [c for c in targets if (c.get("country") or "").upper() == COUNTRY]
    log(f"{len(targets)} club(s) to process (country filter: "
        f"{'OFF' if LEAGUE_ALL else COUNTRY})")

    ok = skipped = failed = 0
    for ch in targets:
        name = ch.get("name") or "?"
        existing = ch.get("socials") or {}
        if existing and not FORCE:
            log(f"  ✔  {name}: already populated ({len(existing)} platforms)")
            skipped += 1
            continue

        qid = search_qid(name)
        if not qid:
            log(f"  ✗  {name}: no Wikidata match")
            failed += 1
            continue

        socials = fetch_socials(qid)
        if not socials:
            log(f"  ·  {name} ({qid}): no social claims found")
            # Still write empty so we don't keep re-querying
            try:
                db.client.table("channels").update({"socials": {}}).eq("id", ch["id"]).execute()
            except Exception:
                pass
            failed += 1
            continue

        try:
            db.client.table("channels").update({"socials": socials}).eq("id", ch["id"]).execute()
            log(f"  +  {name} ({qid}): {len(socials)} platform(s) — "
                + ", ".join(sorted(socials.keys())))
            ok += 1
        except Exception as e:
            log(f"  ✗  {name}: DB update failed — {e}")
            failed += 1
        time.sleep(0.25)  # be polite to Wikidata

    log(f"Done — updated={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
