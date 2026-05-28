#!/usr/bin/env python3
"""Weekly refresh — unattended.

Runs once a week (Monday 04:00 UTC). Covers everything the daily does, PLUS
refreshes stats on ALL videos (not just season / 30-day window).

Steps:
  1. Channel stats — fetch current subs/views/video_count for every channel,
     upsert channels table, write a channel_snapshot row.
  2. Video stats — fetch view/like/comment counts for EVERY video in the DB
     (not just season). Updates the videos table so lifetime top-100s,
     channel detail stats, etc. stay accurate.
  3. Recompute top100_stats + season_views on every channel (depends on the
     fresh view counts written in step 2).

Env vars required:
    YOUTUBE_API_KEY        (regular key — fallback)
    YOUTUBE_API_KEY_HEAVY  (optional — preferred if set, used for big quota jobs)
    SUPABASE_URL
    SUPABASE_KEY
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    # Prefer dedicated heavy-quota key if set, else fall back to regular key.
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip().strip('"').strip("'")
              or os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'"))
    yt_key_source = "YOUTUBE_API_KEY_HEAVY" if os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip() else "YOUTUBE_API_KEY"
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY (or YOUTUBE_API_KEY_HEAVY) / SUPABASE_URL / SUPABASE_KEY")
        return 2

    log(f"Starting weekly refresh — yt_key_source={yt_key_source} url_len={len(sb_url)} key_len={len(sb_key)} yt_len={len(yt_key)}")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    start = time.time()
    errors = 0

    # ── 1. Channel stats + channel_snapshots ─────────────────────
    channels = db.get_all_channels()
    # Players + Federations have their own dedicated crons — skip them here
    channels = [c for c in channels if c.get("entity_type") not in ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub", "NFL")]
    log(f"Step 1: refreshing stats for {len(channels)} channels (Players + Federations excluded)")
    ch_ok = 0
    ch_failed: list[tuple[str, str]] = []

    for i, ch in enumerate(channels, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            ch_failed.append((name, "no youtube_channel_id"))
            continue
        try:
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                ch_failed.append((name, "empty stats response"))
                continue
            db.upsert_channel({**ch, **stats})
            channel_db_id = ch.get("id")
            if channel_db_id:
                db.snapshot_channel(channel_db_id, stats)
            ch_ok += 1
            if i % 20 == 0:
                log(f"  channels {i}/{len(channels)}")
        except Exception as e:
            ch_failed.append((name, str(e)[:200]))
            errors += 1

    log(f"  channels done: ok={ch_ok} failed={len(ch_failed)}")
    if ch_failed:
        for n, e in ch_failed[:5]:
            log(f"    - {n}: {e}")

    # ── 2. Video stats (ALL videos — excluding Player videos) ────
    rows = db.get_all_video_rows()
    # Always fetch id→channel_id once: needed both for the player-exclusion
    # filter AND for the freshness upsert payload (videos.channel_id is
    # NOT NULL — Postgres validates the proposed INSERT row even when
    # ON CONFLICT resolves to UPDATE).
    from src.database import _fetch_all
    _resp = _fetch_all(db.client.table("videos").select("id,channel_id"))
    _vid_to_cid = {r["id"]: (r.get("channel_id") or "") for r in _resp}

    # Exclude Player + Federation videos — handled by their dedicated crons
    _player_cids = {c["id"] for c in db.get_all_channels()
                    if c.get("entity_type") in ("Player", "Federation", "GoverningBody", "OtherClub", "WomenClub", "NFL")}
    if _player_cids:
        rows = [r for r in rows if _vid_to_cid.get(r["id"]) not in _player_cids]
    total_videos = len(rows)
    log(f"Step 2: refreshing stats for {total_videos} videos (Player + Federation videos excluded)")

    # Build yt_id → dbid; skip rows missing either side (defensive against
    # any stale/null entries that would later trip the freshness upsert).
    id_to_dbid: dict[str, str] = {}
    for r in rows:
        yt_id_db = (r.get("youtube_video_id") or "").strip()
        if not yt_id_db or not r.get("id"):
            continue
        id_to_dbid[yt_id_db] = r["id"]
    yt_ids = list(id_to_dbid.keys())

    # Dedupe by id as we go (Supabase batch upsert with duplicate ids has
    # been observed to drop columns from the merged proposed row, which is
    # what trips the NOT NULL check).
    update_buffer: dict[str, dict] = {}
    # Track IDs we requested from YouTube but didn't get back (deleted /
    # private / region-blocked / API hiccup) so we can flag a partial run.
    missing_yt_ids: list[str] = []
    # Stamp `last_updated` on every freshened row so the date histogram on
    # the videos table truthfully reflects when stats were refreshed (the
    # previous payload omitted this column, so weekly runs were invisible
    # in last_updated even though view_count was being updated).
    _now_iso = datetime.now(timezone.utc).isoformat()
    expected = len(yt_ids)

    for b in range(0, len(yt_ids), 50):
        batch = yt_ids[b:b + 50]
        # One retry on transient fetch failure — the previous behaviour
        # silently dropped 50 IDs on any network blip until the next run.
        details = None
        for attempt in (1, 2):
            try:
                details = yt.get_video_details(batch)
                break
            except Exception as e:
                if attempt < 2:
                    log(f"  batch {b}-{b + 50} fetch failed (attempt 1): "
                        f"{e}; retrying in 1s")
                    time.sleep(1)
                else:
                    errors += 1
                    log(f"  batch {b}-{b + 50} fetch failed after retry: {e}")
        if details is None:
            missing_yt_ids.extend(batch)
            continue
        # Flag IDs the API didn't return for this batch (silent skips —
        # deleted videos, region blocks, occasional API drops).
        returned = {(v.get("youtube_video_id") or "").strip() for v in details}
        for q in batch:
            if q not in returned:
                missing_yt_ids.append(q)
        for v in details:
            yt_id = (v.get("youtube_video_id") or "").strip()
            if not yt_id:
                continue
            dbid = id_to_dbid.get(yt_id)
            if not dbid:
                continue
            cid_v = _vid_to_cid.get(dbid) or ""
            if not cid_v:
                # Shouldn't happen — dbid came from videos.id which has channel_id
                # NOT NULL — but skip rather than poison the batch if it ever does.
                continue
            update_buffer[dbid] = {
                "id": dbid,
                # Carry yt_id + channel_id so the proposed INSERT row
                # satisfies BOTH NOT NULL constraints. Postgres validates
                # the proposed INSERT row even when ON CONFLICT resolves
                # to UPDATE — missing either column fails the whole batch
                # with a 23502 NOT NULL violation.
                "youtube_video_id": yt_id,
                "channel_id": cid_v,
                "view_count": v.get("view_count", 0),
                "like_count": v.get("like_count", 0),
                "comment_count": v.get("comment_count", 0),
                "last_updated": _now_iso,
            }
        if (b // 50) % 20 == 0:
            log(f"  fetched stats for {min(b + 50, len(yt_ids))}/{len(yt_ids)}")

    # Coverage gate: previously the script silently reported "success"
    # when only a fraction of in-scope videos came back. Now we compute
    # the % actually populated and feed that into the run status below.
    received = len(update_buffer)
    coverage = (received / expected) if expected else 1.0
    log(f"  coverage: received {received}/{expected} videos "
        f"({coverage * 100:.1f}%); silently missing {len(missing_yt_ids)}")
    if missing_yt_ids:
        log(f"  first 5 missing yt_ids: {missing_yt_ids[:5]}")

    # Flush to Supabase in 500-row chunks
    update_rows = list(update_buffer.values())
    videos_updated = 0
    for b in range(0, len(update_rows), 500):
        try:
            db.client.table("videos").upsert(update_rows[b:b + 500], on_conflict="id").execute()
            videos_updated += len(update_rows[b:b + 500])
        except Exception as e:
            log(f"  upsert chunk {b} failed: {e}")
            errors += 1

    log(f"  videos updated: {videos_updated}/{total_videos}")

    # ── 3. Recompute top100_stats + season_views per channel ─────
    # Refresh channels list (may have new IDs from step 1)
    channels = db.get_all_channels()
    # Players + Federations have their own dedicated crons — skip them here
    club_channels = [c for c in channels if c.get("entity_type") not in ("League", "Player", "Federation", "GoverningBody", "OtherClub", "WomenClub", "NFL")]
    log(f"Step 3: recomputing top100_stats for {len(club_channels)} club channels")

    stats_ok = 0
    for i, ch in enumerate(club_channels, 1):
        ch_id = ch.get("id")
        if not ch_id:
            continue
        try:
            db.refresh_top100_stats(ch_id, get_season_since(ch))
            stats_ok += 1
        except Exception as e:
            log(f"  top100 refresh failed for {ch.get('name', '?')}: {e}")
            errors += 1
        if i % 20 == 0:
            log(f"  top100 stats {i}/{len(club_channels)}")

    log(f"  top100 stats refreshed: {stats_ok}/{len(club_channels)}")

    # ── Done ─────────────────────────────────────────────────────
    elapsed = time.time() - start
    # A run is "partial" if either there are explicit errors OR coverage
    # of the in-scope video set dropped below 95%. The previous version
    # only flagged errors, so a silent loss of 36% of videos (May 24)
    # was logged as a clean success.
    coverage_ok = coverage >= 0.95
    partial = (errors > 0) or (not coverage_ok)
    log(f"Done in {elapsed:.1f}s — channels={ch_ok} videos={videos_updated} "
        f"top100={stats_ok} errors={errors} coverage={coverage * 100:.1f}% "
        f"{'(PARTIAL)' if partial else ''}")

    try:
        status = "weekly_refresh_partial" if partial else "weekly_refresh"
        db.log_fetch(
            ch_ok, 0, status,
            f"{ch_ok} channels, {videos_updated} videos refreshed "
            f"({coverage * 100:.1f}% of {expected} in-scope), "
            f"{stats_ok} top100 recomputed, {errors} errors, "
            f"{len(missing_yt_ids)} silently_missing, {elapsed:.1f}s",
        )
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    try:
        from src.notify import send_run_alert
        send_run_alert("weekly_refresh",
                       ok=not partial,
                       summary=(f"{ch_ok} channels, {videos_updated} videos "
                                f"({coverage * 100:.0f}% cov), "
                                f"{stats_ok} top100 in {elapsed:.0f}s"),
                       error=("partial: "
                              f"{errors} errors, "
                              f"{len(missing_yt_ids)} silently_missing"
                              if partial else ""))
    except Exception as _e:
        log(f"ntfy alert failed (non-fatal): {_e}")

    return 0 if (ch_ok > 0 or videos_updated > 0) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as _exc:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        try:
            from src.notify import send_run_alert
            send_run_alert("weekly_refresh", ok=False,
                           summary="run crashed before completion",
                           error=str(_exc)[:300],
                           priority="urgent")
        except Exception:
            pass
        sys.exit(2)
