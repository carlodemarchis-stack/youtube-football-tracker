"""One-shot refresh of the wc2026 dashboard cache.

The cache row is also rebuilt by every rebuild_all() pass (which the
daily / hourly crons trigger). Run this script when you want to
populate the cache *now* — e.g. after editing the WC 2026 channel
roster manually, swapping a YouTube ID, or right after deploying.

Usage:
  python3 scripts/refresh_wc2026_cache.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database
from src.dashboard_cache import refresh_wc2026, refresh_wc2026_trends


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get(
        "SUPABASE_KEY", "")
    if not (url and key):
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY not set",
              file=sys.stderr)
        return 1
    db = Database(url, key)
    chans = db.get_all_channels()
    refresh_wc2026(db, channels=chans)
    refresh_wc2026_trends(db, channels=chans)
    return 0


if __name__ == "__main__":
    sys.exit(main())
