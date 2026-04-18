#!/usr/bin/env python3
"""Single always-on scheduler that runs all cron jobs internally.

Replaces Railway's cron mechanism (which requires an active deployment
and is unreliable for run-and-exit scripts). This container stays alive
and triggers jobs at the right times using simple sleep loops.

Schedule (UTC):
  - Hourly RSS:   every hour from 05:03 to 22:03
  - Daily refresh: 03:30 every day
  - Weekly refresh: 04:00 every Monday

Env vars required:
    YOUTUBE_API_KEY
    SUPABASE_URL
    SUPABASE_KEY
"""
from __future__ import annotations

import os
import sys
import subprocess
import time
import threading
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[scheduler {ts}] {msg}", flush=True)


def run_script(name: str) -> None:
    """Run a script in a subprocess, streaming its output."""
    path = os.path.join(SCRIPTS_DIR, name)
    log(f"▶ Starting {name}")
    try:
        result = subprocess.run(
            [sys.executable, path],
            capture_output=False,  # let output stream to stdout
            timeout=3600,  # 1h max per script
        )
        if result.returncode == 0:
            log(f"✓ {name} finished OK")
        else:
            log(f"✗ {name} exited with code {result.returncode}")
    except subprocess.TimeoutExpired:
        log(f"✗ {name} timed out after 1h")
    except Exception as e:
        log(f"✗ {name} error: {e}")


def should_run_hourly(hour: int) -> bool:
    """Hourly RSS runs from 05:xx to 22:xx UTC."""
    return 5 <= hour <= 22


def main() -> None:
    log("Scheduler started. Waiting for jobs...")

    # Track what we've already run this cycle to avoid double-firing
    last_hourly_hour = -1
    last_daily_day = -1
    last_weekly_isoweek = (-1, -1)

    while True:
        now = datetime.now(timezone.utc)
        hour = now.hour
        minute = now.minute
        weekday = now.weekday()  # 0=Monday
        day = now.day
        iso_cal = now.isocalendar()  # (year, week, weekday)

        # ── Hourly RSS: at minute 3, hours 5-22 ───────────────
        if minute >= 3 and should_run_hourly(hour) and last_hourly_hour != hour:
            last_hourly_hour = hour
            threading.Thread(
                target=run_script, args=("hourly_rss.py",), daemon=True
            ).start()

        # ── Daily refresh: at 03:30 ───────────────────────────
        if hour == 3 and minute >= 30 and last_daily_day != day:
            last_daily_day = day
            threading.Thread(
                target=run_script, args=("daily_refresh.py",), daemon=True
            ).start()

        # ── Weekly refresh: Monday at 04:00 ───────────────────
        if weekday == 0 and hour == 4 and minute >= 0:
            week_key = (iso_cal[0], iso_cal[1])
            if last_weekly_isoweek != week_key:
                last_weekly_isoweek = week_key
                threading.Thread(
                    target=run_script, args=("weekly_refresh.py",), daemon=True
                ).start()

        # Sleep 30s between checks — precise enough, low overhead
        time.sleep(30)


if __name__ == "__main__":
    log("═" * 50)
    log("Football YouTube Tracker — Scheduler")
    log(f"Python {sys.version}")
    log(f"Scripts dir: {SCRIPTS_DIR}")

    # Verify env vars
    for var in ("YOUTUBE_API_KEY", "SUPABASE_URL", "SUPABASE_KEY"):
        val = os.environ.get(var, "")
        status = "✓" if val else "✗ MISSING"
        log(f"  {var}: {status}")

    log("═" * 50)

    # Run hourly RSS immediately on startup (so redeploy = manual run)
    log("Running hourly_rss.py on startup...")
    run_script("hourly_rss.py")

    main()
