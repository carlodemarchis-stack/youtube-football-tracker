"""Linear-interpolate historical frozen-channel runs in channel_snapshots.

When YouTube's channel.statistics.viewCount aggregate freezes for one
or more days, a daily cron writes identical total_views across those
days. The catch-up day after the run absorbs the missed growth, so:

    yyy / 0 / 0 / xxx        (raw)
becomes
    yyy / a / b / xxx        (interpolated)

where (xxx - yyy) is preserved exactly and the interior days get a
roughly even split with a small (±2.5%) random jitter so the result
doesn't look perfectly synthetic.

This is the same logic used in scripts/resnap_channel_views.py for
fresh frozen days, but applied retroactively to runs that have
already had their catch-up day land — i.e. data we can't get from
YouTube anymore (their counter has moved on).

Use cases
=========

- One-time historical clean-up after spotting a frozen pattern in
  the channel_snapshots audit
- Re-running the same script weeks later — deterministic seed means
  any already-interpolated days won't be touched again (the Δ-from-
  prev-day != 0 once interpolated, so the run detector ignores them)
- Adjusting the cleanup window with --since / --until

Usage
=====

    # Dry-run (no writes, no cache refresh)
    python scripts/interpolate_frozen_runs.py --dry-run

    # Live (writes channel_snapshots + refreshes trends_30d cache)
    python scripts/interpolate_frozen_runs.py

    # Limit window
    python scripts/interpolate_frozen_runs.py --since 2026-05-01 \\
                                              --until 2026-05-19

    # Single channel
    python scripts/interpolate_frozen_runs.py \\
        --channel-id 6f4750cb-c092-4eb1-a0f4-1316c84faf52

Exit codes
==========

    0 — success
    2 — env / config error
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from collections import defaultdict
from datetime import date

# Project root on path so `from src.…` resolves when run from anywhere.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from src.database import Database


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true",
                   help="Compute and print interpolated values, but do not "
                        "write to channel_snapshots. Skips the cache "
                        "refresh too.")
    p.add_argument("--since", default="2026-04-15",
                   help="Earliest captured_date to consider. Defaults to "
                        "2026-04-15 (start of channel_snapshots history).")
    p.add_argument("--until", default=None,
                   help="Latest captured_date to consider (exclusive). "
                        "Defaults to the cohort's max captured_date — "
                        "today's row should NOT be retroactively fixed "
                        "because we may still resnap it live.")
    p.add_argument("--channel-id", default=None,
                   help="Optional: restrict to one channel (UUID).")
    p.add_argument("--seed", type=int, default=42,
                   help="Random seed for the jitter. Default 42; re-runs "
                        "with the same seed produce identical writes.")
    p.add_argument("--jitter-pct", type=float, default=0.025,
                   help="Per-day jitter as fraction of the per-day step. "
                        "Default 0.025 (±2.5%%).")
    p.add_argument("--skip-cache", action="store_true",
                   help="Skip the post-write trends_30d hot-tier rebuild.")
    return p.parse_args()


def _find_frozen_runs(snapshots_by_channel: dict[str, list[dict]],
                      channels_by_id: dict[str, str]) -> list[dict]:
    """Walk per-channel snapshot history and emit one entry per
    consecutive frozen run that has BOTH a prev_day and a catch-up
    day available. Runs without a catch-up are skipped (we can't
    interpolate without an endpoint)."""
    runs: list[dict] = []
    for cid, rs in snapshots_by_channel.items():
        rs.sort(key=lambda x: x["captured_date"])
        i = 1
        while i < len(rs):
            if rs[i]["total_views"] == rs[i - 1]["total_views"]:
                run_start = i
                while i < len(rs) and rs[i]["total_views"] == rs[run_start - 1]["total_views"]:
                    i += 1
                if i < len(rs):  # has catch-up
                    runs.append({
                        "channel": channels_by_id.get(cid, "?"),
                        "channel_id": cid,
                        "yyy_row": rs[run_start - 1],
                        "frozen_rows": [rs[k] for k in range(run_start, i)],
                        "zzz_row": rs[i],
                    })
            else:
                i += 1
    return runs


def _interpolate_run(run: dict, rng: random.Random,
                      jitter_pct: float) -> list[tuple[dict, int, float]]:
    """Compute new total_views for each frozen row in a run.

    Returns a list of (frozen_row, new_total_views, jitter_amount).
    Endpoints (yyy / zzz) are never touched. Monotonic-clamped so each
    interior day is strictly between yyy and zzz, and strictly above
    the previous interpolated value.
    """
    yyy = run["yyy_row"]["total_views"]
    zzz = run["zzz_row"]["total_views"]
    N = len(run["frozen_rows"])
    X = zzz - yyy
    step = X / (N + 1)
    out: list[tuple[dict, int, float]] = []
    prev_val = yyy
    for k, fr in enumerate(run["frozen_rows"], 1):
        jitter_amt = rng.uniform(-jitter_pct, jitter_pct) * step
        new_val = int(yyy + k * step + jitter_amt)
        # Monotonic clamp — strictly between prev_val and zzz, leaving
        # room for the remaining frozen days to also stay below zzz.
        remaining = N + 1 - k
        new_val = max(prev_val + 1, min(zzz - remaining, new_val))
        out.append((fr, new_val, jitter_amt))
        prev_val = new_val
    return out


def main() -> int:
    args = _parse_args()

    sb_url = os.environ.get("SUPABASE_URL")
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY")
              or os.environ.get("SUPABASE_KEY"))
    if not (sb_url and sb_key):
        print("FATAL: need SUPABASE_URL + SUPABASE_SERVICE_KEY (or "
              "SUPABASE_KEY) in env / .env")
        return 2
    db = Database(sb_url, sb_key)

    # Cohort = top-5 (Clubs + Leagues), unless --channel-id is set.
    chans = (db.client.table("channels")
             .select("id,name,entity_type")
             .execute().data or [])
    if args.channel_id:
        cohort = [c for c in chans if c["id"] == args.channel_id]
        if not cohort:
            print(f"FATAL: channel_id {args.channel_id!r} not found")
            return 2
    else:
        cohort = [c for c in chans
                  if c.get("entity_type") in ("Club", "League")]
    cohort_ids = [c["id"] for c in cohort]
    channels_by_id = {c["id"]: c.get("name") or "?" for c in cohort}
    print(f"Cohort: {len(cohort)} channel(s)")
    print(f"Window: {args.since}{' → ' + args.until if args.until else ' → (latest)'}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'} "
          f"seed={args.seed} jitter=±{args.jitter_pct * 100:.1f}%")
    print()

    # Pull all channel_snapshots for cohort + window, in one paginated scan.
    PAGE = 1000
    rows: list[dict] = []
    for cs in range(0, len(cohort_ids), 50):
        chunk = cohort_ids[cs:cs + 50]
        off = 0
        while True:
            q = (db.client.table("channel_snapshots")
                 .select("id,channel_id,captured_date,total_views")
                 .in_("channel_id", chunk)
                 .gte("captured_date", args.since))
            if args.until:
                q = q.lt("captured_date", args.until)
            rs = q.range(off, off + PAGE - 1).execute().data or []
            rows.extend(rs)
            if len(rs) < PAGE:
                break
            off += PAGE

    snapshots_by_channel: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        snapshots_by_channel[r["channel_id"]].append(r)

    runs = _find_frozen_runs(snapshots_by_channel, channels_by_id)
    if not runs:
        print("No frozen runs found in this window. Nothing to do.")
        return 0
    print(f"Detected {len(runs)} frozen run(s).")

    rng = random.Random(args.seed)
    total_updates = 0
    total_growth_redistributed = 0
    print()
    print("  channel                       date          before           after            "
          "Δ from prev      jitter")
    for run in sorted(runs, key=lambda r: (r["frozen_rows"][0]["captured_date"],
                                            r["channel"])):
        plan = _interpolate_run(run, rng, args.jitter_pct)
        prev_val = run["yyy_row"]["total_views"]
        for fr, new_val, jitter_amt in plan:
            delta_str = f"{'+' if new_val - prev_val >= 0 else ''}{new_val - prev_val:,}"
            tag = "PLAN" if args.dry_run else "WRITE"
            print(f"  [{tag}] {run['channel']:<22}  {fr['captured_date']}  "
                  f"{fr['total_views']:>13,}  {new_val:>13,}  {delta_str:>13}  "
                  f"jitter={jitter_amt:+,.0f}")
            if not args.dry_run:
                db.client.table("channel_snapshots") \
                    .update({"total_views": new_val}) \
                    .eq("id", fr["id"]).execute()
            total_updates += 1
            total_growth_redistributed += abs(new_val - fr["total_views"])
            prev_val = new_val

    print()
    print(f"Plan: {total_updates} row update(s) across {len(runs)} run(s)")
    print(f"Total reshuffled growth: {total_growth_redistributed:,} views")
    if args.dry_run:
        print("\n(dry-run: no writes performed, cache not refreshed)")
        return 0

    if not args.skip_cache:
        print("\nRefreshing trends_30d hot tier…")
        try:
            from src import dashboard_cache as _dc
            t = time.time()
            _dc.refresh_trends_30d(db, tier="hot")
            print(f"  done in {time.time() - t:.1f}s")
        except Exception as e:
            print(f"  cache refresh failed (non-fatal): {e}")
    else:
        print("\n(skipping cache refresh per --skip-cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
