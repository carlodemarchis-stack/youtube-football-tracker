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
- Vary your openings. The previous_notes field shows the last few days'
  notes — don't reuse their opening pattern, marquee phrasing, or the
  same metaphors. If yesterday opened with "Yesterday was…", today
  opens differently.

Hard constraints (will be checked)
- Never invent match results, scores, standings, or transfer news. If a
  score isn't already in a video title we hand you, you don't know it.
- Never name a player who isn't in our data unless commenting on a
  notable absence ("nothing from Madrid all day").
- If the payload includes `league_scope`, EVERYTHING you've been
  given is already filtered to that single league. Do NOT observe
  that the league "dominates", "leads", or "publishes the most" — by
  construction it's the only thing in scope, and the user already
  knows. Frame observations as "within <league>": which clubs, which
  formats, which themes stand out INSIDE that league. Never compare
  to other leagues (there's no other-league data here).
- Don't generalize from one channel to a league ("Bayern was quiet" is
  fine; "the Bundesliga was quiet" needs the league-level number).
- If yesterday was clearly a quiet/empty day, say so plainly. Don't
  manufacture drama.
- "Looks like" is fine. "Was" requires the data to back it.

What you'll get
- A JSON blob of yesterday's per-channel and per-league summary.
- The day's viral videos with titles + views. Some carry a `desc` (a
  short description blurb) — use it to understand what the video
  actually is before characterizing it (it disambiguates vague or
  branded titles, confirms a throwback, names the real match). Trust
  `desc` over a flashy title; never quote it verbatim, paraphrase.
- viral_league_breakdown: how the top-10 most-watched split by league.
  If one league dominates the top of the day (e.g. 6+ of the 10 from
  the Premier League), call it out in one sentence. If the spread is
  even, don't force it.
- 7-day baselines so "above/below average" is grounded.
- previous_notes: the last 3 days' notes — read them, then write
  something that doesn't sound like more of the same.

Output format
- Exactly 4-6 short sentences total, ~80 words.
- Put each sentence on its own line (single newline between them).
- Plain text. No markdown headers, no JSON, no list bullets. Don't pad."""


