#!/usr/bin/env python3
"""One-time backfill: precompute top-100 stats for all channels."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.channels import get_season_since


def main():
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = os.environ.get("SUPABASE_KEY", "").strip()
    if not (sb_url and sb_key):
        print("Missing SUPABASE_URL / SUPABASE_KEY")
        return 1

    db = Database(sb_url, sb_key)
    channels = db.get_all_channels()
    print(f"Backfilling top-100 stats for {len(channels)} channels...")

    ok = 0
    for i, ch in enumerate(channels, 1):
        try:
            db.refresh_top100_stats(ch["id"], get_season_since(ch))
            print(f"  [{i}/{len(channels)}] {ch['name']} OK")
            ok += 1
        except Exception as e:
            print(f"  [{i}/{len(channels)}] {ch['name']} FAILED: {e}")
        if i % 10 == 0:
            time.sleep(0.5)

    print(f"Done: {ok}/{len(channels)} channels updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
