"""Persisted pre-computed dashboard data.

Heavy aggregations (especially the format-trend chart on Daily Recap, which
otherwise scans ~5K video rows per first-of-the-hour visitor) get computed
once after data changes and stored in the `dashboard_cache` table. Streamlit
pages then read a single row (≤1KB) instead of paginating through the videos
table.

Refresh hooks:
    - scripts/daily_refresh.py  → calls rebuild_all() at end (full refresh)
    - scripts/hourly_rss.py     → calls rebuild_all() if new videos found

Read path:
    - views/1_Daily_Recap.py    → reads `format_trend` for current scope
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

# Lookback window for the format-trend chart. Must match what the Daily Recap
# page expects (see views/1_Daily_Recap.py: LOOKBACK_DAYS).
FORMAT_TREND_LOOKBACK_DAYS = 14

# Lookback window for the 30-Day Trends page (views/1c_Trends.py).
# Must match LOOKBACK_DAYS in that view.
TRENDS_30D_LOOKBACK_DAYS = 30

# Bucket dates in CET — matches the Daily Recap page (date picker, KPI counts,
# everything else is CET). UTC bucketing would attribute videos published in
# the late-night CET hours (≈22:00–23:59 UTC) to the wrong day.
CET = ZoneInfo("Europe/Rome")


# ── scope_key helpers ────────────────────────────────────────────────────
def scope_all() -> str:
    return "all"


def scope_league(league_name: str) -> str:
    return f"league:{league_name}"


def scope_club(channel_id: str) -> str:
    return f"club:{channel_id}"


# ── read / write ─────────────────────────────────────────────────────────
def read(db, name: str, scope_key: str) -> dict | None:
    """Return the cached payload, or None if not yet computed."""
    try:
        rows = (
            db.client.table("dashboard_cache")
            .select("payload,computed_at")
            .eq("name", name)
            .eq("scope_key", scope_key)
            .limit(1)
            .execute()
            .data or []
        )
    except Exception:
        return None
    if not rows:
        return None
    payload = rows[0].get("payload")
    if isinstance(payload, str):
        # supabase-py sometimes returns jsonb as a string
        try:
            payload = json.loads(payload)
        except Exception:
            return None
    return {"payload": payload, "computed_at": rows[0].get("computed_at")}


def write(db, name: str, scope_key: str, payload: object) -> None:
    """Upsert a cache row. Silently ignores errors — caller logs if needed."""
    try:
        db.client.table("dashboard_cache").upsert(
            {
                "name": name,
                "scope_key": scope_key,
                "payload": payload,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="name,scope_key",
        ).execute()
    except Exception as e:
        print(f"[dashboard_cache] write failed for {name}/{scope_key}: {e}",
              flush=True)


# ── compute: format trend (per-day Long / Shorts / Live counts) ─────────
def _fetch_videos_window(db, start_iso: str, end_iso: str,
                         channel_ids: list[str] | None) -> list[dict]:
    """Paginated scan of videos.published_at + format + channel_id.

    IMPORTANT: must include .order(...) — without an explicit sort key,
    PostgREST may return rows in different orders across pages, causing
    pagination to drop or duplicate rows.
    """
    page = 1000
    offset = 0
    out: list[dict] = []
    while True:
        q = (
            db.client.table("videos")
            .select("published_at,format,duration_seconds,channel_id")
            .gte("published_at", start_iso)
            .lt("published_at", end_iso)
            .order("published_at")
            .range(offset, offset + page - 1)
        )
        if channel_ids:
            q = q.in_("channel_id", list(channel_ids))
        rows = q.execute().data or []
        out.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return out


def compute_format_trend(db, channel_ids: list[str] | None,
                         lookback_days: int = FORMAT_TREND_LOOKBACK_DAYS) -> dict:
    """Compute per-day Long/Shorts/Live counts for the given filter.

    Returns:
        {
            "as_of": "2026-04-29",
            "rows": [
                {"date": "2026-04-15", "long": 7, "short": 12, "live": 2},
                ...
            ]
        }
    """
    # CET-bucketed (matches the Daily Recap page's KPI line + date picker).
    # Window: [start_cet 00:00 CET, today_cet 00:00 CET) — i.e. all of yesterday
    # and the previous (lookback_days - 1) full CET days.
    today_cet = datetime.now(CET).date()
    start_cet = today_cet - timedelta(days=lookback_days)
    start_iso = datetime.combine(start_cet, datetime.min.time(), tzinfo=CET) \
                       .astimezone(timezone.utc).isoformat()
    end_iso   = datetime.combine(today_cet, datetime.min.time(), tzinfo=CET) \
                       .astimezone(timezone.utc).isoformat()

    videos = _fetch_videos_window(db, start_iso, end_iso, channel_ids)

    buckets: dict[str, dict[str, int]] = {}
    # Pre-seed every date so days with 0 videos still render
    d = start_cet
    while d < today_cet:
        buckets[d.isoformat()] = {"long": 0, "short": 0, "live": 0}
        d += timedelta(days=1)

    for v in videos:
        pub = v.get("published_at") or ""
        try:
            # Convert UTC published_at → CET date
            dkey = datetime.fromisoformat(pub.replace("Z", "+00:00")) \
                           .astimezone(CET).date().isoformat()
        except Exception:
            continue
        if dkey not in buckets:
            continue
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        buckets[dkey][fmt] += 1

    rows = [
        {"date": d, "long": b["long"], "short": b["short"], "live": b["live"]}
        for d, b in sorted(buckets.items())
    ]
    return {"as_of": today_cet.isoformat(), "rows": rows}


# ── compute: 30-Day Trends (views/1c_Trends.py) ──────────────────────────
#
# The trends_30d cache holds *everything* the page needs to render in one
# row-read per scope. There are three payload shapes — one per filter
# zoom level (Z1/Z2/Z3) — and the page picks one based on the global
# filter state. No live DB calls happen on the read path when the cache
# is warm.
#
# Payload shape (per scope):
#   {
#     "as_of":        "2026-05-20",
#     "window_start": "2026-04-20",
#     "window_end":   "2026-05-19",
#     "dates":        ["2026-04-20", ..., "2026-05-19"],   # CET, today excluded
#     "cohort": {
#       "by_date": [{"date": ..., "dv": int, "active_videos": int,
#                    "new_long": int, "new_short": int, "new_live": int}]
#     },
#     "breakdown": {                          # absent / empty for Z3
#       "group_label": "league" | "channel",
#       "groups": [
#         {"key": "Premier League" | <channel_id>,
#          "name": "Premier League" | "Liverpool FC",
#          "color": "#9B6AC9",
#          "sort": 0,
#          "by_date": [{"date": ..., "dv": int, "active_videos": int,
#                       "new_videos": int}]}
#       ]
#     }
#   }


def _trends_30d_window():
    """Return (today_cet date, start_cet date, utc_start_iso, utc_end_iso,
    dates_list_iso)."""
    today_cet = datetime.now(CET).date()
    start_cet = today_cet - timedelta(days=TRENDS_30D_LOOKBACK_DAYS)
    start_iso = datetime.combine(start_cet, datetime.min.time(), tzinfo=CET) \
                       .astimezone(timezone.utc).isoformat()
    end_iso = datetime.combine(today_cet, datetime.min.time(), tzinfo=CET) \
                       .astimezone(timezone.utc).isoformat()
    dates = [(start_cet + timedelta(days=i)).isoformat()
             for i in range(TRENDS_30D_LOOKBACK_DAYS)]
    return today_cet, start_cet, start_iso, end_iso, dates


def _scan_channel_view_deltas(db, channel_ids: list[str],
                                dates: list[str]) -> list[dict]:
    """Per-channel-per-day view-deltas computed from channel_snapshots.

    Used as the numerator for the cohort/per-league/per-channel Δ-views
    series on the 30-Day Trends page. Captures view growth across ALL
    content on the channel (including archive videos still earning),
    not only videos within their 30-day tracking window.

    Need one day before `dates[0]` so the first date can be diffed
    against its predecessor. If a channel was added mid-window or
    is missing snapshots, missing-day pairs are silently skipped (no
    fake zeros).

    Returns rows shaped like video_daily_deltas (channel_id,
    captured_date, view_delta) so the existing _build_cohort_by_date /
    _build_group_by_date can consume them unchanged — `video_id` is
    set to the channel_id (active_videos counts then degenerate to
    "channels active that day", which the page no longer renders).
    """
    if not channel_ids or not dates:
        return []
    from datetime import date as _d, timedelta as _td
    extended_start = (_d.fromisoformat(dates[0]) - _td(days=1)).isoformat()
    rows: list[dict] = []
    PAGE = 1000
    cids = list(channel_ids)
    for cs in range(0, len(cids), 50):
        chunk = cids[cs:cs + 50]
        offset = 0
        while True:
            rs = (db.client.table("channel_snapshots")
                  .select("channel_id,captured_date,total_views")
                  .in_("channel_id", chunk)
                  .gte("captured_date", extended_start)
                  .lte("captured_date", dates[-1])
                  .order("captured_date")
                  .range(offset, offset + PAGE - 1)
                  .execute()).data or []
            rows.extend(rs)
            if len(rs) < PAGE:
                break
            offset += PAGE
    # Group by channel, sort by date, compute consecutive deltas.
    by_ch: dict[str, list[dict]] = {}
    for r in rows:
        by_ch.setdefault(r["channel_id"], []).append(r)
    out: list[dict] = []
    date_set = set(dates)
    for cid, ch_rows in by_ch.items():
        ch_rows.sort(key=lambda r: r["captured_date"])
        for i in range(1, len(ch_rows)):
            cur_d = ch_rows[i]["captured_date"]
            if cur_d not in date_set:
                continue
            prev_v = int(ch_rows[i - 1].get("total_views") or 0)
            cur_v = int(ch_rows[i].get("total_views") or 0)
            out.append({
                "channel_id": cid,
                "captured_date": cur_d,
                "view_delta": cur_v - prev_v,
                # video_id absent in source; reuse channel_id so the
                # active-videos set in _build_*_by_date contains one
                # entry per channel-that-reported-that-day. The page no
                # longer renders this, but keeps the shape unchanged.
                "video_id": cid,
            })
    return out


def _scan_pub_videos(db, channel_ids: list[str],
                     start_iso: str, end_iso: str) -> list[dict]:
    """One paginated scan of videos.published_at filtered to the cohort."""
    out: list[dict] = []
    PAGE = 1000
    cids = list(channel_ids or [])
    if not cids:
        return out
    for cs in range(0, len(cids), 50):
        chunk = cids[cs:cs + 50]
        offset = 0
        while True:
            rs = (db.client.table("videos")
                  .select("id,channel_id,published_at,format,duration_seconds,view_count")
                  .in_("channel_id", chunk)
                  .gte("published_at", start_iso)
                  .lt("published_at", end_iso)
                  .order("published_at")
                  .range(offset, offset + PAGE - 1)
                  .execute()).data or []
            out.extend(rs)
            if len(rs) < PAGE:
                break
            offset += PAGE
    return out


def _pub_to_cet_date(pub: str) -> str | None:
    try:
        return datetime.fromisoformat(pub.replace("Z", "+00:00")) \
                       .astimezone(CET).date().isoformat()
    except Exception:
        return None


def _format_of(v: dict) -> str:
    fmt = (v.get("format") or "").lower()
    if fmt in ("long", "short", "live"):
        return fmt
    return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"


def _build_cohort_by_date(deltas: list[dict], pub_videos: list[dict],
                           dates: list[str]) -> list[dict]:
    """Roll deltas + published videos into the cohort by_date series. Pre-seeds
    every date so days with zero activity render as 0 (no NaN gaps)."""
    date_set = set(dates)
    # deltas: Σ view_delta + distinct video count per date
    dv: dict[str, int] = {d: 0 for d in dates}
    seen: dict[str, set[str]] = {d: set() for d in dates}
    for r in deltas:
        d = r["captured_date"]
        if d not in date_set:
            continue
        dv[d] += int(r.get("view_delta") or 0)
        seen[d].add(r["video_id"])
    # videos: format counts per CET-bucketed date
    new_l: dict[str, int] = {d: 0 for d in dates}
    new_s: dict[str, int] = {d: 0 for d in dates}
    new_v: dict[str, int] = {d: 0 for d in dates}
    for v in pub_videos:
        d = _pub_to_cet_date(v.get("published_at") or "")
        if d not in date_set:
            continue
        f = _format_of(v)
        if f == "long":
            new_l[d] += 1
        elif f == "short":
            new_s[d] += 1
        else:
            new_v[d] += 1
    return [
        {"date": d, "dv": dv[d], "active_videos": len(seen[d]),
         "new_long": new_l[d], "new_short": new_s[d], "new_live": new_v[d]}
        for d in dates
    ]


def _build_group_by_date(deltas: list[dict], pub_videos: list[dict],
                          dates: list[str],
                          row_to_group: callable,
                          group_keys: list[str]) -> dict[str, list[dict]]:
    """Generic per-group rollup. row_to_group(row) returns a hashable group
    key (e.g. league name or channel_id), or None to skip. Returns
    {group_key: by_date_list}. Each entry includes the per-format split
    (new_long/short/live) so the per-league / per-channel summary table
    on the page can render the Long/Shorts/Live column without a second
    query."""
    date_set = set(dates)
    dv: dict[tuple[str, str], int] = {}
    seen: dict[tuple[str, str], set[str]] = {}
    new_l: dict[tuple[str, str], int] = {}
    new_s: dict[tuple[str, str], int] = {}
    new_v: dict[tuple[str, str], int] = {}
    for r in deltas:
        d = r["captured_date"]
        if d not in date_set:
            continue
        g = row_to_group(r)
        if g is None:
            continue
        k = (g, d)
        dv[k] = dv.get(k, 0) + int(r.get("view_delta") or 0)
        seen.setdefault(k, set()).add(r["video_id"])
    for v in pub_videos:
        d = _pub_to_cet_date(v.get("published_at") or "")
        if d not in date_set:
            continue
        g = row_to_group(v)
        if g is None:
            continue
        f = _format_of(v)
        k = (g, d)
        if f == "long":
            new_l[k] = new_l.get(k, 0) + 1
        elif f == "short":
            new_s[k] = new_s.get(k, 0) + 1
        else:
            new_v[k] = new_v.get(k, 0) + 1
    out: dict[str, list[dict]] = {}
    for gk in group_keys:
        out[gk] = [
            {"date": d,
             "dv": dv.get((gk, d), 0),
             "active_videos": len(seen.get((gk, d), set())),
             "new_videos": (new_l.get((gk, d), 0) + new_s.get((gk, d), 0)
                            + new_v.get((gk, d), 0)),
             "new_long": new_l.get((gk, d), 0),
             "new_short": new_s.get((gk, d), 0),
             "new_live": new_v.get((gk, d), 0)}
            for d in dates
        ]
    return out


def _build_top_videos(pub_videos: list[dict], db, chans: list[dict],
                       dates: list[str], limit: int = 25) -> list[dict]:
    """Top-N most-watched videos PUBLISHED in the window, ranked by
    view_count. For an in-window-published video, view_count ≈ the views
    it earned during the window (it didn't exist before it), so this is
    "the best fresh uploads of the last 30 days". Ranks from the lean
    pub-video scan, then one batch fetch for full metadata + a join with
    the channels list. Returns plain dicts the page can render directly."""
    by_view: dict[str, int] = {}
    for r in pub_videos:
        vid = r.get("id")
        if vid:
            by_view[vid] = int(r.get("view_count") or 0)
    if not by_view:
        return []
    top_ids = sorted(by_view.keys(), key=lambda v: -by_view[v])[:limit]
    # Fetch metadata in one or two batches.
    meta_by_id: dict[str, dict] = {}
    PAGE = 100
    for cs in range(0, len(top_ids), PAGE):
        chunk = top_ids[cs:cs + PAGE]
        rs = (db.client.table("videos")
              .select("id,youtube_video_id,title,channel_id,thumbnail_url,"
                      "duration_seconds,format,published_at,view_count,"
                      "like_count,comment_count,category,has_paid_promotion")
              .in_("id", chunk).execute()).data or []
        for r in rs:
            meta_by_id[r["id"]] = r
    ch_by_id = {c["id"]: c for c in chans}
    out: list[dict] = []
    for vid in top_ids:
        m = meta_by_id.get(vid)
        if not m:
            continue
        ch = ch_by_id.get(m.get("channel_id")) or {}
        out.append({
            "id": vid,                       # for renderer compatibility
            "video_id": vid,
            "youtube_video_id": m.get("youtube_video_id"),
            "title": m.get("title") or "",
            "thumbnail_url": m.get("thumbnail_url") or "",
            "duration_seconds": int(m.get("duration_seconds") or 0),
            "format": m.get("format") or "",
            "published_at": m.get("published_at"),
            "view_count": int(m.get("view_count") or 0),
            "like_count": int(m.get("like_count") or 0),
            "comment_count": int(m.get("comment_count") or 0),
            "category": m.get("category") or "",
            "has_paid_promotion": bool(m.get("has_paid_promotion")),
            "channel_id": m.get("channel_id"),
            "channel_name": ch.get("name") or "?",
            "channel_color": ch.get("color") or "#888",
            "channel_color2": ch.get("color2") or "#fff",
            "channel_entity_type": ch.get("entity_type") or "",
            "channel_country": ch.get("country") or "",
        })
    return out


def _split_archive_share(chan_deltas: list[dict],
                          pub_videos: list[dict],
                          row_to_group: callable | None = None,
                          group_keys: list[str] | None = None
                          ) -> dict | list[dict]:
    """Split the period's channel-wide view GROWTH into fresh vs older-than-30d.

    fresh  = Σ view_count of videos PUBLISHED inside the 30-day window. A
             video published in-window didn't exist before it, so its whole
             view_count was earned in-window — i.e. its window growth.
    older  = channel-wide Δ total_views − fresh, clamped ≥ 0
           = view growth from videos published MORE than 30 days ago
             ("archive share").

    Approximate: YouTube's channel-level statistics.viewCount (the
    aggregate behind chan_deltas) and per-video view_count are different
    counters that drift, so `older` carries some of that drift. The
    clamp ≥ 0 swallows the negative-drift / view-purge case.

    If row_to_group + group_keys are provided, returns a list of per-group
    splits (one dict per group, in group_keys order). Otherwise a single
    cohort-wide split dict.

    Schema:
        {
          "channel_view_delta": int,   # channel_snapshots Δ over window
          "fresh_view_total":   int,   # Σ view_count of in-window-published videos
          "archive_view_delta": int,   # channel - fresh, clamped ≥ 0
          "archive_share_pct":  float, # 0..100, one decimal
        }
    """
    def _emp() -> dict:
        return {"channel_view_delta": 0, "fresh_view_total": 0,
                "archive_view_delta": 0, "archive_share_pct": 0.0}

    def _finalise(d: dict) -> dict:
        cw = max(d["channel_view_delta"], 0)
        fr = max(d["fresh_view_total"], 0)
        archive = max(cw - fr, 0)
        pct = (archive / cw * 100.0) if cw > 0 else 0.0
        return {
            "channel_view_delta": cw,
            "fresh_view_total": fr,
            "archive_view_delta": archive,
            "archive_share_pct": round(pct, 1),
        }

    if row_to_group is None:
        agg = _emp()
        for r in chan_deltas:
            agg["channel_view_delta"] += int(r.get("view_delta") or 0)
        for r in pub_videos:
            agg["fresh_view_total"] += int(r.get("view_count") or 0)
        return _finalise(agg)

    per: dict[str, dict] = {gk: _emp() for gk in (group_keys or [])}
    for r in chan_deltas:
        g = row_to_group(r)
        if g in per:
            per[g]["channel_view_delta"] += int(r.get("view_delta") or 0)
    for r in pub_videos:
        g = row_to_group(r)
        if g in per:
            per[g]["fresh_view_total"] += int(r.get("view_count") or 0)
    return [_finalise(per[gk]) for gk in (group_keys or [])]


# Viral-breakout detector — see the 30-Day Trends "Viral this month" widget.
VIRAL_FLOOR = 100_000     # peak day must clear this (absolute notability)
VIRAL_SPIKE = 3.0         # peak ≥ 3× the video's own median day (momentum gate)


def _gapfill_series(series: list) -> list:
    """Spread gap-spanning deltas evenly across missing days.

    `series` = [(date_iso, delta), …]. A video_daily_deltas row only exists
    when we snapshotted the video, and its delta = gain since the PREVIOUS
    snapshot — so a multi-day gap dumps several days' growth onto one
    "catch-up" day (e.g. +2.7M on a single day that really spans 3). We
    redistribute each delta evenly across the gap it spans (prev_date+1 …
    cur_date), preserving the total, so the curve shows real daily pace
    instead of fake single-day spikes. The first entry is kept as-is (no
    in-series predecessor to gap against)."""
    from datetime import date as _date, timedelta as _td
    if not series:
        return series
    out: list = []
    prev = None
    for ds, delta in sorted(series):
        cur = _date.fromisoformat(ds)
        if prev is not None and (cur - prev).days > 1:
            gap = (cur - prev).days
            per = delta / gap
            for k in range(1, gap + 1):
                out.append(((prev + _td(days=k)).isoformat(), per))
        else:
            out.append((ds, float(delta)))
        prev = cur
    return out


def _drop_catchup_first(series_sorted: list, published_at) -> list:
    """Drop a video's first measured delta when measurement began MORE than
    one day after publish — that first point is a "catch-up" that collapses
    all the views the video earned before we started tracking it onto a
    single day, a fake spike. A series tracked from launch (first measure
    ≤1 day after publish) is left intact, so genuine day-one surges survive.
    `series_sorted` = [(date_iso, delta), …] sorted ascending."""
    if not series_sorted or not published_at:
        return series_sorted
    from datetime import date as _date
    try:
        pub_d = _date.fromisoformat(str(published_at)[:10])
        first_d = _date.fromisoformat(str(series_sorted[0][0])[:10])
    except Exception:
        return series_sorted
    return series_sorted[1:] if (first_d - pub_d).days > 1 else series_sorted


def _score_viral_series(raw_series: list, sub: int):
    """Gap-fill + score one video's daily-delta series for the viral blend.
    Returns (blend, peak, total, gapfilled_series) or None if it fails the
    momentum gate / absolute floor. Shared by the cache builder and the
    pages' live fallbacks so the rules never diverge."""
    import math
    import statistics
    s = _gapfill_series(raw_series)
    deltas = [d for _, d in s]
    if not deltas:
        return None
    peak = max(deltas)
    med = statistics.median(deltas) if deltas else 0
    if peak < VIRAL_FLOOR or peak < VIRAL_SPIKE * max(med, 1):
        return None
    total = sum(deltas)
    return (total / math.sqrt(max(sub, 1)), peak, total, s)


