#!/usr/bin/env python3
"""One-time backfill for lifetime per-format view aggregates.

Touches ONLY the 3 new columns on the channels table:
  - lifetime_long_views
  - lifetime_short_views
  - lifetime_live_views

Does NOT call YouTube. Does NOT touch any other channel field. Does
NOT modify videos / snapshots / anything else. Pure Supabase read +
write of those three columns.

Run once after migration_v16.sql has been applied. Going forward
daily_refresh.py keeps these in sync (one extra Supabase round-trip
per channel, after the existing per-channel work).

Usage:
    python3 scripts/backfill_lifetime_format_views.py
"""
from __future__ import annotations
import os
import sys
import time
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from src.database import Database  # noqa: E402


def main() -> int:
    sb_url = os.environ["SUPABASE_URL"]
    sb_key = os.environ["SUPABASE_KEY"]
    db = Database(sb_url, sb_key)

    chans = db.get_all_channels()
    print(f"Backfilling {len(chans)} channels…")

    t0 = time.time()
    ok = 0
    failed: list[tuple[str, str]] = []
    for i, ch in enumerate(chans, 1):
        try:
            sums = db.refresh_lifetime_format_views(ch["id"])
            ok += 1
            total = sum(sums.values())
            print(f"[{i:3d}/{len(chans)}] {ch.get('name','?')[:40]:40s}"
                  f"  L={sums['lifetime_long_views']:>12,}"
                  f"  S={sums['lifetime_short_views']:>12,}"
                  f"  V={sums['lifetime_live_views']:>10,}"
                  f"  total={total:>12,}")
        except Exception as e:
            failed.append((ch.get("name", "?"), str(e)[:200]))
            print(f"[{i}/{len(chans)}] {ch.get('name','?')} — ERROR: {e}")

    elapsed = time.time() - t0
    print()
    print(f"Done in {elapsed:.1f}s — ok={ok} failed={len(failed)}")
    if failed:
        print("Failed channels:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
