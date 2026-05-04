#!/usr/bin/env python3
"""Refill missing season videos in the `videos` table.

Walks every channel's UULF/UUSH/UULV playlists from the channel's
season-start to today, fetches details for any video IDs not yet in the
DB, and upserts them into `videos`.

DOES NOT TOUCH channel_snapshots OR video_snapshots — pure catalog
backfill. Safe to run any time without polluting daily counters.

Env vars:
    YOUTUBE_API_KEY        (fallback)
    YOUTUBE_API_KEY_HEAVY  (preferred — quota-heavy job)
    SUPABASE_URL
    SUPABASE_KEY

Usage:
    python scripts/refill_season_videos.py                 # all channels
    python scripts/refill_season_videos.py --only <id>     # one channel by DB id
    python scripts/refill_season_videos.py --league "Serie A"  # one league
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since, COUNTRY_TO_LEAGUE


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Refill a single channel by DB id")
    parser.add_argument("--league", help="Refill all channels in this league name")
    args = parser.parse_args()

    api_key = (os.getenv("YOUTUBE_API_KEY_HEAVY")
               or os.getenv("YOUTUBE_API_KEY"))
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    if not api_key or not supabase_url or not supabase_key:
        log("Missing env vars (YOUTUBE_API_KEY[_HEAVY], SUPABASE_URL, SUPABASE_KEY)")
        return 1

    yt = YouTubeClient(api_key)
    db = Database(supabase_url, supabase_key)

    channels = db.get_all_channels() or []
    if args.only:
        channels = [c for c in channels if c.get("id") == args.only]
    if args.league:
        target = args.league.strip().lower()
        channels = [c for c in channels
                    if (COUNTRY_TO_LEAGUE.get(c.get("country") or "")
                        or "").lower() == target]
    log(f"Refilling {len(channels)} channels")

    started = time.time()
    ok = 0
    failed: list[str] = []
    new_total = 0

    for ch in channels:
        ch_db_id = ch.get("id")
        yt_id = ch.get("youtube_channel_id")
        name = ch.get("name", "?")
        if not ch_db_id or not yt_id:
            continue
        try:
            since = get_season_since(ch)
            season = yt.get_video_ids_since_by_format(yt_id, since)
            long_ids = season.get("long", [])
            short_ids = season.get("shorts", [])
            live_ids = season.get("live", [])

            seen: set[str] = set()
            all_ids: list[str] = []
            for vid in long_ids + short_ids + live_ids:
                if vid not in seen:
                    seen.add(vid)
                    all_ids.append(vid)

            known = db.get_known_video_ids(ch_db_id)
            new_ids = [v for v in all_ids if v not in known]
            if not new_ids:
                ok += 1
                log(f"  {name}: 0 new (have {len(all_ids)} season vids)")
                continue

            long_set, short_set, live_set = set(long_ids), set(short_ids), set(live_ids)
            new_vids: list[dict] = []
            for b in range(0, len(new_ids), 50):
                batch = new_ids[b:b + 50]
                fetched = yt.get_video_details(batch)
                for v in fetched:
                    vid = v["youtube_video_id"]
                    if vid in live_set:
                        v["format"] = "live"
                    elif vid in short_set:
                        v["format"] = "short"
                    else:
                        v["format"] = "long"
                new_vids.extend(fetched)

            if new_vids:
                db.upsert_videos(new_vids, ch_db_id)
                new_total += len(new_vids)
                log(f"  {name}: +{len(new_vids)} new videos")
            ok += 1
        except Exception as e:
            failed.append(f"{name}: {e}")
            log(f"  {name}: FAILED — {e}")
            traceback.print_exc()

    elapsed = time.time() - started
    log(f"Done in {elapsed:.1f}s — channels_ok={ok} "
        f"failed={len(failed)} new_videos={new_total}")
    if failed:
        log("Failures:")
        for f in failed:
            log(f"  - {f}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
