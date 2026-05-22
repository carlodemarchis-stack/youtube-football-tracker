"""Backfill videos.description for the top-5 cohort, this season (Step 1).

First step of the description-attributes project: pull each video's raw
description from YouTube (videos.list, part=snippet — 1 quota unit per
50 ids) and store it in the videos.description text column. The
videos.description_meta jsonb column is intentionally left NULL here —
deriving attributes (clean_body, commercialization profile, …) from the
stored raw is a later phase that needs no further API calls.

Scope: Club + League channels (top-5), published_at >= season start.
~65k videos ≈ ~1,300 quota units ≈ 10–15 min.

Writes are minimal: an upsert carrying only {youtube_video_id,
description}. Existing rows hit the conflict path so only `description`
changes — every other column is preserved (same pattern as
Database.upsert_videos). Deleted/private videos just don't come back
and stay NULL.

Runs on the INTERACTIVE key so it never competes with the daily/hourly
pipeline quota.

Usage:
    python scripts/backfill_descriptions.py --dry-run   # sample, no writes
    python scripts/backfill_descriptions.py             # live
    python scripts/backfill_descriptions.py --since 2025-08-01
    python scripts/backfill_descriptions.py --limit 200 # cap (testing)

Exit codes:
    0 — success
    2 — env / config error
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import DEFAULT_SEASON_START


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Fetch and print a sample of descriptions; no writes.")
    p.add_argument("--since", default=DEFAULT_SEASON_START,
                   help=f"Earliest published_at to include "
                        f"(default season start: {DEFAULT_SEASON_START}).")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap the number of videos processed (for testing).")
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY")
              or os.environ.get("SUPABASE_KEY"))
    yt_key = (os.environ.get("YOUTUBE_API_KEY_INTERACTIVE")
              or os.environ.get("YOUTUBE_API_KEY")
              or os.environ.get("YOUTUBE_API_KEY_DAILY")
              or os.environ.get("YOUTUBE_API_KEY_HEAVY"))
    if not (sb_url and sb_key and yt_key):
        print("FATAL: need SUPABASE_URL + SUPABASE_(SERVICE_)KEY + a "
              "YOUTUBE_API_KEY in env")
        return 2

    db = Database(sb_url, sb_key)
    yt = YouTubeClient(yt_key)

    # Top-5 cohort = Club + League HQ entity types.
    chans = (db.client.table("channels")
             .select("id,entity_type").execute().data or [])
    cohort_ids = [c["id"] for c in chans
                  if c.get("entity_type") in ("Club", "League")]
    print(f"Cohort: {len(cohort_ids)} top-5 channels")
    print(f"Window: published_at >= {args.since}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}"
          + (f"  limit={args.limit}" if args.limit else ""))

    # Collect all video ids for the cohort + window (paginated per chunk).
    PAGE = 1000
    vid_ids: list[str] = []
    for cs in range(0, len(cohort_ids), 50):
        chunk = cohort_ids[cs:cs + 50]
        off = 0
        while True:
            rs = (db.client.table("videos")
                  .select("youtube_video_id")
                  .in_("channel_id", chunk)
                  .gte("published_at", args.since)
                  .range(off, off + PAGE - 1)
                  .execute().data or [])
            vid_ids.extend(r["youtube_video_id"] for r in rs)
            if len(rs) < PAGE:
                break
            off += PAGE
    # Dedupe, keep order.
    seen: set[str] = set()
    vid_ids = [v for v in vid_ids if not (v in seen or seen.add(v))]
    if args.limit:
        vid_ids = vid_ids[:args.limit]
    total = len(vid_ids)
    print(f"Videos to process: {total:,}  "
          f"(~{-(-total // 50):,} quota units)\n")
    if total == 0:
        print("Nothing to do.")
        return 0

    # ── DRY-RUN: fetch first batch, show what we'd store ──────────────
    if args.dry_run:
        sample = vid_ids[:50]
        details = yt.get_video_details(sample, retries=0)
        print(f"Sample of {len(details)} descriptions (first 90 chars):\n")
        for d in details[:20]:
            desc = (d.get("description") or "").replace("\n", " ")
            print(f"  {d['youtube_video_id']}  len={len(d.get('description') or ''):>5}"
                  f"  {desc[:90]}")
        empties = sum(1 for d in details if not (d.get("description") or "").strip())
        print(f"\n  {empties}/{len(details)} empty in sample. "
              f"(dry-run: no writes, description_meta untouched)")
        return 0

    # ── LIVE: fetch in 50s, upsert description only in chunks ─────────
    started = time.time()
    fetched = 0
    written = 0
    empties = 0
    pending: list[dict] = []

    def _flush() -> None:
        nonlocal written, pending
        if not pending:
            return
        for i in range(0, len(pending), 500):
            batch = pending[i:i + 500]
            db.client.table("videos").upsert(
                batch, on_conflict="youtube_video_id"
            ).execute()
        written += len(pending)
        pending = []

    for b in range(0, total, 50):
        ids = vid_ids[b:b + 50]
        details = yt.get_video_details(ids, retries=0)
        fetched += len(details)
        for d in details:
            desc = d.get("description") or ""
            if not desc.strip():
                empties += 1
            pending.append({"youtube_video_id": d["youtube_video_id"],
                            "description": desc})
        if len(pending) >= 500:
            _flush()
        if (b // 50) % 20 == 0:
            print(f"  {min(b + 50, total):>6,}/{total:,}  "
                  f"fetched={fetched:,} written={written:,} "
                  f"empty={empties:,}")
    _flush()

    elapsed = time.time() - started
    print(f"\nDone. fetched={fetched:,} written={written:,} "
          f"empty={empties:,} (missing/deleted={total - fetched:,}) "
          f"in {elapsed/60:.1f} min")
    print("description_meta left NULL — run the phase-2 derive job next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
