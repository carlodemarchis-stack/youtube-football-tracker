"""Backfill missing channel_snapshots rows for WC2026 channels.

Different from scripts/interpolate_frozen_runs.py — that one is for
the YouTube-counter-frozen pattern (consecutive identical totals).
This one handles the case the user actually hit: the daily_wc2026
cron on 2026-06-03 stopped mid-pass at ok=35/62 (no error logged),
so 23 WC2026 channels have NO row for that date at all.

Without a row, the WC2026 Trends page's cohort union logic still
finds the channel via neighbouring days, but per-day-pair chart
deltas drop on the gap day and the 'last day with data' caption can
read wrong.

What this script does
=====================
For each WC2026 channel with no `channel_snapshots` row on the
target date BUT rows on both the day before AND the day after, it
inserts a midpoint snapshot:

    new_row[field] = round((prev[field] + next[field]) / 2)

for every numeric column (subscriber_count, total_views,
video_count, long_form_count, shorts_count, live_count). Stamped
with the target captured_date.

Idempotent: if a row already exists for that (channel_id,
captured_date), Postgres's PK rejects the insert — script counts it
as 'already present' and continues.

Usage
=====
    # Dry run (no writes) for 2026-06-03
    python scripts/backfill_wc2026_missing_snapshot.py 2026-06-03 --dry-run

    # Live
    python scripts/backfill_wc2026_missing_snapshot.py 2026-06-03

Env: SUPABASE_URL, SUPABASE_(SERVICE_)KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database, _fetch_all


_FIELDS = (
    "subscriber_count", "total_views", "video_count",
    "long_form_count", "shorts_count", "live_count",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target_date",
                    help="ISO date of the gap day to interpolate (e.g. 2026-06-03)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't write — just report what would happen.")
    args = ap.parse_args()

    try:
        target = date.fromisoformat(args.target_date)
    except ValueError:
        print(f"FATAL: bad date {args.target_date!r}")
        return 2
    prev_d = (target - timedelta(days=1)).isoformat()
    next_d = (target + timedelta(days=1)).isoformat()
    target_d = target.isoformat()

    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (sb_url and sb_key):
        print("FATAL: missing SUPABASE_URL / SUPABASE_KEY")
        return 2

    db = Database(sb_url, sb_key)

    # WC2026 channel set
    chans = db.client.table("channels").select(
        "id,name,competitions").execute().data or []
    wc = [c for c in chans
          if (c.get("competitions") or {}).get("wc2026")]
    wc_ids = [c["id"] for c in wc]
    name_by_id = {c["id"]: c["name"] for c in wc}
    print(f"WC2026 channels: {len(wc)}")
    print(f"Gap day:     {target_d}")
    print(f"Neighbours:  {prev_d}  <-  ->  {next_d}\n")

    # Pull the three days' snapshots in one go
    rows = _fetch_all(
        db.client.table("channel_snapshots")
        .select("channel_id,captured_date,"
                + ",".join(_FIELDS))
        .in_("channel_id", wc_ids)
        .in_("captured_date", [prev_d, target_d, next_d]))

    snap_by: dict[tuple[str, str], dict] = {}
    for r in rows:
        snap_by[(r["channel_id"], r["captured_date"])] = r

    have_target = sum(1 for c in wc if (c["id"], target_d) in snap_by)
    have_prev   = sum(1 for c in wc if (c["id"], prev_d)   in snap_by)
    have_next   = sum(1 for c in wc if (c["id"], next_d)   in snap_by)
    print(f"Coverage: prev={have_prev}  target={have_target}  next={have_next}\n")

    candidates: list[dict] = []
    skip_already = 0
    skip_no_prev = 0
    skip_no_next = 0
    for c in wc:
        cid = c["id"]
        if (cid, target_d) in snap_by:
            skip_already += 1
            continue
        prev = snap_by.get((cid, prev_d))
        nxt  = snap_by.get((cid, next_d))
        if not prev:
            skip_no_prev += 1
            print(f"  skip {name_by_id.get(cid, cid)[:36]:36s}  "
                  f"no prev-day snapshot")
            continue
        if not nxt:
            skip_no_next += 1
            print(f"  skip {name_by_id.get(cid, cid)[:36]:36s}  "
                  f"no next-day snapshot")
            continue
        row: dict = {"channel_id": cid, "captured_date": target_d}
        for f in _FIELDS:
            a = int(prev.get(f) or 0)
            b = int(nxt.get(f) or 0)
            row[f] = round((a + b) / 2)
        candidates.append(row)

    print(f"\nWould insert {len(candidates)} interpolated row(s) for "
          f"{target_d}")
    print(f"  already had a row:    {skip_already}")
    print(f"  skipped (no prev):    {skip_no_prev}")
    print(f"  skipped (no next):    {skip_no_next}")

    if not candidates:
        print("\nNothing to do.")
        return 0

    if args.dry_run:
        print("\n--dry-run — no rows written. Sample:")
        for r in candidates[:5]:
            print(f"  {name_by_id.get(r['channel_id'], r['channel_id'])[:36]:36s}"
                  f"  subs={r['subscriber_count']:>10,}"
                  f"  views={r['total_views']:>13,}"
                  f"  videos={r['video_count']:>5,}")
        return 0

    # Live insert. Idempotent via composite PK (channel_id, captured_date).
    written = 0
    for i in range(0, len(candidates), 200):
        batch = candidates[i:i + 200]
        try:
            r = (db.client.table("channel_snapshots")
                 .upsert(batch, on_conflict="channel_id,captured_date")
                 .execute())
            written += len(r.data or [])
        except Exception as e:
            print(f"  batch {i}-{i+200} upsert failed: {e}")
    print(f"\nWrote {written} interpolated channel_snapshots rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
