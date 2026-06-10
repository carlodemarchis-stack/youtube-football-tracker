"""Full-catalog backfill of videos.has_paid_promotion.

Going forward, src.youtube_api.get_video_details always fetches the
paidProductPlacementDetails part and src.database.upsert_videos stores
the flag — so every new read + every nightly re-stat populates it.
This script is the ONE-TIME sweep over videos already in the DB that
haven't been re-read since migration_v23.

Strategy:
  - Keyset pagination on videos.id (uuid) so we never hit the large-
    offset statement-timeout that plain .range() does on this table.
  - For each 1000-row page, query the YouTube API in 50-id batches for
    paidProductPlacementDetails (FREE part — 1 unit per call, ~20
    calls per page).
  - Only UPDATE rows whose flag flips to True (column defaults False),
    so the vast majority of rows need no write.

Idempotent + resumable: re-running re-checks everything; already-True
rows stay True (we only ever set True). Safe to interrupt and restart.

Quota: ~total/50 units. ~85k videos ≈ ~1,700 units — well within a
day's 10k budget. Uses the interactive/heavy key.

Usage:
  python3 scripts/backfill_paid_promotion.py
  python3 scripts/backfill_paid_promotion.py --limit 5000   # cap for testing
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database


def log(msg: str) -> None:
    print(msg, flush=True)


def _check_batch(yt_key: str, yt_ids: list[str]) -> set[str]:
    """Return the subset of yt_ids that have hasPaidProductPlacement=True."""
    out: set[str] = set()
    for i in range(0, len(yt_ids), 50):
        b = [x for x in yt_ids[i:i + 50] if x]
        if not b:
            continue
        url = ("https://www.googleapis.com/youtube/v3/videos"
               "?part=paidProductPlacementDetails&id="
               + ",".join(b) + f"&key={yt_key}")
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                d = json.load(r)
        except Exception as e:
            log(f"    batch error (skipped): {e}")
            continue
        for it in d.get("items", []):
            if (it.get("paidProductPlacementDetails") or {}) \
                    .get("hasPaidProductPlacement"):
                out.add(it["id"])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap total videos processed (for testing).")
    args = ap.parse_args()

    yt_key = (os.environ.get("YOUTUBE_API_KEY_INTERACTIVE", "").strip()
              or os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip()
              or os.environ.get("YOUTUBE_API_KEY", "").strip())
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_(SERVICE_)KEY")
        return 1

    db = Database(sb_url, sb_key)
    start = time.time()

    PAGE = 1000
    last_id = "00000000-0000-0000-0000-000000000000"
    total_seen = 0
    total_flagged = 0
    page_no = 0

    while True:
        # Keyset page: id > last_id, ordered by id ascending.
        rows = (db.client.table("videos")
                .select("id,youtube_video_id")
                .gt("id", last_id)
                .order("id", desc=False)
                .limit(PAGE)
                .execute().data) or []
        if not rows:
            break
        page_no += 1
        id_map = {r["youtube_video_id"]: r["id"]
                  for r in rows if r.get("youtube_video_id")}
        yt_ids = list(id_map.keys())

        flagged = _check_batch(yt_key, yt_ids)
        for yid in flagged:
            db.client.table("videos").update(
                {"has_paid_promotion": True}).eq("id", id_map[yid]).execute()

        total_seen += len(rows)
        total_flagged += len(flagged)
        last_id = rows[-1]["id"]
        log(f"  page {page_no:>3} — {total_seen:>6,} videos swept, "
            f"{total_flagged:>4} sponsored (+{len(flagged)} this page)")

        if args.limit and total_seen >= args.limit:
            log(f"  reached --limit {args.limit}, stopping early")
            break

    elapsed = time.time() - start
    log(f"\nDone in {elapsed:.0f}s — {total_seen:,} videos swept, "
        f"{total_flagged} flagged has_paid_promotion=True")
    return 0


if __name__ == "__main__":
    sys.exit(main())
