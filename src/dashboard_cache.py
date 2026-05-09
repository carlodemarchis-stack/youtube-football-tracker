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


# ── rebuild orchestrator ─────────────────────────────────────────────────
def rebuild_all(db, log=print) -> None:
    """Recompute and persist every cached aggregation across every scope.

    Called by daily_refresh and hourly_rss when underlying data has changed.
    Cheap: ~6 queries × ≤5K rows each, ~5–10 seconds total.
    """
    chans = db.get_all_channels()
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
    except Exception as e:
        log(f"[dashboard_cache] daily_note failed (non-fatal): {e}")

    # 4. latest_vibe — share the same lightweight helper used by hourly_rss,
    # so the rebuild path stays a single source of truth.
    refresh_latest_vibe(db, log=log, channels=chans)

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
DURATION_SEASON_SINCE = "2025-08-01"


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
    the vibe stays fresh as new videos land. Per-league / per-club
    versions are intentionally NOT generated — at ~100 channels the
    LLM cost would balloon and the All-Leagues read is what users
    typically open the page on."""
    try:
        from src import ai_note as _an2
        chans = channels if channels is not None else db.get_all_channels()
        chans = [c for c in chans
                 if c.get("entity_type") not in ("Player", "Federation",
                                                  "OtherClub", "WomenClub")]
        all_ch_ids = [c["id"] for c in chans]
        log(f"[dashboard_cache] computing latest_vibe / all "
            f"({len(all_ch_ids)} channels)")
        # 60 instead of 30 so big narrative arcs (championship-clinch,
        # cup final, manager sack) stay visible to the model for ~12-24h
        # after publication, not just the very next hour.
        recent = db.get_recent_videos(limit=60, channel_ids=all_ch_ids)
        chans_by_id = {c["id"]: c for c in chans}
        vibe = _an2.generate_latest_vibe(recent, channels_by_id=chans_by_id, log=log)
        if vibe:
            vibe_html = vibe.replace("\n", "<br>")
            write(db, "latest_vibe", scope_all(),
                  {"text": vibe, "html": vibe_html, "n_videos": len(recent)})
            log(f"[dashboard_cache] latest_vibe WRITTEN ({len(vibe)} chars)")
        else:
            log(f"[dashboard_cache] latest_vibe skipped — generator returned empty")
    except Exception as e:
        log(f"[dashboard_cache] latest_vibe failed (non-fatal): {e}")


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
                   if c.get("entity_type") not in
                      ("Player", "Federation", "OtherClub", "WomenClub")]
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
                    if c.get("entity_type") in
                       ("Player", "Federation", "OtherClub", "WomenClub")]
    included = [c for c in chans
                if c.get("entity_type") not in
                   ("Player", "Federation", "OtherClub", "WomenClub")]
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
