"""Daily NFL channel snapshot — unattended.

Refreshes subs/views/video_count for the 33 NFL channels (32 franchises
+ @NFL HQ) once a day. Writes the channels table + a channel_snapshot
row per channel. NO video ingestion in v0 (see docs/NFL_V0.md) —
that's gated behind a separate scripts/hourly_nfl.py + future videos
roadmap.

Isolated by entity_type="NFL" — does NOT touch top-5 or WC2026
channels. Safe to schedule alongside the existing daily crons.

Schedule on Railway as its own service, daily (e.g. 04:30 UTC).
Quota cost: ~33 YouTube API units/day — negligible.

Env vars: SUPABASE_URL, SUPABASE_KEY, YOUTUBE_API_KEY
          (YOUTUBE_API_KEY_HEAVY preferred when set).
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
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip().strip('"').strip("'")
              or os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'"))
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)
    start = time.time()
    errors = 0

    nfl = [c for c in db.get_all_channels()
           if c.get("entity_type") == "NFL"]
    log(f"Starting daily NFL snapshot — {len(nfl)} channels")
    if not nfl:
        log("No NFL channels in the DB yet — run scripts/import_nfl.py first.")
        return 0

    ok = 0
    failed: list[tuple[str, str]] = []
    for ch in nfl:
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            failed.append((name, "no youtube_channel_id"))
            continue
        try:
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                failed.append((name, "empty stats response"))
                continue
            db.upsert_channel({**ch, **stats})
            if ch.get("id"):
                db.snapshot_channel(ch["id"], stats)
            ok += 1
        except Exception as e:
            failed.append((name, str(e)[:200]))
            errors += 1

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — ok={ok}/{len(nfl)} "
        f"failed={len(failed)} errors={errors}")
    if failed:
        for n, e in failed[:5]:
            log(f"  - {n}: {e}")

    try:
        status = "daily_nfl" if errors == 0 else "daily_nfl_partial"
        db.log_fetch(
            ok, 0, status,
            f"{ok}/{len(nfl)} NFL channels in {elapsed:.1f}s, "
            f"{errors} errors",
        )
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    return 0 if ok > 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as _exc:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
