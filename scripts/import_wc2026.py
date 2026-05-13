"""Import the FIFA World Cup 2026 federation channels from CSV.

For each row:
  - If the youtube_channel_id is already in the channels table,
    only update its `competitions.wc2026` block (don't touch the
    name / handle / country we already have, since those are
    likely the editorial-correct values).
  - If new, add the channel with entity_type="Federation" and
    competitions = {"wc2026": {...}}.

The 4 already-tracked federations (England, France, Germany, Spain)
will get the wc2026 tag added to their existing record; the other
44 are inserted fresh. Run AFTER applying migration_v21.sql.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database


CSV_PATH = (Path(__file__).resolve().parent.parent / "data"
            / "wc2026_federation_youtube - wc2026_federation_youtube.csv.csv")


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY not set",
              file=sys.stderr)
        return 1

    db = Database(url, key)
    chs = db.get_all_channels()
    by_yt = {c.get("youtube_channel_id"): c for c in chs
             if c.get("youtube_channel_id")}

    rows = list(csv.DictReader(open(CSV_PATH)))
    print(f"Importing {len(rows)} WC 2026 federations from {CSV_PATH.name}\n")

    updated = added = 0
    for r in rows:
        yt_id = r["youtube_channel_id"].strip()
        team = r["team"].strip()
        wc_block = {
            "team":          team,
            "group":         r.get("group", "").strip() or None,
            "confederation": r.get("confederation", "").strip() or None,
            "youtube_url":   r.get("youtube_url", "").strip() or None,
            "notes":         r.get("notes", "").strip() or None,
        }
        # Drop None values to keep the JSON tight
        wc_block = {k: v for k, v in wc_block.items() if v is not None}

        existing = by_yt.get(yt_id)
        if existing:
            comps = dict(existing.get("competitions") or {})
            comps["wc2026"] = wc_block
            db.update_channel(existing["id"], {"competitions": comps})
            updated += 1
            print(f"  ↺ {team:<20}  → tag added to '{existing['name']}'")
        else:
            payload = {
                "youtube_channel_id": yt_id,
                "name":               r.get("channel_name", "").strip() or team,
                "handle":             r.get("youtube_url", "").rstrip("/").split("/")[-1] if r.get("youtube_url") else None,
                "sport":              "Football",
                "entity_type":        "Federation",
                "country":            team,  # store the team/country name
                "is_active":          True,
                "competitions":       {"wc2026": wc_block},
            }
            try:
                db.add_channel(payload)
                added += 1
                print(f"  + {team:<20}  inserted as Federation")
            except Exception as e:
                print(f"  ✗ {team:<20}  ERROR: {e}")

    print(f"\n✓ Updated existing: {updated}  ·  Inserted new: {added}")

    # Verification
    chs2 = db.get_all_channels()
    wc_chs = [c for c in chs2 if (c.get("competitions") or {}).get("wc2026")]
    print(f"\nTotal channels with competitions.wc2026: {len(wc_chs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
