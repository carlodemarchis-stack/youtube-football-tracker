#!/usr/bin/env python3
"""Daily channel snapshot — unattended.

Iterates every channel, fetches current stats via the YouTube Data API, upserts
the channel row, and appends a daily snapshot to `channel_snapshots`.

Designed to be called from a cron (GitHub Actions). Exits non-zero on fatal
errors so the CI run shows red. Individual channel failures are logged but do
not abort the whole batch.

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

# Allow running as `python scripts/daily_refresh.py` from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.youtube_api import YouTubeClient


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_KEY", "")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    log("Starting daily channel snapshot")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    total = len(channels)
    log(f"Found {total} channels")

    ok = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ch in enumerate(channels, 1):
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
            row = db.upsert_channel({**ch, **stats})
            cid = row.get("id") or ch.get("id")
            if cid:
                db.snapshot_channel(cid, stats)
            ok += 1
            log(f"[{i}/{total}] {name} — subs={stats.get('subscriber_count'):,} videos={stats.get('video_count')}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"[{i}/{total}] {name} — ERROR: {e}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — ok={ok} failed={len(failed)}")

    # Persist summary to fetch_history so the Refresh Data page shows it
    try:
        if failed:
            err_summary = "; ".join(f"{n}: {e}" for n, e in failed[:10])
            db.log_fetch(ok, 0, "daily_snapshot_partial", f"{len(failed)} failures: {err_summary}")
        else:
            db.log_fetch(ok, 0, "daily_snapshot", f"Snapshot of {ok} channels in {elapsed:.1f}s")
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    if failed:
        log("Failures:")
        for n, e in failed:
            log(f"  - {n}: {e}")
        # Partial failure → exit code 1 so GH Actions shows red but we still
        # kept the successful snapshots.
        return 1 if ok == 0 else 0  # only hard-red if EVERYTHING failed

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