def _build_viral(db, channel_ids: list[str], chans: list[dict],
                 dates: list[str], limit: int) -> list[dict]:
    """Top-N viral 'breakouts' over the window: videos (≤30 days old) ranked
    by the Blend score = views gained ÷ √subscribers, momentum-gated (peak
    day ≥ VIRAL_SPIKE× the video's own median day AND ≥ VIRAL_FLOOR) so only
    genuine spikes qualify. The first measured delta of a video we joined
    mid-life is dropped (catch-up artifact). Each item carries its daily
    series so the page can draw an inline 30-day trajectory."""
    if not channel_ids:
        return []
    subs_by_id = {c["id"]: int(c.get("subscriber_count") or 0) for c in chans}
    series: dict[str, list] = {}
    vch: dict[str, str] = {}
    PAGE = 1000
    cids = list(channel_ids)
    for cs in range(0, len(cids), 50):
        chunk = cids[cs:cs + 50]
        off = 0
        while True:
            rs = (db.client.table("video_daily_deltas")
                  .select("video_id,channel_id,captured_date,view_delta")
                  .in_("channel_id", chunk)
                  .gte("captured_date", dates[0])
                  .lte("captured_date", dates[-1])
                  .order("captured_date")
                  .range(off, off + PAGE - 1).execute().data) or []
            for r in rs:
                series.setdefault(r["video_id"], []).append(
                    (r["captured_date"], int(r.get("view_delta") or 0)))
                vch[r["video_id"]] = r["channel_id"]
            if len(rs) < PAGE:
                break
            off += PAGE
    if not series:
        return []
    raw = {vid: sorted(s) for vid, s in series.items()}
    # First pass (uncorrected) just to pick candidates cheaply, so we only
    # fetch publish dates for the handful that could make the cut.
    cand = []
    for vid, s0 in raw.items():
        sc = _score_viral_series(s0, subs_by_id.get(vch[vid], 0))
        if sc:
            cand.append((sc[0], vid))
    cand.sort(reverse=True)
    cand_ids = [vid for _, vid in cand[:max(limit * 5, 50)]]
    pub: dict[str, str] = {}
    for cs in range(0, len(cand_ids), 200):
        rs = (db.client.table("videos").select("id,published_at")
              .in_("id", cand_ids[cs:cs + 200]).execute().data) or []
        for r in rs:
            pub[r["id"]] = r.get("published_at")
    # Second pass: drop each candidate's catch-up first delta, re-score.
    scored = []
    for vid in cand_ids:
        sc = _score_viral_series(
            _drop_catchup_first(raw[vid], pub.get(vid)),
            subs_by_id.get(vch[vid], 0))
        if not sc:
            continue
        blend, peak, total, s = sc
        sub = max(subs_by_id.get(vch[vid], 0), 1)
        scored.append((blend, vid, peak, total, sub, s))
    scored.sort(reverse=True)
    top = scored[:limit]
    ids = [t[1] for t in top]
    meta: dict[str, dict] = {}
    for cs in range(0, len(ids), 100):
        rs = (db.client.table("videos")
              .select("id,youtube_video_id,title,channel_id,"
                      "thumbnail_url,published_at")
              .in_("id", ids[cs:cs + 100]).execute().data) or []
        for r in rs:
            meta[r["id"]] = r
    out = []
    for score, vid, peak, total, sub, s in top:
        m = meta.get(vid)
        if not m:
            continue
        out.append({
            "youtube_video_id": m.get("youtube_video_id"),
            "title": m.get("title") or "",
            "thumbnail_url": m.get("thumbnail_url") or "",
            "published_at": m.get("published_at"),
            "channel_id": m.get("channel_id"),
            "peak": int(round(peak)), "total": int(round(total)), "subs": sub,
            "reach": total / sub,
            "series": [[d, int(round(x))] for d, x in s],
        })
    return out


