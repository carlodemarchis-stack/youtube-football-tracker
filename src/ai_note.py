"""Daily AI commentary note for the Daily Recap page.

Reads a structured payload describing yesterday's activity in the football
YouTube ecosystem and asks Claude to write a 2-paragraph dry-witty note.

Persisted in the dashboard_cache table so the page reads in ~1ms.

Tweak the prompt by editing SYSTEM_PROMPT below — that's the only thing
you should need to touch when adjusting voice or constraints.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CET = ZoneInfo("Europe/Rome")

# Tweak the voice / constraints here.
SYSTEM_PROMPT = """\
You are the in-house commentator for "YouTube Football Tracker", an
analytics dashboard that watches the YouTube output of the top-5 European
men's football leagues, plus the 5 league channels themselves.

Your job: write a 2-paragraph opening note for today's Daily Recap
covering yesterday's activity in this ecosystem.

Voice
- Smart, dry, mildly witty. Sports-section feature writer who likes data
  more than they admit. Never bombastic. Never fawning.
- One small joke or aside per note is fine. No emojis except a single
  optional one at the end.

Hard constraints (will be checked)
- Never invent match results, scores, standings, or transfer news. If a
  score isn't already in a video title we hand you, you don't know it.
- Never name a player who isn't in our data unless commenting on a
  notable absence ("nothing from Madrid all day").
- Don't generalize from one channel to a league ("Bayern was quiet" is
  fine; "the Bundesliga was quiet" needs the league-level number).
- If yesterday was clearly a quiet/empty day, say so plainly. Don't
  manufacture drama.
- "Looks like" is fine. "Was" requires the data to back it.

What you'll get
- A JSON blob of yesterday's per-channel and per-league summary.
- The day's viral videos with titles + views.
- 7-day baselines so "above/below average" is grounded.

Output: exactly 2 short paragraphs, ~80 words total. Plain text. No
markdown headers, no JSON, no list bullets. Don't pad."""


def compose_payload(db, target_date: date) -> dict:
    """Build the structured input the model will riff on.

    target_date is a CET date (the day we're reporting on).
    """
    from src.channels import COUNTRY_TO_LEAGUE

    day_iso = target_date.isoformat()
    # Window for yesterday in UTC (the videos table stores UTC timestamps)
    day_start_utc = datetime.combine(target_date, datetime.min.time(), tzinfo=CET) \
                            .astimezone(timezone.utc).isoformat()
    day_end_utc = datetime.combine(target_date + timedelta(days=1),
                                   datetime.min.time(), tzinfo=CET) \
                          .astimezone(timezone.utc).isoformat()

    chans = db.get_all_channels()
    ch_by_id = {c["id"]: c for c in chans}
    # Daily Recap excludes Players/Federations/OtherClub/Women — they have
    # their own pages. Keep clubs + leagues here.
    ECOSYSTEM_TYPES = {"Club", "League"}
    ecosystem_ids = [c["id"] for c in chans if c.get("entity_type") in ECOSYSTEM_TYPES]

    # ── Pull yesterday's videos
    videos = []
    page = 1000
    offset = 0
    while True:
        q = (db.client.table("videos")
             .select("title,view_count,channel_id,format,duration_seconds,published_at")
             .gte("published_at", day_start_utc)
             .lt("published_at", day_end_utc)
             .in_("channel_id", ecosystem_ids)
             .order("published_at")
             .range(offset, offset + page - 1))
        rows = q.execute().data or []
        videos.extend(rows)
        if len(rows) < page:
            break
        offset += page

    # ── Per-channel aggregates yesterday
    per_channel: dict[str, dict] = {}
    for v in videos:
        cid = v["channel_id"]
        b = per_channel.setdefault(cid, {"new": 0, "long": 0, "short": 0, "live": 0})
        b["new"] += 1
        f = (v.get("format") or "").lower()
        if f not in ("long", "short", "live"):
            f = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        b[f] += 1

    # ── Per-league rollup
    per_league: dict[str, dict] = {}
    for cid, b in per_channel.items():
        ch = ch_by_id.get(cid) or {}
        if ch.get("entity_type") != "Club":
            continue
        lg = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper())
        if not lg:
            continue
        agg = per_league.setdefault(lg, {"new": 0, "long": 0, "short": 0, "live": 0,
                                          "club_counts": {}})
        agg["new"] += b["new"]
        agg["long"] += b["long"]
        agg["short"] += b["short"]
        agg["live"] += b["live"]
        agg["club_counts"][ch["name"]] = b["new"]

    per_league_out = []
    for lg, agg in sorted(per_league.items(), key=lambda kv: -kv[1]["new"]):
        most_active = max(agg["club_counts"].items(), key=lambda kv: kv[1])
        per_league_out.append({
            "league": lg,
            "new_videos": agg["new"],
            "long": agg["long"],
            "short": agg["short"],
            "live": agg["live"],
            "most_active_club": most_active[0],
            "most_active_count": most_active[1],
        })

    # ── 7-day baselines (videos table for new-video count only)
    bl_start = (target_date - timedelta(days=7))
    bl_start_utc = datetime.combine(bl_start, datetime.min.time(), tzinfo=CET) \
                           .astimezone(timezone.utc).isoformat()
    # Cheap count via head=true would be nice but supabase-py is awkward;
    # instead trust the per-channel baseline figure approximated as
    # (channel.video_count_yesterday vs 7 days ago) — but we don't store
    # daily channel snapshots that finely. So compute a rough total by
    # paging through videos.published_at over the last 7 days.
    base_videos = 0
    offset = 0
    while True:
        rows = (db.client.table("videos")
                .select("youtube_video_id")
                .gte("published_at", bl_start_utc)
                .lt("published_at", day_start_utc)
                .in_("channel_id", ecosystem_ids)
                .order("published_at")
                .range(offset, offset + page - 1)
                .execute().data) or []
        base_videos += len(rows)
        if len(rows) < page:
            break
        offset += page
    baseline_7d_videos = round(base_videos / 7)

    # ── Top-N viral videos yesterday (sort by view_count)
    top_videos = sorted(videos, key=lambda v: v.get("view_count") or 0,
                        reverse=True)[:10]
    viral = []
    for v in top_videos:
        ch = ch_by_id.get(v.get("channel_id")) or {}
        viral.append({
            "club": ch.get("name", "?"),
            "title": (v.get("title") or "")[:200],
            "views": int(v.get("view_count") or 0),
            "format": v.get("format"),
        })

    # ── Cheap title-signal counts
    def _count(rx):
        rxc = re.compile(rx, re.IGNORECASE)
        return sum(1 for v in videos if rxc.search(v.get("title") or ""))
    title_signals = {
        "post_match": _count(r"\b(post[ -]?match|full[ -]?time|highlights)\b"),
        "press_conf": _count(r"\b(press conf|conferenza|pressekonferenz|rueda de prensa)\b"),
        "training":   _count(r"\b(training|allenamento|entrenamiento|trainings|abschlusstraining)\b"),
        "matchday":   _count(r"\b(matchday|gameday|jornada|live)\b"),
        "ucl_uel":    _count(r"\b(champions league|europa league|uefa|conference league)\b"),
        "domestic_cup": _count(r"\b(coppa|copa del rey|fa cup|dfb[- ]?pokal|coupe de france|league cup|carabao)\b"),
    }

    # ── Quiet clubs: 0 videos yesterday and < 3 in the last 7 days too
    # (cheaper estimate: just zero yesterday)
    all_club_ids = [c["id"] for c in chans if c.get("entity_type") == "Club"]
    quiet = []
    for cid in all_club_ids:
        if cid not in per_channel:
            ch = ch_by_id.get(cid) or {}
            quiet.append(ch.get("name", "?"))
    quiet = sorted(quiet)[:20]  # cap so the prompt doesn't bloat

    return {
        "as_of_date": day_iso,
        "weekday": target_date.strftime("%A"),
        "totals": {
            "new_videos": sum(b["new"] for b in per_channel.values()),
            "new_videos_long": sum(b["long"] for b in per_channel.values()),
            "new_videos_short": sum(b["short"] for b in per_channel.values()),
            "new_videos_live": sum(b["live"] for b in per_channel.values()),
        },
        "baselines_7d_avg": {
            "new_videos": baseline_7d_videos,
        },
        "per_league": per_league_out,
        "viral_videos": viral,
        "title_signals": title_signals,
        "quiet_clubs": quiet,
    }


