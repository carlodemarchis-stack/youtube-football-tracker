#!/usr/bin/env python3
"""One-off backfill: seed each WC2026 channel's MOST RECENT upload.

The WC2026 page now shows a "Last upload" column derived (in
refresh_wc2026) from the `videos` table. Full video collection only
started with the daily_wc2026 cron change, so until it has run a few
times the column is all "—". This script fills it immediately and
*durably* at minimal cost:

  1. For each `competitions.wc2026` channel, fetch only its single
     latest upload from the uploads playlist (≈1 quota unit/channel).
  2. get_video_details for those latest videos (≤50/batch).
  3. Upsert that one video per channel into `videos` (idempotent —
     on_conflict the daily cron will simply add more later).
  4. Rebuild dashboard_cache.wc2026 so the page reflects it now.

Cost: ~1 + N playlistItems calls + ~N/50 videos.list calls
(≈ 64 quota units for ~62 channels). Idempotent, no deletes.

Run:  python3 scripts/backfill_wc2026_last_upload.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defensive .env load — same pattern as daily_wc2026.py.
_env_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip('"\''))

from src.database import Database
from src.youtube_api import YouTubeClient


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip().strip('"').strip("'")
              or os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'"))
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip().strip('"').strip("'")
              or os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'"))
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY[_HEAVY] / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    wc = [c for c in channels
          if (c.get("competitions") or {}).get("wc2026")]
    log(f"WC2026 last-upload backfill — {len(wc)} channel(s)")
    if not wc:
        log("No WC2026 channels tagged. Nothing to do.")
        return 0

    start = time.time()
    # video_id -> channel_db_id (so we can group the detail fetch back)
    vid_to_cid: dict[str, str] = {}
    found = 0
    for i, ch in enumerate(wc, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        cdbid = ch.get("id")
        if not yt_id or not cdbid:
            log(f"[{i}/{len(wc)}] {name} — skip (no yt id / db id)")
            continue
        try:
            pl = YouTubeClient._playlist_id(yt_id, "UU")  # uploads playlist
            entries = yt.get_recent_video_entries(pl, max_results=1)
            if not entries:
                log(f"[{i}/{len(wc)}] {name} — no uploads found")
                continue
            vid = entries[0].get("video_id")
            if vid:
                vid_to_cid[vid] = cdbid
                found += 1
                log(f"[{i}/{len(wc)}] {name} — latest {vid} "
                    f"@ {entries[0].get('published', '')[:10]}")
        except Exception as e:
            log(f"[{i}/{len(wc)}] {name} — ERROR: {str(e)[:160]}")

    if not vid_to_cid:
        log("No latest videos resolved. Nothing to upsert.")
        return 1

    # Fetch full details for the latest videos (≤50 per call), then
    # upsert one row per channel into `videos`.
    all_ids = list(vid_to_cid.keys())
    by_channel: dict[str, list[dict]] = {}
    for b in range(0, len(all_ids), 50):
        details = yt.get_video_details(all_ids[b:b + 50])
        for v in details:
            vid = (v.get("youtube_video_id") or "").strip()
            cdbid = vid_to_cid.get(vid)
            if not cdbid:
                continue
            # Format: best-effort (live flag isn't reliable for a single
            # video here) — duration heuristic, same as the cron fallback.
            if not v.get("format"):
                v["format"] = ("long" if (v.get("duration_seconds") or 0) >= 60
                               else "short")
            by_channel.setdefault(cdbid, []).append(v)

    upserted = 0
    for cdbid, vids in by_channel.items():
        try:
            db.upsert_videos(vids, cdbid)
            upserted += len(vids)
        except Exception as e:
            log(f"upsert_videos failed for {cdbid}: {str(e)[:160]}")

    log(f"Upserted {upserted} latest videos across {len(by_channel)} channels")

    # Rebuild the page cache so "Last upload" shows immediately.
    try:
        from src.dashboard_cache import refresh_wc2026
        refresh_wc2026(db, log=log, channels=db.get_all_channels())
    except Exception as e:
        log(f"refresh_wc2026 failed: {str(e)[:200]}")

    log(f"Done in {time.time() - start:.1f}s — "
        f"channels={len(wc)} latest_found={found} upserted={upserted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
