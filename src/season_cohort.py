"""Per-season Top-5 cohort membership (the `season_channels` table).

Display-time gate: a CLUB shows on the live pages only when the ACTIVE
season label is in its `season_channels` membership. Tracking is a
separate concern (`channels.is_active` + `entity_type`), so a promoted
club can be snapshotted from the day it's added while staying hidden
until the season flips — see docs and the rollover plan.

Scope: Top-5 CLUBS only. Non-club channels (the 5 League HQs, WC2026,
NFL, F1, Players, Other, Women, …) pass through untouched — they're not
in `season_channels` and are governed by their own cohort logic.

Pure Python (no Streamlit), so crons can import it too. The active
season resolves to, in order: an explicit override (an admin `?season=`
preview), `app_settings.active_top5_season`, else the date-driven
`current_season_label_safe()` (which flips 25/26 → 26/27 on 2026-07-01).
"""
from __future__ import annotations

from src.cohort import is_club


def resolve_active_season(db, override: str | None = None) -> str:
    """Active Top-5 season label. Precedence: explicit `override` →
    `app_settings.active_top5_season` → `current_season_label_safe()`."""
    if override:
        return override
    try:
        r = (db.client.table("app_settings")
             .select("value").eq("key", "active_top5_season")
             .limit(1).execute().data or [])
        if r and (r[0].get("value") or "").strip():
            return r[0]["value"].strip()
    except Exception:
        pass
    from src.channels import current_season_label_safe
    return current_season_label_safe()


def get_season_cohort_ids(db, season: str) -> set[str]:
    """Set of channel-ids in the Top-5 cohort for `season`
    (from `season_channels`). Empty set on any error / unknown season."""
    ids: set[str] = set()
    try:
        off = 0
        while True:
            rows = (db.client.table("season_channels")
                    .select("channel_id").eq("season", season)
                    .range(off, off + 999).execute().data or [])
            ids.update(r["channel_id"] for r in rows)
            if len(rows) < 1000:
                break
            off += 1000
    except Exception:
        pass
    return ids


def filter_to_season_cohort(channels: list[dict],
                            cohort_ids: set[str]) -> list[dict]:
    """Drop CLUB channels not in the active-season cohort; pass everything
    else through unchanged.

    Fail-open: if `cohort_ids` is empty (table not populated yet, query
    failed, or an unknown season label), return `channels` unchanged —
    never hide the whole cohort on a lookup miss.
    """
    if not cohort_ids:
        return channels
    return [c for c in channels
            if not is_club(c) or c.get("id") in cohort_ids]
