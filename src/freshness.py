"""YouTube view-count freshness check — drives the latency banner.

YouTube's public channel.statistics.viewCount aggregate updates on
Google's own (currently slow) schedule, so the nightly snapshot can
land "frozen" — same total_views as the day before — for many
channels, recovering over the following hours via the resnap jobs.

This module decides whether the amber "view counts updating slowly"
banner should show on a page: it appears only while the most-recent
snapshot day still has frozen channels for that cohort, and
disappears once everyone's been topped up. Per-cohort so Top-5 and
WC2026 are independent.
"""
from __future__ import annotations


def _cohort_ids(db, cohort: str) -> list[str]:
    chans = (db.client.table("channels")
             .select("id,entity_type,competitions").execute().data or [])
    if cohort == "wc2026":
        return [c["id"] for c in chans
                if isinstance(c.get("competitions"), dict)
                and c["competitions"].get("wc2026")]
    return [c["id"] for c in chans
            if c.get("entity_type") in ("Club", "League")]


def cohort_frozen_count(db, cohort: str) -> tuple[int, int]:
    """Return (frozen, total) comparing the two most-recent captured_dates
    present for the cohort. 'frozen' = channels whose latest total_views
    equals the previous day's (YouTube hasn't refreshed them yet).

    Returns (0, total) when there aren't two comparable days yet — i.e.
    no banner before the first comparison is possible. Adapts to whatever
    date-labelling each cohort's daily cron uses (top-5 = yesterday CET,
    wc2026 = UTC) by just taking the two newest dates that exist.
    """
    ids = _cohort_ids(db, cohort)
    if not ids:
        return (0, 0)
    rows = (db.client.table("channel_snapshots")
            .select("captured_date,channel_id,total_views")
            .in_("channel_id", ids)
            .order("captured_date", desc=True)
            .limit(2 * len(ids))
            .execute().data or [])
    by_date: dict[str, dict[str, int]] = {}
    for r in rows:
        by_date.setdefault(r["captured_date"], {})[r["channel_id"]] = \
            int(r.get("total_views") or 0)
    dates = sorted(by_date.keys(), reverse=True)
    if len(dates) < 2:
        return (0, len(ids))
    cur, prev = by_date[dates[0]], by_date[dates[1]]
    frozen = sum(1 for cid, v in cur.items()
                 if cid in prev and v == prev[cid])
    return (frozen, len(cur))


# Shared amber callout. Rendered only when cohort_frozen_count > 0.
LATENCY_NOTICE_HTML = (
    '<div style="background:#3a2e1233;border:1px solid #F5A62355;'
    'border-left:3px solid #F5A623;border-radius:4px;'
    'padding:10px 14px;margin:6px 0 14px 0;'
    'font-size:14px;line-height:1.5;color:#FAFAFA">'
    '⏱️ <b>Heads up — YouTube view counts are updating slowly right now.</b> '
    'YouTube\'s public API has been delaying refreshes of channel '
    'view totals (the same lag shows on YouTube\'s own channel pages — '
    'lifetime views can sit frozen for many hours before they jump). '
    'So today\'s view gains may read low or flat until Google\'s counter '
    'catches up; we backfill the real numbers as soon as it does. Video '
    'counts and new uploads are unaffected.'
    '</div>'
)
