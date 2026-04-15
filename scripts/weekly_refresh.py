#!/usr/bin/env python3
"""Weekly refresh — unattended.

Refreshes view/like/comment/last_fetched on EVERY video in the DB (not just
season videos). Intended to run once a week (Monday early UTC) so that stats
for older/lifetime videos don't go stale — the daily cron only touches season
videos.

Does NOT write to video_snapshots (that's per-video daily history, scoped to
season). This just updates the live `videos` table so Streamlit pages that
show lifetime top-100s, channel detail stats, etc. stay accurate.

Env vars required:
    YOUTUBE_API_KEY
    SUPABASE_URL
    SUPABASE_KEY
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.youtube_api import YouTubeClient


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    yt_key = os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'")
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    log(f"Starting weekly refresh — url_len={len(sb_url)} key_len={len(sb_key)} yt_len={len(yt_key)}")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    rows = db.get_all_video_rows()
    total = len(rows)
    log(f"Found {total} videos in DB")
    if not rows:
        return 0

    id_to_dbid = {r["youtube_video_id"]: r["id"] for r in rows}
    yt_ids = list(id_to_dbid.keys())

    updated = 0
    errors = 0
    start = time.time()
    update_buffer: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for b in range(0, len(yt_ids), 50):
        batch = yt_ids[b:b+50]
        try:
            details = yt.get_video_details(batch)
        except Exception as e:
            errors += 1
            log(f"  batch {b}-{b+50} fetch failed: {e}")
            continue
        for v in details:
            dbid = id_to_dbid.get(v["youtube_video_id"])
            if not dbid:
                continue
            update_buffer.append({
                "id": dbid,
                "view_count": v.get("view_count", 0),
                "like_count": v.get("like_count", 0),
                "comment_count": v.get("comment_count", 0),
                "last_fetched": now_iso,
            })
        if (b // 50) % 20 == 0:
            log(f"  fetched stats for {min(b+50, len(yt_ids))}/{len(yt_ids)}")

    # Flush to Supabase in 500-row chunks
    for b in range(0, len(update_buffer), 500):
        try:
            db.client.table("videos").upsert(update_buffer[b:b+500], on_conflict="id").execute()
            updated += len(update_buffer[b:b+500])
        except Exception as e:
            log(f"  upsert chunk {b} failed: {e}")
            errors += 1

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — videos_updated={updated} batch_errors={errors}")

    try:
        status = "weekly_refresh" if errors == 0 else "weekly_refresh_partial"
        db.log_fetch(0, 0, status, f"{updated} videos refreshed, {errors} errors, {elapsed:.1f}s")
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    return 0 if updated > 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
