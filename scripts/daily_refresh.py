#!/usr/bin/env python3
"""Daily refresh — unattended.

For every channel:
  1. Fetch current channel stats and upsert channels table.
  2. Append row to channel_snapshots (daily per-channel counters).
  3. Ingest any NEW season videos published since SEASON_SINCE (keeps the DB
     current without needing a manual refresh in the Streamlit app).
  4. Snapshot every season video's current view/like/comment counts into
     video_snapshots (daily per-video counters → enables velocity charts).

Designed to be called from GitHub Actions. Exits non-zero on fatal errors so
the CI run shows red. Individual channel failures are logged but do not abort
the whole batch.

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
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since
# Per-video snapshots are scoped to a rolling window: only videos published
# in the last SNAPSHOT_WINDOW_DAYS get a daily snapshot. Older videos rarely
# change enough to justify the row cost, and velocity/trending use cases
# care about fresh content anyway.
SNAPSHOT_WINDOW_DAYS = 30


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_KEY", "")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    sb_url = sb_url.strip().strip('"').strip("'")
    sb_key = sb_key.strip().strip('"').strip("'")
    yt_key = yt_key.strip().strip('"').strip("'")

    log(f"Starting daily refresh — url_len={len(sb_url)} key_len={len(sb_key)} yt_len={len(yt_key)}")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    # Players + Federations have their own dedicated crons — skip them here.
    channels = [c for c in channels
                if c.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")]
    total = len(channels)
    log(f"Found {total} channels (Players + Federations excluded — handled by their dedicated crons)")

    ok = 0
    new_videos_total = 0
    video_snapshots_written = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ch in enumerate(channels, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            failed.append((name, "no youtube_channel_id"))
            continue
        try:
            # ── 1. Channel stats + channel snapshot ──
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                failed.append((name, "empty stats response"))
                continue
            row = db.upsert_channel({**ch, **stats})
            channel_db_id = row.get("id") or ch.get("id")
            if channel_db_id:
                db.snapshot_channel(channel_db_id, stats)

            # ── 2. Ingest any NEW season videos ──
            ch_season_since = get_season_since(ch)
            if ch.get("entity_type") != "League" and channel_db_id:
                season = yt.get_video_ids_since_by_format(yt_id, ch_season_since)
                long_ids = season.get("long", [])
                short_ids = season.get("shorts", [])
                live_ids = season.get("live", [])

                seen = set()
                all_ids: list[str] = []
                for vid in long_ids + short_ids + live_ids:
                    if vid not in seen:
                        seen.add(vid)
                        all_ids.append(vid)

                known = db.get_known_video_ids(channel_db_id)
                new_ids = [v for v in all_ids if v not in known]
                if new_ids:
                    long_set, short_set, live_set = set(long_ids), set(short_ids), set(live_ids)
                    new_vids = []
                    for b in range(0, len(new_ids), 50):
                        batch = new_ids[b:b+50]
                        fetched = yt.get_video_details(batch)
                        for v in fetched:
                            vid = v["youtube_video_id"]
                            if vid in live_set:
                                v["format"] = "live"
                            elif vid in short_set:
                                v["format"] = "short"
                            else:
                                v["format"] = "long"
                        new_vids.extend(fetched)
                    if new_vids:
                        db.upsert_videos(new_vids, channel_db_id)
                        new_videos_total += len(new_vids)

            # Refresh precomputed top-100 stats + season_views on the channel
            if channel_db_id:
                try:
                    db.refresh_top100_stats(channel_db_id, ch_season_since)
                except Exception:
                    pass

            ok += 1
            extra = f" (+{len(new_ids) if ch.get('entity_type') != 'League' else 0} new)" if ch.get("entity_type") != "League" else ""
            log(f"[{i}/{total}] {name} — subs={stats.get('subscriber_count', 0):,} videos={stats.get('video_count', 0)}{extra}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"[{i}/{total}] {name} — ERROR: {e}")

    # ── 3. Snapshot recent-window video stats (rolling 30 days) ──
    window_cutoff = (datetime.now(timezone.utc) - timedelta(days=SNAPSHOT_WINDOW_DAYS)).date().isoformat()
    log(f"Snapshotting videos published since {window_cutoff} ({SNAPSHOT_WINDOW_DAYS}d window)...")
    try:
        season_rows = db.get_season_video_rows(window_cutoff)
        log(f"  {len(season_rows)} videos in window")
        if season_rows:
            # Refresh stats for all of them in batches
            id_to_dbid = {r["youtube_video_id"]: r["id"] for r in season_rows}
            yt_ids = list(id_to_dbid.keys())
            snap_rows: list[dict] = []
            for b in range(0, len(yt_ids), 50):
                batch = yt_ids[b:b+50]
                details = yt.get_video_details(batch)
                for v in details:
                    dbid = id_to_dbid.get(v["youtube_video_id"])
                    if not dbid:
                        continue
                    snap_rows.append({
                        "video_id": dbid,
                        "view_count": v.get("view_count", 0),
                        "like_count": v.get("like_count", 0),
                        "comment_count": v.get("comment_count", 0),
                    })
                if b % 500 == 0:
                    log(f"  fetched stats for {min(b+50, len(yt_ids))}/{len(yt_ids)}")
            video_snapshots_written = db.snapshot_videos_batch(snap_rows)
            log(f"  wrote {video_snapshots_written} video_snapshots rows")

            # Pre-compute per-video daily deltas — powers the Daily Recap
            # "Most watched" section without scanning raw snapshots at page load.
            try:
                from datetime import date as _date
                _today_iso = _date.today().isoformat()
                deltas_written = db.compute_video_daily_deltas(_today_iso)
                log(f"  wrote {deltas_written} video_daily_deltas rows for {_today_iso}")
            except Exception as e:
                log(f"  video_daily_deltas step skipped: {e}")

            # ALSO update the videos table so Streamlit sees fresh view counts
            # (upsert view/like/comment on existing rows; keeps title/thumbnail too)
            try:
                update_rows = []
                now = datetime.now(timezone.utc).isoformat()
                for v in snap_rows:
                    update_rows.append({
                        "id": v["video_id"],
                        "view_count": v["view_count"],
                        "like_count": v["like_count"],
                        "comment_count": v["comment_count"],
                    })
                for b in range(0, len(update_rows), 500):
                    db.client.table("videos").upsert(update_rows[b:b+500], on_conflict="id").execute()
            except Exception as e:
                log(f"  videos table freshness update skipped: {e}")
    except Exception as e:
        log(f"season snapshot step failed: {e}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — channels_ok={ok} failed={len(failed)} new_videos={new_videos_total} video_snapshots={video_snapshots_written}")

    try:
        if failed:
            err_summary = "; ".join(f"{n}: {e}" for n, e in failed[:10])
            db.log_fetch(ok, new_videos_total, "daily_snapshot_partial", f"{len(failed)} failures: {err_summary}")
        else:
            db.log_fetch(
                ok, new_videos_total, "daily_snapshot",
                f"{ok} channels, {new_videos_total} new videos, {video_snapshots_written} video snapshots in {elapsed:.1f}s",
            )
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    if failed:
        log("Failures:")
        for n, e in failed:
            log(f"  - {n}: {e}")
        return 1 if ok == 0 else 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
