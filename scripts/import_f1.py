"""One-off F1 roster import.

Reads data/f1_teams.json (10 constructor channels, channel-ids
verified) + resolves the @Formula1 HQ handle, then inserts/upserts
11 rows into the channels table with:

  - entity_type = "F1"  (the isolation key — every existing exclusion
                          list adds this so top-5 surfaces are safe)
  - country     = "UK"  (the FIA / F1 Group HQ; teams are mixed
                          nationalities so 'UK' is the closest
                          mono-country proxy for v0)
  - competitions.f1 = {role: 'hq'|'team', team, championship: 'F1'}

Fetches initial subs/views/video_count via the YouTube API in the
same pass so the /f1 page is immediately useful without waiting for
the daily cron to run.

Idempotent: re-running upserts on youtube_channel_id (no duplicates,
no schema drift, safe to invoke whenever the roster changes).

Usage:
  python3 scripts/import_f1.py
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


def _resolve_f1_hq(yt_key: str) -> dict | None:
    """Resolve the @Formula1 handle to channel_id + canonical title.
    Mirrors the NFL import — API is the source of truth."""
    url = (f"https://www.googleapis.com/youtube/v3/channels"
           f"?part=snippet&forHandle=%40Formula1&key={yt_key}")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.load(r)
    except Exception as e:
        log(f"FATAL: @Formula1 handle lookup failed: {e}")
        return None
    items = data.get("items") or []
    if not items:
        log("FATAL: no @Formula1 channel found via forHandle")
        return None
    it = items[0]
    return {
        "youtube_channel_id": it["id"],
        "handle": (it["snippet"].get("customUrl") or "@formula1").lstrip("@"),
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

    teams_file = Path(__file__).resolve().parent.parent / "data" / "f1_teams.json"
    teams = json.loads(teams_file.read_text())
    log(f"Loaded {len(teams)} teams from {teams_file.name}")

    f1_hq = _resolve_f1_hq(yt_key)
    if not f1_hq:
        log("FATAL: couldn't resolve @Formula1 HQ — aborting")
        return 1
    log(f"Resolved @Formula1 → {f1_hq['title']} ({f1_hq['youtube_channel_id']})")

    # HQ goes first so it sorts to the top of the /f1 table.
    rows = [{
        "team": f1_hq["title"],
        "team_full": f1_hq["title"],
        "url": f"https://www.youtube.com/@{f1_hq['handle']}",
        "channel_id": f1_hq["youtube_channel_id"],
        "handle": f1_hq["handle"],
        "role": "hq",
    }] + [{**t, "role": "team"} for t in teams]

    log(f"\nImporting {len(rows)} F1 channels (HQ first, then {len(teams)} teams)...")
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
                "country": "UK",
                "entity_type": "F1",
                "competitions": {
                    "f1": {
                        "role": r["role"],
                        "team": r.get("team_full") or r["team"],
                        "championship": "F1",
                    },
                },
                **stats,
            }
            saved = db.upsert_channel(payload)
            db_id = (saved or {}).get("id")
            if db_id:
                db.snapshot_channel(db_id, stats)
            ok += 1
            log(f"  ✓ {name[:32]:<33}  subs={int(stats.get('subscriber_count') or 0):>11,}"
                f"  views={int(stats.get('total_views') or 0):>13,}"
                f"  videos={int(stats.get('video_count') or 0):>5,}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"  ✗ {name}: {e}")

    log(f"\nDone — ok={ok}/{len(rows)} failed={len(failed)}")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