def compose_payload(db, target_date: date, league: str | None = None) -> dict:
    """Build the structured input the model will riff on.

    target_date is a CET date (the day we're reporting on).

    Restricted to the top-5 European leagues only (Serie A, Premier League,
    La Liga, Bundesliga, Ligue 1) plus their 5 league channels. MLS / other
    leagues live in COUNTRY_TO_LEAGUE but are NOT considered here.

    `league` (Z2): when set, the allowlist narrows to just that one
    league + its channel, so the note becomes a single-league daily
    recap. None (Z1) = all top-5.
    """
    from src.channels import COUNTRY_TO_LEAGUE

    # Hard allowlist — anything outside this set is invisible to the AI note.
    TOP5_LEAGUES = {"Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"}
    if league:
        TOP5_LEAGUES = {league} if league in TOP5_LEAGUES else set()

    day_iso = target_date.isoformat()
    # Window for yesterday in UTC (the videos table stores UTC timestamps)
    day_start_utc = datetime.combine(target_date, datetime.min.time(), tzinfo=CET) \
                            .astimezone(timezone.utc).isoformat()
    day_end_utc = datetime.combine(target_date + timedelta(days=1),
                                   datetime.min.time(), tzinfo=CET) \
                          .astimezone(timezone.utc).isoformat()

    chans = db.get_all_channels()
    ch_by_id = {c["id"]: c for c in chans}
    # Restrict to top-5 European leagues. Excludes Players / Federations /
    # OtherClub / Women (own pages) AND MLS or any other league.
    def _is_top5(c: dict) -> bool:
        if c.get("entity_type") not in ("Club", "League"):
            return False
        lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())
        return lg in TOP5_LEAGUES
    ecosystem_ids = [c["id"] for c in chans if _is_top5(c)]

    # ── Pull yesterday's videos
    videos = []
    page = 1000
    offset = 0
    while True:
        q = (db.client.table("videos")
             .select("title,view_count,channel_id,format,duration_seconds,"
                     "published_at,description")
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

    # ── Per-league rollup (includes the league's own channel — Serie A,
    # Bundesliga etc. — alongside its clubs, since they share country codes)
    per_league: dict[str, dict] = {}
    for cid, b in per_channel.items():
        ch = ch_by_id.get(cid) or {}
        if ch.get("entity_type") not in ("Club", "League"):
            continue
        lg = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper())
        if not lg:
            continue
        agg = per_league.setdefault(lg, {"new": 0, "long": 0, "short": 0, "live": 0,
                                          "club_counts": {},
                                          "league_channel_count": 0})
        agg["new"] += b["new"]
        agg["long"] += b["long"]
        agg["short"] += b["short"]
        agg["live"] += b["live"]
        if ch.get("entity_type") == "League":
            agg["league_channel_count"] = b["new"]
        else:
            agg["club_counts"][ch["name"]] = b["new"]

    per_league_out = []
    for lg, agg in sorted(per_league.items(), key=lambda kv: -kv[1]["new"]):
        most_active = (max(agg["club_counts"].items(), key=lambda kv: kv[1])
                       if agg["club_counts"] else (None, 0))
        per_league_out.append({
            "league": lg,
            "new_videos": agg["new"],
            "long": agg["long"],
            "short": agg["short"],
            "live": agg["live"],
            "most_active_club": most_active[0],
            "most_active_count": most_active[1],
            "league_channel_count": agg["league_channel_count"],
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
    viral_league_counts: dict[str, int] = {}
    for v in top_videos:
        ch = ch_by_id.get(v.get("channel_id")) or {}
        league = COUNTRY_TO_LEAGUE.get((ch.get("country") or "").upper())
        _vrow = {
            "club": ch.get("name", "?"),
            "league": league,
            "title": (v.get("title") or "")[:200],
            "views": int(v.get("view_count") or 0),
            "format": v.get("format"),
        }
        _vsnip = _desc_snippet(v.get("description") or "")
        if _vsnip:
            _vrow["desc"] = _vsnip
        viral.append(_vrow)
        if league:
            viral_league_counts[league] = viral_league_counts.get(league, 0) + 1

    # Sort breakdown by count desc so the dominant league is first.
    viral_league_breakdown = sorted(
        [{"league": lg, "count": n} for lg, n in viral_league_counts.items()],
        key=lambda r: -r["count"],
    )

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

    # ── Quiet clubs: 0 videos yesterday. Restricted to top-5 league clubs.
    top5_club_ids = [c["id"] for c in chans
                     if c.get("entity_type") == "Club"
                     and COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper()) in TOP5_LEAGUES]
    quiet = []
    for cid in top5_club_ids:
        if cid not in per_channel:
            ch = ch_by_id.get(cid) or {}
            quiet.append(ch.get("name", "?"))
    quiet = sorted(quiet)[:20]  # cap so the prompt doesn't bloat

    return {
        # When set, the model is told to frame observations as "within
        # <league>" instead of stating obvious truths ("La Liga leads"
        # when by construction it's the only league in the payload).
        **({"league_scope": league} if league else {}),
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
        "viral_league_breakdown": viral_league_breakdown,
        "title_signals": title_signals,
        "quiet_clubs": quiet,
    }


def fetch_previous_notes(db, target_date: date, n: int = 3,
                         league: str | None = None) -> list[dict]:
    """Read the last n days of daily_note rows BEFORE target_date.
    Returns oldest-first list of {date, text}. Used to feed the model
    so it can avoid repeating yesterday's opener / phrasing.

    `league` (Z2): fetch the per-league keyed rows (f'{date}|{league}')
    so anti-repetition is scoped to the same league, not the global
    note. None (Z1) = the plain date key."""
    out = []
    for back in range(1, n + 1):
        d = (target_date - timedelta(days=back)).isoformat()
        key = f"{d}|{league}" if league else d
        try:
            row = (db.client.table("dashboard_cache")
                   .select("payload")
                   .eq("name", "daily_note")
                   .eq("scope_key", key)
                   .limit(1)
                   .execute().data or [])
        except Exception:
            row = []
        if row and row[0].get("payload"):
            txt = (row[0]["payload"].get("text") or "").strip()
            if txt:
                out.append({"date": d, "text": txt})
    out.reverse()  # oldest → newest so model sees recency progression
    return out



# Common short forms a feature writer might use. Maps each variant to the
# canonical channel name as stored in the channels table. When the model
# writes "Barça", we still want the FC Barcelona dot rendered next to it.
# Keep this conservative — when in doubt, leave it off (better to miss a
# decoration than to mis-attribute a club).
NAME_ALIASES: dict[str, str] = {
    # Serie A
    "Inter": "Inter",
    "Milan": "AC Milan",
    "Juve": "Juventus",
    "Roma": "AS Roma",
    "Lazio": "S.S. Lazio",
    "Napoli": "SSC Napoli",
    "Atalanta": "Atalanta BC",
    "Fiorentina": "ACF Fiorentina",
    "Bologna": "Bologna FC 1909",
    # Premier League
    "Spurs": "Tottenham Hotspur",
    "Tottenham": "Tottenham Hotspur",
    "Man United": "Manchester United",
    "Man Utd": "Manchester United",
    "Man City": "Man City",
    "City": "Man City",
    "Liverpool": "Liverpool FC",
    "Chelsea": "Chelsea Football Club",
    "Newcastle": "Newcastle United",
    "West Ham": "West Ham United FC",
    "Villa": "Aston Villa Football Club",
    "Aston Villa": "Aston Villa Football Club",
    "Forest": "Nottingham Forest FC ",
    "Brighton": "Official Brighton & Hove Albion FC",
    # Bundesliga
    "Bayern": "FC Bayern München",
    "Dortmund": "Borussia Dortmund",
    "BVB": "Borussia Dortmund",
    "Leverkusen": "Bayer 04 Leverkusen",
    "Leipzig": "RB Leipzig",
    # La Liga
    "Barça": "FC Barcelona",
    "Barca": "FC Barcelona",
    "Barcelona": "FC Barcelona",
    "Real": "Real Madrid",
    "Atléti": "Atlético de Madrid",
    "Atleti": "Atlético de Madrid",
    "Atletico": "Atlético de Madrid",
    "Atlético Madrid": "Atlético de Madrid",
    "Sevilla": "Sevilla FC",
    "Betis": "Real Betis Balompié",
    "Valencia": "Valencia CF",
    "Athletic": "Athletic Club",
    "Villarreal": "Villarreal CF",
    "Real Sociedad": "Real Sociedad TV",
    # Ligue 1
    "PSG": "PSG - Paris Saint-Germain",
    "Paris Saint-Germain": "PSG - Paris Saint-Germain",
    "OM": "OM",
    "Marseille": "OM",
    "Lyon": "Olympique Lyonnais",
    "OL": "Olympique Lyonnais",
    "Monaco": "AS MONACO",
    "Lille": "LOSC",
    "Lens": "RCLens",
    "Nice": "OGC Nice",
}


def decorate_with_badges(note_text: str, channels: list[dict],
                         color_map: dict | None = None,
                         dual_map: dict | None = None) -> str:
    """Inject the standard concentric club dot, or the league flag,
    before each recognized name in the note. Returns HTML-ready string.

    Uses src.dot.dual_dot() at the standard 14 px size — same format as
    every page's table dots — so the AI note visually matches the rest
    of the app. color_map / dual_map can be omitted; the function falls
    back to the global ones built from channel records.

    - Case-sensitive, word-boundary matching avoids mid-word hits.
    - Longest names match first ('Real Madrid' before 'Real').
    - Placeholders prevent regex re-matching of injected HTML.
    """
    import re
    from src.channels import LEAGUE_FLAG
    from src.dot import channel_badge

    if color_map is None or dual_map is None:
        try:
            from src.filters import (get_global_color_map,
                                      get_global_color_map_dual)
            color_map = color_map or get_global_color_map()
            dual_map = dual_map or get_global_color_map_dual()
        except Exception:
            pass
        # Last-resort fallback: derive from channel records' color/color2
        if not color_map:
            color_map = {c["name"]: (c.get("color") or "#636EFA")
                         for c in channels}
        if not dual_map:
            dual_map = {c["name"]: (c.get("color") or "#636EFA",
                                    c.get("color2") or "#FFFFFF")
                        for c in channels}

    name_by_canonical = {c["name"]: c for c in channels}
    targets: dict[str, dict] = {}
    for c in channels:
        targets[c["name"]] = c
    for alias, canonical in NAME_ALIASES.items():
        if canonical in name_by_canonical and alias not in targets:
            targets[alias] = name_by_canonical[canonical]

    leagues = {lg: flag for lg, flag in LEAGUE_FLAG.items()}

    # League names always win over channel names. The Bundesliga channel
    # (entity_type='League') has the same name as the league itself; we
    # want the German flag rendered, not the channel's own brand color.
    # We keep the league HQ channel record on the side so the link
    # target for a league mention can still point at YouTube.
    from src.channels import COUNTRY_TO_LEAGUE as _C2L
    _league_hq_by_canonical: dict[str, dict] = {}
    for c in channels:
        if c.get("entity_type") == "League":
            lg = _C2L.get((c.get("country") or "").strip()) or c.get("name")
            if lg:
                _league_hq_by_canonical[lg] = c
    for lg in leagues:
        targets.pop(lg, None)

    by_length = sorted(
        [(name, ("club", ch)) for name, ch in targets.items()] +
        [(name, ("league", flag)) for name, flag in leagues.items()],
        key=lambda kv: -len(kv[0]),
    )

    placeholders: list[str] = []
    decorated = note_text

    for name, (kind, payload) in by_length:
        # Match the name as a whole word, optionally followed by a
        # possessive 's or '. The trailing apostrophe-s ends up *inside*
        # the rendered name so 'Bundesliga's' becomes a single decorated
        # span instead of a leftover orphan 's.
        pattern = (r"(?<![\w'])"
                   + re.escape(name)
                   + r"(?:['’]s|['’])?"
                   + r"(?!\w)")
        rx = re.compile(pattern)
        if not rx.search(decorated):
            continue

        # Same badge format as the core-page tables (src.dot.channel_badge):
        # league channels get a country flag in a fixed-size box, clubs get
        # the standard concentric dual_dot. Identical visual treatment makes
        # the inline AI note match the dots in every table on the site.
        def _yt_url(ch: dict) -> str | None:
            """YouTube channel URL — prefers @handle, falls back to /channel/<id>.
            Returns None if neither is available so the rendered name stays
            unlinked rather than producing a broken <a>."""
            handle = (ch.get("handle") or "").lstrip("@").strip()
            yt_id = (ch.get("youtube_channel_id") or "").strip()
            if handle:
                return f"https://www.youtube.com/@{handle}"
            if yt_id:
                return f"https://www.youtube.com/channel/{yt_id}"
            return None

        if kind == "club":
            ch = payload
            badge = channel_badge(ch, color_map, dual_map, 14)
            link_url = _yt_url(ch)
        else:
            # Synthetic League channel record so channel_badge returns the
            # flag in the same standard box wrapper as table cells.
            country_for_league = {
                "Serie A": "IT", "Premier League": "EN", "La Liga": "ES",
                "Bundesliga": "DE", "Ligue 1": "FR", "MLS": "US",
            }.get(name)
            badge = channel_badge(
                {"entity_type": "League", "country": country_for_league},
                color_map, dual_map, 14,
            )
            league_hq = _league_hq_by_canonical.get(name)
            link_url = _yt_url(league_hq) if league_hq else None

        def _build_repl(m, _url=link_url, _badge=badge):
            displayed = m.group(0)  # includes any possessive suffix
            # Inline span: small gap between badge and name, no flex wrapper
            # (flex changes baseline alignment inside italic body text).
            inner = (
                f'<span style="white-space:nowrap">'
                f'{_badge}&nbsp;<span style="vertical-align:middle">{displayed}</span>'
                f'</span>'
            )
            if not _url:
                return inner   # no link target — render the badged name plain
            # color:inherit so the link doesn't read as a bright blue
            # external link inside body prose, but text-decoration:underline
            # with a subtle gray color so it's visibly clickable. Tightening
            # the underline offset keeps it under the name not the badge.
            return (
                f'<a href="{_url}" target="_blank" rel="noopener" '
                f'style="color:inherit;text-decoration:underline;'
                f'text-decoration-color:rgba(255,255,255,0.45);'
                f'text-underline-offset:3px">'
                f'{inner}</a>'
            )
        # Replace each match with a placeholder; the real HTML for each
        # placeholder is stored separately so a later regex never sees
        # the inserted markup.
        for m in list(rx.finditer(decorated)):
            idx = len(placeholders)
            placeholders.append(_build_repl(m))
            decorated = decorated.replace(m.group(0),
                                          f"\x00BADGE_{idx}\x00", 1)

    for i, html in enumerate(placeholders):
        decorated = decorated.replace(f"\x00BADGE_{i}\x00", html)

    return decorated


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


def generate_daily_note(payload: dict,
                        previous_notes: list[dict] | None = None,
                        log=print) -> str | None:
    """Call Claude, return note text or None on failure / unsafe output.

    previous_notes: optional list of {date, text} for the last few days.
    Passed alongside `payload` so the model can avoid repetition.
    """
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

    full_input = {
        "yesterday": payload,
        "previous_notes": previous_notes or [],
    }
    user_message = json.dumps(full_input, indent=2, ensure_ascii=False)
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

    # Post-process: enforce one sentence per line. The model is asked to
    # do this in the system prompt but doesn't always — splitting here
    # guarantees the final layout regardless. Splits on sentence-ending
    # punctuation followed by a space + capital letter (or quote).
    note = _one_sentence_per_line(note)

    return note


LATEST_VIBE_PROMPT = """\
You are the in-house commentator for "YouTube Football Tracker", an
analytics dashboard watching the YouTube output of the top-5 European
men's football leagues plus the 5 league channels themselves.

Your job: write a SHORT 1-2 paragraph "vibe check" on the most-recent
videos right now. Not yesterday's recap — what is the ecosystem
*currently* posting and why does that feel like the way it does.

Voice
- Smart, dry, observational. Sports-section feature writer who reads
  the upload feed all day. Never bombastic, never fawning.
- Treat every club with respect. No "shouting into the void", no
  "small club X struggles to break N views", no implicit punch-down
  on lower-resourced clubs. Their output IS the ecosystem.
- One small aside is fine. No emojis except a single optional one.

Hard constraints (will be checked)
- DO NOT comment on view counts, likes, comments, or "engagement".
  These are FRESH videos — they haven't had time to accumulate
  audience yet, so view numbers reveal nothing useful and look silly
  the moment they update. The view_count field is in the data only
  to disambiguate, not to grade content.
- DO NOT rank or compare clubs by performance metrics.
- Never invent match results, scores, standings, or transfer news. If
  a score isn't already in a video title we hand you, you don't know it.
- NEVER state who is champion, leads the table, qualified, or got
  relegated as a real-world fact. You don't have standings. You may say
  a club is *posting* celebration/title content; you may NOT declare
  them the actual champion. ("Juventus is posting celebration content"
  is fine ONLY if their FRESH titles say so; "Juventus's championship"
  as a fact is forbidden.)
- ARCHIVE/THROWBACK GUARD: items with `"archive": true` are throwbacks,
  classics, anniversaries, or old-season content (a 2015/16 rewind, an
  "on this day"). They are HISTORY, not current news. A championship,
  trophy, score, or transfer in an archive title is a PAST event — never
  present it as something happening now, and never let one drive the
  headline. If most title-win/result language comes from archive items,
  the real story is "clubs are publishing throwback content", not a
  current result.
- Never name a player who isn't in the data.
- If the payload includes `league_scope`, EVERYTHING you've been
  given is already filtered to that single league. Do NOT observe
  that the league "dominates", "leads", or "publishes the most" — by
  construction it's the only thing in scope. Frame observations as
  "within <league>": which clubs, which formats, which themes stand
  out INSIDE that league. Never compare to other leagues (there's
  no other-league data here).
- Don't generalize from one channel to a league.
- Don't write the obvious ("there are videos from many clubs",
  "Shorts are short"). If you can't find a non-obvious observation,
  keep it shorter.
- If the recent feed is genuinely thin, say so plainly.

What you'll get
- A JSON blob with the last ~60 most-recent videos (channel, title,
  format, age, league, category) and a quick aggregate (counts by
  format, counts by league, time span covered).

What you'll get (continued)
- Each item has an `archive` flag (true = throwback/classic/old-season).
  `totals.archive` is how many of the feed are archive. Discount these
  for any "what's happening now" read.

What to lean on
- READ THE TITLES, but only FRESH (archive:false) ones for current
  events. If several fresh titles converge on a real moment ("CHAMPIONS
  OF ITALY", "WE ARE CHAMPIONS", a manager leaving, a cup final) that's
  a fair headline — describe what they're POSTING, don't assert league
  standings. One fresh title alone is weak; convergence across fresh
  items is the signal. If the title-win language is mostly archive,
  don't lead with a championship at all.
- USE `desc` (the short description blurb) when present — it's the most
  reliable disambiguator. It tells you what a video ACTUALLY is when the
  title is vague or branded: confirms a throwback, reveals the real
  opponent/competition, or shows a "Champions ..." title is a series/
  sponsor campaign rather than a title win. Trust `desc` over a flashy
  title. Never quote it verbatim — paraphrase.
- Format mix: is the feed Shorts-heavy right now, Long-heavy, Live
  showing up post-matchday, etc. Only mention this if it's NOT what
  you'd expect for the moment (post-matchday Long-heavy is normal).
- League skew: is one league posting noticeably more right now —
  often a tell for fixture timing.
- Cadence: are uploads clustered (post-matchday burst) or sparse
  (international break / off-day).
- Theme drift if obvious from titles or category (highlights, press
  conferences, training, transfer announcements). Don't force it.

Output format
- Exactly 3-5 short sentences total, ~60-80 words.
- Put each sentence on its own line (single newline between them).
- Plain text. No markdown, no JSON, no list bullets."""


# Throwback / classic / old-season content. A club uploads these all the
# time (a "REWIND" of a 2015/16 derby, an "On this day" anniversary), and
# because they're freshly UPLOADED their age_hours looks current — so the
# model used to read an old title-win as a current championship. We flag
# them so the note can discount them and never assert a historical result
# as a present-day fact.
_ARCHIVE_KW = (
    "rewind", "flashback", "throwback", "on this day", "years ago",
    "anniversary", "un secolo", "#tbt", "retro", "classic match",
    "greatest goals", "all goals 20", "best goals of", "legends of",
    "the story of", "vintage", "from the archive", "decades",
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# Cut a description down to its editorial lead — the bit BEFORE the
# repeated SUBSCRIBE/social footer — so notes get real context without
# the boilerplate eating tokens. Cuts at the first blank line or a footer
# marker (multilingual), collapses whitespace, caps length.
_DESC_FOOTER_RE = re.compile(
    r"(?is)(\n\s*\n|subscribe|►|▶|🔔|🛒|follow us|instagram\s*:|"
    r"join (us|as|the)|download the|suscr[ií]bete|s[ií]guenos|abonne|"
    r"abonnier|iscriviti|seguici|segueix|🎥|📸|📱)"
)


def _desc_snippet(text: str, n: int = 200) -> str:
    """First editorial paragraph of a description, footer stripped, ≤ n chars."""
    if not text:
        return ""
    t = text.strip()
    m = _DESC_FOOTER_RE.search(t)
    if m and m.start() > 0:
        t = t[:m.start()]
    t = " ".join(t.split())
    return t[:n].strip()


def _is_archive_item(title: str, category: str, current_year: int) -> bool:
    """Heuristic: is this video about a PAST event (not current news)?
    Triggers on throwback keywords OR a 4-digit year two+ seasons old
    (<= current_year-2, so the live 2025/26 season's "2025" never trips)."""
    t = (title or "").lower()
    if any(k in t for k in _ARCHIVE_KW):
        return True
    for m in _YEAR_RE.finditer(t):
        if int(m.group(0)) <= current_year - 2:
            return True
    return (category or "").lower() in ("classic", "throwback", "archive")


def compose_latest_payload(videos: list[dict],
                            channels_by_id: dict | None = None) -> dict:
    """Build a small structured payload for the latest-vibe note.

    videos: rows from db.get_recent_videos (already filtered to the
    visible scope by the caller — All Leagues / one league / one club).
    channels_by_id: id → channel dict so we can attach channel name +
    league. Optional; if missing we fall back to embedded channel_name.
    """
    from src.channels import COUNTRY_TO_LEAGUE
    if not videos:
        return {"items": [], "totals": {"videos": 0}}

    items = []
    fmt_counts = {"long": 0, "short": 0, "live": 0}
    league_counts: dict[str, int] = {}
    now_utc = datetime.now(timezone.utc)
    _cur_year = now_utc.year
    archive_count = 0
    oldest = newest = None
    # Up to 60 — big narrative arcs (championship clinches, cup
    # finals, manager moves) need a wider window than just the
    # very-latest hour.
    for v in videos[:60]:
        ch = (channels_by_id or {}).get(v.get("channel_id")) or {}
        ch_name = v.get("channel_name") or ch.get("name") or "?"
        country = (ch.get("country") or "").strip()
        league = COUNTRY_TO_LEAGUE.get(country, "")
        fmt = (v.get("format") or "").lower()
        if fmt not in ("long", "short", "live"):
            fmt = "long" if (v.get("duration_seconds") or 0) >= 60 else "short"
        fmt_counts[fmt] += 1
        if league:
            league_counts[league] = league_counts.get(league, 0) + 1
        # Effective publish time (live uses actual_start_time)
        pub_iso = v.get("effective_date") or v.get("published_at") or ""
        age_hours = None
        if pub_iso:
            try:
                pub_dt = datetime.fromisoformat(pub_iso.replace("Z", "+00:00"))
                age_hours = round((now_utc - pub_dt).total_seconds() / 3600, 1)
                if oldest is None or pub_dt < oldest:
                    oldest = pub_dt
                if newest is None or pub_dt > newest:
                    newest = pub_dt
            except Exception:
                pass
        _title = (v.get("title") or "").strip()[:160]
        _archive = _is_archive_item(_title, v.get("category") or "", _cur_year)
        if _archive:
            archive_count += 1
        item = {
            "channel": ch_name,
            "league": league,
            "title": _title,
            "format": fmt,
            "view_count": int(v.get("view_count") or 0),
            "age_hours": age_hours,
            "category": v.get("category") or "",
            "archive": _archive,
        }
        _snip = _desc_snippet(v.get("description") or "")
        if _snip:
            item["desc"] = _snip
        items.append(item)

    span_hours = None
    if oldest and newest:
        span_hours = round((newest - oldest).total_seconds() / 3600, 1)

    return {
        "items": items,
        "totals": {
            "videos": len(items),
            "by_format": fmt_counts,
            "by_league": league_counts,
            "span_hours": span_hours,
            "archive": archive_count,
        },
    }


def generate_latest_vibe(videos: list[dict],
                         channels_by_id: dict | None = None,
                         log=print,
                         league: str | None = None) -> str | None:
    """Call Claude for a short vibe note on the latest videos. Returns
    plain text (one sentence per line) or None on failure / empty input."""
    if not videos:
        return None

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("[latest_vibe] ANTHROPIC_API_KEY missing — skipping")
        return None

    try:
        import anthropic
    except ImportError:
        log("[latest_vibe] anthropic package not installed — skipping")
        return None

    payload = compose_latest_payload(videos, channels_by_id=channels_by_id)
    if league:
        payload = {"league_scope": league, **payload}
    user_message = json.dumps(payload, indent=2, ensure_ascii=False)

    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=220,
                temperature=0.6,
                system=LATEST_VIBE_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            log(f"[latest_vibe] Claude API error: {e}")
            return None
        except Exception as e:
            log(f"[latest_vibe] error: {e}")
            return None
    else:
        return None

    note = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if not note:
        return None

    # Anti-BS: reject if model invented a score not in titles.
    titles = [it.get("title", "") for it in payload.get("items", [])]
    if _looks_like_invented_score(note, titles):
        log("[latest_vibe] rejected — note contains a score not in source titles")
        return None

    return _one_sentence_per_line(note)


# ── 30-Day Trends vibe ───────────────────────────────────────────
# Narrates a trailing-30-day shape: Δ video views (from per-video
# deltas), new uploads by format, league/channel breakdown, top
# performers. Reads from the precomputed trends_30d cache payload —
# no fresh aggregation in the model's hands. Z1 (All Leagues,
# scope_all) + Z2 (per-league, scope_league).
TRENDS_30D_VIBE_PROMPT = """\
You are the in-house analyst for "YouTube Football Tracker", a
dashboard watching the YouTube output of the top-5 European men's
football leagues plus the 5 league channels themselves.

Your job: write a SHORT "30-day shape" read — the narrative of how
these channels' YouTube output has moved over the trailing 30 days.
Not yesterday, not the season — the past month, the trajectory.

Voice
- Smart, dry, observational. A sports-data feature writer.
- Treat every club and league with respect. No punch-down on
  lower-resourced clubs. Modest output is still the story.
- At most one light aside. No more than one optional emoji.

Hard constraints (will be checked)
- Use ONLY the numbers in the JSON. Never invent or recompute a
  figure, ranking, percentage, league table, score, transfer, or a
  player/manager name. If it isn't in the data, you don't know it.
- "Δ views" here is each channel's lifetime view-count growth (the
  day-over-day diff of `total_views` on the channel), summed across
  the cohort. So it captures growth on EVERY video on those channels
  — including older archive videos still earning views — not just
  the recently-published cohort.
- "New videos" = videos PUBLISHED during the trailing 30 days. This
  is a DIFFERENT cohort from the views above. Don't divide Δ views by
  new-video count to fake an "engagement per upload" rate — old
  videos contribute heavily to the numerator.
- Occasional sharp negative dips happen when YouTube scrubs a
  channel's lifetime view counter (anti-spam recalibration). If you
  see a clear single-day -X drop on a specific channel, note it as
  a recalibration, not a real audience loss.
- If the payload includes `league_scope`, EVERYTHING you've been
  given is already filtered to that single league. Do NOT observe
  that the league "dominates", "leads", or "publishes the most" — by
  construction it's the only thing in scope. Frame inside the
  league: which clubs, which formats, which themes drove the trend.
  Never compare to other leagues (no other-league data here).
- Don't generalize from one channel to a league.
- Don't state the obvious ("clubs post videos", "Shorts are short",
  "Premier League is popular"). If you can't find a non-obvious
  observation, keep it shorter.

What you'll get
- A JSON payload with:
  - `window`: trailing dates covered
  - `totals`: cohort Δ views, new-video count, format split
  - `by_week`: 5 weekly buckets so you can see acceleration / decay
  - `by_league` (Z1) or `by_channel` (Z2): the breakdown
  - `top_videos`: titles + channel + format + window-delta for the
    biggest movers — READ THE TITLES, they tell you what happened
    (championships, debuts, viral moments, kit launches)

What to lean on
- `archive_share_pct` is the headline metric most weeks: it tells you
  what % of the cohort's 30-day Δ views came from videos OLDER than
  30 days post-publish (i.e. the back-catalogue still earning). A
  high archive share (>50%) means new uploads aren't pulling the
  cohort — the legacy library is. A low archive share means the
  cycle is fresh-content-driven. ALWAYS mention this if it's
  >50% or <30% — it's the most non-obvious observation on the page.
  Per-league/per-channel groups also carry their own archive_share_pct
  so you can spot the divergence (e.g. "Bayern's growth is 35%
  archive vs. Inter's 75%").
- Trajectory: is the cohort's Δ views climbing week-over-week or
  decaying? Is uploading pace ramping into matchweek bursts?
- Format mix shifts: Shorts surging in week 4, Live spike on a
  specific weekend (probably matchday).
- Concentration: did one league or one club carry an outsized share
  of the cohort delta? Cite the figure from `by_league` /
  `by_channel` directly.
- Top video narrative: the top-5 titles often hand you the headline
  for free — title clinches, Coppa Italia finals, late-season
  drama, viral dressing-room clips.

Output format
- Exactly 3-5 short sentences total, ~70-90 words.
- Put each sentence on its own line (single newline between them).
- Plain text. No markdown, no JSON, no list bullets, no bold."""


def _weekly_buckets(by_date: list[dict], window_dates: list[str]) -> list[dict]:
    """Roll a daily by_date series into 5 weekly buckets so the model
    can see week-over-week shape without 30 individual data points."""
    from datetime import date as _d, timedelta as _td
    if not window_dates:
        return []
    start = _d.fromisoformat(window_dates[0])
    end = _d.fromisoformat(window_dates[-1])
    # Five rolling 6-day buckets aligned to the start; the trailing
    # bucket may be 5 days if the window isn't a perfect 30.
    bucket_size = max(1, (end - start).days // 5 or 6)
    rows_by_date = {r["date"]: r for r in (by_date or [])}
    out: list[dict] = []
    d = start
    while d <= end:
        b_end = min(d + _td(days=bucket_size - 1), end)
        b_view_delta = 0
        b_new = 0
        b_long = b_short = b_live = 0
        cur = d
        while cur <= b_end:
            row = rows_by_date.get(cur.isoformat()) or {}
            b_view_delta += int(row.get("dv") or 0)
            b_long += int(row.get("new_long") or 0)
            b_short += int(row.get("new_short") or 0)
            b_live += int(row.get("new_live") or 0)
            b_new += int(row.get("new_long") or 0) + int(row.get("new_short") or 0) + int(row.get("new_live") or 0)
            cur += _td(days=1)
        out.append({
            "week_start": d.isoformat(),
            "week_end": b_end.isoformat(),
            "view_delta": b_view_delta,
            "new_videos": b_new,
            "long": b_long, "short": b_short, "live": b_live,
        })
        d = b_end + _td(days=1)
    return out


def compose_trends_30d_payload(trends_payload: dict,
                                league: str | None = None) -> dict:
    """Condense a cached trends_30d payload into the smaller blob the
    LLM sees. Keeps totals + weekly buckets + the breakdown summary +
    top 10 videos. Strips the raw 30 per-day points for the model.
    """
    if not trends_payload:
        return {"totals": {}, "by_week": [], "top_videos": []}

    dates = trends_payload.get("dates") or []
    cohort_by_date = (trends_payload.get("cohort") or {}).get("by_date") or []

    total_dv = sum(int(r.get("dv") or 0) for r in cohort_by_date)
    total_long = sum(int(r.get("new_long") or 0) for r in cohort_by_date)
    total_short = sum(int(r.get("new_short") or 0) for r in cohort_by_date)
    total_live = sum(int(r.get("new_live") or 0) for r in cohort_by_date)
    total_new = total_long + total_short + total_live
    days = max(len(dates), 1)

    # Archive contribution split — what % of the Δ views came from
    # videos OLDER than 30 days post-publish (channel-wide growth that
    # the tracked-pool can't see). Pre-computed by the cache builder.
    split = (trends_payload.get("cohort") or {}).get("split") or {}

    totals = {
        "view_delta": total_dv,
        "new_videos": total_new,
        "by_format": {"long": total_long, "short": total_short, "live": total_live},
        "avg_dv_per_day": int(total_dv / days),
        "avg_new_per_day": round(total_new / days, 1),
        "archive_share_pct": float(split.get("archive_share_pct") or 0.0),
        "archive_view_delta": int(split.get("archive_view_delta") or 0),
        "fresh_view_total": int(split.get("fresh_view_total") or 0),
    }

    by_week = _weekly_buckets(cohort_by_date, dates)

    # Breakdown groups — compress each group's per-day series into
    # window totals. The label depends on whether it's a per-league
    # (Z1) or per-channel (Z2) payload.
    bd = trends_payload.get("breakdown") or {}
    groups_summary = []
    for g in (bd.get("groups") or []):
        g_dv = sum(int(r.get("dv") or 0) for r in (g.get("by_date") or []))
        g_new = sum(int(r.get("new_videos") or 0) for r in (g.get("by_date") or []))
        groups_summary.append({
            "name": g.get("name") or g.get("key"),
            "view_delta": g_dv,
            "new_videos": g_new,
        })
    # Each group's archive share too — useful when the model wants to
    # contrast "X earns from new uploads" vs "Y still lives off the
    # back-catalogue".
    for g, summary in zip((bd.get("groups") or []), groups_summary):
        gs = g.get("split") or {}
        summary["archive_share_pct"] = float(gs.get("archive_share_pct") or 0.0)
    # Sort by Δ views desc so the model sees the heaviest contributors first.
    groups_summary.sort(key=lambda r: -r["view_delta"])

    top_videos_in = trends_payload.get("top_videos") or []
    top_videos = []
    for v in top_videos_in[:10]:
        pub = (v.get("published_at") or "")
        try:
            pub_date = pub[:10]
        except Exception:
            pub_date = ""
        top_videos.append({
            "title": (v.get("title") or "").strip()[:160],
            "channel": v.get("channel_name") or "?",
            "format": (v.get("format") or "").lower() or "?",
            "delta_in_window": int(v.get("delta_in_window") or 0),
            "published": pub_date,
        })

    out = {
        "window": {
            "start": trends_payload.get("window_start"),
            "end": trends_payload.get("window_end"),
            "days": days,
        },
        "totals": totals,
        "by_week": by_week,
        "top_videos": top_videos,
    }
    label = "by_channel" if bd.get("group_label") == "channel" else "by_league"
    out[label] = groups_summary
    if league:
        out = {"league_scope": league, **out}
    return out


def generate_trends_30d_vibe(trends_payload: dict,
                              league: str | None = None,
                              log=print) -> str | None:
    """Call Claude (haiku) for a 30-day-shape vibe note. Returns plain
    text (one sentence per line) or None on failure / empty input."""
    if not trends_payload or not trends_payload.get("dates"):
        return None

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("[trends_30d_vibe] ANTHROPIC_API_KEY missing — skipping")
        return None
    try:
        import anthropic
    except ImportError:
        log("[trends_30d_vibe] anthropic package not installed — skipping")
        return None

    payload = compose_trends_30d_payload(trends_payload, league=league)
    user_message = json.dumps(payload, indent=2, ensure_ascii=False)

    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=240,
                temperature=0.6,
                system=TRENDS_30D_VIBE_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            log(f"[trends_30d_vibe] Claude API error: {e}")
            return None
        except Exception as e:
            log(f"[trends_30d_vibe] error: {e}")
            return None
    else:
        return None

    note = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if not note:
        return None
    # Anti-BS: reject if model invented a score not present in top_videos titles.
    titles = [it.get("title", "") for it in payload.get("top_videos", [])]
    if _looks_like_invented_score(note, titles):
        log("[trends_30d_vibe] rejected — note contains a score not in source titles")
        return None
    return _one_sentence_per_line(note)


def _one_sentence_per_line(text: str) -> str:
    """Reformat so each sentence sits on its own line.

    Splits on `[.!?]` followed by whitespace + an uppercase letter or
    open quote. Conservative: won't split on common abbreviations
    inside the writing voice we expect (the model is told not to use
    e.g. "Mr." or scores, so the abbreviation surface is small).
    """
    # First collapse any existing internal newlines + double spaces so
    # we control the layout, regardless of what the model emitted.
    text = re.sub(r"\s+", " ", text).strip()
    # Insert a newline after sentence-ending punctuation when followed
    # by a capital letter or open-quote that starts the next sentence.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", text)
    return "\n".join(p.strip() for p in parts if p.strip())


# ── Season vibe ──────────────────────────────────────────────────
# Same architecture as the latest vibe (haiku, cached in
# dashboard_cache, refreshed nightly). DIFFERENCE: this narrates the
# *season so far* — accumulated output since the season start — so
# unlike the latest note, season view totals ARE meaningful and may
# be discussed (they're handed in pre-aggregated; the model never
# computes or invents them).
SEASON_VIBE_PROMPT = """\
You are the in-house analyst for "YouTube Football Tracker", a
dashboard watching the YouTube output of the top-5 European men's
football leagues plus the 5 league channels themselves.

Your job: write a SHORT "season so far" read — the narrative of how
these channels have used YouTube since the season started. Not a
single day; the shape of the whole season to date.

Voice
- Smart, dry, observational. A sports-data feature writer.
- Treat every club and league with respect. No punch-down on
  lower-resourced clubs — modest output is still part of the story,
  never a failing to mock.
- At most one light aside. No more than one optional emoji.

Hard constraints (will be checked)
- Use ONLY the numbers in the JSON. Never invent or recompute a
  figure, ranking, percentage, league table, score, transfer, or a
  player/manager name. If it isn't in the data, you don't know it.
- "Season" here means videos PUBLISHED since the season start date in
  the data — say it that way if you reference scope. Do not imply it
  measures views gained during the season.
- Don't state the obvious ("clubs post videos", "Shorts are short").
  If you can't find a non-obvious angle, write less.
- Don't generalize a whole league from one club.
- If the payload includes `league_scope`, EVERYTHING you've been
  given is already filtered to that single league. Do NOT observe
  that the league "dominates", "leads", or "publishes the most" — by
  construction it's the only thing in scope. Frame observations as
  "within <league>": which clubs, which formats, which themes stand
  out INSIDE that league. Never compare to other leagues (there's
  no other-league data here).
- Every leader carries its own "league" field. NEVER place a club in
  a league it doesn't belong to (Barcelona / Real Madrid are La Liga;
  Arsenal / Man Utd are the Premier League). If you cite a club next
  to a league, they must match the data.

What you'll get
- A JSON digest: per-league totals (channels, season videos, season
  views, format split, views/video), the top channels by season
  views, and any channels that are unusually Shorts-heavy this season.

What to lean on
- Who is actually driving the season's attention (the leaders block).
- Format strategy: is a league or a notable channel leaning hard into
  Shorts vs long-form this season — only call it out if it stands out.
- League-level skew: one league out-publishing or out-viewing the
  rest, and whether volume vs. views diverge (lots of videos, modest
  views, or vice-versa) — that contrast is usually the real story.
- Anything genuinely non-obvious in the digest.

Output format
- HARD CAP: 3-4 sentences, 90 words MAX. Going over is a failure.
- Put each sentence on its own line (single newline between them).
- Plain text. No markdown, no JSON, no bullets."""


def compose_season_payload(channels: list[dict],
                           season_start: str = "") -> dict:
    """Pre-aggregated season digest for the season-vibe note. Small by
    design (per-league rollup + a few leaders) so the call stays cheap
    and the model can only narrate figures we computed."""
    from src.channels import COUNTRY_TO_LEAGUE
    if not channels:
        return {"season_start": season_start, "totals": {"channels": 0},
                "per_league": [], "leaders": [], "shorts_heavy": []}

    def _i(c, k):
        try:
            return int(c.get(k) or 0)
        except Exception:
            return 0

    lg: dict[str, dict] = {}
    rows = []
    for c in channels:
        # Both clubs and the 5 league channels carry a country that
        # maps to the league (e.g. the "Premier League" channel →
        # England → Premier League), so one lookup covers both.
        league = COUNTRY_TO_LEAGUE.get((c.get("country") or "").strip())
        if not league:
            continue
        sv = _i(c, "season_video_count")
        svw = _i(c, "season_views")
        lo = _i(c, "season_long_videos")
        sh = _i(c, "season_short_videos")
        li = _i(c, "season_live_videos")
        b = lg.setdefault(league, {"channels": 0, "videos": 0, "views": 0,
                                   "long": 0, "short": 0, "live": 0})
        b["channels"] += 1
        b["videos"] += sv
        b["views"] += svw
        b["long"] += lo
        b["short"] += sh
        b["live"] += li
        rows.append({
            "channel": c.get("name") or "?",
            "league": league,
            "is_league_channel": c.get("entity_type") == "League",
            "videos": sv,
            "views": svw,
            "format_videos": {"long": lo, "short": sh, "live": li},
        })

    per_league = []
    for name, b in lg.items():
        v = b["videos"]
        fv = b["long"] + b["short"] + b["live"]
        per_league.append({
            "league": name,
            "channels": b["channels"],
            "season_videos": v,
            "season_views": b["views"],
            "views_per_video": (b["views"] // v) if v else 0,
            "format_pct": {
                "long": round(100 * b["long"] / fv, 1) if fv else 0,
                "short": round(100 * b["short"] / fv, 1) if fv else 0,
                "live": round(100 * b["live"] / fv, 1) if fv else 0,
            },
        })
    per_league.sort(key=lambda r: -r["season_views"])

    leaders = sorted(rows, key=lambda r: -r["views"])[:8]

    shorts_heavy = []
    for r in rows:
        fv = sum(r["format_videos"].values())
        if fv >= 50:
            pct = round(100 * r["format_videos"]["short"] / fv, 1)
            if pct >= 70:
                shorts_heavy.append({"channel": r["channel"],
                                     "league": r["league"],
                                     "shorts_pct": pct,
                                     "season_videos": r["videos"]})
    shorts_heavy.sort(key=lambda r: -r["shorts_pct"])
    shorts_heavy = shorts_heavy[:5]

    return {
        "season_start": season_start,
        "totals": {
            "channels": len(rows),
            "season_videos": sum(r["videos"] for r in rows),
            "season_views": sum(r["views"] for r in rows),
        },
        "per_league": per_league,
        "leaders": leaders,
        "shorts_heavy": shorts_heavy,
    }


def generate_season_vibe(channels: list[dict],
                         season_start: str = "",
                         log=print,
                         league: str | None = None) -> str | None:
    """Call Claude for a short 'season so far' note. Returns plain text
    (one sentence per line) or None on failure / empty input."""
    if not channels:
        return None

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("[season_vibe] ANTHROPIC_API_KEY missing — skipping")
        return None

    try:
        import anthropic
    except ImportError:
        log("[season_vibe] anthropic package not installed — skipping")
        return None

    payload = compose_season_payload(channels, season_start=season_start)
    if league:
        payload = {"league_scope": league, **payload}
    if not payload.get("leaders"):
        log("[season_vibe] no season data — skipping")
        return None
    user_message = json.dumps(payload, indent=2, ensure_ascii=False)

    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=260,
                temperature=0.6,
                system=SEASON_VIBE_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            log(f"[season_vibe] Claude API error: {e}")
            return None
        except Exception as e:
            log(f"[season_vibe] error: {e}")
            return None
    else:
        return None

    note = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if not note:
        return None
    return _one_sentence_per_line(note)


# ── Season-Top vibe ──────────────────────────────────────────────
# Narrates the *biggest hits* of the season — the top videos by
# views. Unlike latest_vibe (fresh videos, no audience yet) these
# numbers ARE meaningful: they're the season's proven winners. So
# the model may discuss what wins, but only from the figures/titles
# we hand it — never an invented score, player, or transfer.
SEASON_TOP_VIBE_PROMPT = """\
You are the in-house analyst for "YouTube Football Tracker", a
dashboard watching the top-5 European men's football leagues.

Your job: write a SHORT read on the season's BIGGEST VIDEOS so far —
the most-viewed uploads since the season started. What kind of
content actually wins, and who owns the leaderboard.

Voice
- Smart, dry, observational. A sports-data feature writer.
- Respect every club and league. No punch-down on smaller clubs.
- At most one light aside. No more than one optional emoji.

Hard constraints (will be checked)
- Use ONLY the numbers and video titles in the JSON. Never invent or
  recompute a figure, ranking, league table, score, transfer, or a
  player/manager name. If a score/result isn't already printed in a
  title we gave you, you don't know it.
- "Season" = videos PUBLISHED since the season start in the data.
- Cite all counts/views ONLY from `totals`. The `items` list is just
  a sample of the leaders for context — never count or total it, and
  never describe the set by the sample size. The set size is
  `totals.videos_in_top_set`; refer to it as that (e.g. "top 100").
- Don't state the obvious ("popular videos get views"). If there's no
  non-obvious angle, write less.
- Don't generalize a whole league from one viral hit.
- If the payload includes `league_scope`, EVERYTHING you've been
  given is already filtered to that single league. Do NOT observe
  that the league "dominates" the top set or "owns" the leaderboard
  — by construction it's the only thing in scope. Frame observations
  as "within <league>": which clubs lead, which formats win, which
  themes recur INSIDE that league. Never compare to other leagues
  (there's no other-league data here).

What you'll get
- The top videos by views (channel, league, title, format, views)
  plus aggregates: format mix of the winners, league mix by count and
  by views, the #1 video's share of the top set, publish-date span.

What to lean on
- READ THE TITLES. The pattern in what tops the chart is usually
  printed there — title launches, derbies, cup runs, marquee
  signings, viral one-offs. Lead with the real pattern.
- Format of the winners: are the season's biggest hits Shorts or
  long-form? Only flag it if it's not what you'd expect.
- Concentration: is the top set one mega-hit + a long tail, or evenly
  spread — and does one league dominate by views while another
  dominates by sheer count? That contrast is usually the story.

Output format
- HARD CAP: 3-4 sentences, 90 words MAX. Going over is a failure.
- Put each sentence on its own line (single newline between them).
- Plain text. No markdown, no JSON, no bullets."""


def compose_season_top_payload(top_videos: list[dict],
                               channels_by_id: dict | None = None) -> dict:
    """Compact digest of the season's top videos for the note."""
    from src.channels import COUNTRY_TO_LEAGUE
    if not top_videos:
        return {"items": [], "totals": {"videos": 0}}

    def _league_of(v):
        ch = (channels_by_id or {}).get(v.get("channel_id")) or {}
        return COUNTRY_TO_LEAGUE.get((ch.get("country") or "").strip(), "")

    def _fmt_of(v):
        f = (v.get("format") or "").lower()
        if f in ("long", "short", "live"):
            return f
        return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"

    # Aggregates MUST cover the FULL top set — otherwise the model is
    # handed "47 of N" numbers while videos_in_top_set / top1_share are
    # computed over all of them, and it narrates "22 of the top 100"
    # (the old bug: aggregates were accumulated only over the 40-item
    # prompt sample).
    fmt_counts = {"long": 0, "short": 0, "live": 0}
    lg_count: dict[str, int] = {}
    lg_views: dict[str, int] = {}
    oldest = newest = None
    for v in top_videos:
        league = _league_of(v)
        fmt_counts[_fmt_of(v)] += 1
        vc = int(v.get("view_count") or 0)
        if league:
            lg_count[league] = lg_count.get(league, 0) + 1
            lg_views[league] = lg_views.get(league, 0) + vc
        pub = v.get("published_at") or ""
        if pub:
            try:
                d = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                if oldest is None or d < oldest:
                    oldest = d
                if newest is None or d > newest:
                    newest = d
            except Exception:
                pass

    # The per-video sample handed to the model is capped (token budget);
    # it's explicitly the "top N shown" and separate from the totals.
    SAMPLE = 40
    items = []
    for v in top_videos[:SAMPLE]:
        ch = (channels_by_id or {}).get(v.get("channel_id")) or {}
        items.append({
            "channel": v.get("channel_name") or ch.get("name") or "?",
            "league": _league_of(v),
            "title": (v.get("title") or "").strip()[:160],
            "format": _fmt_of(v),
            "view_count": int(v.get("view_count") or 0),
        })

    grand_total = sum(int(v.get("view_count") or 0) for v in top_videos)
    top1 = int(top_videos[0].get("view_count") or 0) if top_videos else 0
    span_days = None
    if oldest and newest:
        span_days = round((newest - oldest).total_seconds() / 86400, 1)

    return {
        "note_scope": (f"Aggregates below cover the full top "
                       f"{len(top_videos)} videos by views. 'items' is "
                       f"only the top {len(items)} shown for reference — "
                       f"do not say 'top {len(items)}'."),
        "items": items,
        "totals": {
            "videos_in_top_set": len(top_videos),
            "by_format": fmt_counts,
            "by_league_count": lg_count,
            "by_league_views": lg_views,
            "top1_view_count": top1,
            "top1_share_pct": round(100 * top1 / grand_total, 1) if grand_total else 0,
            "publish_span_days": span_days,
        },
    }


def generate_season_top_vibe(top_videos: list[dict],
                             channels_by_id: dict | None = None,
                             log=print,
                             league: str | None = None) -> str | None:
    """Call Claude for a short note on the season's top videos. Returns
    plain text (one sentence per line) or None on failure / empty."""
    if not top_videos:
        return None

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("[season_top_vibe] ANTHROPIC_API_KEY missing — skipping")
        return None

    try:
        import anthropic
    except ImportError:
        log("[season_top_vibe] anthropic package not installed — skipping")
        return None

    payload = compose_season_top_payload(top_videos,
                                         channels_by_id=channels_by_id)
    if league:
        payload = {"league_scope": league, **payload}
    user_message = json.dumps(payload, indent=2, ensure_ascii=False)

    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=260,
                temperature=0.6,
                system=SEASON_TOP_VIBE_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            log(f"[season_top_vibe] Claude API error: {e}")
            return None
        except Exception as e:
            log(f"[season_top_vibe] error: {e}")
            return None
    else:
        return None

    note = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    if not note:
        return None

    # Anti-BS: reject if the model invented a scoreline not in titles.
    titles = [it.get("title", "") for it in payload.get("items", [])]
    if _looks_like_invented_score(note, titles):
        log("[season_top_vibe] rejected — invented score not in source titles")
        return None

    return _one_sentence_per_line(note)