def compute_trends_30d_all(db, chans: list[dict],
                           leagues_to_channels: dict[str, list[str]]) -> dict:
    """Z1 payload — cohort across all 101 top-5 channels (clubs + League HQs)
    + per-league breakdown for the 5 leagues."""
    from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_COLOR_CHART
    today_cet, start_cet, start_iso, end_iso, dates = _trends_30d_window()

    # Cohort = all clubs + the 5 League channels (entity_type Club|League).
    cohort_ids = [c["id"] for c in chans
                  if c.get("entity_type") in ("Club", "League")]
    # Cohort + breakdown Δ-views: derived from channel_snapshots so the
    # number is channel-wide (captures archive videos still earning).
    # We ALSO pull video_daily_deltas — partly for the Top-25 ranking
    # at the bottom of the page, and partly to compute the "archive
    # share" split: channel_Δ - tracked_Δ = how much of the cohort's
    # view growth came from videos OLDER than their 30-day post-publish
    # tracking window. Surfaces as totals_split.archive_share_pct in
    # the payload + a KPI on the page.
    chan_deltas = _scan_channel_view_deltas(db, cohort_ids, dates)
    pubs = _scan_pub_videos(db, cohort_ids, start_iso, end_iso)
    cohort = _build_cohort_by_date(chan_deltas, pubs, dates)

    # League key resolver — works for both clubs and League HQ channels.
    # Country-based mapping is used uniformly so quirky brand names
    # ("LALIGA EA SPORTS", "Ligue 1 McDonald's") still resolve to their
    # canonical league.
    ch_to_league: dict[str, str] = {}
    for c in chans:
        if c.get("entity_type") in ("Club", "League"):
            lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
            if lg:
                ch_to_league[c["id"]] = lg

    LEAGUES = ["Premier League", "La Liga", "Serie A", "Bundesliga", "Ligue 1"]
    per_league = _build_group_by_date(
        chan_deltas, pubs, dates,
        row_to_group=lambda r: ch_to_league.get(r.get("channel_id")),
        group_keys=LEAGUES,
    )
    # Fresh (in-window-published) vs older-than-30d split — cohort + per-league
    cohort_split = _split_archive_share(chan_deltas, pubs)
    league_splits = _split_archive_share(
        chan_deltas, pubs,
        row_to_group=lambda r: ch_to_league.get(r.get("channel_id")),
        group_keys=LEAGUES,
    )
    groups = [
        {"key": lg, "name": lg, "color": LEAGUE_COLOR_CHART.get(lg, "#888"),
         "sort": i, "by_date": per_league[lg],
         "split": league_splits[i]}
        for i, lg in enumerate(LEAGUES)
    ]
    return {
        "as_of": today_cet.isoformat(),
        "window_start": dates[0], "window_end": dates[-1],
        "dates": dates,
        "cohort": {"by_date": cohort, "split": cohort_split},
        "breakdown": {"group_label": "league", "groups": groups},
        "top_videos": _build_top_videos(pubs, db, chans, dates, limit=25),
        "viral": _build_viral(db, cohort_ids, chans, dates, limit=10),
    }


def compute_trends_30d_league(db, league: str,
                              channel_ids: list[str],
                              chans: list[dict]) -> dict:
    """Z2 payload — cohort summed across this league's channels + per-channel
    breakdown (one line per club + the League HQ)."""
    today_cet, start_cet, start_iso, end_iso, dates = _trends_30d_window()
    if not channel_ids:
        return {
            "as_of": today_cet.isoformat(),
            "window_start": dates[0], "window_end": dates[-1],
            "dates": dates,
            "cohort": {"by_date": _build_cohort_by_date([], [], dates)},
            "breakdown": {"group_label": "channel", "groups": []},
        }

    # Channel-snapshot deltas drive the cohort + per-channel Δ-views series.
    # Per-video deltas (video_daily_deltas) only feed the Top-25 list.
    chan_deltas = _scan_channel_view_deltas(db, channel_ids, dates)
    pubs = _scan_pub_videos(db, channel_ids, start_iso, end_iso)
    cohort = _build_cohort_by_date(chan_deltas, pubs, dates)

    # Sort channels: League HQ first, clubs A→Z. Carry name + brand colour.
    cohort_set = set(channel_ids)
    z2_chans = [c for c in chans if c["id"] in cohort_set]
    z2_chans.sort(key=lambda c: (
        0 if c.get("entity_type") == "League" else 1,
        (c.get("name") or "").lower(),
    ))
    cid_order = [c["id"] for c in z2_chans]
    per_channel = _build_group_by_date(
        chan_deltas, pubs, dates,
        row_to_group=lambda r: r.get("channel_id"),
        group_keys=cid_order,
    )
    name_of = {c["id"]: (c.get("name") or "?") for c in z2_chans}
    color_of = {c["id"]: (c.get("color") or "#888") for c in z2_chans}
    # Cohort split + per-channel splits.
    cohort_split = _split_archive_share(chan_deltas, pubs)
    channel_splits = _split_archive_share(
        chan_deltas, pubs,
        row_to_group=lambda r: r.get("channel_id"),
        group_keys=cid_order,
    )
    groups = [
        {"key": cid, "name": name_of[cid],
         "color": color_of[cid], "sort": i,
         "by_date": per_channel[cid],
         "split": channel_splits[i]}
        for i, cid in enumerate(cid_order)
    ]
    return {
        "as_of": today_cet.isoformat(),
        "window_start": dates[0], "window_end": dates[-1],
        "dates": dates,
        "cohort": {"by_date": cohort, "split": cohort_split},
        "breakdown": {"group_label": "channel", "groups": groups},
        "top_videos": _build_top_videos(pubs, db, chans, dates, limit=25),
        "viral": _build_viral(db, channel_ids, chans, dates, limit=10),
    }


def compute_trends_30d_club(db, channel_id: str,
                              chans: list[dict] | None = None) -> dict:
    """Z3 payload — single channel, cohort series only. Breakdown is empty
    (per-channel-of-one-channel would be redundant with the cohort line).
    Cohort Δ-views uses channel_snapshots; Top-25 still uses per-video
    deltas so the ranking remains meaningful at single-channel scope."""
    today_cet, start_cet, start_iso, end_iso, dates = _trends_30d_window()
    chan_deltas = _scan_channel_view_deltas(db, [channel_id], dates)
    pubs = _scan_pub_videos(db, [channel_id], start_iso, end_iso)
    cohort = _build_cohort_by_date(chan_deltas, pubs, dates)
    if chans is None:
        chans = db.get_all_channels()
    return {
        "as_of": today_cet.isoformat(),
        "window_start": dates[0], "window_end": dates[-1],
        "dates": dates,
        "cohort": {"by_date": cohort,
                    "split": _split_archive_share(chan_deltas, pubs)},
        "breakdown": {"group_label": "channel", "groups": []},
        "top_videos": _build_top_videos(pubs, db, chans, dates, limit=25),
        "viral": _build_viral(db, [channel_id], chans, dates, limit=5),
    }


def refresh_trends_30d(db, log=print, channels: list[dict] | None = None,
                       tier: str = "hot",
                       refresh_vibes: bool = True) -> None:
    """Recompute and persist trends_30d for the requested tier.

      tier="hot"  → Z1 (scope_all) + 5 × Z2 (scope_league)
                    Cheap; runs on every rebuild_all (hourly + nightly).
      tier="cold" → 101 × Z3 (scope_club) — every cohort channel.
                    Heavier; runs nightly from scripts/daily_refresh.py
                    after the hot pass.

    refresh_vibes: when running tier="cold", also regenerate the
      trends_30d_vibe AI notes (Z1 + 5 × Z2). Default True so the
      nightly cron stays self-contained. Manual data-correction
      scripts (resnap_channel_views, interpolate_frozen_runs) pass
      False since they only need the cache rows rebuilt — re-firing
      ~6 haiku calls per recovery run wastes LLM quota.

    The 101 channels = 96 Clubs + 5 League HQs in the top-5 cohort.
    """
    from src.channels import COUNTRY_TO_LEAGUE
    chans = channels if channels is not None else db.get_all_channels()
    # Season cohort gate (Top-5) — see rebuild_all. Idempotent if `chans`
    # was already gated upstream. No-op at 25/26.
    from src.season_cohort import (
        resolve_active_season, get_season_cohort_ids, filter_to_season_cohort,
    )
    chans = filter_to_season_cohort(
        chans, get_season_cohort_ids(db, resolve_active_season(db)))
    clubs = [c for c in chans if c.get("entity_type") == "Club"]
    # All 5 top-5 League HQ channels — entity_type alone identifies them.
    leagues_chs = [c for c in chans if c.get("entity_type") == "League"]

    # League → channel_ids (clubs in that league + that league's HQ).
    # Country-based mapping handles both clubs and League HQ uniformly,
    # so the brand-name leagues (LALIGA EA SPORTS, Ligue 1 McDonald's)
    # still attach to their canonical league key.
    league_to_chs: dict[str, list[str]] = {}
    for c in clubs + leagues_chs:
        lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
        if lg:
            league_to_chs.setdefault(lg, []).append(c["id"])

    if tier == "hot":
        log("[dashboard_cache] trends_30d / all")
        payload = compute_trends_30d_all(db, chans, league_to_chs)
        write(db, "trends_30d", scope_all(), payload)
        for lg, cids in league_to_chs.items():
            log(f"[dashboard_cache] trends_30d / league:{lg} "
                f"({len(cids)} channels)")
            payload = compute_trends_30d_league(db, lg, cids, chans)
            write(db, "trends_30d", scope_league(lg), payload)
    elif tier == "cold":
        cohort = clubs + leagues_chs   # 96 + 5 = 101
        for i, c in enumerate(cohort, 1):
            log(f"[dashboard_cache] trends_30d / club:{c['id']} "
                f"({c.get('name')}) — {i}/{len(cohort)}")
            try:
                payload = compute_trends_30d_club(db, c["id"], chans=chans)
                write(db, "trends_30d", scope_club(c["id"]), payload)
            except Exception as e:
                log(f"  failed (non-fatal): {e}")
        # AI vibe note runs once at the end of the cold tier — keeps it
        # to a nightly cadence even when the hot tier ticks hourly via
        # rebuild_all. Z1 + 5 × Z2 ≈ 6 haiku calls per day; Z3 is
        # skipped intentionally (a per-club vibe over 30 days would be
        # ~100 calls/night for marginal narrative value).
        # Manual recovery scripts pass refresh_vibes=False to skip this.
        if refresh_vibes:
            refresh_trends_30d_vibe(db, log=log)
        else:
            log("[dashboard_cache] skipping trends_30d_vibe regen "
                "(refresh_vibes=False)")
    else:
        log(f"[dashboard_cache] refresh_trends_30d: unknown tier '{tier}'")


def refresh_trends_30d_vibe(db, log=print) -> None:
    """Read back the just-written trends_30d cache rows and generate
    a short Claude vibe note per scope (scope_all + 5 × scope_league).
    Persists under cache key 'trends_30d_vibe' so the page reads a
    single row at render time.

    Best-effort; failures are non-fatal (the page already falls back
    to no-note when the row is missing)."""
    try:
        from src import ai_note as _an2
        from src.channels import COUNTRY_TO_LEAGUE
        _chans_for_decor = db.get_all_channels()

        def _html(_note: str) -> str:
            # Badge + YouTube-link decoration on each recognized name,
            # then <br>-ify line breaks for HTML rendering.
            return _an2.decorate_with_badges(_note, _chans_for_decor) \
                       .replace("\n", "<br>")

        # Z1 — All Leagues
        z1 = read(db, "trends_30d", scope_all())
        z1_payload = (z1 or {}).get("payload") if z1 else None
        if z1_payload:
            log("[dashboard_cache] computing trends_30d_vibe / all")
            note = _an2.generate_trends_30d_vibe(z1_payload, log=log)
            if note:
                write(db, "trends_30d_vibe", scope_all(),
                      {"text": note, "html": _html(note)})
                log(f"[dashboard_cache] trends_30d_vibe/all WRITTEN "
                    f"({len(note)} chars)")
            else:
                log("[dashboard_cache] trends_30d_vibe/all skipped — empty")
        else:
            log("[dashboard_cache] trends_30d_vibe/all skipped — no payload")

        # Z2 — one note per top-5 league
        for lg in ("Premier League", "La Liga", "Serie A",
                   "Bundesliga", "Ligue 1"):
            row = read(db, "trends_30d", scope_league(lg))
            row_payload = (row or {}).get("payload") if row else None
            if not row_payload:
                log(f"[dashboard_cache] trends_30d_vibe/league:{lg} "
                    f"skipped — no payload")
                continue
            log(f"[dashboard_cache] computing trends_30d_vibe / league:{lg}")
            note = _an2.generate_trends_30d_vibe(row_payload, league=lg, log=log)
            if note:
                write(db, "trends_30d_vibe", scope_league(lg),
                      {"text": note, "html": _html(note)})
                log(f"[dashboard_cache] trends_30d_vibe/league:{lg} "
                    f"WRITTEN ({len(note)} chars)")
            else:
                log(f"[dashboard_cache] trends_30d_vibe/league:{lg} "
                    f"skipped — empty")
    except Exception as e:
        log(f"[dashboard_cache] trends_30d_vibe failed (non-fatal): {e}")


