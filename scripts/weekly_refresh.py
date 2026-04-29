#!/usr/bin/env python3
"""Weekly refresh — unattended.

Runs once a week (Monday 04:00 UTC). Covers everything the daily does, PLUS
refreshes stats on ALL videos (not just season / 30-day window).

Steps:
  1. Channel stats — fetch current subs/views/video_count for every channel,
     upsert channels table, write a channel_snapshot row.
  2. Video stats — fetch view/like/comment counts for EVERY video in the DB
     (not just season). Updates the videos table so lifetime top-100s,
     channel detail stats, etc. stay accurate.
  3. Recompute top100_stats + season_views on every channel (depends on the
     fresh view counts written in step 2).

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

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    # Prefer dedicated heavy-quota key if set, else fall back to regular key.
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip().strip('"').strip("'")
              or os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'"))
    yt_key_source = "YOUTUBE_API_KEY_HEAVY" if os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip() else "YOUTUBE_API_KEY"
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY (or YOUTUBE_API_KEY_HEAVY) / SUPABASE_URL / SUPABASE_KEY")
        return 2

    log(f"Starting weekly refresh — yt_key_source={yt_key_source} url_len={len(sb_url)} key_len={len(sb_key)} yt_len={len(yt_key)}")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    start = time.time()
    errors = 0

    # ── 1. Channel stats + channel_snapshots ─────────────────────
    channels = db.get_all_channels()
    # Players + Federations have their own dedicated crons — skip them here
    channels = [c for c in channels if c.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")]
    log(f"Step 1: refreshing stats for {len(channels)} channels (Players + Federations excluded)")
    ch_ok = 0
    ch_failed: list[tuple[str, str]] = []

    for i, ch in enumerate(channels, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            ch_failed.append((name, "no youtube_channel_id"))
            continue
        try:
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                ch_failed.append((name, "empty stats response"))
                continue
            db.upsert_channel({**ch, **stats})
            channel_db_id = ch.get("id")
            if channel_db_id:
                db.snapshot_channel(channel_db_id, stats)
            ch_ok += 1
            if i % 20 == 0:
                log(f"  channels {i}/{len(channels)}")
        except Exception as e:
            ch_failed.append((name, str(e)[:200]))
            errors += 1

    log(f"  channels done: ok={ch_ok} failed={len(ch_failed)}")
    if ch_failed:
        for n, e in ch_failed[:5]:
            log(f"    - {n}: {e}")

    # ── 2. Video stats (ALL videos — excluding Player videos) ────
    rows = db.get_all_video_rows()
    # Exclude Player + Federation videos — handled by their dedicated crons
    _player_cids = {c["id"] for c in db.get_all_channels()
                    if c.get("entity_type") in ("Player", "Federation", "OtherClub", "WomenClub")}
    if _player_cids:
        # get_all_video_rows returns id + youtube_video_id only, not channel_id.
        # Fetch channel_id for all videos once, then filter.
        _resp = db.client.table("videos").select("id,channel_id").execute().data or []
        _vid_to_cid = {r["id"]: r.get("channel_id") for r in _resp}
        rows = [r for r in rows if _vid_to_cid.get(r["id"]) not in _player_cids]
    total_videos = len(rows)
    log(f"Step 2: refreshing stats for {total_videos} videos (Player + Federation videos excluded)")

    id_to_dbid = {r["youtube_video_id"]: r["id"] for r in rows}
    yt_ids = list(id_to_dbid.keys())

    update_buffer: list[dict] = []

    for b in range(0, len(yt_ids), 50):
        batch = yt_ids[b:b + 50]
        try:
            details = yt.get_video_details(batch)
        except Exception as e:
            errors += 1
            log(f"  batch {b}-{b + 50} fetch failed: {e}")
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
            })
        if (b // 50) % 20 == 0:
            log(f"  fetched stats for {min(b + 50, len(yt_ids))}/{len(yt_ids)}")

    # Flush to Supabase in 500-row chunks
    videos_updated = 0
    for b in range(0, len(update_buffer), 500):
        try:
            db.client.table("videos").upsert(update_buffer[b:b + 500], on_conflict="id").execute()
            videos_updated += len(update_buffer[b:b + 500])
        except Exception as e:
            log(f"  upsert chunk {b} failed: {e}")
            errors += 1

    log(f"  videos updated: {videos_updated}/{total_videos}")

    # ── 3. Recompute top100_stats + season_views per channel ─────
    # Refresh channels list (may have new IDs from step 1)
    channels = db.get_all_channels()
    # Players + Federations have their own dedicated crons — skip them here
    club_channels = [c for c in channels if c.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    log(f"Step 3: recomputing top100_stats for {len(club_channels)} club channels")

    stats_ok = 0
    for i, ch in enumerate(club_channels, 1):
        ch_id = ch.get("id")
        if not ch_id:
            continue
        try:
            db.refresh_top100_stats(ch_id, get_season_since(ch))
            stats_ok += 1
        except Exception as e:
            log(f"  top100 refresh failed for {ch.get('name', '?')}: {e}")
            errors += 1
        if i % 20 == 0:
            log(f"  top100 stats {i}/{len(club_channels)}")

    log(f"  top100 stats refreshed: {stats_ok}/{len(club_channels)}")

    # ── Done ─────────────────────────────────────────────────────
    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — channels={ch_ok} videos={videos_updated} top100={stats_ok} errors={errors}")

    try:
        status = "weekly_refresh" if errors == 0 else "weekly_refresh_partial"
        db.log_fetch(
            ch_ok, 0, status,
            f"{ch_ok} channels, {videos_updated} videos refreshed, "
            f"{stats_ok} top100 recomputed, {errors} errors, {elapsed:.1f}s",
        )
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    return 0 if (ch_ok > 0 or videos_updated > 0) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
