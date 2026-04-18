"""Backfill category/theme for all existing videos using detect_theme()."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.analytics import detect_theme

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    print("Set SUPABASE_URL and SUPABASE_KEY in .env")
    sys.exit(1)

db = Database(SUPABASE_URL, SUPABASE_KEY)

# Fetch all videos
print("Fetching all videos...")
all_rows: list[dict] = []
page_size = 1000
offset = 0
while True:
    resp = (
        db.client.table("videos")
        .select("id,title,duration_seconds,format,category")
        .range(offset, offset + page_size - 1)
        .execute()
    )
    batch = resp.data or []
    all_rows.extend(batch)
    if len(batch) < page_size:
        break
    offset += page_size

print(f"Found {len(all_rows)} videos. Classifying...")

# Classify and find videos that need updating
updates: list[dict] = []
for v in all_rows:
    new_cat = detect_theme(
        v.get("title", ""),
        v.get("duration_seconds"),
        v.get("format"),
    )
    old_cat = v.get("category") or ""
    if new_cat != old_cat:
        updates.append({"id": v["id"], "category": new_cat})

print(f"{len(updates)} videos need category update.")

# Batch update
for i in range(0, len(updates), 50):
    batch = updates[i : i + 50]
    for row in batch:
        db.client.table("videos").update(
            {"category": row["category"]}
        ).eq("id", row["id"]).execute()
    done = min(i + 50, len(updates))
    print(f"  Updated {done}/{len(updates)}")

print("Done!")
