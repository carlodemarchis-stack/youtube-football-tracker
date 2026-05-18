#!/usr/bin/env python3
"""Data-freshness heartbeat — independent staleness alarm.

The per-cron `send_run_alert` only fires when a script actually runs.
The failure mode we hit before (Railway usage limit) *blocks the cron
from starting at all* — so no alert fires and the data silently goes
stale, exactly when a launch has the most eyes on it.

This is a tiny, standalone monitor (2 cheap indexed queries, no
YouTube quota) meant to run on its own lightweight schedule, separate
from every data cron, so it can shout even when they're dead:

  - channel_snapshots: the daily pipeline stamps captured_date = run
    day (UTC) for every channel. Newest should be today or yesterday;
    ≥2 days behind ⇒ the daily pipeline is down.
  - videos.last_updated: refreshed by the daily/weekly video passes;
    ≥3 days behind ⇒ the video pipeline is stale.

Fires one ntfy alert (reusing src.notify) and exits non-zero when
stale, so the run log shows red too.

Env: SUPABASE_URL, SUPABASE_KEY  (+ NTFY_TOPIC for the push).
Run:  python3 scripts/heartbeat.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defensive .env load — same pattern as the other scripts.
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

SNAP_MAX_AGE_DAYS = 2   # channel_snapshots: today/yesterday OK, ≥2 = down
VIDEO_MAX_AGE_DAYS = 3   # videos.last_updated tolerance


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _newest(db, table: str, col: str) -> str | None:
    try:
        r = (db.client.table(table).select(col)
             .order(col, desc=True).limit(1).execute().data or [])
        return r[0][col] if r else None
    except Exception as e:
        log(f"query {table}.{col} failed: {e}")
        return None


def main() -> int:
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip().strip('"').strip("'")
              or os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'"))
    if not (sb_url and sb_key):
        log("FATAL: missing SUPABASE_URL / SUPABASE_KEY")
        return 2

    db = Database(sb_url, sb_key)
    today = datetime.now(timezone.utc).date()

    problems: list[str] = []
    info: list[str] = []

    snap = _newest(db, "channel_snapshots", "captured_date")
    if snap:
        try:
            age = (today - date.fromisoformat(str(snap)[:10])).days
        except Exception:
            age = 999
        info.append(f"channel_snapshots newest={snap} ({age}d old)")
        if age >= SNAP_MAX_AGE_DAYS:
            problems.append(
                f"channel_snapshots STALE — newest {snap}, {age} days "
                f"behind (daily pipeline likely not running)")
    else:
        problems.append("channel_snapshots — no rows / query failed")

    vid = _newest(db, "videos", "last_updated")
    if vid:
        try:
            vdt = datetime.fromisoformat(str(vid).replace("Z", "+00:00"))
            if vdt.tzinfo is None:
                vdt = vdt.replace(tzinfo=timezone.utc)
            vage = (datetime.now(timezone.utc) - vdt).days
        except Exception:
            vage = 999
        info.append(f"videos.last_updated newest={str(vid)[:19]} ({vage}d old)")
        if vage >= VIDEO_MAX_AGE_DAYS:
            problems.append(
                f"videos STALE — last_updated {str(vid)[:10]}, {vage} days "
                f"behind (video pipeline stale)")
    else:
        info.append("videos.last_updated — unavailable (skipped)")

    ok = not problems
    summary = " · ".join(info) if info else "no signals read"
    err = " | ".join(problems)
    log(("OK — " if ok else "STALE — ") + summary
        + (f" | {err}" if err else ""))

    try:
        from src.notify import send_run_alert
        send_run_alert(
            "heartbeat",
            ok=ok,
            summary=summary,
            error=err,
            priority=("high" if not ok else None),
        )
    except Exception as e:
        log(f"ntfy alert failed (non-fatal): {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
