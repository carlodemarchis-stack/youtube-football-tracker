"""Per-player channel history for the Players page modal.

Computed by the nightly Daily Players cron and stored in dashboard_cache
(name='player_history', scope_key=<channel db id>). The `videos` table
only holds this season's videos, so the full history comes straight from
YouTube's per-format upload playlists (dates only, cheap) plus the
popular-video playlists for the top-by-views list.

Payload:
    {"first": "YYYY-MM-DD",
     "months": {"YYYY-MM": [long, shorts, live], ...},
     "top": [{"id", "t", "v", "d", "f"}, ...]}   # top 10 by views
"""
from __future__ import annotations

from collections import defaultdict

from src.dashboard_cache import write

_TOP_N = 10
_POP_PER_FORMAT = 50


def compute(yt, youtube_channel_id: str) -> dict:
    dated = yt.get_dated_ids_by_format(youtube_channel_id)
    months: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    dates: list[str] = []
    for idx, key in enumerate(("long", "shorts", "live")):
        for _vid, published in dated[key]:
            if not published:
                continue
            months[published[:7]][idx] += 1
            dates.append(published[:10])

    pop = yt.get_popular_video_ids(youtube_channel_id)
    fmt_of: dict[str, str] = {}
    for key in ("long", "shorts", "live"):
        for vid in pop[key][:_POP_PER_FORMAT]:
            fmt_of.setdefault(vid, key)
    details = yt.get_video_details(list(fmt_of), retries=0) if fmt_of else []
    details.sort(key=lambda v: -(v.get("view_count") or 0))
    top = [{"id": v["youtube_video_id"],
            "t": v.get("title", ""),
            "v": int(v.get("view_count") or 0),
            "d": (v.get("published_at") or "")[:10],
            "f": fmt_of.get(v["youtube_video_id"], "long")}
           for v in details[:_TOP_N]]

    return {"first": min(dates) if dates else "",
            "months": dict(sorted(months.items())),
            "top": top}


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
