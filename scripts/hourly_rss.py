#!/usr/bin/env python3
"""Hourly lightweight new-video check via YouTube RSS feeds.

For every channel:
  1. Fetch the free RSS feed (no API quota).
  2. Compare video IDs against what's already in the DB.
  3. For genuinely new videos only, call videos.list to get full details
     (stats, duration, liveStreamingDetails) — typically 0-2 API calls total.

This does NOT update channel stats or snapshots — that stays in daily_refresh.py.
It only makes new videos appear in the app within ~1 hour instead of ~24h.

Env vars required:
    YOUTUBE_API_KEY
    SUPABASE_URL
    SUPABASE_KEY
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import gzip
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
RSS_TIMEOUT = 15  # seconds per feed

# Realistic browser UA — bare "Mozilla/5.0" triggers YouTube anti-bot 404
_UA_PRIMARY = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_UA_FALLBACK = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_RSS_HEADERS = {
    "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Connection": "close",
}

# Namespaces in YouTube RSS
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _fetch_rss_raw(url: str, ua: str) -> bytes | None:
    """One HTTP attempt with a given UA. Returns body or None on failure."""
    headers = {"User-Agent": ua, **_RSS_HEADERS}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=RSS_TIMEOUT) as resp:
        body = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            try:
                body = gzip.decompress(body)
            except Exception:
                pass
        return body


def fetch_rss_video_ids(youtube_channel_id: str) -> list[dict]:
    """Fetch recent video IDs + published dates from a channel's RSS feed.
    Returns list of {"video_id": str, "published": str, "title": str}.
    Free — no API quota used. Retries once with a different UA on 404/403."""
    url = RSS_URL.format(youtube_channel_id)
    xml_data: bytes | None = None
    last_err = ""
    for attempt, ua in enumerate((_UA_PRIMARY, _UA_FALLBACK), 1):
        try:
            xml_data = _fetch_rss_raw(url, ua)
            break
        except HTTPError as e:
            last_err = f"HTTP {e.code}"
            # 404/403 may be anti-bot — retry once with fallback UA
            if e.code in (403, 404) and attempt == 1:
                time.sleep(0.5)
                continue
            break
        except (URLError, TimeoutError) as e:
            last_err = str(e)
            break
    if xml_data is None:
        log(f"  RSS fetch failed for {youtube_channel_id}: {last_err}")
        return []

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return []

    entries = []
    for entry in root.findall("atom:entry", NS):
        vid_el = entry.find("yt:videoId", NS)
        pub_el = entry.find("atom:published", NS)
        title_el = entry.find("atom:title", NS)
        if vid_el is not None and vid_el.text:
            entries.append({
                "video_id": vid_el.text,
                "published": pub_el.text if pub_el is not None else "",
                "title": title_el.text if title_el is not None else "",
            })
    return entries


def main() -> int:
    yt_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = os.environ.get("SUPABASE_KEY", "").strip()

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    total = len(channels)
    log(f"Hourly RSS check — {total} channels")

    # Build a quick lookup: youtube_channel_id → db channel record
    ch_by_yt_id: dict[str, dict] = {}
    for ch in channels:
        yt_id = ch.get("youtube_channel_id")
        if yt_id and ch.get("entity_type") != "League":
            ch_by_yt_id[yt_id] = ch

    # Collect all new video IDs across all channels
    all_new: list[tuple[str, str]] = []  # (youtube_video_id, channel_db_id)
    rss_ok = 0
    rss_fail = 0
    api_fallback_ok = 0
    api_fallback_fail = 0
    start = time.time()

    for i, (yt_id, ch) in enumerate(ch_by_yt_id.items(), 1):
        channel_db_id = ch["id"]
        rss_entries = fetch_rss_video_ids(yt_id)
        if not rss_entries:
            rss_fail += 1
            # Fallback: use YouTube Data API playlistItems (1 quota unit)
            # Uploads playlist ID = channel ID with UC → UU prefix
            uploads_pl_id = "UU" + yt_id[2:] if yt_id.startswith("UC") else None
            if uploads_pl_id:
                rss_entries = yt.get_recent_video_entries(uploads_pl_id, max_results=20)
                if rss_entries:
                    api_fallback_ok += 1
                else:
                    api_fallback_fail += 1
            if not rss_entries:
                continue
        else:
            rss_ok += 1

        # Filter to season videos only
        ch_season_since = get_season_since(ch)
        rss_season = [
            e for e in rss_entries
            if e["published"] >= ch_season_since
        ]
        if not rss_season:
            continue

        # Check which are new
        rss_video_ids = [e["video_id"] for e in rss_season]
        known = db.get_known_video_ids(channel_db_id)
        new_ids = [vid for vid in rss_video_ids if vid not in known]

        if new_ids:
            log(f"  [{i}/{len(ch_by_yt_id)}] {ch['name']}: {len(new_ids)} new video(s)")
            for vid in new_ids:
                all_new.append((vid, channel_db_id))

        # Pause between requests to avoid YouTube rate-limiting
        time.sleep(0.3)

    rss_elapsed = time.time() - start
    log(
        f"RSS scan done in {rss_elapsed:.1f}s — "
        f"rss_ok={rss_ok} rss_fail={rss_fail} "
        f"api_fallback_ok={api_fallback_ok} api_fallback_fail={api_fallback_fail} "
        f"new_videos={len(all_new)}"
    )

    if not all_new:
        log("No new videos found. Done.")
        return 0

    # ── Fetch full details for new videos via API ────────────────
    # Group by channel for format detection and upsert
    by_channel: dict[str, list[str]] = {}
    for vid, ch_id in all_new:
        by_channel.setdefault(ch_id, []).append(vid)

    total_inserted = 0
    for ch_id, new_ids in by_channel.items():
        ch = next((c for c in channels if c["id"] == ch_id), None)
        if not ch:
            continue
        yt_ch_id = ch["youtube_channel_id"]

        # Detect format via playlist membership (same as daily_refresh)
        # Only check the playlists for these specific IDs
        try:
            season = yt.get_video_ids_since_by_format(yt_ch_id, get_season_since(ch))
            long_set = set(season.get("long", []))
            short_set = set(season.get("shorts", []))
            live_set = set(season.get("live", []))
        except Exception as e:
            log(f"  Playlist check failed for {ch['name']}: {e}")
            long_set = short_set = live_set = set()

        # Fetch full video details
        fetched = yt.get_video_details(new_ids)
        for v in fetched:
            vid = v["youtube_video_id"]
            if vid in live_set:
                v["format"] = "live"
            elif vid in short_set:
                v["format"] = "short"
            else:
                v["format"] = "long"

        if fetched:
            db.upsert_videos(fetched, ch_id)
            total_inserted += len(fetched)
            try:
                db.refresh_top100_stats(ch_id, get_season_since(ch))
            except Exception:
                pass
            log(f"  Inserted {len(fetched)} videos for {ch['name']}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — {total_inserted} new videos inserted")

    # Log to fetch_log
    try:
        db.log_fetch(
            len(by_channel), total_inserted, "hourly_rss",
            f"{rss_ok} RSS feeds, {total_inserted} new videos in {elapsed:.1f}s",
        )
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
