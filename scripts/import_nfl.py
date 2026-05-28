"""One-off NFL roster import.

Reads data/nfl_teams.json (32 franchises, channel-ids verified) +
resolves the @NFL HQ handle, then inserts/upserts 33 rows into the
channels table with:

  - entity_type = "NFL"  (the isolation key — every existing exclusion
                           list adds this so top-5 surfaces are safe)
  - country     = "US"
  - competitions.nfl = {conference, division}  (stored for future
                           grouping; v0 doesn't render it)

Fetches initial subs/views/video_count via the YouTube API in the same
pass so the /nfl page is immediately useful without waiting for the
daily cron to run.

Idempotent: re-running upserts on youtube_channel_id (no duplicates,
no schema drift, safe to invoke whenever the roster changes).

Usage:
  python3 scripts/import_nfl.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient


def log(msg: str) -> None:
    print(msg, flush=True)


def _resolve_nfl_hq(yt_key: str) -> dict | None:
    """Resolve the @NFL handle to channel_id + canonical title. We
    don't trust hard-coded ids here — the API is the source of truth."""
    url = (f"https://www.googleapis.com/youtube/v3/channels"
           f"?part=snippet&forHandle=%40NFL&key={yt_key}")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
    except Exception as e:
        log(f"FATAL: @NFL handle lookup failed: {e}")
        return None
    items = data.get("items") or []
    if not items:
        log("FATAL: no @NFL channel found via forHandle")
        return None
    it = items[0]
    return {
        "youtube_channel_id": it["id"],
        "handle": (it["snippet"].get("customUrl") or "@nfl").lstrip("@"),
        "title": it["snippet"]["title"],
    }


def main() -> int:
    yt_key = (os.environ.get("YOUTUBE_API_KEY_INTERACTIVE", "").strip()
              or os.environ.get("YOUTUBE_API_KEY", "").strip())
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_(SERVICE_)KEY")
        return 1
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    teams_file = Path(__file__).resolve().parent.parent / "data" / "nfl_teams.json"
    teams = json.loads(teams_file.read_text())
    log(f"Loaded {len(teams)} teams from {teams_file.name}")

    nfl_hq = _resolve_nfl_hq(yt_key)
    if not nfl_hq:
        log("FATAL: couldn't resolve @NFL HQ — aborting")
        return 1
    log(f"Resolved @NFL → {nfl_hq['title']} ({nfl_hq['youtube_channel_id']})")

    # HQ goes first so it sorts to the top of the /nfl table.
    rows = [{
        "team": nfl_hq["title"],
        "url": f"https://www.youtube.com/@{nfl_hq['handle']}",
        "channel_id": nfl_hq["youtube_channel_id"],
        "handle": nfl_hq["handle"],
        "conference": "—",
        "division": "—",
    }] + teams

    log(f"\nImporting {len(rows)} NFL channels (HQ first, then 32 teams)...")
    ok = 0
    failed: list[tuple[str, str]] = []
    for r in rows:
        name = r["team"]
        yt_id = r["channel_id"]
        try:
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                failed.append((name, "empty stats response"))
                continue
            payload = {
                "youtube_channel_id": yt_id,
                "name": name,
                "handle": r.get("handle"),
                "country": "US",
                "entity_type": "NFL",
                "competitions": {
                    "nfl": {
                        "conference": r.get("conference"),
                        "division": r.get("division"),
                    },
                },
                **stats,
            }
            saved = db.upsert_channel(payload)
            db_id = (saved or {}).get("id")
            if db_id:
                db.snapshot_channel(db_id, stats)
            ok += 1
            log(f"  ✓ {name[:30]:<31}  subs={int(stats.get('subscriber_count') or 0):>11,}"
                f"  views={int(stats.get('total_views') or 0):>13,}"
                f"  videos={int(stats.get('video_count') or 0):>5,}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"  ✗ {name}: {e}")

    log(f"\nDone — ok={ok}/{len(rows)} failed={len(failed)}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
