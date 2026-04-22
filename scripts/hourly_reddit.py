#!/usr/bin/env python3
"""Hourly Reddit scan — isolated from YouTube crons.

Uses Reddit's public .json endpoints — no OAuth credentials required.
Anonymous rate limit (~60 req/min) accommodates our ~34 req/hour easily.

For every tracked subreddit:
  1. Fetch subreddit stats (subscribers, active_users, description) — 1 call
  2. Fetch the latest 25 posts — 1 call
  3. Upsert posts into reddit_posts
  4. Upsert a daily snapshot to reddit_snapshots

Env vars required:
    SUPABASE_URL
    SUPABASE_KEY
    REDDIT_USER_AGENT  (optional — defaults to a sensible UA)
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env defensively (dotenv has a frame-inspection bug on some setups)
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"\''))

from src.reddit_api import RedditClient, last_error
from src.reddit_db import (
    get_tracked_subreddits, upsert_subreddit_stats, upsert_posts, add_snapshot,
)


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    for v in ("SUPABASE_URL", "SUPABASE_KEY"):
        if not os.environ.get(v):
            log(f"FATAL: missing env var {v}")
            return 2

    client = RedditClient()
    subreddits = get_tracked_subreddits()
    log(f"Hourly Reddit scan — {len(subreddits)} subreddit(s)")

    if not subreddits:
        log("No subreddits tracked. Add rows to reddit_subreddits to start.")
        return 0

    ok = 0
    fail = 0
    posts_upserted = 0
    start = time.time()

    # Snapshot threshold: if we haven't snapshotted today yet, do it
    today = date.today().isoformat()

    for i, row in enumerate(subreddits, 1):
        name = row["subreddit"]
        sub_id = row["id"]
        try:
            info = client.get_subreddit_info(name)
            if not info:
                log(f"  [{i}/{len(subreddits)}] r/{name}: {last_error() or 'unknown error'}")
                fail += 1
                continue
            upsert_subreddit_stats(name, info)

            posts = client.get_recent_posts(name, limit=25)
            inserted = upsert_posts(sub_id, posts)
            posts_upserted += inserted

            # Count last-24h activity for snapshot
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            posts_24h = sum(1 for p in posts if p.created_at >= since)
            comments_24h = sum(p.num_comments for p in posts if p.created_at >= since)

            # Daily snapshot — upsert handles duplicate safely
            add_snapshot(sub_id, info.subscribers, info.active_users,
                         posts_24h, comments_24h)

            log(f"  [{i}/{len(subreddits)}] r/{name}: {info.subscribers:,} subs, "
                f"{info.active_users:,} active, {inserted} posts upserted")
            ok += 1
        except Exception as e:
            log(f"  [{i}/{len(subreddits)}] r/{name}: ERROR {e}")
            traceback.print_exc()
            fail += 1

        # Gentle rate-limit: ~30 subreddits × 2 calls each = 60 calls, well
        # under Reddit's 100/min limit. Sleep 0.2s = 10s total padding.
        time.sleep(0.2)

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — ok={ok} fail={fail} posts_upserted={posts_upserted}")

    # Optional: log to fetch_history so Data page can show last Reddit run time
    try:
        from src.database import Database
        db = Database(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        db.log_fetch(
            ok, posts_upserted, "hourly_reddit",
            f"{ok} subreddits, {posts_upserted} posts in {elapsed:.1f}s",
        )
    except Exception:
        pass

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
