#!/usr/bin/env python3
"""One-time backfill: populate actual_start_time for existing live videos.

Fetches liveStreamingDetails from YouTube for every video with format='live'
and writes actualStartTime (or scheduledStartTime) back to the DB.

Usage:
    python scripts/backfill_live_start_time.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database

YT_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
SB_URL = os.environ.get("SUPABASE_URL", "").strip()
SB_KEY = os.environ.get("SUPABASE_KEY", "").strip()

if not (YT_KEY and SB_URL and SB_KEY):
    print("Set YOUTUBE_API_KEY, SUPABASE_URL, SUPABASE_KEY in .env")
    sys.exit(1)

from googleapiclient.discovery import build

youtube = build("youtube", "v3", developerKey=YT_KEY)
db = Database(SB_URL, SB_KEY)

# Fetch all live videos from DB
print("Fetching live videos from DB...")
resp = (
    db.client.table("videos")
    .select("id,youtube_video_id,title,actual_start_time")
    .eq("format", "live")
    .execute()
)
live_videos = resp.data or []
print(f"Found {len(live_videos)} live videos total")

# Filter to those without actual_start_time already set
to_backfill = [v for v in live_videos if not v.get("actual_start_time")]
print(f"{len(to_backfill)} need backfill")

if not to_backfill:
    print("Nothing to do!")
    sys.exit(0)

updated = 0
skipped = 0

for i in range(0, len(to_backfill), 50):
    batch = to_backfill[i:i + 50]
    yt_ids = [v["youtube_video_id"] for v in batch]

    resp = youtube.videos().list(
        part="liveStreamingDetails",
        id=",".join(yt_ids),
    ).execute()

    yt_data = {}
    for item in resp.get("items", []):
        live_details = item.get("liveStreamingDetails", {})
        actual = live_details.get("actualStartTime") or live_details.get("scheduledStartTime")
        if actual:
            yt_data[item["id"]] = actual

    for v in batch:
        yt_id = v["youtube_video_id"]
        start_time = yt_data.get(yt_id)
        if start_time:
            db.client.table("videos").update(
                {"actual_start_time": start_time}
            ).eq("id", v["id"]).execute()
            updated += 1
            print(f"  ✓ {v['title'][:60]} → {start_time}")
        else:
            skipped += 1
            print(f"  - {v['title'][:60]} — no liveStreamingDetails")

    print(f"  Batch {i//50 + 1}: processed {len(batch)} videos")
    time.sleep(0.2)  # gentle rate limiting

print(f"\nDone! Updated: {updated}, Skipped (no data): {skipped}")
