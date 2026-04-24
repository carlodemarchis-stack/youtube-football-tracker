#!/usr/bin/env python3
"""Backfill `channels.dob` for every Player via Wikidata.

Strategy:
  1. Search Wikidata for the player's name.
  2. Pick the first result whose description hints at football
     (keywords: football, footballer, soccer, goalkeeper, striker, etc.).
  3. Fetch P569 (date of birth) from that entity.
  4. UPDATE channels SET dob = … WHERE id = …

Idempotent — run any time to re-sync. Skips players whose dob is already set
unless FORCE=1 is exported.
"""
from __future__ import annotations

import os
import sys
import time
import requests

_WIKI = requests.Session()
_WIKI.headers.update({
    "User-Agent": "YTFT-PlayersBackfill/1.0 (https://github.com/carlodemarchis-stack/youtube-football-tracker; contact via LinkedIn)",
    "Accept": "application/json",
})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database

FORCE = os.environ.get("FORCE") == "1"
FOOTBALL_KEYWORDS = (
    "football", "footballer", "soccer", "goalkeeper", "striker",
    "midfielder", "defender", "forward", "winger",
)

# Channel name in DB → exact name to feed to the SPARQL label match.
# Only needed when the channel title differs from the player's real name.
NAME_MAP: dict[str, str] = {
    "UR · Cristiano": "Cristiano Ronaldo",
    "Leo Messi": "Lionel Messi",
    "Neymar Jr": "Neymar",
    "Vini Jr": "Vinícius Júnior",
    "Rio Ferdinand Presents": "Rio Ferdinand",
    "Ben Foster - The Cycling GK": "Ben Foster",
    "That Peter Crouch Podcast": "Peter Crouch",
    "Peter Crouch": "Peter Crouch",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def sparql_footballer_dob(name: str) -> str | None:
    """Query Wikidata SPARQL for a person labelled `name` with occupation
    association-football-player (Q937857). Returns DOB as YYYY-MM-DD or None."""
    q = (
        'SELECT ?p ?dob WHERE { '
        f'?p rdfs:label "{name}"@en . '
        '?p wdt:P106 wd:Q937857 . '
        '?p wdt:P569 ?dob . '
        '} LIMIT 1'
    )
    try:
        r = _WIKI.get(
            "https://query.wikidata.org/sparql",
            params={"query": q, "format": "json"},
            timeout=20,
        )
        r.raise_for_status()
        bindings = r.json().get("results", {}).get("bindings", []) or []
        if not bindings:
            return None
        dob = bindings[0]["dob"]["value"]  # e.g. "1987-06-24T00:00:00Z"
        return dob[:10]
    except Exception as e:
        log(f"  sparql error for {name}: {e}")
        return None


def wikidata_search(name: str) -> str | None:
    """Best-effort name → Wikidata QID, filtered by football-ish description."""
    try:
        r = _WIKI.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbsearchentities", "search": name, "language": "en",
                "format": "json", "type": "item", "limit": 10,
            },
            timeout=15,
        )
        r.raise_for_status()
        hits = r.json().get("search", []) or []
    except Exception as e:
        log(f"  search error: {e}")
        return None

    for h in hits:
        desc = (h.get("description") or "").lower()
        if any(k in desc for k in FOOTBALL_KEYWORDS):
            return h.get("id")
    # Fall back to first result if nothing matched keywords
    return hits[0]["id"] if hits else None


def fetch_dob(qid: str) -> str | None:
    """Return YYYY-MM-DD or None."""
    try:
        r = _WIKI.get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "wbgetentities", "ids": qid,
                "props": "claims", "format": "json",
            },
            timeout=15,
        )
        r.raise_for_status()
        claims = r.json()["entities"][qid]["claims"]
        p569 = claims.get("P569") or []
        if not p569:
            return None
        time_str = p569[0]["mainsnak"]["datavalue"]["value"]["time"]
        # Wikidata time format: "+1985-02-05T00:00:00Z"
        return time_str[1:11]
    except Exception as e:
        log(f"  dob fetch error for {qid}: {e}")
        return None


def main() -> int:
    sb_url = os.environ["SUPABASE_URL"]
    sb_key = os.environ["SUPABASE_KEY"]
    db = Database(sb_url, sb_key)

    players = [c for c in db.get_all_channels() if c.get("entity_type") == "Player"]
    log(f"Found {len(players)} players")

    ok = skipped = failed = 0
    for p in players:
        name = p.get("name") or "?"
        if p.get("dob") and not FORCE:
            log(f"  ✔  {name}: already set ({p['dob']})")
            skipped += 1
            continue

        search_name = NAME_MAP.get(name, name)
        dob = sparql_footballer_dob(search_name)
        if not dob:
            # Fallback: broader search, then fetch_dob on first footballer-ish hit
            qid = wikidata_search(search_name)
            if qid:
                dob = fetch_dob(qid)
        if not dob:
            log(f"  ✗  {name}: no DOB found (searched as '{search_name}')")
            failed += 1
            continue

        try:
            db.client.table("channels").update({"dob": dob}).eq("id", p["id"]).execute()
            log(f"  +  {name}: {dob}")
            ok += 1
        except Exception as e:
            log(f"  ✗  {name}: DB update failed — {e}")
            failed += 1
        time.sleep(0.2)  # be polite to Wikidata

    log(f"Done — updated={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
