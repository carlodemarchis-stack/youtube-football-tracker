#!/usr/bin/env python3
"""Snapshot-only recovery script.

Runs ONLY step 3 of daily_refresh.py: fetch current view/like/comment
counts for every video published in the rolling 30-day window, write
to video_snapshots (with an optional override date), recompute
video_daily_deltas, and update the videos table for freshness.

Skips the per-channel stats + new-video ingest (those succeeded last
night). Cheap on quota: ~12K videos / 50 per call ≈ 240 units.

Use case: when the main daily_refresh hit a quota wall mid-run and
the snapshot step wrote 0 rows. Run with --date YYYY-MM-DD to
backdate today's fetched view counts to yesterday so the missing
deltas can be computed (only meaningful if you run within a few
hours of the missed window — view counts drift over time).

Usage:
    python3 scripts/snapshot_videos_only.py                    # date = today UTC
    python3 scripts/snapshot_videos_only.py --date 2026-05-03  # backdate

Env vars:
    YOUTUBE_API_KEY_DAILY  (preferred — fresh dedicated quota)
    YOUTUBE_API_KEY_HEAVY  (fallback)
    YOUTUBE_API_KEY        (fallback)
    SUPABASE_URL
    SUPABASE_KEY
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.youtube_api import YouTubeClient

SNAPSHOT_WINDOW_DAYS = 30


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None,
                    help="Override captured_date (YYYY-MM-DD). Defaults to today UTC.")
    args = ap.parse_args()

    target_date = args.date or datetime.now(timezone.utc).date().isoformat()
    # Validate
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        log(f"FATAL: --date {target_date!r} not in YYYY-MM-DD format")
        return 2

    yt_key = (os.environ.get("YOUTUBE_API_KEY_DAILY", "")
              or os.environ.get("YOUTUBE_API_KEY_HEAVY", "")
              or os.environ.get("YOUTUBE_API_KEY", ""))
    if os.environ.get("YOUTUBE_API_KEY_DAILY"):
        yt_key_source = "YOUTUBE_API_KEY_DAILY"
    elif os.environ.get("YOUTUBE_API_KEY_HEAVY"):
        yt_key_source = "YOUTUBE_API_KEY_HEAVY"
    else:
        yt_key_source = "YOUTUBE_API_KEY"

    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_KEY", "")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY (or _DAILY / _HEAVY) / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt_key = yt_key.strip().strip('"').strip("'")
    sb_url = sb_url.strip().strip('"').strip("'")
    sb_key = sb_key.strip().strip('"').strip("'")

    log(f"Snapshot-only recovery — target captured_date={target_date} key_source={yt_key_source}")

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    window_cutoff = (datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_WINDOW_DAYS)).date().isoformat()
    log(f"Snapshotting videos published since {window_cutoff} ({SNAPSHOT_WINDOW_DAYS}d window)")

    season_rows = db.get_season_video_rows(window_cutoff)
    log(f"  {len(season_rows)} videos in window")
    if not season_rows:
        log("Nothing to snapshot — exiting")
        return 0

    id_to_dbid = {r["youtube_video_id"]: r["id"] for r in season_rows}
    yt_ids = list(id_to_dbid.keys())
    snap_rows: list[dict] = []
    start = time.time()
    for b in range(0, len(yt_ids), 50):
        batch = yt_ids[b:b + 50]
        details = yt.get_video_details(batch)
        for v in details:
            dbid = id_to_dbid.get(v["youtube_video_id"])
            if not dbid:
                continue
            snap_rows.append({
                "video_id": dbid,
                "view_count": v.get("view_count", 0),
                "like_count": v.get("like_count", 0),
                "comment_count": v.get("comment_count", 0),
            })
        if b % 500 == 0:
            log(f"  fetched stats for {min(b + 50, len(yt_ids))}/{len(yt_ids)}")

    fetched_elapsed = time.time() - start
    log(f"Fetched {len(snap_rows)} video stats in {fetched_elapsed:.1f}s")

    written = db.snapshot_videos_batch(snap_rows, captured_date=target_date)
    log(f"Wrote {written} video_snapshots rows for captured_date={target_date}")

    # Recompute deltas for the target date now that snapshots exist.
    try:
        deltas_written = db.compute_video_daily_deltas(target_date)
        log(f"Wrote {deltas_written} video_daily_deltas rows for {target_date}")
    except Exception as e:
        log(f"video_daily_deltas step skipped: {e}")

    # Refresh the videos table so Streamlit sees the new counts. We only
    # do this when snapshotting today (don't overwrite live state with
    # values that were merely the best estimate for a backdated row).
    today_iso = datetime.now(timezone.utc).date().isoformat()
    if target_date == today_iso:
        try:
            update_rows = [{
                "id": v["video_id"],
                "view_count": v["view_count"],
                "like_count": v["like_count"],
                "comment_count": v["comment_count"],
            } for v in snap_rows]
            for b in range(0, len(update_rows), 500):
                db.client.table("videos").upsert(update_rows[b:b + 500], on_conflict="id").execute()
            log(f"Updated {len(update_rows)} rows in videos table for freshness")
        except Exception as e:
            log(f"videos-table freshness update skipped: {e}")
    else:
        log(f"Skipping videos-table freshness update (target {target_date} != today {today_iso})")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — snapshots={written} deltas={deltas_written if 'deltas_written' in locals() else 'n/a'}")

    try:
        db.log_fetch(0, 0, "snapshot_only_recovery",
                     f"captured_date={target_date} snapshots={written} key={yt_key_source} elapsed={elapsed:.1f}s")
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
