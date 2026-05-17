#!/usr/bin/env python3
"""Daily Players refresh — dedicated cron, isolated from the rest.

For every channel with entity_type='Player':
  1. Fetch current channel stats and upsert.
  2. Snapshot channel stats to channel_snapshots.
  3. Ingest any NEW season videos.
  4. Snapshot every season video's stats to video_snapshots.
  5. Refresh top100_stats for the player.

Runs at 00:00 UTC (≈ 01:00 CET) — separate service from daily_refresh.py
so the Players feature can be killed with zero impact elsewhere.

Env vars required:
    YOUTUBE_API_KEY
    SUPABASE_URL
    SUPABASE_KEY
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defensive .env load
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip('"\''))

from src.database import Database, _fetch_all
from src.youtube_api import YouTubeClient
from src.channels import get_season_since


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    yt_key = os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'")
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY / SUPABASE_URL / SUPABASE_KEY")
        return 2

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    players = [c for c in channels if c.get("entity_type") == "Player"]
    log(f"Daily Players refresh — {len(players)} player(s)")
    if not players:
        log("No Player channels found. Nothing to do.")
        try:
            db.log_fetch(0, 0, "daily_players", "no players tracked")
        except Exception:
            pass
        return 0

    ok = 0
    new_videos_total = 0
    video_snapshots_written = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ch in enumerate(players, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            failed.append((name, "no youtube_channel_id"))
            continue

        try:
            # ── 1. Channel stats + snapshot ──
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
            if channel_db_id:
                season = yt.get_video_ids_since_by_format(yt_id, ch_season_since)
                long_ids = season.get("long", [])
                short_ids = season.get("shorts", [])
                live_ids = season.get("live", [])
                all_ids: list[str] = []
                seen: set[str] = set()
                for vid in long_ids + short_ids + live_ids:
                    if vid not in seen:
                        seen.add(vid); all_ids.append(vid)

                known = db.get_known_video_ids(channel_db_id)
                new_ids = [v for v in all_ids if v not in known]
                if new_ids:
                    long_set, short_set, live_set = set(long_ids), set(short_ids), set(live_ids)
                    new_vids: list[dict] = []
                    for b in range(0, len(new_ids), 50):
                        fetched = yt.get_video_details(new_ids[b:b + 50])
                        for v in fetched:
                            vid = v["youtube_video_id"]
                            if vid in live_set: v["format"] = "live"
                            elif vid in short_set: v["format"] = "short"
                            else: v["format"] = "long"
                        new_vids.extend(fetched)
                    if new_vids:
                        db.upsert_videos(new_vids, channel_db_id)
                        new_videos_total += len(new_vids)

            # ── 3. Refresh top100 stats + season_views ──
            if channel_db_id:
                try:
                    db.refresh_top100_stats(channel_db_id, ch_season_since)
                except Exception:
                    pass

            ok += 1
            log(f"[{i}/{len(players)}] {name} — subs={stats.get('subscriber_count', 0):,} "
                f"videos={stats.get('video_count', 0)}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"[{i}/{len(players)}] {name} — ERROR: {e}")

    # ── 4. Snapshot each player's videos' current stats ──
    # Fetch all video IDs for player channels and snapshot their counts.
    try:
        player_cids = [p["id"] for p in players if p.get("id")]
        if player_cids:
            # Fetch all videos belonging to these channels
            vid_rows: list[dict] = []
            for cid in player_cids:
                # Paginated — high-output channels (Bayern, Real, etc.)
                # have well over 1000 videos, the unpaginated read would
                # silently truncate.
                vid_rows.extend(
                    _fetch_all(db.client.table("videos")
                               .select("id,youtube_video_id,channel_id")
                               .eq("channel_id", cid))
                )
            # Build both yt_id → dbid AND yt_id → channel_id maps. The
            # channel_id is needed in the freshness upsert payload (videos.channel_id
            # is NOT NULL — without it, a stray INSERT path on the upsert would
            # blow up the same way youtube_video_id used to).
            id_to_dbid: dict[str, str] = {}
            id_to_cid: dict[str, str] = {}
            for r in vid_rows:
                yt_id_db = (r.get("youtube_video_id") or "").strip()
                if not yt_id_db:
                    continue
                id_to_dbid[yt_id_db] = r["id"]
                id_to_cid[yt_id_db] = r.get("channel_id") or ""
            yt_ids = list(id_to_dbid.keys())
            log(f"Snapshotting {len(yt_ids)} player videos")
            snap_rows: list[dict] = []
            for b in range(0, len(yt_ids), 50):
                details = yt.get_video_details(yt_ids[b:b + 50])
                for v in details:
                    yt_id = (v.get("youtube_video_id") or "").strip()
                    if not yt_id:
                        continue
                    dbid = id_to_dbid.get(yt_id)
                    if not dbid:
                        continue
                    snap_rows.append({
                        "video_id": dbid,
                        # Carry yt_id + channel_id so the freshness upsert
                        # can satisfy both NOT NULL constraints — Postgres
                        # validates the proposed INSERT row even when
                        # ON CONFLICT will resolve to UPDATE.
                        "youtube_video_id": yt_id,
                        "channel_id": id_to_cid.get(yt_id) or "",
                        "view_count": v.get("view_count", 0),
                        "like_count": v.get("like_count", 0),
                        "comment_count": v.get("comment_count", 0),
                    })
            if snap_rows:
                # video_snapshots + video_daily_deltas are intentionally
                # NOT written for this isolated entity: no surface
                # consumes them (this page shows only channel stats +
                # season_* + last-upload; Daily Recap is core-only) and
                # the rows grew unbounded. The get_video_details fetch +
                # the videos-freshness upsert below are kept on purpose —
                # refresh_top100_stats reads videos.view_count for
                # season_*, so the videos table must stay current.
                try:
                    # Dedupe by `id` first — Supabase batch upsert with duplicate
                    # ids has been observed to produce odd merged payloads where
                    # individual columns vanish, including youtube_video_id, which
                    # then trips the NOT NULL check on the proposed INSERT row.
                    by_id: dict[str, dict] = {}
                    for r in snap_rows:
                        yt = (r.get("youtube_video_id") or "").strip()
                        cid_v = (r.get("channel_id") or "").strip()
                        if not yt or not cid_v:
                            continue
                        by_id[r["video_id"]] = {
                            "id": r["video_id"],
                            "youtube_video_id": yt,
                            "channel_id": cid_v,  # NOT NULL — keep upsert safe even if Postgres tries the INSERT path
                            "view_count": r["view_count"],
                            "like_count": r["like_count"],
                            "comment_count": r["comment_count"],
                        }
                    update_rows = list(by_id.values())
                    skipped = len(snap_rows) - len(update_rows)
                    if skipped:
                        log(f"videos freshness: dropped {skipped} row(s) (missing yt_id/channel_id or dup)")
                    for b in range(0, len(update_rows), 500):
                        db.client.table("videos").upsert(
                            update_rows[b:b + 500], on_conflict="id"
                        ).execute()
                except Exception as e:
                    log(f"videos freshness update skipped: {e}")
    except Exception as e:
        log(f"video snapshot step failed: {e}")

    # ── 5. Pre-compute the "last upload per channel" map so the Players
    # page reads one cheap cached row instead of scanning the whole
    # videos table on every render (write-time, not render-time). ──
    try:
        from src.dashboard_cache import refresh_last_upload
        refresh_last_upload(db, "Player", log=log)
    except Exception as e:
        log(f"last_upload cache refresh skipped (non-fatal): {e}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — ok={ok} failed={len(failed)} "
        f"new_videos={new_videos_total} video_snapshots={video_snapshots_written}")

    try:
        status = "daily_players_partial" if failed else "daily_players"
        summary = (f"{ok} players, {new_videos_total} new videos, "
                   f"{video_snapshots_written} video snapshots in {elapsed:.1f}s")
        if failed:
            summary += f" | {len(failed)} failed: {'; '.join(f'{n}: {e}' for n, e in failed[:5])}"
        db.log_fetch(ok, new_videos_total, status, summary)
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    try:
        from src.notify import send_run_alert
        send_run_alert("daily_players",
                       ok=not failed,
                       summary=(f"{ok} players, {new_videos_total} new videos, "
                                f"{video_snapshots_written} snapshots in {elapsed:.0f}s"),
                       error=(f"{len(failed)} failed" if failed else ""))
    except Exception as _e:
        log(f"ntfy alert failed (non-fatal): {_e}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