def _looks_like_invented_score(text: str, video_titles: list[str]) -> bool:
    """Reject any score pattern (e.g. '2-1') in the note that doesn't appear
    in the source video titles. Cheap insurance against hallucinated results."""
    found_scores = re.findall(r"\b(\d+\s*[-–]\s*\d+)\b", text)
    if not found_scores:
        return False
    title_blob = " ".join(video_titles).lower()
    for s in found_scores:
        norm = re.sub(r"\s*", "", s).replace("–", "-")
        if norm in re.sub(r"\s*", "", title_blob).replace("–", "-"):
            continue
        return True  # at least one score in note that isn't grounded
    return False


def generate_daily_note(payload: dict, log=print) -> str | None:
    """Call Claude, return note text or None on failure / unsafe output."""
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("[ai_note] ANTHROPIC_API_KEY missing — skipping")
        return None

    # Skip generation entirely on near-empty days (no signal worth riffing on).
    total_new = payload.get("totals", {}).get("new_videos", 0)
    if total_new < 10:
        return ("Quiet day across the ecosystem — fewer than 10 videos posted "
                "across all 90+ tracked channels. Likely an international break "
                "or post-matchday cooldown.")

    try:
        import anthropic
    except ImportError:
        log("[ai_note] anthropic package not installed — skipping")
        return None

    user_message = "Yesterday's data:\n\n" + json.dumps(payload, indent=2,
                                                       ensure_ascii=False)
    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=300,
                temperature=0.6,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            log(f"[ai_note] Claude API error: {e}")
            return None
        except Exception as e:
            log(f"[ai_note] error: {e}")
            return None
    else:
        return None

    note = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if not note:
        return None

    # Anti-BS: reject if the model invented a score not in source titles.
    titles = [v.get("title", "") for v in payload.get("viral_videos", [])]
    if _looks_like_invented_score(note, titles):
        log("[ai_note] rejected — note contains a score not in source titles")
        return None

    return note
