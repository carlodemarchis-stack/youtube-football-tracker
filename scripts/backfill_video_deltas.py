#!/usr/bin/env python3
"""Backfill video_daily_deltas from existing video_snapshots.

Run once after creating the video_daily_deltas table (db/video_deltas_schema.sql).
For each distinct captured_date in video_snapshots, compute the delta against
the prior snapshot for each video and upsert a row.

Safe to re-run — upsert on (video_id, captured_date) is idempotent.

Usage:
    python scripts/backfill_video_deltas.py               # all dates
    python scripts/backfill_video_deltas.py 2025-08-01   # from this date onward
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env defensively
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"\''))

from src.database import Database


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = os.environ.get("SUPABASE_KEY", "").strip()
    if not (sb_url and sb_key):
        log("FATAL: SUPABASE_URL / SUPABASE_KEY not set")
        return 2

    since = sys.argv[1] if len(sys.argv) > 1 else None
    db = Database(sb_url, sb_key)

    # Collect distinct captured_dates. We use channel_snapshots as the date
    # source (one row per channel per day, much smaller than video_snapshots).
    # The daily cron writes both tables the same day, so dates line up.
    seen: set[str] = set()
    page_size = 1000
    offset = 0
    while True:
        q = (
            db.client.table("channel_snapshots")
            .select("captured_date")
            .order("captured_date", desc=False)
        )
        if since:
            q = q.gte("captured_date", since)
        resp = q.range(offset, offset + page_size - 1).execute()
        rows = resp.data or []
        if not rows:
            break
        for r in rows:
            seen.add(r["captured_date"])
        if len(rows) < page_size:
            break
        offset += page_size

    dates = sorted(seen)
    log(f"Backfilling video_daily_deltas for {len(dates)} date(s): {dates[0] if dates else 'none'} → {dates[-1] if dates else 'none'}")

    total = 0
    for i, d in enumerate(dates, 1):
        try:
            n = db.compute_video_daily_deltas(d)
            total += n
            log(f"  [{i}/{len(dates)}] {d}: {n} rows")
        except Exception as e:
            log(f"  [{i}/{len(dates)}] {d}: FAILED — {e}")

    log(f"Done — {total} rows upserted across {len(dates)} date(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
