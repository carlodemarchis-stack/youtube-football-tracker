#!/usr/bin/env python3
"""Backfill the `videos.language` + `videos.language_source` columns.

Strategy (priority order, see src/lang_detect.py):
  1. snippet.defaultAudioLanguage / defaultLanguage from YouTube — but we
     only get these when re-fetching from the API. To keep this offline /
     free, this backfill ONLY does steps 2-3:
       2. lingua-py title detection (with channel-country prior)
       3. channel-country fallback when (2) is ambiguous

The YouTube field gets captured going forward by hourly_rss / daily_refresh
via the new "youtube_language" key in get_video_details. New rows therefore
land with language_source='youtube' when YouTube provides it; otherwise
the next nightly invocation of this backfill (or the inline path) fills
them in with detection/prior.

Usage:
    python3 scripts/backfill_video_language.py [--limit N] [--dry-run]
                                               [--rerun-source <kind>]

Default: only fills rows where language IS NULL. Use --rerun-source to
recompute (e.g. --rerun-source prior to upgrade old country-fallback rows
to detected once new lingua heuristics are tuned).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database, _fetch_all
from src.lang_detect import detect_language, is_available


def _iter_batches(rows: list[dict], n: int) -> Iterable[list[dict]]:
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap rows updated this run (debugging / quotas).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute but don't write back to Supabase.")
    ap.add_argument("--rerun-source", default=None,
                    choices=["prior", "detected", "youtube", "all"],
                    help="Recompute rows whose current language_source matches.")
    args = ap.parse_args()

    if not is_available():
        print("ERROR: lingua-language-detector not installed. "
              "Add to requirements and `pip install lingua-language-detector`.")
        return 2

    db = Database(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    # Build channel_id → country lookup for the prior.
    chans = db.get_all_channels()
    cid_to_country = {c["id"]: (c.get("country") or "").upper() for c in chans}
    print(f"Loaded {len(chans)} channels for country prior")

    # Pull the videos that need updating.
    q = db.client.table("videos").select("id,title,channel_id,language,language_source")
    if args.rerun_source == "all":
        pass  # all rows
    elif args.rerun_source:
        q = q.eq("language_source", args.rerun_source)
    else:
        q = q.is_("language", "null")

    rows = _fetch_all(q)
    if args.limit:
        rows = rows[:args.limit]
    print(f"Fetched {len(rows)} candidate video rows")

    src_count = {"detected": 0, "prior": 0, "skipped": 0}
    updates: list[dict] = []
    t0 = time.time()
    for r in rows:
        country = cid_to_country.get(r.get("channel_id"))
        lang, source = detect_language(
            title=r.get("title"),
            channel_country=country,
            youtube_lang=None,  # not available offline
        )
        if not lang:
            src_count["skipped"] += 1
            continue
        # No-op if value didn't change (avoid pointless writes on rerun).
        if r.get("language") == lang and r.get("language_source") == source:
            src_count["skipped"] += 1
            continue
        updates.append({
            "id": r["id"],
            "language": lang,
            "language_source": source,
        })
        src_count[source] = src_count.get(source, 0) + 1

    elapsed = time.time() - t0
    print(f"Detection complete in {elapsed:.1f}s — "
          f"detected={src_count.get('detected', 0)} "
          f"prior={src_count.get('prior', 0)} "
          f"skipped={src_count['skipped']}")

    if args.dry_run:
        # Sample 10 detected rows for sanity check
        sample = [r for r in rows[:200] if r.get("title")][:10]
        for r in sample:
            country = cid_to_country.get(r.get("channel_id"))
            lang, source = detect_language(r.get("title"), country)
            print(f"  [{lang}/{source}] {(r.get('title') or '')[:80]}")
        return 0

    if not updates:
        print("Nothing to write.")
        return 0

    # Bulk upsert in batches of 500.
    written = 0
    for batch in _iter_batches(updates, 500):
        try:
            db.client.table("videos").upsert(batch, on_conflict="id").execute()
            written += len(batch)
        except Exception as e:
            print(f"batch upsert failed (size={len(batch)}): {e}")
    print(f"Wrote {written}/{len(updates)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
