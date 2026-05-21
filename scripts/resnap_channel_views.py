"""Surgical channel-snapshot resnap — refetch channel.statistics for the
top-5 cohort and overwrite the channel_snapshots row for one specific
date. Does NOT touch video_snapshots, videos, or run the heavy bits of
daily_refresh.

Use case
========

YouTube's channel.statistics.viewCount aggregate is updated by Google
on a lazy/batched cadence (12–36h windows for high-volume channels).
When the daily_refresh cron happens to fall inside one of those frozen
windows, EVERY top-5 channel comes back with the previous day's
viewCount value. The chart on Daily Recap then flat-lines for that day
(see scripts/daily_refresh.py's frozen-cohort guard for the warning).

A few hours later YouTube's batch usually catches up. This script lets
you recover the lost growth by re-pulling the live values and writing
them into the existing channel_snapshots row (upsert on
(channel_id, captured_date)). After the resnap it refreshes the
30-Day Trends hot-tier cache so the page picks up the corrected
totals.

Scope
=====

- Cohort: TOP-5 ONLY (96 Clubs + 5 League HQs = 101 channels). Other
  entity types (Players, Federations, GoverningBody, OtherClub,
  WomenClub) have their own dedicated daily crons and aren't part of
  the Daily Recap / 30-Day Trends cohort.
- Caches: refresh_trends_30d(tier='hot') only. Daily Recap reads
  channel_snapshots live so it doesn't need a cache rebuild. AI vibe
  notes are nightly-only, kept on the regular cron's schedule.

Usage
=====

    # default: yesterday CET (the date the last cron would have written)
    python scripts/resnap_channel_views.py

    # override
    python scripts/resnap_channel_views.py --target-date 2026-05-20

    # dry-run: report what would change, write nothing
    python scripts/resnap_channel_views.py --dry-run

Exit codes
==========

    0 — success
    2 — env / config error (missing SUPABASE_SERVICE_KEY, etc.)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Make the project root importable when run as `python scripts/…` from anywhere.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from src.youtube_api import YouTubeClient
from src.database import Database


CET = ZoneInfo("Europe/Rome")


def _yesterday_cet() -> str:
    return (datetime.now(CET).date() - timedelta(days=1)).isoformat()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--target-date", default=_yesterday_cet(),
                   help="ISO date (YYYY-MM-DD) to overwrite. Defaults to "
                        "yesterday CET (the date the last cron would have "
                        "written).")
    p.add_argument("--dry-run", action="store_true",
                   help="Compare live API to stored values, print diff, "
                        "write nothing.")
    p.add_argument("--skip-cache", action="store_true",
                   help="Skip the trends_30d hot-tier cache refresh.")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    target_date = args.target_date

    # Validate target_date format early — fail loud, not in the loop.
    try:
        date.fromisoformat(target_date)
    except ValueError:
        print(f"FATAL: --target-date must be YYYY-MM-DD, got {target_date!r}")
        return 2

    # Writes go through service key so RLS doesn't silently drop them.
    yt_key = (os.environ.get("YOUTUBE_API_KEY")
              or os.environ.get("YOUTUBE_API_KEY_DAILY")
              or os.environ.get("YOUTUBE_API_KEY_HEAVY"))
    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY")
              or os.environ.get("SUPABASE_KEY"))
    if not (yt_key and sb_url and sb_key):
        print("FATAL: need YOUTUBE_API_KEY (or _DAILY/_HEAVY) + SUPABASE_URL "
              "+ SUPABASE_SERVICE_KEY (or SUPABASE_KEY) in env / .env")
        return 2

    print(f"Target date: {target_date}  (dry-run={args.dry_run})")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    # Cohort = top-5 (Clubs + Leagues). Same shape as Daily Recap +
    # 30-Day Trends cohort.
    chans = (db.client.table("channels")
             .select("id,name,youtube_channel_id,entity_type")
             .execute().data or [])
    top5 = [c for c in chans if c.get("entity_type") in ("Club", "League")]
    if not top5:
        print("FATAL: empty top-5 cohort")
        return 2
    print(f"Cohort: {len(top5)} channels (Clubs + League HQs)")

    # Pre-fetch existing rows for target_date in ONE query — saves 101
    # round-trips and lets us print Δ per channel.
    existing = (db.client.table("channel_snapshots")
                .select("channel_id,total_views,captured_at")
                .eq("captured_date", target_date)
                .in_("channel_id", [c["id"] for c in top5])
                .execute().data or [])
    prev = {r["channel_id"]: int(r.get("total_views") or 0) for r in existing}
    print(f"Existing rows for {target_date}: {len(prev)}/{len(top5)}")

    # The diagnostic gold — count what changes.
    started = time.time()
    updated = 0
    unchanged = 0
    new_row = 0
    errors = 0
    total_gain = 0   # Σ of positive deltas (the "recovered" Δ views)
    for i, c in enumerate(top5, 1):
        try:
            stats = yt.get_channel_stats(c["youtube_channel_id"])
            if not stats:
                errors += 1
                print(f"  [{i:>3}/{len(top5)}]  {c['name']:<32}  ERROR empty stats")
                continue
            new_tv = int(stats.get("total_views", 0) or 0)
            cur_tv = prev.get(c["id"])
            if cur_tv is None:
                # No existing row for this date — fresh insert.
                if not args.dry_run:
                    db.snapshot_channel(c["id"], stats,
                                        captured_date=target_date)
                new_row += 1
                print(f"  [{i:>3}/{len(top5)}]  {c['name']:<32}  NEW  "
                      f"{new_tv:>14,}")
            elif new_tv != cur_tv:
                delta = new_tv - cur_tv
                if delta > 0:
                    total_gain += delta
                if not args.dry_run:
                    db.snapshot_channel(c["id"], stats,
                                        captured_date=target_date)
                updated += 1
                print(f"  [{i:>3}/{len(top5)}]  {c['name']:<32}  "
                      f"{cur_tv:>14,} → {new_tv:>14,}  Δ={delta:+,}")
            else:
                unchanged += 1
                # Quiet on still-frozen channels — keeps the log readable.
        except Exception as e:
            errors += 1
            print(f"  [{i:>3}/{len(top5)}]  {c['name']:<32}  ERROR {e}")

    elapsed = time.time() - started
    print()
    print(f"Resnap done in {elapsed:.1f}s")
    print(f"  updated      {updated:>4}  (Δ vs stored != 0)")
    print(f"  unchanged    {unchanged:>4}  (still frozen at stored value)")
    print(f"  new_row      {new_row:>4}  (no prior row for {target_date})")
    print(f"  errors       {errors:>4}")
    if total_gain:
        print(f"  Σ recovered  {total_gain:>14,}  views")
    pct_frozen = (unchanged / len(top5) * 100) if top5 else 0
    if pct_frozen >= 50:
        print(f"  ⚠️ {pct_frozen:.0f}% of cohort STILL frozen — YouTube's "
              "batch hasn't caught up; consider re-running in 2-3 hours.")
    if args.dry_run:
        print("\n(dry-run: no writes performed)")
        return 0

    # Refresh trends_30d hot tier so the page picks up the corrected
    # totals immediately. Daily Recap reads channel_snapshots live so
    # doesn't need a cache rebuild.
    if not args.skip_cache:
        print("\nRefreshing trends_30d hot tier…")
        try:
            from src import dashboard_cache as _dc
            t = time.time()
            _dc.refresh_trends_30d(db, tier="hot")
            print(f"  done in {time.time() - t:.1f}s")
        except Exception as e:
            print(f"  cache refresh failed (non-fatal): {e}")
    else:
        print("\n(skipping cache refresh per --skip-cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
