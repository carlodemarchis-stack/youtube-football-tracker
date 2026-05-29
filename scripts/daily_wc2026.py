#!/usr/bin/env python3
"""Daily WC2026 refresh — dedicated cron, isolated from the rest.

For every channel with `competitions.wc2026` set (the 48 qualified
national teams + FIFA + 6 confederations + any per-country alt
channels, e.g. Argentina AFAOFICIAL, Spain RFEF, Saudi NT brand):

  1. Fetch current channel stats from the YouTube API.
  2. Upsert the channels table (subs / views / videos / long-form /
     shorts / lives etc.).
  3. Snapshot the per-day counters into channel_snapshots — that
     table has a (channel_id, captured_date) unique constraint, so
     even if the same channel was also picked up by daily_federations
     earlier in the night the row deduplicates cleanly.
  3b. Ingest any NEW videos since season start into the videos table.
  4. Snapshot every recent-window video's view/like/comment counts
     into video_snapshots + video_daily_deltas — rolling 30-day
     window, scoped to the WC2026 channel set only.
  5. Refresh the dashboard_cache.wc2026 payload so the FIFA World Cup
     2026 page reads fresh numbers without re-aggregating live.

Video collection is deliberately scoped + rolling-windowed and does
NOT compute the top100/season_* precompute (kept lightweight). It
exists so per-video view/engagement history accumulates from now →
the tournament — that time-series can only be built going forward,
never backfilled. WC2026 channels are excluded from every core
surface by CONVENTIONS §10 (entity_type + competitions.wc2026), so
these videos never leak into Latest / Top / Season / Daily Recap.

Runs at 00:00 UTC (≈ 02:00 CEST, 01:00 CET) on its own Railway service
so the WC2026 feature can be killed with zero impact elsewhere.

Env vars required:
    YOUTUBE_API_KEY        (rotates to YOUTUBE_API_KEY_BACKUP on quota)
    SUPABASE_URL
    SUPABASE_KEY
Env vars optional:
    YOUTUBE_API_KEY_HEAVY  (used instead of YOUTUBE_API_KEY when set)
    NTFY_TOPIC             (run-completion alert)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defensive .env load — same pattern as daily_federations.py so the
# script works locally without python-dotenv installed.
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

from src.database import Database, _fetch_all
from src.youtube_api import YouTubeClient
from src.channels import get_season_since

# Per-video snapshots: full-lifetime for the WC2026 cohort. Every video
# published on/after WC_TRACK_START gets a daily snapshot for its whole
# life (no rolling age cutoff) so we keep each tournament video's full
# trajectory — group-stage clips don't drop off before the final. The
# row cost is tiny (snapshots are 1 quota unit / 50 videos) and now has
# consumers: the WC2026 Viral + Trends video surfaces. WC_TRACK_START is
# the first day of full daily_wc2026 coverage, so there's no gap.
WC_TRACK_START = "2026-05-14"


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    # Prefer the heavy key when set (matches the pattern in
    # daily_refresh.py / hourly_rss.py — keeps WC2026 daily refreshes
    # from sharing quota with the streamlit page).
    yt_key = (os.environ.get("YOUTUBE_API_KEY_HEAVY", "").strip().strip('"').strip("'")
              or os.environ.get("YOUTUBE_API_KEY", "").strip().strip('"').strip("'"))
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")
    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY[_HEAVY] / SUPABASE_URL / SUPABASE_KEY")
        return 2
    yt_key_source = ("YOUTUBE_API_KEY_HEAVY"
                     if os.environ.get("YOUTUBE_API_KEY_HEAVY") else
                     "YOUTUBE_API_KEY")
    log(f"Starting daily_wc2026 — yt_key_source={yt_key_source}")

    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    wc = [c for c in channels
          if (c.get("competitions") or {}).get("wc2026")]
    log(f"Daily WC2026 refresh — {len(wc)} channel(s)")
    if not wc:
        log("No WC2026 channels found (need competitions.wc2026 tag). "
            "Nothing to do.")
        try:
            db.log_fetch(0, 0, "daily_wc2026", "no wc2026 channels tagged")
        except Exception:
            pass
        return 0

    ok = 0
    new_videos_total = 0
    video_snapshots_written = 0
    failed: list[tuple[str, str]] = []
    start = time.time()

    for i, ch in enumerate(wc, 1):
        name = ch.get("name", "?")
        yt_id = ch.get("youtube_channel_id")
        if not yt_id:
            failed.append((name, "no youtube_channel_id"))
            continue
        try:
            # ── 1. Fresh stats from YouTube ──
            stats = yt.get_channel_stats(yt_id)
            if not stats:
                failed.append((name, "empty stats response"))
                continue

            # ── 2. Upsert channels table ──
            row = db.upsert_channel({**ch, **stats})
            channel_db_id = row.get("id") or ch.get("id")

            # ── 3. Snapshot row into channel_snapshots ──
            if channel_db_id:
                db.snapshot_channel(channel_db_id, stats)

            # ── 3b. Ingest any NEW videos since season start ──
            # Mirrors daily_refresh / daily_federations: only fetch
            # details for IDs not already in the DB, tag the format.
            if channel_db_id:
                ch_season_since = get_season_since(ch)
                season = yt.get_video_ids_since_by_format(yt_id, ch_season_since)
                long_ids = season.get("long", [])
                short_ids = season.get("shorts", [])
                live_ids = season.get("live", [])
                seen: set[str] = set()
                all_ids: list[str] = []
                for vid in long_ids + short_ids + live_ids:
                    if vid not in seen:
                        seen.add(vid)
                        all_ids.append(vid)
                known = db.get_known_video_ids(channel_db_id)
                new_ids = [v for v in all_ids if v not in known]
                if new_ids:
                    long_set, short_set, live_set = (
                        set(long_ids), set(short_ids), set(live_ids))
                    new_vids: list[dict] = []
                    for b in range(0, len(new_ids), 50):
                        fetched = yt.get_video_details(new_ids[b:b + 50])
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

            ok += 1
            log(f"[{i}/{len(wc)}] {name} — "
                f"subs={stats.get('subscriber_count', 0):,} "
                f"videos={stats.get('video_count', 0)}")
        except Exception as e:
            failed.append((name, str(e)[:200]))
            log(f"[{i}/{len(wc)}] {name} — ERROR: {e}")

    # ── 4. Snapshot WC2026 video stats (full-lifetime since WC_TRACK_START) ──
    # Scoped to the WC2026 channel set + a published_at floor. NOT
    # db.get_season_video_rows() — that's global and would pull every
    # channel's videos into this isolated cron.
    window_cutoff = WC_TRACK_START
    log(f"Snapshotting WC2026 videos published since {window_cutoff} "
        f"(full-lifetime)...")
    try:
        wc_cids = [c["id"] for c in wc if c.get("id")]
        vid_rows: list[dict] = []
        if wc_cids:
            vid_rows = _fetch_all(
                db.client.table("videos")
                .select("id,youtube_video_id,channel_id")
                .in_("channel_id", wc_cids)
                .gte("published_at", window_cutoff)
            )
        log(f"  {len(vid_rows)} videos in window")
        if vid_rows:
            id_to_dbid: dict[str, str] = {}
            id_to_cid: dict[str, str] = {}
            for r in vid_rows:
                _ytid = (r.get("youtube_video_id") or "").strip()
                if not _ytid:
                    continue
                id_to_dbid[_ytid] = r["id"]
                id_to_cid[_ytid] = r.get("channel_id") or ""
            yt_ids = list(id_to_dbid.keys())
            snap_rows: list[dict] = []
            for b in range(0, len(yt_ids), 50):
                details = yt.get_video_details(yt_ids[b:b + 50])
                for v in details:
                    _ytid = (v.get("youtube_video_id") or "").strip()
                    if not _ytid:
                        continue
                    dbid = id_to_dbid.get(_ytid)
                    if not dbid:
                        continue
                    snap_rows.append({
                        "video_id": dbid,
                        # Carry yt_id + channel_id so the freshness
                        # upsert satisfies both NOT NULL constraints
                        # even when Postgres validates the INSERT path.
                        "youtube_video_id": _ytid,
                        "channel_id": id_to_cid.get(_ytid) or "",
                        "view_count": v.get("view_count", 0),
                        "like_count": v.get("like_count", 0),
                        "comment_count": v.get("comment_count", 0),
                    })
            if snap_rows:
                video_snapshots_written = db.snapshot_videos_batch(snap_rows)
                log(f"  wrote {video_snapshots_written} video_snapshots rows")
                try:
                    _today_iso = date.today().isoformat()
                    _n = db.compute_video_daily_deltas(_today_iso)
                    log(f"  wrote {_n} video_daily_deltas rows for {_today_iso}")
                except Exception as e:
                    log(f"  video_daily_deltas step skipped: {e}")
                # Keep the videos table fresh (view/like/comment) too.
                try:
                    by_id: dict[str, dict] = {}
                    for r in snap_rows:
                        _yt = (r.get("youtube_video_id") or "").strip()
                        _cid = (r.get("channel_id") or "").strip()
                        if not _yt or not _cid:
                            continue
                        by_id[r["video_id"]] = {
                            "id": r["video_id"],
                            "youtube_video_id": _yt,
                            "channel_id": _cid,
                            "view_count": r["view_count"],
                            "like_count": r["like_count"],
                            "comment_count": r["comment_count"],
                        }
                    update_rows = list(by_id.values())
                    for b in range(0, len(update_rows), 500):
                        db.client.table("videos").upsert(
                            update_rows[b:b + 500], on_conflict="id"
                        ).execute()
                except Exception as e:
                    log(f"  videos freshness update skipped: {e}")
    except Exception as e:
        log(f"video snapshot step failed: {e}")

    # ── 4b. Per-channel aggregate recompute (no API cost) ──
    # refresh_top100_stats + refresh_lifetime_format_views read from
    # the videos table and write summary columns on the channel row
    # (top100_views, top100_avg_age_days, lifetime_long_views, …).
    # Without this they were stuck at the values the one-off popular
    # backfill wrote on 2026-05-29 and would silently drift as new
    # videos arrived. Pure Supabase — no quota impact.
    log(f"Recomputing top100 + lifetime-format aggregates for {len(wc)} "
        "WC2026 channels...")
    _agg_ok = 0
    for _ch in wc:
        _cid = _ch.get("id")
        if not _cid:
            continue
        try:
            db.refresh_top100_stats(_cid)
            db.refresh_lifetime_format_views(_cid)
            _agg_ok += 1
        except Exception as e:
            log(f"  aggregate recompute failed for "
                f"{_ch.get('name', '?')}: {e}")
    log(f"  aggregates refreshed: {_agg_ok}/{len(wc)}")

    # ── 5. Refresh dashboard_cache.wc2026 ──
    # Same logic as scripts/refresh_wc2026_cache.py — keep the page's
    # read-side cache in sync with the freshly-snapshotted numbers.
    try:
        from src.dashboard_cache import refresh_wc2026, refresh_wc2026_trends
        # Re-pull channels so the cache payload reflects the just-written
        # stats (we mutated channels in step 2). Pass them explicitly so
        # refresh_wc2026 doesn't do a redundant DB read.
        _fresh_chans = db.get_all_channels()
        refresh_wc2026(db, log=log, channels=_fresh_chans)
        # Video-layer trends (Δ-views, top videos, viral) — depends on the
        # video_daily_deltas just computed above.
        refresh_wc2026_trends(db, log=log, channels=_fresh_chans)
    except Exception as e:
        log(f"dashboard_cache refresh failed (non-fatal): {e}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — ok={ok} failed={len(failed)} "
        f"new_videos={new_videos_total} video_snapshots={video_snapshots_written}")

    # ── Run summary (db log + ntfy) ──
    try:
        status = "daily_wc2026_partial" if failed else "daily_wc2026"
        summary = (f"{ok} wc2026 channels, {new_videos_total} new videos, "
                   f"{video_snapshots_written} video snapshots in {elapsed:.1f}s")
        if failed:
            summary += (f" | {len(failed)} failed: "
                        + "; ".join(f"{n}: {e}" for n, e in failed[:5]))
        db.log_fetch(ok, new_videos_total, status, summary)
    except Exception as e:
        log(f"log_fetch failed (non-fatal): {e}")

    try:
        from src.notify import send_run_alert
        send_run_alert(
            "daily_wc2026",
            ok=not failed,
            summary=(f"{ok} wc2026 channels, {new_videos_total} new videos, "
                     f"{video_snapshots_written} snapshots in {elapsed:.0f}s"),
            error=(f"{len(failed)} failed" if failed else ""),
        )
    except Exception as _e:
        log(f"ntfy alert failed (non-fatal): {_e}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
