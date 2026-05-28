#!/usr/bin/env python3
"""One-off: refresh popular-videos pool for Top-5 channels with UUPV included.

Mirrors the popular-ingest block from views/7_Refresh_Data.py
(process_channel → get_popular_video_ids + videos.list + upsert) but
runs unattended from the CLI. Lets us pick up popular live streams
(UUPV, just added) for the existing 101 Top-5 channels without
touching season ingestion, daily refresh, or any other flow.

Steps per channel:
  1. get_popular_video_ids() — UULP + UUPS + UUPV (~12 API units)
  2. get_video_details() in batches of 50 (~8-12 units depending on
     how many IDs the popular playlists return)
  3. Tag each video with format (live > short > long precedence)
  4. Upsert the union into the videos table
  5. db.refresh_top100_stats() — pure DB, no API
  6. db.refresh_lifetime_format_views() — pure DB, no API

After all channels: rebuild the dashboard cache so /all-channels picks
up the new view splits.

Budget: ~20-25 units per channel × 101 channels ≈ 2.5k units one-off.
Comfortably inside a single key's daily quota.

Env vars: SUPABASE_URL, SUPABASE_KEY (service key for local RLS),
         YOUTUBE_API_KEY (or YOUTUBE_API_KEY_HEAVY).
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient
from src.analytics import classify_videos


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip()
              or os.environ.get("YOUTUBE_API_KEY", "").strip())
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)
    start = time.time()

    channels = [c for c in db.get_all_channels()
                if c.get("entity_type") in ("Club", "League")]
    log(f"Top-5 channels in scope: {len(channels)}")

    ok = 0
    failed: list[tuple[str, str]] = []
    total_saved = 0
    total_live_ids = 0

    for i, ch in enumerate(channels, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        ch_db_id = ch.get("id")
        if not yt_id or not ch_db_id:
            failed.append((name, "missing youtube_channel_id or id"))
            continue
        try:
            # get_popular_video_ids swallows all exceptions (network,
            # 404, …) and returns empty lists — so for any channel
            # with non-zero videos that comes back fully empty we
            # retry once after a short pause. Catches transient
            # DNS blips like the one observed mid-run.
            pop = yt.get_popular_video_ids(yt_id)
            long_ids  = pop.get("long")  or []
            short_ids = pop.get("shorts") or []
            live_ids  = pop.get("live")  or []
            if (not long_ids and not short_ids and not live_ids
                    and int(ch.get("video_count") or 0) > 0):
                time.sleep(3)
                pop = yt.get_popular_video_ids(yt_id)
                long_ids  = pop.get("long")  or []
                short_ids = pop.get("shorts") or []
                live_ids  = pop.get("live")  or []
            total_live_ids += len(live_ids)
            all_ids = long_ids + short_ids + live_ids
            if not all_ids:
                log(f"  [{i:3d}/{len(channels)}] {name[:36]:36s}  no popular IDs")
                ok += 1
                continue

            live_set  = set(live_ids)
            short_set = set(short_ids)
            videos: list[dict] = []
            for b in range(0, len(all_ids), 50):
                batch = all_ids[b:b + 50]
                details = yt.get_video_details(batch)
                for v in details:
                    vid = v.get("youtube_video_id", "")
                    if vid in live_set:
                        v["format"] = "live"
                    elif vid in short_set:
                        v["format"] = "short"
                    else:
                        v["format"] = "long"
                videos.extend(details)

            if videos:
                db.upsert_videos(classify_videos(videos), ch_db_id)
                total_saved += len(videos)

            # Pure-DB recomputes — no API cost, just keep aggregates fresh.
            try:
                db.refresh_top100_stats(ch_db_id)
            except Exception as e:
                log(f"  refresh_top100_stats failed for {name}: {e}")
            try:
                db.refresh_lifetime_format_views(ch_db_id)
            except Exception as e:
                log(f"  refresh_lifetime_format_views failed for {name}: {e}")

            log(f"  [{i:3d}/{len(channels)}] {name[:36]:36s}"
                f"  L={len(long_ids):>3d}  S={len(short_ids):>3d}"
                f"  V={len(live_ids):>3d}  saved={len(videos):>3d}")
            ok += 1
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"  [{i:3d}/{len(channels)}] {name[:36]:36s}  ERROR: {e}")

    elapsed = time.time() - start
    log(f"\nPopular ingest done in {elapsed:.1f}s — ok={ok}/{len(channels)} "
        f"failed={len(failed)} videos_saved={total_saved} "
        f"live_ids_discovered={total_live_ids}")
    if failed:
        for n, e in failed[:5]:
            log(f"  - {n}: {e}")

    # Rebuild dashboard caches so /all-channels picks up the new
    # view splits + format counts. Pure Supabase reads.
    log("\nRebuilding dashboard cache...")
    try:
        from src import dashboard_cache as _dc
        _dc.rebuild_all(db, log=log)
        log("Cache rebuild done.")
    except Exception as e:
        log(f"Cache rebuild FAILED (non-fatal): {e}")
        traceback.print_exc()

    try:
        db.log_fetch(
            ok, 0, "popular_uupv_backfill",
            f"{ok}/{len(channels)} Top-5 channels, {total_saved} videos "
            f"saved, {total_live_ids} live IDs discovered "
            f"in {elapsed:.1f}s",
        )
    except Exception as e:
        log(f"log_fetch skipped: {e}")

    return 0 if ok > 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
