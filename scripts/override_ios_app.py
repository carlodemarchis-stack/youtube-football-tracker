"""Manual iOS app override for a single channel.

Usage:
    python3 scripts/override_ios_app.py "Club Name" <itunes-url-or-id>
    python3 scripts/override_ios_app.py "Club Name" --clear

The `--clear` form marks the channel as having no official iOS app
(useful when no real app exists and we want the table to render "—").

Edits data/digital_footprint.json in place; commit + push separately.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

UA = {"User-Agent": "ytft-lab/1.0"}
SNAP = Path(__file__).resolve().parent.parent / "data" / "digital_footprint.json"


def extract_id(arg: str) -> int | None:
    """Accept either a bare numeric ID or any App Store URL containing /id<n>."""
    if arg.isdigit():
        return int(arg)
    m = re.search(r"/id(\d+)", arg)
    return int(m.group(1)) if m else None


_C2APP = {"it":"it","gb":"gb","uk":"gb","england":"gb","de":"de",
          "es":"es","fr":"fr","us":"us"}


def fetch(app_id: int, country: str = "us") -> dict | None:
    """iTunes Lookup. Country affects which store's rating count is
    returned; pass the channel's country for the most accurate
    figures."""
    cc = _C2APP.get((country or "").lower(), country or "us")
    r = requests.get("https://itunes.apple.com/lookup",
                     params={"id": app_id, "country": cc, "entity": "software"},
                     headers=UA, timeout=20)
    results = (r.json() or {}).get("results") or []
    return results[0] if results else None


def to_payload(a: dict) -> dict:
    return {
        "present": True,
        "name": a.get("trackName"),
        "developer": a.get("artistName"),
        "version": a.get("version"),
        "first_release": (a.get("releaseDate") or "")[:10],
        "last_update": (a.get("currentVersionReleaseDate") or "")[:10],
        "rating": a.get("averageUserRating"),
        "rating_count": a.get("userRatingCount"),
        "size_mb": round(int(a.get("fileSizeBytes", 0)) / 1024 / 1024, 1),
        "languages": len(a.get("languageCodesISO2A") or []),
        "language_list": a.get("languageCodesISO2A") or [],
        "min_os": a.get("minimumOsVersion"),
        "price": a.get("formattedPrice"),
        "store_url": a.get("trackViewUrl"),
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 1

    club, arg = sys.argv[1], sys.argv[2]
    d = json.load(open(SNAP))
    target = next((c for c in d["channels"] if c["name"] == club), None)
    if not target:
        # Loose match — case-insensitive substring
        target = next((c for c in d["channels"]
                       if club.lower() in c["name"].lower()), None)
    if not target:
        print(f"ERROR: club '{club}' not found in snapshot", file=sys.stderr)
        print("Available names:", file=sys.stderr)
        for c in d["channels"]:
            print(f"  {c['name']}", file=sys.stderr)
        return 1

    if arg == "--clear":
        target["ios_app"] = {"present": False}
        SNAP.write_text(json.dumps(d, indent=2, ensure_ascii=False))
        print(f"✓ Cleared iOS match for {target['name']}")
        return 0

    app_id = extract_id(arg)
    if not app_id:
        print(f"ERROR: could not extract iTunes ID from '{arg}'", file=sys.stderr)
        return 1
    # Use the channel's country so rating_count reflects its home store.
    a = fetch(app_id, target.get("country") or "us")
    if not a:
        print(f"ERROR: iTunes lookup returned no result for id={app_id}",
              file=sys.stderr)
        return 1
    new = to_payload(a)
    target["ios_app"] = new
    SNAP.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    print(f"✓ Overrode {target['name']}")
    print(f"  Name      : {new['name']}")
    print(f"  Developer : {new['developer']}")
    print(f"  Updated   : {new['last_update']}")
    print(f"  Rating    : {new['rating']} ({new['rating_count']} ratings)")
    print(f"  URL       : {new['store_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
