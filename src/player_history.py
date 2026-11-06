"""Per-player channel history for the Player detail page.

Computed by the nightly Daily Players cron and stored in dashboard_cache
(name='player_history', scope_key=<channel db id>). The `videos` table
only holds this season's videos, so the full history comes straight from
YouTube: the per-format upload playlists (ids, dates, format) and
videos.list for each video's views and title.

Payload:
    {"first": "YYYY-MM-DD",
     "videos": [[youtube_id, "YYYY-MM-DD", views, format, title], ...],
     "totals": [long, shorts, live],          # all videos, for the Players table
     "per_year": {"YYYY": uploads, ...}}
format is "long" | "shorts" | "live"; title is cut to 90 chars.
"""
from __future__ import annotations

from src.dashboard_cache import write

_TITLE_MAX = 90


def compute(yt, youtube_channel_id: str) -> dict:
    dated = yt.get_dated_ids_by_format(youtube_channel_id)
    fmt_of: dict[str, str] = {}
    date_of: dict[str, str] = {}
    for key in ("long", "shorts", "live"):
        for vid, published in dated[key]:
            if published:
                fmt_of.setdefault(vid, key)
                date_of.setdefault(vid, published[:10])
    details = yt.get_video_details(list(fmt_of), retries=0) if fmt_of else []
    videos = [[v["youtube_video_id"],
               date_of[v["youtube_video_id"]],
               int(v.get("view_count") or 0),
               fmt_of[v["youtube_video_id"]],
               (v.get("title") or "")[:_TITLE_MAX]]
              for v in details if v["youtube_video_id"] in date_of]
    videos.sort(key=lambda r: r[1])
    return {"first": videos[0][1] if videos else "", "videos": videos,
            **summarize(videos)}


def summarize(videos: list[list]) -> dict:
    """Small per-player totals the Players table reads without the
    full video list."""
    totals = [0, 0, 0]
    per_year: dict[str, int] = {}
    for _id, day, _views, fmt, _title in videos:
        totals[("long", "shorts", "live").index(fmt)] += 1
        per_year[day[:4]] = per_year.get(day[:4], 0) + 1
    return {"totals": totals, "per_year": per_year}


def refresh(db, yt, players: list[dict], log=print) -> None:
    done = 0
    for p in players:
        yt_id = p.get("youtube_channel_id") or ""
        if not yt_id.startswith("UC"):
            continue
        try:
            write(db, "player_history", p["id"], compute(yt, yt_id))
            done += 1
        except Exception as e:
            log(f"  player_history failed for {p.get('name')}: {e}")
    log(f"  ✓ player_history — {done}/{len(players)} players")
