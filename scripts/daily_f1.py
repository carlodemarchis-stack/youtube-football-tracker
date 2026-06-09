"""Daily F1 channel snapshot — unattended.

Refreshes subs/views/video_count for the 11 F1 channels (10 teams +
@Formula1 HQ) once a day. Writes the channels table + a
channel_snapshot row per channel. NO video ingestion in v0 (mirrors
NFL v0) — that's gated behind a future scripts/hourly_f1.py.

Isolated by entity_type="F1" — does NOT touch top-5 or WC2026
channels. Safe to schedule alongside the existing daily crons.

Schedule on Railway as its own service, daily (e.g. 04:45 UTC).
Quota cost: ~11 YouTube API units/day — negligible.

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

    f1 = [c for c in db.get_all_channels()
          if c.get("entity_type") == "F1"]
    log(f"Starting daily F1 snapshot — {len(f1)} channels")
    if not f1:
        log("No F1 channels in the DB yet — run scripts/import_f1.py first.")
        return 0

    ok = 0
    failed: list[tuple[str, str]] = []
    for ch in f1:
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
    log(f"Done in {elapsed:.1f}s — ok={ok}/{len(f1)} "
        f"failed={len(failed)} errors={errors}")
    if failed:
        for n, e in failed[:5]:
            log(f"  - {n}: {e}")

    try:
        status = "daily_f1" if errors == 0 else "daily_f1_partial"
        db.log_fetch(
            ok, 0, status,
            f"{ok}/{len(f1)} F1 channels in {elapsed:.1f}s, "
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
