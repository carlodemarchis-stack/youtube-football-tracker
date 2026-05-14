#!/usr/bin/env python3
"""Daily WC2026 refresh — dedicated cron, isolated from the rest.

For every channel with `competitions.wc2026` set (the 48 qualified
national teams + FIFA + 6 confederations + any per-country alt
channels, e.g. Argentina AFAOFICIAL, Spain RFEF, Saudi NT brand):

  1. Fetch current channel stats from the YouTube API.
  2. Upsert the channels table (subs / views / videos / long-form /
     shorts / lives etc.).
  3. Snapshot the per-day counters into channel_snapshots — that
     table has a (channel_id, captured_date) unique constraint, so
     even if the same channel was also picked up by daily_federations
     earlier in the night the row deduplicates cleanly.
  4. Refresh the dashboard_cache.wc2026 payload so the FIFA World Cup
     2026 page reads fresh numbers without re-aggregating live.

We deliberately do NOT touch the videos table here — daily_federations
and the per-club / per-confederation cron jobs already keep their
videos current. This script's job is just the channel-level snapshot
needed to draw trend lines from now → tournament end.

Runs at 00:00 UTC (≈ 02:00 CEST, 01:00 CET) on its own Railway service
so the WC2026 feature can be killed with zero impact elsewhere.

Env vars required:
    YOUTUBE_API_KEY        (rotates to YOUTUBE_API_KEY_BACKUP on quota)
    SUPABASE_URL
    SUPABASE_KEY
Env vars optional:
    YOUTUBE_API_KEY_HEAVY  (used instead of YOUTUBE_API_KEY when set)
    NTFY_TOPIC             (run-completion alert)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defensive .env load — same pattern as daily_federations.py so the
# script works locally without python-dotenv installed.
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
    # Prefer the heavy key when set (matches the pattern in
    # daily_refresh.py / hourly_rss.py — keeps WC2026 daily refreshes
    # from sharing quota with the streamlit page).
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip().strip('"').strip("'")
              or os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'"))
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY[_HEAVY] / SUPABASE_URL / SUPABASE_KEY")
        return 2
    yt_key_source = ("YOUTUBE_API_KEY_HEAVY"
                     if os.environ.get("YOUTUBE_API_KEY_HEAVY") else
                     "YOUTUBE_API_KEY")
    log(f"Starting daily_wc2026 — yt_key_source={yt_key_source}")

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    wc = [c for c in channels
          if (c.get("competitions") or {}).get("wc2026")]
    log(f"Daily WC2026 refresh — {len(wc)} channel(s)")
    if not wc:
        log("No WC2026 channels found (need competitions.wc2026 tag). "
            "Nothing to do.")
        try:
            db.log_fetch(0, 0, "daily_wc2026", "no wc2026 channels tagged")
        except Exception:
            pass
        return 0

    ok = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ch in enumerate(wc, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            failed.append((name, "no youtube_channel_id"))
            continue
        try:
            # ── 1. Fresh stats from YouTube ──
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                failed.append((name, "empty stats response"))
                continue

            # ── 2. Upsert channels table ──
            row = db.upsert_channel({**ch, **stats})
            channel_db_id = row.get("id") or ch.get("id")

            # ── 3. Snapshot row into channel_snapshots ──
            if channel_db_id:
                db.snapshot_channel(channel_db_id, stats)

            ok += 1
            log(f"[{i}/{len(wc)}] {name} — "
                f"subs={stats.get('subscriber_count', 0):,} "
                f"videos={stats.get('video_count', 0)}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"[{i}/{len(wc)}] {name} — ERROR: {e}")

    # ── 4. Refresh dashboard_cache.wc2026 ──
    # Same logic as scripts/refresh_wc2026_cache.py — keep the page's
    # read-side cache in sync with the freshly-snapshotted numbers.
    try:
        from src.dashboard_cache import refresh_wc2026
        # Re-pull channels so the cache payload reflects the just-written
        # stats (we mutated channels in step 2). Pass them explicitly so
        # refresh_wc2026 doesn't do a redundant DB read.
        refresh_wc2026(db, log=log, channels=db.get_all_channels())
    except Exception as e:
        log(f"dashboard_cache refresh failed (non-fatal): {e}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — ok={ok} failed={len(failed)}")

    # ── Run summary (db log + ntfy) ──
    try:
        status = "daily_wc2026_partial" if failed else "daily_wc2026"
        summary = f"{ok} wc2026 channels in {elapsed:.1f}s"
        if failed:
            summary += (f" | {len(failed)} failed: "
                        + "; ".join(f"{n}: {e}" for n, e in failed[:5]))
        db.log_fetch(ok, 0, status, summary)
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    try:
        from src.notify import send_run_alert
        send_run_alert(
            "daily_wc2026",
            ok=not failed,
            summary=f"{ok} wc2026 channels in {elapsed:.0f}s",
            error=(f"{len(failed)} failed" if failed else ""),
        )
    except Exception as _e:
        log(f"ntfy alert failed (non-fatal): {_e}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