# ── rebuild orchestrator ─────────────────────────────────────────────────
def rebuild_all(db, log=print) -> None:
    """Recompute and persist every cached aggregation across every scope.

    Called by daily_refresh and hourly_rss when underlying data has changed.
    Cheap: ~6 queries × ≤5K rows each, ~5–10 seconds total.
    """
    chans = db.get_all_channels()
    # Season cohort gate (Top-5): drop CLUBS not in the ACTIVE season so
    # the cron-built caches match the display gate — a promoted club that's
    # tracked but not yet shown contributes no cached rows. Non-club
    # channels (Leagues, WC2026, …) pass through. No-op at 25/26.
    from src.season_cohort import (
        resolve_active_season, get_season_cohort_ids, filter_to_season_cohort,
    )
    chans = filter_to_season_cohort(
        chans, get_season_cohort_ids(db, resolve_active_season(db)))
    clubs = [c for c in chans
             if c.get("entity_type") == "Club"]
    leagues_to_clubs: dict[str, list[str]] = {}
    for c in clubs:
        from src.channels import COUNTRY_TO_LEAGUE
        lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
        if lg:
            leagues_to_clubs.setdefault(lg, []).append(c["id"])

    all_club_ids = [c["id"] for c in clubs]

    # 1. format_trend: ALL clubs
    log("[dashboard_cache] computing format_trend / all")
    payload = compute_format_trend(db, all_club_ids)
    write(db, "format_trend", scope_all(), payload)

    # 2. format_trend: per-league
    for lg, ids in leagues_to_clubs.items():
        log(f"[dashboard_cache] computing format_trend / league:{lg} ({len(ids)} clubs)")
        payload = compute_format_trend(db, ids)
        write(db, "format_trend", scope_league(lg), payload)

    # 2b. trends_30d (hot tier) — scope_all + 5 × scope_league. Powers
    #     views/1c_Trends.py. Cold tier (per-club) is run separately
    #     from scripts/daily_refresh.py to keep the hourly path light.
    try:
        refresh_trends_30d(db, log=log, channels=chans, tier="hot")
    except Exception as e:
        log(f"[dashboard_cache] trends_30d (hot) failed (non-fatal): {e}")

    # 3. daily_note: yesterday's witty AI commentary (best-effort).
    # Decoration (inline country flags before each club/league name)
    # happens here at write time, so pages just read ready-to-render HTML.
    try:
        import os as _os
        from src import ai_note as _an
        from datetime import datetime as _dt, timedelta as _td
        target = (_dt.now(CET).date() - _td(days=1))
        _key_present = bool(_os.environ.get("ANTHROPIC_API_KEY"))
        log(f"[dashboard_cache] computing daily_note / {target.isoformat()} "
            f"(ANTHROPIC_API_KEY {'set' if _key_present else 'MISSING'})")
        payload = _an.compose_payload(db, target)
        prev_notes = _an.fetch_previous_notes(db, target, n=3)
        log(f"[dashboard_cache] previous_notes for context: {len(prev_notes)} day(s)")
        note = _an.generate_daily_note(payload, previous_notes=prev_notes, log=log)
        if note:
            # Bake <br> per sentence into the HTML so consumers don't need
            # to handle the per-line layout themselves (Streamlit's
            # markdown processor collapses bare newlines).
            html = _an.decorate_with_badges(note, chans).replace("\n", "<br>")
            write(db, "daily_note", target.isoformat(),
                  {"text": note,         # raw model output (kept for debug)
                   "html": html,         # decoration-ready HTML used by pages
                   "payload_summary": {
                      "total_new_videos": payload["totals"]["new_videos"],
                      "weekday": payload["weekday"],
                   }})
            log(f"[dashboard_cache] daily_note WRITTEN ({len(note)} chars)")
        else:
            log(f"[dashboard_cache] daily_note skipped — generator returned empty")

        # Z2 — per-league daily note. Same machinery, league-scoped
        # payload + league-scoped anti-repetition, composite cache key
        # f"{date}|{league}". The Daily Recap page reads this when a
        # single league is selected; Home stays on the Z1 date key.
        for _lg in ("Serie A", "Premier League", "La Liga",
                    "Bundesliga", "Ligue 1"):
            try:
                _key = f"{target.isoformat()}|{_lg}"
                _pl = _an.compose_payload(db, target, league=_lg)
                _prev = _an.fetch_previous_notes(db, target, n=3, league=_lg)
                _nl = _an.generate_daily_note(_pl, previous_notes=_prev, log=log)
                if _nl:
                    _hl = _an.decorate_with_badges(_nl, chans).replace("\n", "<br>")
                    write(db, "daily_note", _key,
                          {"text": _nl, "html": _hl,
                           "payload_summary": {
                              "total_new_videos": _pl["totals"]["new_videos"],
                              "weekday": _pl["weekday"]}})
                    log(f"[dashboard_cache] daily_note/{_lg} WRITTEN "
                        f"({len(_nl)} chars)")
                else:
                    log(f"[dashboard_cache] daily_note/{_lg} skipped "
                        f"— generator returned empty")
            except Exception as _e:
                log(f"[dashboard_cache] daily_note/{_lg} failed "
                    f"(non-fatal): {_e}")
    except Exception as e:
        log(f"[dashboard_cache] daily_note failed (non-fatal): {e}")

    # 4. latest_vibe — share the same lightweight helper used by hourly_rss,
    # so the rebuild path stays a single source of truth.
    refresh_latest_vibe(db, log=log, channels=chans)

    # 4b. season_vibe — nightly only (season aggregates change once a
    # day). Runs after the season recompute earlier in the rebuild so
    # it narrates fresh season_* numbers.
    refresh_season_vibe(db, log=log, channels=chans)

    # 5. profile_sentences — one AI line per flagged club (Outliers page).
    refresh_profile_sentences(db, log=log, channels=chans)

    # 6. duration_buckets — Season Shorts / Long distribution charts.
    refresh_duration_buckets(db, log=log)

    # 7. publish_cadence — Season per-month video counts. Two payloads:
    #    "all"      → per-league per-month (zoom-1 chart)
    #    "league:X" → per-format (Long/Shorts/Live) per-month for league X
    refresh_publish_cadence(db, log=log, channels=chans)

    # 8. concentration — per-club Pareto stats (% of videos = 80% of views,
    #    top-1, top-10 share, median, mean). One payload per league.
    refresh_concentration(db, log=log, channels=chans)

    # 9. home_top — Top-5 view gainers + Top-5 publishers (last 7 days).
    #    The Home page is unauthenticated and high-traffic; this saves
    #    a 14-day snapshot scan + 7-day video pagination per cold pageview.
    refresh_home_top(db, log=log, channels=chans)

    # 10. publishing_pulse — Season videos-per-day + zero-day-counts per
    #     scope. Powers the column charts on the Season page (Z1 + Z2).
    #     Z3 reuses df_vids client-side, no entry needed.
    try:
        refresh_publishing_pulse(db, log=log, channels=chans)
    except Exception as e:
        log(f"[dashboard_cache] publishing_pulse failed (non-fatal): {e}")

    # 11. season_top — Three ranked tables (top 100 by views, top 5 by
    #     likes, top 5 by comments) per scope. Powers Season Top page
    #     for Z1 + Z2 — read path becomes a single dashboard_cache row.
    #     Z3 (single club) skipped — those queries are fast enough live.
    try:
        refresh_season_top(db, log=log, channels=chans)
    except Exception as e:
        log(f"[dashboard_cache] season_top failed (non-fatal): {e}")

    # 11b. season_top_vibe — AI read of the season's biggest hits.
    # Runs right after season_top because it reads that cache row.
    refresh_season_top_vibe(db, log=log, channels=chans)

    # 12. top_no1_videos — Each core channel's all-time #1 most-viewed
    #     video, sorted by views. Powers the No. 1 Videos page (top table).
    try:
        refresh_top_no1_videos(db, log=log, channels=chans)
    except Exception as e:
        log(f"[dashboard_cache] top_no1_videos failed (non-fatal): {e}")

    # 13. season_top_no1_videos — Same shape as top_no1_videos but
    #     restricted to the current season. Powers No. 1 Videos page
    #     (bottom table).
    try:
        refresh_season_top_no1_videos(db, log=log, channels=chans)
    except Exception as e:
        log(f"[dashboard_cache] season_top_no1_videos failed (non-fatal): {e}")

    # 14. wc2026 — 48 qualified federations + FIFA + 6 confederations.
    #     Page reads this directly instead of filtering channels live.
    try:
        refresh_wc2026(db, log=log, channels=chans)
    except Exception as e:
        log(f"[dashboard_cache] wc2026 failed (non-fatal): {e}")

    log("[dashboard_cache] rebuild done")


# Bucket definitions used by Season-page duration distribution charts.
# Kept here so cron + page render share a single source of truth.
DURATION_BUCKETS_SHORTS = [
    (0, 6), (7, 12), (13, 18), (19, 24), (25, 30),
    (31, 36), (37, 42), (43, 48), (49, 54), (55, 60),
]
DURATION_BUCKETS_LONG = [
    (60,    180,    "1-3m"),
    (181,   300,    "3-5m"),
    (301,   600,    "5-10m"),
    (601,   900,    "10-15m"),
    (901,   1800,   "15-30m"),
    (1801,  3600,   "30-60m"),
    (3601,  5400,   "60-90m"),
    (5401,  86400,  "90m+"),
]
# Derived from src.channels.DEFAULT_SEASON_START so this auto-rolls
# when the new European season starts. Was hardcoded "2025-08-01".
from src.channels import DEFAULT_SEASON_START as _DEFAULT_SEASON_START
from src.cohort import is_top5_cohort
DURATION_SEASON_SINCE: str = _DEFAULT_SEASON_START


