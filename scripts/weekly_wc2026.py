#!/usr/bin/env python3
"""Weekly WC2026 maintenance — unattended.

Two jobs the daily cron doesn't cover:

  1. **Popular-IDs re-fetch** — re-runs the UULP / UUPS / UUPV pull
     per channel so new videos that climbed into YouTube's
     popularity playlists since last run enter our pool. Without
     this the Top Videos page freezes whatever was popular on
     2026-05-29 (the one-off backfill date).

  2. **Stat refresh on EVERY WC2026 video in the videos table** —
     daily_wc2026 only snapshots videos published since
     WC_TRACK_START (2026-05-14). The ~25k historical popular
     videos ingested by the backfill never get a view-count refresh.
     Weekly we batch them all through videos.list so their counts
     don't drift.

Then per-channel aggregate recompute (refresh_top100_stats +
refresh_lifetime_format_views) to keep summary columns honest.

Budget (62 channels, ~25k historical videos):
  - Popular re-fetch: ~1.5k API units
  - Stat refresh:     ~500 units (25k / 50)
  - Aggregates:       0 units (pure Supabase)
  Total:              ~2k units/week

Env vars: SUPABASE_URL, SUPABASE_KEY, YOUTUBE_API_KEY (or
         YOUTUBE_API_KEY_HEAVY preferred when set).
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient
from src.analytics import classify_videos


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _refresh_popular_ids(yt, db, channels) -> tuple[int, int, int]:
    """Job 1 — re-fetch UULP/UUPS/UUPV per channel.
    Returns (ok_count, videos_saved, live_ids_discovered)."""
    ok = 0
    saved = 0
    live_seen = 0
    for i, ch in enumerate(channels, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        ch_db_id = ch.get("id")
        if not yt_id or not ch_db_id:
            continue
        try:
            pop = yt.get_popular_video_ids(yt_id)
            long_ids  = pop.get("long")  or []
            short_ids = pop.get("shorts") or []
            live_ids  = pop.get("live")  or []
            # One retry on suspicious all-empty (matches the backfill
            # script — catches transient DNS / quota blips).
            if (not long_ids and not short_ids and not live_ids
                    and int(ch.get("video_count") or 0) > 0):
                time.sleep(3)
                pop = yt.get_popular_video_ids(yt_id)
                long_ids  = pop.get("long")  or []
                short_ids = pop.get("shorts") or []
                live_ids  = pop.get("live")  or []
            live_seen += len(live_ids)
            all_ids = long_ids + short_ids + live_ids
            if not all_ids:
                ok += 1
                continue
            live_set  = set(live_ids)
            short_set = set(short_ids)
            videos: list[dict] = []
            for b in range(0, len(all_ids), 50):
                details = yt.get_video_details(all_ids[b:b + 50])
                for v in details:
                    vid = v.get("youtube_video_id", "")
                    if vid in live_set:
                        v["format"] = "live"
                    elif vid in short_set:
                        v["format"] = "short"
                    else:
                        v["format"] = "long"
                videos.extend(details)
            if videos:
                db.upsert_videos(classify_videos(videos), ch_db_id)
                saved += len(videos)
            ok += 1
            if i % 10 == 0:
                log(f"  popular re-fetch {i}/{len(channels)} — running "
                    f"total: {saved} videos saved")
        except Exception as e:
            log(f"  popular re-fetch failed for {name}: {e}")
    return ok, saved, live_seen


def _refresh_video_stats(yt, db, channel_ids) -> tuple[int, int]:
    """Job 2 — re-stat every WC2026 video already in the DB.
    Returns (refreshed_count, total_in_scope)."""
    if not channel_ids:
        return 0, 0
    # Keyset-paginate by id — NOT _fetch_all's offset paging, which on this
    # WC2026-wide result (tens of thousands of rows across 62 high-volume
    # channels) made a deep-offset page exceed the Postgres statement
    # timeout (error 57014) and killed the whole weekly run. Each keyset
    # page is an indexed id-range scan, flat regardless of depth.
    rows: list[dict] = []
    _last_id = ""
    while True:
        _q = (db.client.table("videos")
              .select("id,youtube_video_id,channel_id")
              .in_("channel_id", channel_ids)
              .order("id", desc=False)
              .limit(1000))
        if _last_id:
            _q = _q.gt("id", _last_id)
        _page = _q.execute().data or []
        rows.extend(_page)
        if len(_page) < 1000:
            break
        _last_id = _page[-1]["id"]
    expected = len(rows)
    log(f"  {expected:,} WC2026 video rows in scope")
    id_to_dbid: dict[str, str] = {}
    id_to_cid:  dict[str, str] = {}
    for r in rows:
        _ytid = (r.get("youtube_video_id") or "").strip()
        if not _ytid:
            continue
        id_to_dbid[_ytid] = r["id"]
        id_to_cid[_ytid]  = r.get("channel_id") or ""
    yt_ids = list(id_to_dbid.keys())
    _now_iso = datetime.now(timezone.utc).isoformat()
    update_buffer: dict[str, dict] = {}
    for b in range(0, len(yt_ids), 50):
        batch = yt_ids[b:b + 50]
        try:
            details = yt.get_video_details(batch)
        except Exception as e:
            log(f"  batch {b}-{b+50} fetch failed: {e}")
            continue
        for v in details:
            _yt = (v.get("youtube_video_id") or "").strip()
            if not _yt:
                continue
            dbid = id_to_dbid.get(_yt)
            cid_v = id_to_cid.get(_yt) or ""
            if not dbid or not cid_v:
                continue
            update_buffer[dbid] = {
                "id": dbid,
                "youtube_video_id": _yt,
                "channel_id": cid_v,
                "view_count":    v.get("view_count", 0),
                "like_count":    v.get("like_count", 0),
                "comment_count": v.get("comment_count", 0),
                "last_updated":  _now_iso,
            }
        if (b // 50) % 20 == 0:
            log(f"  fetched stats for {min(b + 50, len(yt_ids)):,}/"
                f"{len(yt_ids):,}")
    update_rows = list(update_buffer.values())
    for b in range(0, len(update_rows), 500):
        try:
            db.client.table("videos").upsert(
                update_rows[b:b + 500], on_conflict="id"
            ).execute()
        except Exception as e:
            log(f"  upsert batch {b}-{b+500} failed: {e}")
    return len(update_rows), expected


def main() -> int:
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip()
              or os.environ.get("YOUTUBE_API_KEY", "").strip())
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)
    start = time.time()

    channels = [c for c in db.get_all_channels()
                if (c.get("competitions") or {}).get("wc2026")]
    log(f"Weekly WC2026 maintenance — {len(channels)} channels in scope")
    if not channels:
        log("No WC2026 channels — run scripts/import_wc2026.py first.")
        return 0

    log("\n── Job 1: re-fetch popular IDs (UULP / UUPS / UUPV) ──")
    pop_ok, pop_saved, live_seen = _refresh_popular_ids(yt, db, channels)
    log(f"  popular re-fetch: ok={pop_ok}/{len(channels)} "
        f"videos_saved={pop_saved} live_ids={live_seen}")

    log("\n── Job 2: refresh stats on every WC2026 video in DB ──")
    refreshed, in_scope = _refresh_video_stats(
        yt, db, [c["id"] for c in channels if c.get("id")])
    cov = (refreshed / in_scope * 100) if in_scope else 100.0
    log(f"  stat refresh: {refreshed:,}/{in_scope:,} ({cov:.1f}% coverage)")

    log("\n── Job 3: recompute per-channel aggregates (no API) ──")
    agg_ok = 0
    for ch in channels:
        cid = ch.get("id")
        if not cid:
            continue
        try:
            db.refresh_top100_stats(cid)
            db.refresh_lifetime_format_views(cid)
            agg_ok += 1
        except Exception as e:
            log(f"  aggregate recompute failed for "
                f"{ch.get('name', '?')}: {e}")
    log(f"  aggregates refreshed: {agg_ok}/{len(channels)}")

    log("\n── Cache rebuild ──")
    try:
        from src import dashboard_cache as _dc
        _dc.rebuild_all(db, log=log)
        log("  cache rebuild done")
    except Exception as e:
        log(f"  cache rebuild FAILED (non-fatal): {e}")
        traceback.print_exc()

    elapsed = time.time() - start
    log(f"\nWeekly WC2026 done in {elapsed:.1f}s — "
        f"popular_saved={pop_saved} stats_refreshed={refreshed:,} "
        f"aggregates={agg_ok}/{len(channels)}")

    try:
        db.log_fetch(
            agg_ok, 0, "weekly_wc2026",
            f"popular_saved={pop_saved}, stats_refreshed={refreshed}, "
            f"aggregates={agg_ok}/{len(channels)}, "
            f"in {elapsed:.0f}s",
        )
    except Exception as e:
        log(f"log_fetch skipped: {e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        sys.exit(2)
