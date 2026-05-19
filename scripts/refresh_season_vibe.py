"""One-shot refresh of the Season AI notes (season_vibe +
season_top_vibe) in dashboard_cache.

Both notes are also rebuilt every nightly rebuild_all() pass (via
the daily refresh cron). Run this when you want to populate them
*now* — e.g. right after deploying, instead of waiting for the next
nightly run. season_top_vibe reads the existing season_top cache
row (refreshed nightly), so no extra video query here.

Needs ANTHROPIC_API_KEY + SUPABASE creds in the environment. Locally
those aren't in .env, so run it with a Railway service's env:

  railway run --service "Daily update" python3 scripts/refresh_season_vibe.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database
from src.dashboard_cache import refresh_season_vibe, refresh_season_top_vibe


def main() -> int:
    url = os.environ.get("SUPABASE_URL", "")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "")
           or os.environ.get("SUPABASE_KEY", ""))
    if not (url and key):
        print("ERROR: SUPABASE_URL / SUPABASE_KEY not set", file=sys.stderr)
        return 1
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set (run via railway run)",
              file=sys.stderr)
        return 1
    db = Database(url, key)
    refresh_season_vibe(db)
    refresh_season_top_vibe(db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
