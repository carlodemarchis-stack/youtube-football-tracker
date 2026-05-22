"""Hourly top-up resnap — chases YouTube's lazy view-count aggregate.

YouTube's public channel.statistics.viewCount updates on Google's own
(currently slow) schedule, so the nightly daily_refresh routinely
writes a cohort where most channels are "frozen" — same total_views as
the day before. Those values trickle in over the following hours.

This job runs hourly AFTER daily_refresh and tops up whatever YouTube
has released since the last pass. Designed to be scheduled in the
window 01:00 → 07:00 CET (last run 07:00) so the temporal "smear"
(topping up yesterday with a few of today's overnight views) stays
negligible.

Each run:
  1. DB-only frozen detection for yesterday CET (vs the day before)
  2. If 0 frozen → all caught up → ntfy ✓ and exit (cheap no-op)
  3. Else re-pull the frozen channels from YouTube, write any that
     moved, rebuild the trends_30d caches (hot + cold) if anything
     changed
  4. Always send an ntfy with the still-frozen count

Stop condition is enforced by the cron window (no runs after 07:00
CET), not by this script — each invocation is independent and
self-gating.

Target date defaults to yesterday CET (the row daily_refresh wrote).
Cohort = top-5 (96 Clubs + 5 League HQs = 101).

Usage:
    python scripts/resnap_hourly.py
    python scripts/resnap_hourly.py --target-date 2026-05-21
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from src.database import Database
from src.youtube_api import YouTubeClient
from src.notify import send_ntfy

CET = ZoneInfo("Europe/Rome")


def _yesterday_cet() -> str:
    return (datetime.now(CET).date() - timedelta(days=1)).isoformat()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--target-date", default=_yesterday_cet(),
                   help="ISO date to top up (default: yesterday CET).")
    args = p.parse_args()
    target = args.target_date
    try:
        date.fromisoformat(target)
    except ValueError:
        print(f"FATAL: --target-date must be YYYY-MM-DD, got {target!r}")
        return 2

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY")
              or os.environ.get("SUPABASE_KEY"))
    # Resnap runs on the INTERACTIVE key so its hourly top-ups never
    # compete with the main pipeline's daily/hourly quota. Falls back
    # to the general keys only if INTERACTIVE isn't configured.
    yt_key = (os.environ.get("YOUTUBE_API_KEY_INTERACTIVE")
              or os.environ.get("YOUTUBE_API_KEY")
              or os.environ.get("YOUTUBE_API_KEY_DAILY")
              or os.environ.get("YOUTUBE_API_KEY_HEAVY"))
    if not (sb_url and sb_key and yt_key):
        print("FATAL: need SUPABASE_URL + SUPABASE_(SERVICE_)KEY + a "
              "YOUTUBE_API_KEY in env")
        return 2

    db = Database(sb_url, sb_key)
    yt = YouTubeClient(yt_key)

    # Cohort = top-5 (Clubs + League HQs).
    chans = (db.client.table("channels")
             .select("id,name,youtube_channel_id,entity_type")
             .execute().data or [])
    cohort = [c for c in chans if c.get("entity_type") in ("Club", "League")]
    cohort_by_id = {c["id"]: c for c in cohort}
    cohort_ids = list(cohort_by_id.keys())
    n_cohort = len(cohort_ids)

    yesterday = (date.fromisoformat(target) - timedelta(days=1)).isoformat()

    def _views_on(d: str) -> dict[str, int]:
        rows = (db.client.table("channel_snapshots")
                .select("channel_id,total_views")
                .eq("captured_date", d)
                .in_("channel_id", cohort_ids)
                .execute().data or [])
        return {r["channel_id"]: int(r.get("total_views") or 0) for r in rows}

    cur = _views_on(target)
    prev = _views_on(yesterday)

    # Frozen = same total_views as the previous day (and we have both rows).
    frozen_ids = [cid for cid, v in cur.items()
                  if cid in prev and v == prev[cid]]

    print(f"[resnap_hourly] target={target}  cohort={n_cohort}  "
          f"frozen={len(frozen_ids)}")

    # ── All caught up → cheap no-op ───────────────────────────────
    if not frozen_ids:
        msg = f"All {n_cohort} channels caught up for {target}. ✓"
        print(f"[resnap_hourly] {msg}")
        send_ntfy(title="🔄 Hourly resnap — all healthy",
                  message=f"{target}: 0 frozen / {n_cohort}. {msg}",
                  priority="low", tags=["white_check_mark"])
        return 0

    # ── Re-pull the frozen channels from YouTube ──────────────────
    started = time.time()
    updated = 0
    still_frozen = 0
    errors = 0
    recovered_views = 0
    for cid in frozen_ids:
        c = cohort_by_id[cid]
        try:
            stats = yt.get_channel_stats(c["youtube_channel_id"])
            if not stats:
                errors += 1
                continue
            new_tv = int(stats.get("total_views", 0) or 0)
            old_tv = cur.get(cid, 0)
            if new_tv != old_tv:
                db.snapshot_channel(cid, stats, captured_date=target)
                updated += 1
                if new_tv > old_tv:
                    recovered_views += new_tv - old_tv
            else:
                still_frozen += 1
        except Exception as e:
            errors += 1
            print(f"  {c.get('name')}: ERROR {e}")

    elapsed = time.time() - started
    print(f"[resnap_hourly] updated={updated} still_frozen={still_frozen} "
          f"errors={errors} recovered={recovered_views:,} in {elapsed:.1f}s")

    # ── Rebuild caches only if something actually changed ─────────
    if updated > 0:
        try:
            from src import dashboard_cache as _dc
            t = time.time()
            _dc.refresh_trends_30d(db, tier="hot")
            _dc.refresh_trends_30d(db, tier="cold", refresh_vibes=False)
            print(f"[resnap_hourly] caches rebuilt in {time.time()-t:.1f}s")
        except Exception as e:
            print(f"[resnap_hourly] cache rebuild failed (non-fatal): {e}")

    # ── Always ntfy with the still-frozen count ───────────────────
    remaining = still_frozen + errors
    healthy = remaining == 0
    body = (f"{target}: recovered {updated} (+{recovered_views:,} views), "
            f"still frozen {remaining}/{n_cohort}"
            + (f", {errors} errors" if errors else ""))
    send_ntfy(
        title=("🔄 Hourly resnap — all healthy" if healthy
               else f"🔄 Hourly resnap — {remaining} still frozen"),
        message=body,
        priority="low" if healthy else "default",
        tags=["white_check_mark"] if healthy else ["hourglass_flowing_sand"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
