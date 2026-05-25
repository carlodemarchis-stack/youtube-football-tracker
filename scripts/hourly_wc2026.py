#!/usr/bin/env python3
"""Hourly WC2026 new-video check — dedicated cron, isolated.

Lightweight by design: detects ONLY newly-published videos for the
WC2026 channel set (`competitions.wc2026`) and upserts them, so the
"Latest Videos" page is fresh within the hour. It deliberately does
NOT refresh channel stats / channel_snapshots / video_snapshots /
video_daily_deltas / top100 — all of that stays in daily_wc2026.py.
"No useless fetch": a quiet hour costs only ~1 quota unit per channel
(the uploads-playlist probe) and nothing else.

Reuses, without duplicating logic:
  • hourly_rss.py's playlistItems.list new-video detection
    (fetch_recent_video_entries + get_known_video_ids diff)
  • daily_wc2026.py step 3b format-classify + upsert block

Key priority (intentionally DIFFERENT from hourly_rss.py):
    YOUTUBE_API_KEY_BACKUP → YOUTUBE_API_KEY_HOURLY
    → YOUTUBE_API_KEY_HEAVY → YOUTUBE_API_KEY

Distributes load across keys: BACKUP first so this 24/7 cron's
~1.5K quota units/day come off a separate GCP project from the
top-5 hourly_rss (which uses _HOURLY). Falls back if BACKUP isn't
set on the service.

CONVENTIONS §10: scoped to competitions.wc2026 only — these videos
never leak into core Latest / Top / Season / Daily Recap.

Runs every hour, 24/7, on its own Railway service (WC2026 channels
are global — Asia/Americas/Oceania upload outside European daytime —
and isolation keeps the WC2026 feature independently killable).

Env vars required:
    YOUTUBE_API_KEY        (final fallback)
    SUPABASE_URL
    SUPABASE_KEY
Env vars optional (in priority order):
    YOUTUBE_API_KEY_BACKUP (preferred — dedicated quota bucket)
    YOUTUBE_API_KEY_HOURLY (next — shared with hourly_rss)
    YOUTUBE_API_KEY_HEAVY  (next)
    NTFY_TOPIC             (run-completion alert)
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Defensive .env load — same pattern as daily_wc2026.py so the script
# works locally without python-dotenv installed.
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

from src.database import Database
from src.youtube_api import YouTubeClient
from src.channels import get_season_since


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fetch_recent_video_entries(yt: YouTubeClient, youtube_channel_id: str) -> list[dict]:
    """Recent video IDs + published dates via playlistItems.list.
    Uploads playlist ID = channel ID with UC → UU. 1 quota unit.
    Verbatim from hourly_rss.py — same proven probe."""
    if not youtube_channel_id.startswith("UC"):
        return []
    uploads_pl_id = "UU" + youtube_channel_id[2:]
    try:
        return yt.get_recent_video_entries(uploads_pl_id, max_results=20)
    except Exception as e:
        log(f"  playlistItems fetch failed for {youtube_channel_id}: {e}")
        return []


def main() -> int:
    # BACKUP first to distribute load — this 24/7 cron lives on its
    # own quota bucket instead of sharing _HOURLY with hourly_rss.
    # Falls back if BACKUP isn't set on the service.
    def _e(name: str) -> str:
        return os.environ.get(name, "").strip().strip('"').strip("'")
    _backup = _e("YOUTUBE_API_KEY_BACKUP")
    _hourly = _e("YOUTUBE_API_KEY_HOURLY")
    _heavy = _e("YOUTUBE_API_KEY_HEAVY")
    yt_key = _backup or _hourly or _heavy or _e("YOUTUBE_API_KEY")
    yt_key_source = ("YOUTUBE_API_KEY_BACKUP" if _backup else
                     "YOUTUBE_API_KEY_HOURLY" if _hourly else
                     "YOUTUBE_API_KEY_HEAVY" if _heavy else
                     "YOUTUBE_API_KEY")
    sb_url = os.environ.get("SUPABASE_URL", "").strip().strip('"').strip("'")
    sb_key = os.environ.get("SUPABASE_KEY", "").strip().strip('"').strip("'")

    if not (yt_key and sb_url and sb_key):
        log("FATAL: missing YOUTUBE_API_KEY[_HOURLY/_HEAVY] / "
            "SUPABASE_URL / SUPABASE_KEY")
        return 2

    log(f"Hourly WC2026 check — yt_key_source={yt_key_source}")
    yt = YouTubeClient(yt_key)
    db = Database(sb_url, sb_key)

    channels = db.get_all_channels()
    # Same scoping one-liner as daily_wc2026.py (CONVENTIONS §10).
    wc = [c for c in channels
          if (c.get("competitions") or {}).get("wc2026")]
    total = len(wc)
    log(f"Hourly WC2026 check — {total} channel(s) (playlistItems API)")
    if not wc:
        log("No WC2026 channels tagged (competitions.wc2026). Nothing to do.")
        try:
            db.log_fetch(0, 0, "hourly_wc2026", "no wc2026 channels tagged")
        except Exception:
            pass
        return 0

    # ── Scan: which channels have genuinely new videos? ──────────
    all_new: list[tuple[str, str]] = []  # (youtube_video_id, channel_db_id)
    ok = 0
    fail = 0
    start = time.time()

    for i, ch in enumerate(wc, 1):
        yt_id = ch.get("youtube_channel_id")
        channel_db_id = ch.get("id")
        if not yt_id or not channel_db_id:
            fail += 1
            continue
        entries = fetch_recent_video_entries(yt, yt_id)
        if not entries:
            fail += 1
            continue
        ok += 1

        # Season filter, mirroring hourly_rss.py / daily_wc2026.py.
        ch_season_since = get_season_since(ch)
        in_season = [e for e in entries if e["published"] >= ch_season_since]
        if not in_season:
            continue

        video_ids = [e["video_id"] for e in in_season]
        known = db.get_known_video_ids(channel_db_id)
        new_ids = [vid for vid in video_ids if vid not in known]
        if new_ids:
            log(f"  [{i}/{total}] {ch.get('name','?')}: "
                f"{len(new_ids)} new video(s)")
            for vid in new_ids:
                all_new.append((vid, channel_db_id))

    scan_elapsed = time.time() - start
    log(f"Scan done in {scan_elapsed:.1f}s — ok={ok} fail={fail} "
        f"new_videos={len(all_new)}")

    if not all_new:
        log("No new videos found. Done.")
        try:
            from src.notify import send_run_alert
            send_run_alert(
                "hourly_wc2026",
                ok=True,
                summary=f"No new videos. {ok}/{total} WC2026 channels "
                        f"scanned in {scan_elapsed:.0f}s.",
                priority="low",
            )
        except Exception as _e:
            log(f"ntfy alert failed (non-fatal): {_e}")
        try:
            db.log_fetch(ok, 0, "hourly_wc2026",
                         f"{ok}/{total} scanned, 0 new in {scan_elapsed:.1f}s")
        except Exception:
            pass
        return 0

    # ── Fetch + classify + upsert ONLY the new videos ────────────
    # Verbatim shape of daily_wc2026.py step 3b: format via playlist
    # membership, then videos.list details, then upsert_videos.
    by_channel: dict[str, list[str]] = {}
    for vid, ch_id in all_new:
        by_channel.setdefault(ch_id, []).append(vid)

    total_inserted = 0
    for ch_id, new_ids in by_channel.items():
        ch = next((c for c in wc if c["id"] == ch_id), None)
        if not ch:
            continue
        yt_ch_id = ch["youtube_channel_id"]
        try:
            season = yt.get_video_ids_since_by_format(
                yt_ch_id, get_season_since(ch))
            long_set = set(season.get("long", []))
            short_set = set(season.get("shorts", []))
            live_set = set(season.get("live", []))
        except Exception as e:
            log(f"  Playlist check failed for {ch.get('name','?')}: {e}")
            long_set = short_set = live_set = set()

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
            db.upsert_videos(new_vids, ch_id)
            total_inserted += len(new_vids)
            log(f"  Inserted {len(new_vids)} videos for {ch.get('name','?')}")

    elapsed = time.time() - start
    log(f"Done in {elapsed:.1f}s — {total_inserted} new videos inserted")

    # Keep the WC2026 page's read-side cache in sync so the "Last
    # upload" column reflects the new clips within the hour. DB-only
    # (no YouTube quota); only runs when something actually changed.
    try:
        from src.dashboard_cache import refresh_wc2026, refresh_wc2026_trends
        _fresh_chans = db.get_all_channels()
        refresh_wc2026(db, log=log, channels=_fresh_chans)
        # Video-layer trends only need a rebuild when new clips arrived —
        # the Δ-views series itself moves on the daily snapshot, not hourly.
        if total_inserted:
            refresh_wc2026_trends(db, log=log, channels=_fresh_chans)
    except Exception as e:
        log(f"dashboard_cache refresh failed (non-fatal): {e}")

    try:
        db.log_fetch(
            len(by_channel), total_inserted, "hourly_wc2026",
            f"{ok}/{total} scanned, {total_inserted} new videos "
            f"in {elapsed:.1f}s",
        )
    except Exception:
        pass

    try:
        from src.notify import send_run_alert
        send_run_alert(
            "hourly_wc2026",
            ok=True,
            summary=(f"WC2026: {total_inserted} new videos across "
                     f"{len(by_channel)} channels "
                     f"({ok}/{total} scanned in {elapsed:.0f}s)"),
        )
    except Exception as _e:
        log(f"ntfy alert failed (non-fatal): {_e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as _exc:
        log("FATAL unhandled exception:")
        traceback.print_exc()
        try:
            from src.notify import send_run_alert
            send_run_alert("hourly_wc2026", ok=False,
                           summary="run crashed before completion",
                           error=str(_exc)[:300],
                           priority="urgent")
        except Exception:
            pass
        sys.exit(2)
