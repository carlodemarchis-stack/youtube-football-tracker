"""Season "one-hit wonder" detector.

A one-hit wonder = a single video that vastly outperforms the channel's
typical season output AND meaningfully carries the channel's reach.
Distinct from the Viral page (which is a 30-day cohort-wide momentum
score) — this is season-long, channel-self-relative, and historic.

Definition (all four gates must pass):
  1. view_count ≥ MIN_VIEWS                — notability floor
  2. view_count ≥ MIN_LIFT × channel median — vastly outsized vs the
                                              channel's own normal
  3. view_count / channel_total ≥ MIN_SHARE — materially carries the
                                              channel
  4. channel has ≥ MIN_VIDEOS season videos — so "median" is meaningful

Returned video dicts carry the same fields as the videos table plus:
  `lift`  — view_count / channel_median (int, e.g. 2074)
  `share` — view_count / channel_total  (0..1 float)
"""
from __future__ import annotations

import statistics

import streamlit as st

from src.database import _fetch_all

MIN_VIEWS = 500_000
MIN_LIFT = 10
MIN_SHARE = 0.15
MIN_VIDEOS = 20


@st.cache_data(ttl=1800, show_spinner=False)
def _load(_db, ids_tuple: tuple[str, ...], since: str) -> list[dict]:
    """One paginated scan of season videos for the given channels. Pulls
    every field render_top_season_videos_table needs."""
    if not ids_tuple:
        return []
    ids = list(ids_tuple)
    rows: list[dict] = []
    for i in range(0, len(ids), 50):
        rs = _fetch_all(
            _db.client.table("videos")
            .select("id,youtube_video_id,channel_id,title,view_count,"
                    "like_count,comment_count,published_at,"
                    "duration_seconds,format,thumbnail_url,category")
            .gte("published_at", since)
            .in_("channel_id", ids[i:i + 50])
        )
        rows.extend(rs)
    return rows


def compute(rows: list[dict]) -> list[dict]:
    """Return the list of one-hit wonder videos across `rows`, sorted by
    lift descending. Each enriched with `lift` (int) and `share` (float)."""
    per: dict[str, list[dict]] = {}
    for r in rows:
        cid = r.get("channel_id")
        if cid:
            per.setdefault(cid, []).append(r)

    out: list[dict] = []
    for cid, vs in per.items():
        if len(vs) < MIN_VIDEOS:
            continue
        views = [int(v.get("view_count") or 0) for v in vs]
        total = sum(views)
        if total <= 0:
            continue
        med = statistics.median(views) or 1
        for v in vs:
            vc = int(v.get("view_count") or 0)
            if vc < MIN_VIEWS:
                continue
            lift = vc / med
            share = vc / total
            if lift < MIN_LIFT or share < MIN_SHARE:
                continue
            v2 = dict(v)
            v2["lift"] = int(round(lift))
            v2["share"] = share
            # Integer percentage for the table column (the renderer
            # int()-casts extra-col values, so 0..1 share would round to 0).
            v2["share_pct"] = int(round(share * 100))
            out.append(v2)
    out.sort(key=lambda v: -v.get("lift", 0))
    return out


def top_n(db, channel_ids: list[str], since: str, n: int) -> list[dict]:
    """Top N one-hit wonders, filtered to `channel_ids`. Reads the
    precomputed `season_one_hits` cache when present (written by
    src.season_compute); falls back to a live scan + compute when cold."""
    try:
        from src import dashboard_cache as _dc
        row = _dc.read(db, "season_one_hits", _dc.scope_all())
        hits = ((row or {}).get("payload") or {}).get("hits") or []
        if hits:
            ids_set = set(channel_ids)
            return [h for h in hits if h.get("channel_id") in ids_set][:n]
    except Exception:
        pass
    rows = _load(db, tuple(sorted(channel_ids)), since)
    return compute(rows)[:n]
