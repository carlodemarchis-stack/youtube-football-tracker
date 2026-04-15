#!/usr/bin/env python3
"""Reclassify every video in the DB using the current detect_theme().

No YouTube API calls — just reads title/duration/format from Supabase,
recomputes the category, and writes it back. Cheap and idempotent.
Run whenever theme rules change.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.analytics import detect_theme


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")
    if not (sb_url and sb_key):
        log("FATAL: SUPABASE_URL / SUPABASE_KEY missing")
        return 2

    db = Database(sb_url, sb_key)
    log("Fetching all videos…")
    # Paginated fetch of minimal cols
    all_rows: list[dict] = []
    page_size = 1000
    offset = 0
    while True:
        resp = (
            db.client.table("videos")
            .select("id,youtube_video_id,title,duration_seconds,format,category")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    log(f"  {len(all_rows)} videos")

    changed: list[dict] = []
    same = 0
    start = time.time()
    for r in all_rows:
        new_cat = detect_theme(r.get("title") or "", r.get("duration_seconds"), r.get("format"))
        if new_cat != (r.get("category") or ""):
            # Include youtube_video_id so upsert-as-insert (on NOT NULL) passes
            changed.append({
                "id": r["id"],
                "youtube_video_id": r["youtube_video_id"],
                "category": new_cat,
            })
        else:
            same += 1

    log(f"  {len(changed)} will change, {same} unchanged")
    if not changed:
        log("Nothing to do.")
        return 0

    # Upsert in 500-row chunks
    written = 0
    for b in range(0, len(changed), 500):
        try:
            db.client.table("videos").upsert(changed[b:b+500], on_conflict="id").execute()
            written += len(changed[b:b+500])
        except Exception as e:
            log(f"  chunk {b} failed: {e}")
    log(f"Done in {time.time()-start:.1f}s — updated {written}/{len(changed)}")
    return 0 if written == len(changed) else 1


if __name__ == "__main__":
    sys.exit(main())
