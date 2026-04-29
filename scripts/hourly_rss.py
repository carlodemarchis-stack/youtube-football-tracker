#!/usr/bin/env python3
"""Hourly lightweight new-video check via YouTube Data API.

For every channel:
  1. Fetch recent videos via playlistItems.list on the uploads playlist
     (1 quota unit per channel — ~2,400/day total).
  2. Compare video IDs against what's already in the DB.
  3. For genuinely new videos only, call videos.list + format-playlist
     membership to classify long/short/live and fetch full details.

Previously used YouTube's public RSS feed (feeds/videos.xml?channel_id=…)
which was free but returned 404 globally as of April 2026. Switched to the
API for predictability.

This does NOT update channel stats or snapshots — that stays in daily_refresh.py.
It only makes new videos appear in the app within ~1 hour instead of ~24h.

Env vars required:
    YOUTUBE_API_KEY        (regular key — fallback)
    YOUTUBE_API_KEY_HEAVY  (optional — preferred if set, used for big quota jobs)
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

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_recent_video_entries(yt: YouTubeClient, youtube_channel_id: str) -> list[dict]:
    """Fetch recent video IDs + published dates via playlistItems.list.
    Uploads playlist ID = channel ID with UC → UU prefix.
    Returns list of {"video_id": str, "published": str, "title": str}.
    Costs 1 quota unit per call."""
    if not youtube_channel_id.startswith("UC"):
        return []
    uploads_pl_id = "UU" + youtube_channel_id[2:]
    try:
        return yt.get_recent_video_entries(uploads_pl_id, max_results=20)
    except Exception as e:
        log(f"  playlistItems fetch failed for {youtube_channel_id}: {e}")
        return []


def main() -> int:
    # Prefer dedicated heavy-quota key if set, else fall back to regular key.
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip()
              or os.environ.get("YOUTUBE_API_KEY", "").strip())
    yt_key_source = "YOUTUBE_API_KEY_HEAVY" if os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip() else "YOUTUBE_API_KEY"
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = os.environ.get("SUPABASE_KEY", "").strip()

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY (or YOUTUBE_API_KEY_HEAVY) / SUPABASE_URL / SUPABASE_KEY")
        return 2

    log(f"Hourly check — yt_key_source={yt_key_source}")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    total = len(channels)
    log(f"Hourly check — {total} channels (playlistItems API)")

    # Build a quick lookup: youtube_channel_id → db channel record.
    # Skip leagues (no videos to check) and Players (handled by daily_players.py only).
    ch_by_yt_id: dict[str, dict] = {}
    for ch in channels:
        yt_id = ch.get("youtube_channel_id")
        if yt_id and ch.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub"):
            ch_by_yt_id[yt_id] = ch

    # Collect all new video IDs across all channels
    all_new: list[tuple[str, str]] = []  # (youtube_video_id, channel_db_id)
    ok = 0
    fail = 0
    start = time.time()

    for i, (yt_id, ch) in enumerate(ch_by_yt_id.items(), 1):
        channel_db_id = ch["id"]
        entries = fetch_recent_video_entries(yt, yt_id)
        if not entries:
            fail += 1
            continue
        ok += 1

        # Filter to season videos only
        ch_season_since = get_season_since(ch)
        in_season = [e for e in entries if e["published"] >= ch_season_since]
        if not in_season:
            continue

        # Check which are new
        video_ids = [e["video_id"] for e in in_season]
        known = db.get_known_video_ids(channel_db_id)
        new_ids = [vid for vid in video_ids if vid not in known]

        if new_ids:
            log(f"  [{i}/{len(ch_by_yt_id)}] {ch['name']}: {len(new_ids)} new video(s)")
            for vid in new_ids:
                all_new.append((vid, channel_db_id))

    scan_elapsed = time.time() - start
    log(f"Scan done in {scan_elapsed:.1f}s — ok={ok} fail={fail} new_videos={len(all_new)}")

    if not all_new:
        log("No new videos found. Done.")
        return 0

    # ── Fetch full details for new videos via API ────────────────
    # Group by channel for format detection and upsert
    by_channel: dict[str, list[str]] = {}
    for vid, ch_id in all_new:
        by_channel.setdefault(ch_id, []).append(vid)

    total_inserted = 0
    for ch_id, new_ids in by_channel.items():
        ch = next((c for c in channels if c["id"] == ch_id), None)
        if not ch:
            continue
        yt_ch_id = ch["youtube_channel_id"]

        # Detect format via playlist membership (same as daily_refresh)
        # Only check the playlists for these specific IDs
        try:
            season = yt.get_video_ids_since_by_format(yt_ch_id, get_season_since(ch))
            long_set = set(season.get("long", []))
            short_set = set(season.get("shorts", []))
            live_set = set(season.get("live", []))
        except Exception as e:
            log(f"  Playlist check failed for {ch['name']}: {e}")
            long_set = short_set = live_set = set()

        # Fetch full video details
        fetched = yt.get_video_details(new_ids)
        for v in fetched:
            vid = v["youtube_video_id"]
            if vid in live_set:
                v["format"] = "live"
            elif vid in short_set:
                v["format"] = "short"
            else:
                v["format"] = "long"

        if fetched:
            db.upsert_videos(fetched, ch_id)
            total_inserted += len(fetched)
            try:
                db.refresh_top100_stats(ch_id, get_season_since(ch))
            except Exception:
                pass
            log(f"  Inserted {len(fetched)} videos for {ch['name']}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — {total_inserted} new videos inserted")

    # Log to fetch_log
    try:
        db.log_fetch(
            len(by_channel), total_inserted, "hourly_rss",
            f"{ok} channels scanned, {total_inserted} new videos in {elapsed:.1f}s",
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
