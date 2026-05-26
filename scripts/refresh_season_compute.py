"""One-shot refresh of the season pre-compute caches.

Writes the three Season-level caches (season_one_hits,
season_videos_per_day, season_heatmap) — see src/season_compute.py.

Usage:
  python3 scripts/refresh_season_compute.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database
from src.season_compute import refresh as refresh_season


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get(
        "SUPABASE_KEY", "")
    if not (url and key):
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY not set",
              file=sys.stderr)
        return 1
    db = Database(url, key)
    refresh_season(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
