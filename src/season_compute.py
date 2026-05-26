"""Pre-compute Season heavy surfaces once per refresh.

Three Season-level surfaces all share the same paginated scan of every
top-5 season video — running them live on each render hits Supabase
hard. This module does ONE scan + writes THREE single-row caches:

  - season_one_hits        — the ~30 enriched one-hit-wonder videos.
  - season_videos_per_day  — per-channel daily upload counts.
  - season_heatmap         — per-channel 7×24 (CET) post counts + view-sums.

Each cache is a single dashboard_cache row at scope_all(); pages filter
to a league / club in memory. Wired into daily_refresh after the
snapshot pass; manual prefill via scripts/refresh_season_compute.py.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from src.database import _fetch_all
from src.channels import COUNTRY_TO_LEAGUE, get_season_since
from src.dashboard_cache import write, scope_all
from src import onehit as _oh

_CET = ZoneInfo("Europe/Rome")


def _scan(db, channel_ids: list[str], since: str) -> list[dict]:
    """One paginated scan of every season video for the cohort, with the
    union of fields any of the three consumers need."""
    if not channel_ids:
        return []
    rows: list[dict] = []
    ids = list(channel_ids)
    for i in range(0, len(ids), 50):
        rs = _fetch_all(
            db.client.table("videos")
            .select("id,youtube_video_id,channel_id,title,view_count,"
                    "like_count,comment_count,published_at,"
                    "duration_seconds,format,thumbnail_url,category")
            .gte("published_at", since)
            .in_("channel_id", ids[i:i + 50])
        )
        rows.extend(rs)
    return rows


def refresh(db, log=print, channels: list[dict] | None = None) -> None:
    """Scan once + write the three season caches at scope_all()."""
    chans = channels if channels is not None else db.get_all_channels()
    top5 = [c for c in chans
            if c.get("entity_type") in ("Club", "League")
            and COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
            and c.get("id")]
    ids = [c["id"] for c in top5]
    leagues_present = {COUNTRY_TO_LEAGUE[(c.get("country") or "").strip()]
                       for c in top5}
    since = min(get_season_since(league=lg) for lg in leagues_present)
    rows = _scan(db, ids, since)
    log(f"[season_compute] scanned {len(rows):,} videos across "
        f"{len(ids)} channels")

    # 1. One-hit wonders — reuse the existing definition.
    hits = _oh.compute(rows)
    write(db, "season_one_hits", scope_all(), {"hits": hits})
    log(f"[season_compute] season_one_hits WRITTEN ({len(hits)} hits)")

    # 2. Videos per day — {channel_id: {date_iso: count}}. ISO strings
    # so the JSONB payload round-trips cleanly; consumers parse to date.
    vpd: dict[str, dict[str, int]] = {}
    for r in rows:
        cid = r.get("channel_id")
        pa = r.get("published_at") or ""
        if not cid or not pa:
            continue
        try:
            d = pd.to_datetime(pa, utc=True).date().isoformat()
        except Exception:
            continue
        per = vpd.setdefault(cid, {})
        per[d] = per.get(d, 0) + 1
    write(db, "season_videos_per_day", scope_all(), {"per_channel": vpd})
    log(f"[season_compute] season_videos_per_day WRITTEN "
        f"({len(vpd)} channels)")

    # 3. Heatmap — {channel_id: {"counts": 7×24, "sums": 7×24}}, CET-bucketed.
    hm: dict[str, dict] = {}
    for r in rows:
        cid = r.get("channel_id")
        pa = r.get("published_at") or ""
        if not cid or not pa:
            continue
        try:
            dt = pd.to_datetime(pa, utc=True).tz_convert(_CET)
        except Exception:
            continue
        dow, hr = dt.weekday(), dt.hour
        g = hm.setdefault(cid, {
            "counts": [[0] * 24 for _ in range(7)],
            "sums": [[0] * 24 for _ in range(7)],
        })
        g["counts"][dow][hr] += 1
        g["sums"][dow][hr] += int(r.get("view_count") or 0)
    write(db, "season_heatmap", scope_all(), {"per_channel": hm})
    log(f"[season_compute] season_heatmap WRITTEN ({len(hm)} channels)")
