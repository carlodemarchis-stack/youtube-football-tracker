"""Backfill WC2026 channels whose video_count froze on a middle day.

Different from scripts/backfill_wc2026_missing_snapshot.py (no row) and
scripts/interpolate_frozen_runs.py (view-count freezes). This case:
a row DOES exist on the target day, but `video_count` (and its
long_form_count / shorts_count / live_count children) equals the
previous day's value while the next day's value moves on. The cron
ran but YouTube's playlist totalResults endpoint served a stale
cached count for those channels — net effect: a fake 0 Δ-videos on
the target day, with the missed growth absorbed into the next day.

For each WC2026 channel where snapshot[target].video_count ==
snapshot[prev_d].video_count AND snapshot[next_d].video_count >
snapshot[target].video_count, we update the target row's four count
columns to the linear midpoint of prev and next:

    new[col] = round((prev[col] + next[col]) / 2)

That preserves the (prev → next) Δ exactly while spreading it
plausibly across the two days. `total_views` and `subscriber_count`
are NEVER touched — this script is video-counts-only.

Idempotent: re-running on already-interpolated data won't re-trigger
(the new midpoint != prev, so the freeze-detection condition fails).

Usage:
    python scripts/backfill_wc2026_frozen_videocount.py 2026-05-22 --dry-run
    python scripts/backfill_wc2026_frozen_videocount.py 2026-05-22

Env: SUPABASE_URL, SUPABASE_(SERVICE_)KEY.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.database import Database, _fetch_all


_FIELDS = ("video_count", "long_form_count",
           "shorts_count", "live_count")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target_date",
                    help="ISO date of the frozen middle day "
                         "(e.g. 2026-05-22)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Don't write — just report what would happen.")
    args = ap.parse_args()

    try:
        target = date.fromisoformat(args.target_date)
    except ValueError:
        print(f"FATAL: bad date {args.target_date!r}")
        return 2
    prev_d = (target - timedelta(days=1)).isoformat()
    next_d = (target + timedelta(days=1)).isoformat()
    target_d = target.isoformat()

    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (sb_url and sb_key):
        print("FATAL: missing SUPABASE_URL / SUPABASE_KEY")
        return 2

    db = Database(sb_url, sb_key)

    chans = (db.client.table("channels")
             .select("id,name,competitions").execute().data) or []
    wc = [c for c in chans
          if (c.get("competitions") or {}).get("wc2026")]
    wc_ids = [c["id"] for c in wc]
    name_by_id = {c["id"]: c["name"] for c in wc}
    print(f"WC2026 channels: {len(wc)}")
    print(f"Target day:  {target_d}")
    print(f"Neighbours:  {prev_d}  <-  ->  {next_d}\n")

    rows = _fetch_all(
        db.client.table("channel_snapshots")
        .select("channel_id,captured_date," + ",".join(_FIELDS))
        .in_("channel_id", wc_ids)
        .in_("captured_date", [prev_d, target_d, next_d]))
    snap = {(r["channel_id"], r["captured_date"]): r for r in rows}

    candidates: list[dict] = []
    skip_no_prev = skip_no_target = skip_no_next = 0
    skip_static  = skip_not_frozen = 0
    for c in wc:
        cid = c["id"]
        p = snap.get((cid, prev_d))
        t = snap.get((cid, target_d))
        n = snap.get((cid, next_d))
        if not p:
            skip_no_prev += 1;   continue
        if not t:
            skip_no_target += 1; continue
        if not n:
            skip_no_next += 1;   continue
        # Freeze pattern: target == prev for video_count, AND next > target.
        if int(t.get("video_count") or 0) != int(p.get("video_count") or 0):
            skip_not_frozen += 1; continue
        if int(n.get("video_count") or 0) <= int(t.get("video_count") or 0):
            # No growth on the next day either → channel was genuinely
            # static for this stretch. Don't fabricate motion.
            skip_static += 1
            continue
        new_row: dict = {
            "channel_id": cid, "captured_date": target_d,
        }
        for f in _FIELDS:
            a = int(p.get(f) or 0)
            b = int(n.get(f) or 0)
            new_row[f] = round((a + b) / 2)
        candidates.append({
            "name": name_by_id.get(cid, "?"),
            "row":  new_row,
            "old":  {f: int(t.get(f) or 0) for f in _FIELDS},
        })

    print(f"To interpolate: {len(candidates)} channels")
    print(f"  not frozen on this day:    {skip_not_frozen}")
    print(f"  static (no growth either): {skip_static}")
    print(f"  missing prev / target / next snapshots: "
          f"{skip_no_prev} / {skip_no_target} / {skip_no_next}")
    print()

    if not candidates:
        print("Nothing to do.")
        return 0

    print(f"{'channel':40s}  {'before':>7s}  {'after':>7s}  "
          f"{'old':>7s} → {'new':>7s}  (video_count)")
    for x in candidates[:30]:
        n = x["name"][:40]
        old = x["old"]["video_count"]
        new = x["row"]["video_count"]
        # Need before/after again for the print row
        cid = x["row"]["channel_id"]
        p = snap.get((cid, prev_d))
        nxt = snap.get((cid, next_d))
        b = int((p or {}).get("video_count") or 0)
        a = int((nxt or {}).get("video_count") or 0)
        print(f"{n:40s}  {b:>7,}  {a:>7,}  {old:>7,} → {new:>7,}")
    if len(candidates) > 30:
        print(f"  … +{len(candidates) - 30} more")

    if args.dry_run:
        print("\n--dry-run — no rows written.")
        return 0

    # Live update via upsert on (channel_id, captured_date) PK so the
    # interpolated row replaces the frozen one.
    written = 0
    payloads = [x["row"] for x in candidates]
    for i in range(0, len(payloads), 200):
        batch = payloads[i:i + 200]
        try:
            r = (db.client.table("channel_snapshots")
                 .upsert(batch, on_conflict="channel_id,captured_date")
                 .execute())
            written += len(r.data or [])
        except Exception as e:
            print(f"  batch {i}-{i+200} upsert failed: {e}")
    print(f"\nUpdated {written} interpolated channel_snapshots rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