def refresh_duration_buckets(db, log=print) -> None:
    """Pre-compute Season Shorts / Long duration-distribution buckets.

    Same data the Season page used to load live on every cold cache —
    ~25K shorts + ~15K longs paginated. Now computed once a night,
    written to dashboard_cache, read instantly by the page.
    """
    from src.database import _fetch_all

    try:
        # ── Shorts ────────────────────────────────────────────
        log(f"[dashboard_cache] computing duration_buckets/shorts")
        agg_s = {f"{lo}-{hi}s": {"label": f"{lo}-{hi}s", "lo": lo,
                                 "views": 0, "videos": 0}
                 for lo, hi in DURATION_BUCKETS_SHORTS}
        rows = _fetch_all(
            db.client.table("videos")
            .select("duration_seconds,view_count")
            .eq("format", "short")
            .gte("published_at", DURATION_SEASON_SINCE)
        )
        for r in rows:
            d = int(r.get("duration_seconds") or 0)
            v = int(r.get("view_count") or 0)
            if not (0 <= d <= 60):
                continue
            for lo, hi in DURATION_BUCKETS_SHORTS:
                if lo <= d <= hi:
                    key = f"{lo}-{hi}s"
                    agg_s[key]["views"] += v
                    agg_s[key]["videos"] += 1
                    break
        shorts_out = []
        for lo, hi in DURATION_BUCKETS_SHORTS:
            b = agg_s[f"{lo}-{hi}s"]
            b["avg_views"] = (b["views"] // b["videos"]) if b["videos"] else 0
            shorts_out.append(b)
        write(db, "duration_buckets", "shorts",
              {"buckets": shorts_out, "since": DURATION_SEASON_SINCE})
        log(f"[dashboard_cache] duration_buckets/shorts WRITTEN ({len(rows)} rows)")

        # ── Long ──────────────────────────────────────────────
        log(f"[dashboard_cache] computing duration_buckets/long")
        agg_l = {label: {"label": label, "lo": lo, "views": 0, "videos": 0}
                 for lo, _, label in DURATION_BUCKETS_LONG}
        rows = _fetch_all(
            db.client.table("videos")
            .select("duration_seconds,view_count")
            .eq("format", "long")
            .gte("published_at", DURATION_SEASON_SINCE)
        )
        for r in rows:
            d = int(r.get("duration_seconds") or 0)
            v = int(r.get("view_count") or 0)
            if d < 60:
                continue
            for lo, hi, label in DURATION_BUCKETS_LONG:
                if lo <= d <= hi:
                    agg_l[label]["views"] += v
                    agg_l[label]["videos"] += 1
                    break
        long_out = []
        for _, _, label in DURATION_BUCKETS_LONG:
            b = agg_l[label]
            b["avg_views"] = (b["views"] // b["videos"]) if b["videos"] else 0
            long_out.append(b)
        write(db, "duration_buckets", "long",
              {"buckets": long_out, "since": DURATION_SEASON_SINCE})
        log(f"[dashboard_cache] duration_buckets/long WRITTEN ({len(rows)} rows)")
    except Exception as e:
        log(f"[dashboard_cache] duration_buckets failed (non-fatal): {e}")


def refresh_publish_cadence(db, log=print, channels: list[dict] | None = None) -> None:
    """Pre-compute publish-cadence chart payloads (Season page).

    Two scopes:
      - "all"        → per-league per-month video counts (zoom-1 chart).
                       Limited to Top-5 league channels + their clubs.
      - "league:<L>" → per-format (Long / Shorts / Live) per-month video
                       counts for clubs/league of league L (zoom-2 chart).

    Single big paginated read of (channel_id, published_at, format,
    duration_seconds), then in-memory aggregation. Cron-cheap.
    """
    from src.database import _fetch_all
    from src.channels import COUNTRY_TO_LEAGUE
    from datetime import date as _date

    try:
        chans = channels if channels is not None else db.get_all_channels()
        # Build channel_id → league mapping. Only Top-5 leagues + their
        # clubs / League channels are in scope; Players / Federations /
        # OtherClubs / WomenClubs are excluded by entity_type.
        ch_to_lg: dict[str, str] = {}
        ch_format: dict[str, str] = {}  # for downstream optional uses
        for c in chans:
            if c.get("entity_type") not in ("Club", "League"):
                continue
            lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
            if not lg:
                continue
            ch_to_lg[c["id"]] = lg
        if not ch_to_lg:
            log("[dashboard_cache] publish_cadence: no in-scope channels, skipping")
            return

        log(f"[dashboard_cache] computing publish_cadence ({len(ch_to_lg)} channels)")
        rows = _fetch_all(
            db.client.table("videos")
            .select("channel_id,published_at,format,duration_seconds,view_count")
            .gte("published_at", DURATION_SEASON_SINCE)
        )

        # all → {league: {YYYY-MM: {videos, views}}}
        league_month: dict[str, dict[str, dict[str, int]]] = {}
        # per-league → {YYYY-MM: {format: {videos, views}}}
        league_fmt_month: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
        # Publishing-rhythm grids (CET). 7 dows × 24 hours.
        # all → 2D counts + sum_views (for avg_views/cell)
        # per-league → same shape
        from datetime import datetime as _dt
        def _empty_grid():
            return [[0] * 24 for _ in range(7)]
        all_count = _empty_grid()
        all_views = _empty_grid()
        league_count: dict[str, list] = {}
        league_views: dict[str, list] = {}

        for r in rows:
            cid = r.get("channel_id")
            lg = ch_to_lg.get(cid)
            if not lg:
                continue
            pa = r.get("published_at") or ""
            if len(pa) < 7:
                continue
            month = pa[:7]
            v = int(r.get("view_count") or 0)
            bucket = league_month.setdefault(lg, {}).setdefault(
                month, {"videos": 0, "views": 0}
            )
            bucket["videos"] += 1
            bucket["views"] += v
            fmt = (r.get("format") or "").lower()
            if fmt not in ("long", "short", "live"):
                fmt = "long" if (r.get("duration_seconds") or 0) >= 60 else "short"
            label = {"long": "Long", "short": "Shorts", "live": "Live"}[fmt]
            fbucket = league_fmt_month.setdefault(lg, {}) \
                                      .setdefault(month, {}) \
                                      .setdefault(label, {"videos": 0, "views": 0})
            fbucket["videos"] += 1
            fbucket["views"] += v
            # ── Heatmap accumulators (CET-bucketed) ──
            try:
                pub_dt = _dt.fromisoformat(pa.replace("Z", "+00:00")).astimezone(CET)
                dow = pub_dt.weekday()  # 0=Mon
                hr = pub_dt.hour
                all_count[dow][hr] += 1
                all_views[dow][hr] += v
                lg_c = league_count.setdefault(lg, _empty_grid())
                lg_v = league_views.setdefault(lg, _empty_grid())
                lg_c[dow][hr] += 1
                lg_v[dow][hr] += v
            except Exception:
                pass

        # ── Write the "all" payload (zoom-1 charts) ──
        all_rows = []
        for lg, mc in league_month.items():
            for month, b in mc.items():
                all_rows.append({"month": month, "league": lg,
                                 "videos": b["videos"], "views": b["views"]})
        write(db, "publish_cadence", scope_all(),
              {"rows": all_rows,
               "since": DURATION_SEASON_SINCE,
               "as_of": _date.today().isoformat()})
        log(f"[dashboard_cache] publish_cadence/all WRITTEN "
            f"({len(all_rows)} (month, league) pairs)")

        # ── Write per-league payloads (zoom-2 charts) ──
        for lg, fmt_map in league_fmt_month.items():
            payload_rows = []
            for month, fc in fmt_map.items():
                for fmt_label, b in fc.items():
                    payload_rows.append({"month": month, "format": fmt_label,
                                         "videos": b["videos"],
                                         "views": b["views"]})
            write(db, "publish_cadence", scope_league(lg),
                  {"rows": payload_rows,
                   "since": DURATION_SEASON_SINCE,
                   "as_of": _date.today().isoformat()})
            log(f"[dashboard_cache] publish_cadence/{lg} WRITTEN "
                f"({len(payload_rows)} (month, format) pairs)")

        # ── Publishing heatmap payloads (7×24 dow×hour grids, CET) ──
        # Used by Season Z1 (all scope) and Season Z2 (per-league).
        # Z3 is computed live from the already-loaded df_vids (no cache).
        write(db, "publishing_heatmap", scope_all(),
              {"counts": all_count,
               "sum_views": all_views,
               "since": DURATION_SEASON_SINCE,
               "as_of": _date.today().isoformat()})
        _total_h = sum(sum(row) for row in all_count)
        log(f"[dashboard_cache] publishing_heatmap/all WRITTEN ({_total_h} videos)")
        for lg, grid in league_count.items():
            write(db, "publishing_heatmap", scope_league(lg),
                  {"counts": grid,
                   "sum_views": league_views.get(lg, _empty_grid()),
                   "since": DURATION_SEASON_SINCE,
                   "as_of": _date.today().isoformat()})
            _ln = sum(sum(row) for row in grid)
            log(f"[dashboard_cache] publishing_heatmap/{lg} WRITTEN ({_ln} videos)")
    except Exception as e:
        log(f"[dashboard_cache] publish_cadence failed (non-fatal): {e}")


def refresh_concentration(db, log=print, channels: list[dict] | None = None) -> None:
    """Pre-compute per-club views-concentration (Pareto) stats per league.

    For each Top-5 league, compute for every Club channel:
      - n_videos, total_views
      - n_to_80, pct_to_80   → smallest k s.t. cumulative views ≥ 80%
      - top1_pct, top10_pct  → share of total views from the top 1 / 10 vids
      - median_views, avg_views

    Lower pct_to_80 = more hit-driven (long tail). Used by Season-page zoom-2
    to compare "density" across clubs in a league.
    """
    from src.database import _fetch_all
    from src.channels import COUNTRY_TO_LEAGUE
    from datetime import date as _date

    try:
        chans = channels if channels is not None else db.get_all_channels()
        # Map channel_id → (league, name, entity_type). Only Club rows go
        # into the bar chart, but League rows are kept for completeness.
        ch_meta: dict[str, dict] = {}
        for c in chans:
            if c.get("entity_type") not in ("Club", "League"):
                continue
            lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
            if not lg:
                continue
            ch_meta[c["id"]] = {
                "league": lg, "name": c["name"],
                "entity_type": c.get("entity_type"),
                "handle": c.get("handle", ""),
            }
        if not ch_meta:
            log("[dashboard_cache] concentration: no in-scope channels, skipping")
            return

        log(f"[dashboard_cache] computing concentration ({len(ch_meta)} channels)")
        rows = _fetch_all(
            db.client.table("videos")
            .select("channel_id,view_count")
            .gte("published_at", DURATION_SEASON_SINCE)
        )

        # channel_id → list[view_count]
        per_ch: dict[str, list[int]] = {}
        for r in rows:
            cid = r.get("channel_id")
            if cid not in ch_meta:
                continue
            per_ch.setdefault(cid, []).append(int(r.get("view_count") or 0))

        # league → list of per-club stat dicts
        per_league: dict[str, list[dict]] = {}
        for cid, views in per_ch.items():
            if not views:
                continue
            meta = ch_meta[cid]
            views_sorted = sorted(views, reverse=True)
            n = len(views_sorted)
            total = sum(views_sorted)
            if total <= 0:
                continue
            # cumulative share
            cum = 0
            n_to_80 = n
            for i, v in enumerate(views_sorted, 1):
                cum += v
                if cum / total >= 0.80:
                    n_to_80 = i
                    break
            pct_to_80 = (n_to_80 / n * 100.0) if n else 0.0
            top1_pct = (views_sorted[0] / total * 100.0) if n >= 1 else 0.0
            top10 = sum(views_sorted[:10])
            top10_pct = (top10 / total * 100.0)
            avg_v = total / n
            mid = n // 2
            median_v = (views_sorted[mid] if n % 2 == 1
                        else (views_sorted[mid - 1] + views_sorted[mid]) / 2)
            stat = {
                "channel_id": cid,
                "name": meta["name"],
                "handle": meta["handle"],
                "entity_type": meta["entity_type"],
                "n_videos": n,
                "total_views": total,
                "n_to_80": n_to_80,
                "pct_to_80": round(pct_to_80, 2),
                "top1_pct": round(top1_pct, 2),
                "top10_pct": round(top10_pct, 2),
                "avg_views": int(avg_v),
                "median_views": int(median_v),
            }
            per_league.setdefault(meta["league"], []).append(stat)

        for lg, stats in per_league.items():
            stats.sort(key=lambda s: s["pct_to_80"])  # most concentrated first
            write(db, "concentration", scope_league(lg),
                  {"rows": stats,
                   "since": DURATION_SEASON_SINCE,
                   "as_of": _date.today().isoformat()})
            log(f"[dashboard_cache] concentration/{lg} WRITTEN ({len(stats)} clubs)")

        # ── "all" scope: one row per league, treating ALL videos from
        # every club (entity_type='Club') in that league as a single
        # catalog. Same Pareto math, league-aggregate level.
        league_views: dict[str, list[int]] = {}
        for cid, views in per_ch.items():
            meta = ch_meta[cid]
            # Include both Club and League channels in the league-level
            # aggregate — League channels (e.g. @seriea) are a real part
            # of each league's catalog and shouldn't be hidden.
            if meta["entity_type"] not in ("Club", "League"):
                continue
            league_views.setdefault(meta["league"], []).extend(views)
        all_rows = []
        for lg, views in league_views.items():
            if not views:
                continue
            views_sorted = sorted(views, reverse=True)
            n = len(views_sorted)
            total = sum(views_sorted)
            if total <= 0:
                continue
            cum = 0
            n_to_80 = n
            for i, v in enumerate(views_sorted, 1):
                cum += v
                if cum / total >= 0.80:
                    n_to_80 = i
                    break
            pct_to_80 = (n_to_80 / n * 100.0) if n else 0.0
            top1_pct = (views_sorted[0] / total * 100.0)
            top10 = sum(views_sorted[:10])
            top10_pct = (top10 / total * 100.0)
            avg_v = total / n
            mid = n // 2
            median_v = (views_sorted[mid] if n % 2 == 1
                        else (views_sorted[mid - 1] + views_sorted[mid]) / 2)
            all_rows.append({
                "league": lg,
                "n_videos": n,
                "total_views": total,
                "n_to_80": n_to_80,
                "pct_to_80": round(pct_to_80, 2),
                "top1_pct": round(top1_pct, 2),
                "top10_pct": round(top10_pct, 2),
                "avg_views": int(avg_v),
                "median_views": int(median_v),
            })
        all_rows.sort(key=lambda r: r["pct_to_80"])
        write(db, "concentration", scope_all(),
              {"rows": all_rows,
               "since": DURATION_SEASON_SINCE,
               "as_of": _date.today().isoformat()})
        log(f"[dashboard_cache] concentration/all WRITTEN ({len(all_rows)} leagues)")
    except Exception as e:
        log(f"[dashboard_cache] concentration failed (non-fatal): {e}")


def refresh_home_top(db, log=print, channels: list[dict] | None = None) -> None:
    """Pre-compute the Home-page leaderboards (last 7 days):
      - Top-5 channels by Δ total_views
      - Top-5 channels by # videos published, with Long/Shorts/Live split

    Both restricted to Top-5 European leagues (clubs + the 5 league channels).
    Single payload at scope='all' — Home page reads one row, looks up
    channel meta, renders. No live aggregations.
    """
    from src.database import _fetch_all
    from src.channels import COUNTRY_TO_LEAGUE
    from src.growth import delta as _delta_fn, group_by_channel as _gbc
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz, date as _date

    TOP5 = {"Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"}

    try:
        chans = channels if channels is not None else db.get_all_channels()
        # Season cohort gate — see rebuild_all. Covers the direct
        # hourly_rss call (no channels=). No-op at 25/26.
        from src.season_cohort import (
            resolve_active_season, get_season_cohort_ids, filter_to_season_cohort)
        chans = filter_to_season_cohort(
            chans, get_season_cohort_ids(db, resolve_active_season(db)))
        ch_by_id = {c["id"]: c for c in chans}

        def _in_scope(ch: dict) -> bool:
            if not ch or ch.get("entity_type") not in ("Club", "League"):
                return False
            lg = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper())
            return lg in TOP5

        # ── 7d view gains ────────────────────────────────────
        since = (_dt.utcnow().date() - _td(days=14)).isoformat()
        log(f"[dashboard_cache] computing home_top / view gains (since {since})")
        snaps = db.get_all_snapshots(since_date=since)
        by_ch = _gbc(snaps)
        gainers: list[dict] = []
        for cid, s in by_ch.items():
            ch = ch_by_id.get(cid)
            if not _in_scope(ch) or len(s) < 2:
                continue
            d7 = _delta_fn(s, "total_views", 7)
            if d7 is None:
                continue
            gainers.append({
                "channel_id": cid,
                "name": ch["name"],
                "handle": ch.get("handle", ""),
                "country": ch.get("country"),
                "entity_type": ch.get("entity_type"),
                "views": int(s[-1].get("total_views", 0) or 0),
                "subs": int(s[-1].get("subscriber_count", 0) or 0),
                "d7_views": int(d7),
            })
        gainers.sort(key=lambda g: g["d7_views"], reverse=True)
        top5_views = gainers[:5]

        # ── 7d publishers ───────────────────────────────────
        start_iso = (_dt.now(_tz.utc) - _td(days=7)).isoformat()
        log(f"[dashboard_cache] computing home_top / publishers (since {start_iso})")
        rows = _fetch_all(
            db.client.table("videos")
            .select("channel_id,format,duration_seconds,published_at")
            .gte("published_at", start_iso)
        )
        pub_counts: dict[str, dict] = {}
        for v in rows:
            ch = ch_by_id.get(v.get("channel_id"))
            if not _in_scope(ch):
                continue
            f = (v.get("format") or "").lower()
            if f not in ("long", "short", "live"):
                f = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
            e = pub_counts.setdefault(ch["id"], {
                "channel_id": ch["id"], "name": ch["name"],
                "handle": ch.get("handle", ""),
                "country": ch.get("country"),
                "entity_type": ch.get("entity_type"),
                "long": 0, "short": 0, "live": 0, "count": 0,
            })
            e[f] += 1
            e["count"] += 1
        top5_pubs = sorted(pub_counts.values(),
                           key=lambda r: r["count"], reverse=True)[:5]

        write(db, "home_top", scope_all(),
              {"top5_views": top5_views,
               "top5_pubs": top5_pubs,
               "as_of": _date.today().isoformat()})
        log(f"[dashboard_cache] home_top WRITTEN "
            f"({len(top5_views)} gainers, {len(top5_pubs)} publishers)")
    except Exception as e:
        log(f"[dashboard_cache] home_top failed (non-fatal): {e}")


def refresh_profile_sentences(db, log=print, channels: list[dict] | None = None) -> None:
    """Pre-generate one AI sentence per flagged club and persist to
    dashboard_cache. The Outliers page reads from cache so the table
    can show sentences for every flagged channel without firing 96
    LLM calls per pageload.

    Cheap: ~96 channels × claude-haiku-4-5 ≈ a few cents per nightly run."""
    try:
        from src import profile as _prof
        chans = channels if channels is not None else db.get_all_channels()
        profiles = _prof.compute_all_profiles(chans)
        n_total = len(profiles)
        n_written = 0
        log(f"[dashboard_cache] computing profile_sentences for {n_total} clubs")
        for cid, p in profiles.items():
            if not (p["league"]["tags"] or p["size"]["tags"]):
                continue  # nothing to say about a typical club
            chan = next((c for c in chans if c["id"] == cid), None)
            if not chan:
                continue
            sentence = _prof.generate_profile_sentence(chan, p, log=log)
            if sentence:
                write(db, "profile_sentence", cid,
                      {"text": sentence, "tag_count": p["tag_count"]})
                n_written += 1
        log(f"[dashboard_cache] profile_sentences WRITTEN ({n_written}/{n_total})")
    except Exception as e:
        log(f"[dashboard_cache] profile_sentences failed (non-fatal): {e}")


def refresh_latest_vibe(db, log=print, channels: list[dict] | None = None) -> None:
    """Generate the All-Leagues 'latest vibe' note and persist to
    dashboard_cache. Lightweight: one Anthropic call.

    Called both by rebuild_all (daily) and hourly_rss (every hour) so
    the vibe stays fresh as new videos land. Z1 (All Leagues,
    scope_all) + Z2 (per-league, scope_league) — ~6 haiku calls/hour,
    a few cents/day, so the Latest page's per-league view is fresh too.
    Per-CLUB is still skipped (would be ~100 calls/hour)."""
    try:
        from src import ai_note as _an2
        from src.channels import COUNTRY_TO_LEAGUE
        chans = channels if channels is not None else db.get_all_channels()
        chans = [c for c in chans
                 if is_top5_cohort(c)]
        # Season cohort gate — see rebuild_all. Covers the direct
        # hourly_rss call (no channels=). No-op at 25/26.
        from src.season_cohort import (
            resolve_active_season, get_season_cohort_ids, filter_to_season_cohort)
        chans = filter_to_season_cohort(
            chans, get_season_cohort_ids(db, resolve_active_season(db)))
        chans_by_id = {c["id"]: c for c in chans}

        def _one(scope_key: str, ids: list[str], label: str,
                 league: str | None = None) -> None:
            if not ids:
                log(f"[dashboard_cache] latest_vibe/{label} skipped — no channels")
                return
            # 60 (not 30) so big narrative arcs (title clinch, cup
            # final, manager sack) stay visible ~12-24h, not 1 hour.
            recent = db.get_recent_videos(limit=60, channel_ids=ids,
                                          with_description=True)
            log(f"[dashboard_cache] computing latest_vibe/{label} "
                f"({len(ids)} channels, {len(recent)} videos)")
            vibe = _an2.generate_latest_vibe(recent, channels_by_id=chans_by_id,
                                             log=log, league=league)
            if vibe:
                # Badge + YouTube-link decoration on each recognized name.
                _html = _an2.decorate_with_badges(vibe, chans).replace("\n", "<br>")
                write(db, "latest_vibe", scope_key,
                      {"text": vibe, "html": _html,
                       "n_videos": len(recent)})
                log(f"[dashboard_cache] latest_vibe/{label} WRITTEN "
                    f"({len(vibe)} chars)")
            else:
                log(f"[dashboard_cache] latest_vibe/{label} skipped "
                    f"— generator returned empty")

        # Z1 — All Leagues
        _one(scope_all(), [c["id"] for c in chans], "all")
        # Z2 — one note per league
        by_lg: dict[str, list[str]] = {}
        for c in chans:
            lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
            if lg:
                by_lg.setdefault(lg, []).append(c["id"])
        for lg in sorted(by_lg):
            _one(scope_league(lg), by_lg[lg], f"league:{lg}", league=lg)
    except Exception as e:
        log(f"[dashboard_cache] latest_vibe failed (non-fatal): {e}")


def refresh_wc2026_latest_vibe(db, log=print,
                               channels: list[dict] | None = None) -> None:
    """Generate a 'latest vibe' note over the WC2026 cohort's recent
    videos and persist it under its OWN dimension (wc2026_latest_vibe)
    so it doesn't collide with the Top-5 latest_vibe. One Anthropic
    call; called from the WC2026 cron alongside refresh_wc2026."""
    try:
        from src import ai_note as _an2
        chans = channels if channels is not None else db.get_all_channels()
        wc = [c for c in chans
              if (c.get("competitions") or {}).get("wc2026")]
        if not wc:
            return
        chans_by_id = {c["id"]: c for c in wc}
        ids = [c["id"] for c in wc]
        recent = db.get_recent_videos(limit=60, channel_ids=ids,
                                      with_description=True)
        log(f"[dashboard_cache] computing wc2026_latest_vibe "
            f"({len(ids)} channels, {len(recent)} videos)")
        _wc_guidance = (
            "COHORT CONTEXT: these are national-team, FIFA and "
            "confederation channels in the run-up to the 2026 World "
            "Cup. It is OBVIOUS and not worth saying that the feed is "
            "World-Cup-related — NEVER state that the content is about "
            "the World Cup, that it's 'almost entirely WC2026', or "
            "anything to that effect. Skip the generality entirely and "
            "go straight to the SPECIFIC stories in the titles: which "
            "nations posted, standout clips, named players / managers / "
            "matches, qualifiers, friendlies, draws."
        )
        vibe = _an2.generate_latest_vibe(recent, channels_by_id=chans_by_id,
                                         log=log, extra_guidance=_wc_guidance)
        if vibe:
            # Plain line-break HTML — skip the Top-5 badge decoration
            # (tuned for club names, would misfire on national teams).
            _html = vibe.replace("\n", "<br>")
            write(db, "wc2026_latest_vibe", scope_all(),
                  {"text": vibe, "html": _html, "n_videos": len(recent)})
            log(f"[dashboard_cache] wc2026_latest_vibe WRITTEN "
                f"({len(vibe)} chars)")
        else:
            log("[dashboard_cache] wc2026_latest_vibe skipped — empty")
    except Exception as e:
        log(f"[dashboard_cache] wc2026_latest_vibe failed (non-fatal): {e}")


def refresh_season_vibe(db, log=print, channels: list[dict] | None = None) -> None:
    """Generate the 'season so far' note and persist to dashboard_cache.
    Nightly only (season aggregates move once a day). Z1 (All Leagues,
    scope_all) + Z2 (per-league, scope_league) — one haiku call each
    (~6 total), so the Season page's per-league view is pre-warmed
    too. Same structure as refresh_season_top_vibe."""
    try:
        from src import ai_note as _an2
        from src.channels import get_season_since as _gss, COUNTRY_TO_LEAGUE
        chans = channels if channels is not None else db.get_all_channels()
        chans = [c for c in chans
                 if is_top5_cohort(c)]
        season_start = _gss()

        def _one(scope_key: str, subset: list[dict], label: str,
                 league: str | None = None) -> None:
            if not subset:
                log(f"[dashboard_cache] season_vibe/{label} skipped — no channels")
                return
            log(f"[dashboard_cache] computing season_vibe/{label} "
                f"({len(subset)} channels, since {season_start})")
            vibe = _an2.generate_season_vibe(subset, season_start=season_start,
                                             log=log, league=league)
            if vibe:
                # Badge + YouTube-link decoration on each recognized name.
                _html = _an2.decorate_with_badges(vibe, chans).replace("\n", "<br>")
                write(db, "season_vibe", scope_key,
                      {"text": vibe, "html": _html,
                       "season_start": season_start})
                log(f"[dashboard_cache] season_vibe/{label} WRITTEN "
                    f"({len(vibe)} chars)")
            else:
                log(f"[dashboard_cache] season_vibe/{label} skipped "
                    f"— generator returned empty")

        # Z1 — All Leagues
        _one(scope_all(), chans, "all")
        # Z2 — one note per league
        by_lg: dict[str, list[dict]] = {}
        for c in chans:
            lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
            if lg:
                by_lg.setdefault(lg, []).append(c)
        for lg in sorted(by_lg):
            _one(scope_league(lg), by_lg[lg], f"league:{lg}", league=lg)
    except Exception as e:
        log(f"[dashboard_cache] season_vibe failed (non-fatal): {e}")


def refresh_season_top_vibe(db, log=print, channels: list[dict] | None = None) -> None:
    """Generate the 'season's biggest hits' note from the already-cached
    season_top payload and persist to dashboard_cache. MUST run after
    refresh_season_top (it reads those cache rows). Nightly only.

    Z1 (All Leagues, scope_all) + Z2 (per-league, scope_league) — one
    haiku call each (~6 total), so the per-league reads on the Season
    Top page are pre-warmed too. Z3 (single club) is skipped: the
    per-club query is fast live and not worth ~100 LLM calls."""
    try:
        from src import ai_note as _an2
        from src.channels import COUNTRY_TO_LEAGUE
        chans = channels if channels is not None else db.get_all_channels()
        chans_by_id = {c["id"]: c for c in chans}

        def _one(scope_key: str, label: str,
                 league: str | None = None) -> None:
            row = read(db, "season_top", scope_key)
            top_views = ((row or {}).get("payload") or {}).get("top_views") or []
            if not top_views:
                log(f"[dashboard_cache] season_top_vibe/{label} skipped "
                    f"— no season_top cache")
                return
            log(f"[dashboard_cache] computing season_top_vibe/{label} "
                f"({len(top_views)} top videos)")
            vibe = _an2.generate_season_top_vibe(
                top_views, channels_by_id=chans_by_id, log=log, league=league)
            if vibe:
                # Badge + YouTube-link decoration on each recognized name.
                _html = _an2.decorate_with_badges(vibe, chans).replace("\n", "<br>")
                write(db, "season_top_vibe", scope_key,
                      {"text": vibe, "html": _html,
                       "n_videos": len(top_views)})
                log(f"[dashboard_cache] season_top_vibe/{label} WRITTEN "
                    f"({len(vibe)} chars)")
            else:
                log(f"[dashboard_cache] season_top_vibe/{label} skipped "
                    f"— generator returned empty")

        # Z1 — All Leagues
        _one(scope_all(), "all")

        # Z2 — one note per league (same league set refresh_season_top
        # built scope_league rows for).
        leagues = sorted({
            COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
            for c in chans
            if is_top5_cohort(c)
            and COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
        })
        for lg in leagues:
            _one(scope_league(lg), f"league:{lg}", league=lg)
    except Exception as e:
        log(f"[dashboard_cache] season_top_vibe failed (non-fatal): {e}")


# ── publishing_pulse: videos-per-day + zero-day-counts per scope ─────────
# Single paginated query → both metrics derived in Python. Z3 (single
# club) reuses df_vids client-side, so no per-club cache entry is written
# here — Z1 (All Leagues) and Z2 (per-league) only.
PUBLISHING_PULSE_SINCE = DURATION_SEASON_SINCE


def _compute_publishing_pulse(db, scope_ids: list[str]):
    """Returns (date_iso_strings, daily_counts, days_by_channel_id_map)."""
    if not scope_ids:
        return [], [], {}
    from src.database import _fetch_all
    import pandas as _pd
    from collections import Counter as _Counter
    rows = _fetch_all(
        db.client.table("videos")
          .select("channel_id,published_at")
          .gte("published_at", PUBLISHING_PULSE_SINCE)
          .in_("channel_id", scope_ids)
    )
    counts: _Counter = _Counter()
    days_by_ch: dict[str, set] = {}
    for r in rows:
        cid = r.get("channel_id")
        pa = r.get("published_at") or ""
        if not cid or not pa:
            continue
        try:
            d = _pd.to_datetime(pa, utc=True).date()
        except Exception:
            continue
        counts[d] += 1
        days_by_ch.setdefault(cid, set()).add(d)
    keys = sorted(counts.keys())
    return ([d.isoformat() for d in keys],
            [counts[d] for d in keys],
            days_by_ch)


def refresh_publishing_pulse(db, log=print, channels: list[dict] | None = None) -> None:
    """Compute videos-per-day + zero-day counts for All Leagues (Z1) and
    each league (Z2). Writes two cache entries per scope:

        videos_per_day  → {"days": ["YYYY-MM-DD",...], "counts": [int,...],
                           "since": "YYYY-MM-DD", "total_days": int}
        zero_day_counts → {"rows": [{"channel_id":..., "name":...,
                                     "zero_days":..., "published_days":...}],
                           "since": "YYYY-MM-DD", "total_days": int}

    Z3 (single-club) data is derived from df_vids on the page — no cache
    needed there.
    """
    from src.channels import COUNTRY_TO_LEAGUE
    chans = channels if channels is not None else db.get_all_channels()
    scope_chans = [c for c in chans
                   if is_top5_cohort(c)]
    if not scope_chans:
        log("[dashboard_cache] publishing_pulse skipped — no channels in scope")
        return

    today = datetime.now(timezone.utc).date()
    import pandas as _pd
    since_date = _pd.to_datetime(PUBLISHING_PULSE_SINCE).date()
    total_days = (today - since_date).days + 1

    def _zd_rows(scope_chans_local: list[dict], days_by_ch: dict[str, set]) -> list[dict]:
        out = []
        for ch in scope_chans_local:
            cid = ch.get("id")
            if not cid:
                continue
            pub_days = len(days_by_ch.get(cid, set()))
            out.append({
                "channel_id": cid,
                "name": ch.get("name") or cid,
                "zero_days": max(0, total_days - pub_days),
                "published_days": pub_days,
            })
        return out

    # ── Z1: all-leagues scope ────────────────────────────────
    z1_ids = [c["id"] for c in scope_chans if c.get("id")]
    log(f"[dashboard_cache] publishing_pulse / all ({len(z1_ids)} channels)")
    z1_dates, z1_counts, z1_days_by_ch = _compute_publishing_pulse(db, z1_ids)
    write(db, "videos_per_day", scope_all(), {
        "days": z1_dates, "counts": z1_counts,
        "since": PUBLISHING_PULSE_SINCE, "total_days": total_days,
    })
    write(db, "zero_day_counts", scope_all(), {
        "rows": _zd_rows(scope_chans, z1_days_by_ch),
        "since": PUBLISHING_PULSE_SINCE, "total_days": total_days,
    })

    # ── Z2: per-league ───────────────────────────────────────
    leagues_to_chans: dict[str, list[dict]] = {}
    for c in scope_chans:
        country = (c.get("country") or "").upper()
        lg = COUNTRY_TO_LEAGUE.get(country)
        if lg:
            leagues_to_chans.setdefault(lg, []).append(c)

    for lg, lg_chans in leagues_to_chans.items():
        lg_ids = [c["id"] for c in lg_chans if c.get("id")]
        if not lg_ids:
            continue
        log(f"[dashboard_cache] publishing_pulse / league:{lg} ({len(lg_ids)} channels)")
        lg_dates, lg_counts, lg_days_by_ch = _compute_publishing_pulse(db, lg_ids)
        write(db, "videos_per_day", scope_league(lg), {
            "days": lg_dates, "counts": lg_counts,
            "since": PUBLISHING_PULSE_SINCE, "total_days": total_days,
        })
        write(db, "zero_day_counts", scope_league(lg), {
            "rows": _zd_rows(lg_chans, lg_days_by_ch),
            "since": PUBLISHING_PULSE_SINCE, "total_days": total_days,
        })

    log("[dashboard_cache] publishing_pulse done")


# ── season_top: Top-100 by views + Top-5 by likes / comments per scope ──
# Powers views/3b_Season_Top_Videos.py at all zoom levels.
# Read path: 1 dashboard_cache row, ~80KB JSON, zero query work.

SEASON_TOP_SINCE = DURATION_SEASON_SINCE  # same season window as everything else


def _fetch_season_top_payload(db, channel_ids: list[str] | None,
                              excluded_ids: list[str] | None = None) -> dict:
    """Run the three ranked-table queries once, return the payload dict.

    Mirrors the Z1 perf trick from src/season_top.py: when an exclusion
    list is provided, fetch globally + filter client-side. Otherwise
    pass channel_ids directly (used at Z2 — small leagues are fine
    with a 20-element IN clause)."""
    ex = set(excluded_ids or [])

    def _fetch(order_by: str, limit: int) -> list[dict]:
        if ex:
            rows = db.get_top_season_videos(
                channel_ids=None, since=SEASON_TOP_SINCE,
                limit=max(limit * 4, 50), order_by=order_by,
            ) or []
            return [r for r in rows if r.get("channel_id") not in ex][:limit]
        return db.get_top_season_videos(
            channel_ids=channel_ids, since=SEASON_TOP_SINCE,
            limit=limit, order_by=order_by,
        ) or []

    return {
        "top_views":    _fetch("view_count", 100),
        "top_likes":    _fetch("like_count", 5),
        "top_comments": _fetch("comment_count", 5),
        "since":        SEASON_TOP_SINCE,
    }


def refresh_season_top(db, log=print, channels: list[dict] | None = None) -> None:
    """Compute and persist the three season-top tables for All Leagues
    (Z1) and each league (Z2). Z3 (single-club) skipped — single-channel
    queries are already fast (no IN list).

        season_top → {"top_views": [...100], "top_likes": [...5],
                      "top_comments": [...5], "since": "YYYY-MM-DD"}
    """
    from src.channels import COUNTRY_TO_LEAGUE
    chans = channels if channels is not None else db.get_all_channels()
    isolated_ids = [c["id"] for c in chans
                    if not is_top5_cohort(c)]
    included = [c for c in chans
                if is_top5_cohort(c)]
    if not included:
        log("[dashboard_cache] season_top skipped — no channels in scope")
        return

    # ── Z1 (all leagues): global query + client-side exclude ─────────
    z1_ids = [c["id"] for c in included if c.get("id")]
    log(f"[dashboard_cache] season_top / all ({len(z1_ids)} channels)")
    write(db, "season_top", scope_all(),
          _fetch_season_top_payload(db, z1_ids, excluded_ids=isolated_ids))

    # ── Z2 (per-league): direct IN filter, small enough to be fast ──
    leagues_to_chans: dict[str, list[dict]] = {}
    for c in included:
        country = (c.get("country") or "").upper()
        lg = COUNTRY_TO_LEAGUE.get(country)
        if lg:
            leagues_to_chans.setdefault(lg, []).append(c)

    for lg, lg_chans in leagues_to_chans.items():
        lg_ids = [c["id"] for c in lg_chans if c.get("id")]
        if not lg_ids:
            continue
        log(f"[dashboard_cache] season_top / league:{lg} ({len(lg_ids)} channels)")
        write(db, "season_top", scope_league(lg),
              _fetch_season_top_payload(db, lg_ids))

    log("[dashboard_cache] season_top done")


# ── top_no1_videos: each core channel's #1 video, sorted by views ────────
# Powers views/4c_No1_Videos.py. Single dashboard_cache row at scope_all.
# Read path: 1 row, ~100KB JSON.

def _refresh_no1_per_channel(
    db,
    cache_name: str,
    log,
    channels: list[dict] | None,
    *,
    since: str | None = None,
    global_probe: int = 5000,
) -> None:
    """Shared engine for `top_no1_videos` (all-time) and
    `season_top_no1_videos` (season). Pulls each core channel's #1
    video — by view_count — within the optional season window.

    `since` = ISO date filter on published_at (None = all-time).
    """
    chans = channels if channels is not None else db.get_all_channels()
    core = [c for c in chans
            if is_top5_cohort(c)
            and c.get("id")]
    core_ids = {c["id"] for c in core}
    if not core_ids:
        log(f"[dashboard_cache] {cache_name} skipped — no core channels")
        return

    SELECT = ("id,youtube_video_id,channel_id,title,published_at,"
              "duration_seconds,format,category,view_count,"
              "like_count,comment_count,thumbnail_url,"
              "channels(name,handle)")

    # ── Pass 1: global top probe, dedupe by channel ─────────────────
    log(f"[dashboard_cache] {cache_name} / probing top {global_probe}")
    q = (db.client.table("videos")
         .select(SELECT)
         .order("view_count", desc=True)
         .limit(global_probe))
    if since:
        q = q.gte("published_at", since)
    rows = q.execute().data or []

    by_channel: dict[str, dict] = {}
    for v in rows:
        cid = v.get("channel_id")
        if cid in core_ids and cid not in by_channel:
            by_channel[cid] = v

    # ── Pass 2: per-channel fallback for any core channel not seen ──
    missing = [c for c in core if c["id"] not in by_channel]
    if missing:
        log(f"[dashboard_cache] {cache_name} / fallback for {len(missing)} long-tail channels")
        for c in missing:
            try:
                fq = (db.client.table("videos")
                      .select(SELECT)
                      .eq("channel_id", c["id"])
                      .order("view_count", desc=True)
                      .limit(1))
                if since:
                    fq = fq.gte("published_at", since)
                resp = fq.execute().data or []
                if resp:
                    by_channel[c["id"]] = resp[0]
            except Exception as e:
                log(f"  fallback failed for {c.get('name','?')}: {e}")

    out = sorted(by_channel.values(),
                 key=lambda v: int(v.get("view_count") or 0),
                 reverse=True)
    write(db, cache_name, scope_all(),
          {"rows": out, "count": len(out), "since": since})
    log(f"[dashboard_cache] {cache_name} done — {len(out)} channels")


def refresh_top_no1_videos(db, log=print, channels: list[dict] | None = None) -> None:
    """All-time #1 video per core channel (no time filter)."""
    _refresh_no1_per_channel(db, "top_no1_videos", log, channels, since=None)


def refresh_season_top_no1_videos(db, log=print, channels: list[dict] | None = None) -> None:
    """Current-season #1 video per core channel (published >= SEASON_TOP_SINCE)."""
    _refresh_no1_per_channel(db, "season_top_no1_videos", log, channels,
                             since=SEASON_TOP_SINCE)


# ── compute: FIFA World Cup 2026 (48 teams + 7 governing bodies) ────────
def refresh_wc2026(db, log=print, channels: list[dict] | None = None) -> None:
    """Pre-compute the render-ready payload for the WC 2026 page.

    Same shape the page used to compute live every render: one row per
    channel with team, confederation, YT identity, basic stats (subs /
    views / videos / long / shorts / live) + last_fetched + last_upload
    (most recent video's date). The page just reads + paints; no
    per-render aggregation.

    Cache key: ('wc2026', 'all').
    """
    chans = channels if channels is not None else db.get_all_channels()
    wc = [c for c in chans if (c.get("competitions") or {}).get("wc2026")]
    # Each channel's most recent upload date (YYYY-MM-DD), computed at
    # write-time (same pattern as refresh_last_upload) so the page never
    # scans the videos table on render. WC2026 videos are collected by
    # the daily_wc2026 cron — "" for channels we have no videos for yet.
    # Each channel's latest upload date. Query the MAX per channel (62
    # tiny limit-1 lookups) instead of paginating EVERY WC2026 video row
    # (~52K, growing) just to take the first per channel — that offset
    # scan crossed the Postgres statement timeout (57014) and silently
    # skipped the whole wc2026 cache rebuild.
    _wc_ids = [c["id"] for c in wc if c.get("id")]
    _last_up: dict[str, str] = {}
    for _cid in _wc_ids:
        try:
            _r = (db.client.table("videos")
                  .select("published_at")
                  .eq("channel_id", _cid)
                  .order("published_at", desc=True)
                  .limit(1).execute().data or [])
            if _r:
                _last_up[_cid] = (_r[0].get("published_at") or "")[:10]
        except Exception:
            pass
    # Keep the same shape the page expects — channel-like dict with
    # nested `competitions.wc2026`. Lets the view consume cache rows
    # and live-channel rows interchangeably with no extra branching.
    rows = []
    for c in wc:
        w = (c.get("competitions") or {}).get("wc2026") or {}
        rows.append({
            "youtube_channel_id": c.get("youtube_channel_id"),
            "name":             c.get("name"),
            "handle":           c.get("handle"),
            "entity_type":      c.get("entity_type"),
            "color":            c.get("color"),
            "color2":           c.get("color2"),
            "subscriber_count": int(c.get("subscriber_count") or 0),
            "total_views":      int(c.get("total_views") or 0),
            "video_count":      int(c.get("video_count") or 0),
            "long_form_count":  int(c.get("long_form_count") or 0),
            "shorts_count":     int(c.get("shorts_count") or 0),
            "live_count":       int(c.get("live_count") or 0),
            "last_fetched":     c.get("last_fetched"),
            "last_upload":      _last_up.get(c.get("id"), ""),
            "competitions":     {"wc2026": w},
        })
    # Pre-sort by subscribers desc — the page's default ordering, so the
    # first paint is already correctly ordered.
    rows.sort(key=lambda r: -(r["subscriber_count"]))
    write(db, "wc2026", scope_all(),
          {"count": len(rows), "rows": rows})
    log(f"  ✓ wc2026 cache rebuilt — {len(rows)} channels")


# Confederation chart colours for the WC2026 trends breakdown — the WC
# analog of LEAGUE_COLOR_CHART. Keys match competitions.wc2026.confederation.
_WC_CONFED_COLOR = {
    "UEFA": "#2a6df4", "CONMEBOL": "#f4b32a", "CONCACAF": "#2ec27e",
    "CAF": "#e0533d", "AFC": "#9b59b6", "OFC": "#1ab6c4", "FIFA": "#326295",
}
_WC_CONFED_ORDER = ["UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC", "FIFA"]


def compute_wc2026_trends(db, chans: list[dict]) -> dict:
    """Build the WC2026 video-layer trends payload — same shape as the
    top-5 trends_30d Z1 payload, but scoped to the WC2026 cohort and
    grouped by confederation instead of league. Reuses the cohort-agnostic
    builders. Returned dict is what refresh_wc2026_trends persists; pages
    can also call this directly as a cold-cache live fallback."""
    wc = [c for c in chans if (c.get("competitions") or {}).get("wc2026")]
    cohort_ids = [c["id"] for c in wc if c.get("id")]
    today_cet, start_cet, start_iso, end_iso, dates = _trends_30d_window()
    if not cohort_ids:
        return {
            "as_of": today_cet.isoformat(),
            "window_start": dates[0], "window_end": dates[-1], "dates": dates,
            "cohort": {"by_date": _build_cohort_by_date([], [], dates),
                       "split": _split_archive_share([], [])},
            "breakdown": {"group_label": "confederation", "groups": []},
            "top_videos": [], "viral": [],
        }

    chan_deltas = _scan_channel_view_deltas(db, cohort_ids, dates)
    pubs = _scan_pub_videos(db, cohort_ids, start_iso, end_iso)
    cohort = _build_cohort_by_date(chan_deltas, pubs, dates)

    # channel_id → confederation (from competitions.wc2026). Alts + governing
    # bodies carry their own confederation tag, so this groups everything.
    conf_of: dict[str, str] = {}
    for c in wc:
        cf = ((c.get("competitions") or {}).get("wc2026") or {}).get("confederation")
        if c.get("id") and cf:
            conf_of[c["id"]] = cf
    present = set(conf_of.values())
    confeds = [k for k in _WC_CONFED_ORDER if k in present] + \
              sorted(present - set(_WC_CONFED_ORDER))

    per_conf = _build_group_by_date(
        chan_deltas, pubs, dates,
        row_to_group=lambda r: conf_of.get(r.get("channel_id")),
        group_keys=confeds,
    )
    cohort_split = _split_archive_share(chan_deltas, pubs)
    conf_splits = _split_archive_share(
        chan_deltas, pubs,
        row_to_group=lambda r: conf_of.get(r.get("channel_id")),
        group_keys=confeds,
    )
    groups = [
        {"key": cf, "name": cf,
         "color": _WC_CONFED_COLOR.get(cf, "#888"), "sort": i,
         "by_date": per_conf[cf], "split": conf_splits[i]}
        for i, cf in enumerate(confeds)
    ]
    # Precompute Z1 sub-scope variants ('Teams only' / 'Confederations
    # only') for the Viral and Top-25 lists. Without this the page had
    # to recompute live every time a user picked a sub-scope — minutes
    # of database scans per session for what is a deterministic
    # nightly-stable result. A governing-body channel is one whose
    # wc2026.team matches its wc2026.confederation (catches BOTH the
    # main FIFA / UEFA / … channels AND alt channels like FIFA+).
    def _is_gov(c: dict) -> bool:
        w = (c.get("competitions") or {}).get("wc2026") or {}
        t  = (w.get("team") or "").strip()
        cf = (w.get("confederation") or "").strip()
        return t != "" and t == cf

    wc_teams   = [c for c in wc if not _is_gov(c)]
    wc_confeds = [c for c in wc if _is_gov(c)]
    teams_ids   = [c["id"] for c in wc_teams   if c.get("id")]
    confeds_ids = [c["id"] for c in wc_confeds if c.get("id")]

    # Scope the pubs scan once per sub-scope (only contributes to
    # top_videos — viral re-queries video_daily_deltas inside
    # _build_viral). Reusing the cohort-wide pubs would double-count
    # videos from out-of-scope channels in the Top-25.
    pubs_teams   = [p for p in pubs if p.get("channel_id") in set(teams_ids)]
    pubs_confeds = [p for p in pubs if p.get("channel_id") in set(confeds_ids)]

    return {
        "as_of": today_cet.isoformat(),
        "window_start": dates[0], "window_end": dates[-1],
        "dates": dates,
        "cohort": {"by_date": cohort, "split": cohort_split},
        "breakdown": {"group_label": "confederation", "groups": groups},
        "top_videos": _build_top_videos(pubs, db, wc, dates, limit=25),
        "top_videos_teams": _build_top_videos(
            pubs_teams, db, wc_teams, dates, limit=25),
        "top_videos_confeds": _build_top_videos(
            pubs_confeds, db, wc_confeds, dates, limit=25),
        "viral": _build_viral(db, cohort_ids, wc, dates, limit=10),
        "viral_teams": _build_viral(
            db, teams_ids, wc_teams, dates, limit=10),
        "viral_confeds": _build_viral(
            db, confeds_ids, wc_confeds, dates, limit=10),
    }


def refresh_wc2026_trends(db, log=print,
                          channels: list[dict] | None = None) -> None:
    """Compute + persist the WC2026 video-layer trends payload. Feeds the
    WC2026 Trends video layer, Viral, and Daily Recap pages so they read
    one cached row instead of aggregating live.

    Cache key: ('wc2026_trends', 'all').
    """
    chans = channels if channels is not None else db.get_all_channels()
    payload = compute_wc2026_trends(db, chans)
    write(db, "wc2026_trends", scope_all(), payload)
    log(f"  ✓ wc2026_trends cache rebuilt — {len(payload['viral'])} viral, "
        f"{len(payload['top_videos'])} top videos")


def refresh_wc2026_pub_daily(db, log=print, channels=None) -> None:
    """Per-day + per-channel PUBLISHED video counts (long/short/live) for
    the WC2026 cohort across the whole tracking window.

    Precomputed here (cron) so the WC2026 Trends page reads a tiny cached
    payload instead of scanning ~10K videos live on every cold render —
    that live scan (~9s) made the page hang after the KPIs. by_channel is
    stored raw so the page rolls it into per-team using its own mapping
    (no team-mapping coupling in the cron).

    Cache key: ('wc2026_pub_daily', 'all').
    """
    chans = channels if channels is not None else db.get_all_channels()
    wc = [c for c in chans if (c.get("competitions") or {}).get("wc2026")]
    ids = [c["id"] for c in wc if c.get("id")]
    if not ids:
        write(db, "wc2026_pub_daily", scope_all(),
              {"by_date": {}, "by_channel": {}, "tot": [0, 0, 0]})
        return
    # Window = earliest cohort snapshot date → tomorrow (CET), matching the
    # page's snapshot-driven date axis.
    mn = None
    for i in range(0, len(ids), 50):
        r = (db.client.table("channel_snapshots").select("captured_date")
             .in_("channel_id", ids[i:i + 50])
             .order("captured_date").limit(1).execute().data or [])
        if r and (mn is None or r[0]["captured_date"] < mn):
            mn = r[0]["captured_date"]
    _today = datetime.now(CET).date()
    start = ((date.fromisoformat(mn) if mn else _today)
             - timedelta(days=1)).isoformat()
    end = (_today + timedelta(days=1)).isoformat()
    pubs = _scan_pub_videos(db, ids, start, end)
    _IX = {"long": 0, "short": 1, "live": 2}
    by_date: dict[str, list[int]] = {}
    by_channel: dict[str, list[int]] = {}
    tot = [0, 0, 0]
    for v in pubs:
        d = _pub_to_cet_date(v.get("published_at") or "")
        if not d:
            continue
        ix = _IX.get(_format_of(v), 2)
        by_date.setdefault(d, [0, 0, 0])[ix] += 1
        by_channel.setdefault(v.get("channel_id") or "", [0, 0, 0])[ix] += 1
        tot[ix] += 1
    write(db, "wc2026_pub_daily", scope_all(),
          {"by_date": by_date, "by_channel": by_channel, "tot": tot})
    log(f"  ✓ wc2026_pub_daily rebuilt — {len(pubs)} videos, "
        f"{len(by_date)} days")


def refresh_last_upload(db, entity_type: str, log=print,
                        channels: list[dict] | None = None) -> None:
    """Pre-compute {channel_id: 'YYYY-MM-DD'} of each channel's most
    recent upload for one entity type.

    Written at cron time so the Players / Other Clubs / Women /
    Federations pages can read one cheap cached row instead of scanning
    the whole `videos` table (a paginated _fetch_all over every video of
    every channel) on each render. That render-time scan, only masked by
    a 1h cache, is what made those pages slow on a cold cache (e.g.
    right after a deploy). Moving it to write-time removes the cliff.

    Cache key: ('last_upload', <entity_type>). Payload: {"map": {...}}.
    """
    from src.database import _fetch_all
    chans = channels if channels is not None else db.get_all_channels()
    ids = [c["id"] for c in chans
           if c.get("entity_type") == entity_type and c.get("id")]
    if not ids:
        write(db, "last_upload", entity_type, {"map": {}})
        log(f"  ✓ last_upload[{entity_type}] — 0 channels")
        return
    # Newest-first scan; first row seen per channel = its latest upload.
    # Same query the page used to run every render — now once per night.
    rows = _fetch_all(
        db.client.table("videos")
        .select("channel_id,published_at")
        .in_("channel_id", ids)
        .order("published_at", desc=True)
    )
    m: dict[str, str] = {}
    for r in rows:
        cid = r.get("channel_id")
        if cid and cid not in m:
            m[cid] = (r.get("published_at") or "")[:10]
    write(db, "last_upload", entity_type, {"map": m})
    log(f"  ✓ last_upload[{entity_type}] — {len(m)}/{len(ids)} channels")
