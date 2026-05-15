"""Channel profile / outlier detection.

Compares each channel's structural ratios against two peer sets:
  1. League peers (same competition)
  2. Size cohort (similar subscriber-count tier across all leagues)

Uses log-transformed ratios + median + MAD for robust z-scores that
survive the heavy power-law tail of football audience data — without
log10, raw z-scores would just say "the giants are giant".
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

from src.channels import COUNTRY_TO_LEAGUE


# Tag vocabulary — each axis has a positive (z>0) and a negative (z<0) tag.
# Keep the wording short and human; these render as small chips.
TAG_VOCAB = {
    "vps":          ("👀 Active audience",       "😴 Dormant audience"),
    "vpv":          ("🎯 Current hits",          "❄️ Cold streak"),
    "spv":          ("📺 Audience > output",     "💪 Output > audience"),
    "spy":          ("🚀 Fast-growing",          "🐢 Stagnant growth"),
    "vpy":          ("📡 Steady producer",       "🔇 Low cadence"),
    "shorts_share": ("⚡ Shorts-first",           "🎬 Long-form focused"),
    "engagement":   ("💬 Engaged community",     "🤐 Silent viewers"),
    "pace_change":  ("📈 Ramping up",            "🐌 Slowing down"),
}

AXIS_LABEL = {
    "vps":          "Season Views / Sub",
    "vpv":          "Season Views / Video",
    "spv":          "Subs / Video",
    "spy":          "Subs / Year",
    "vpy":          "Videos / Year",
    "shorts_share": "Format mix",
    "engagement":   "Engagement rate",
    "pace_change":  "Pace change",
}

# Ratio axes use log10 (power-law tails). Proportion / multiplier axes
# are scored on their raw value.
LOG_AXES    = {"vps", "vpv", "spv", "spy", "vpy", "pace_change"}
LINEAR_AXES = {"shorts_share", "engagement"}
ALL_AXES    = (*LOG_AXES, *LINEAR_AXES)

# Sample-size guards.
# • 5 season videos is enough to compute season_VPV / engagement / pace
#   (single uploads still influence numbers but variance is bounded).
# • 20 season videos before we trust shorts_share — a single upload
#   can swing the percentage by 5%+ below that count.
MIN_SEASON_VIDEOS_FOR_RATES = 5
MIN_SEASON_VIDEOS_FOR_SHARE = 20

# Season start used for pace-change calculations. Matches the season_*
# columns on the channels table, populated by the daily cron. Derived
# from src.channels.DEFAULT_SEASON_START so it auto-rolls when the new
# European season starts (was hardcoded "2025-08-01").
from src.channels import DEFAULT_SEASON_START as _DEFAULT_SEASON_START
SEASON_START_ISO: str = _DEFAULT_SEASON_START

# Size buckets (log-spaced). Used for the size-cohort lens.
SIZE_BUCKETS = [
    ("Tiny",  0,         50_000),
    ("Small", 50_000,    500_000),
    ("Mid",   500_000,   5_000_000),
    ("Giant", 5_000_000, float("inf")),
]

Z_THRESHOLD = 1.5  # |z| ≥ this → tag fires


def _age_years(channel: dict, now: datetime | None = None) -> float | None:
    """Channel age in years from launched_at; None if missing or too young."""
    iso = channel.get("launched_at") or ""
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if now is None:
            now = datetime.now(timezone.utc)
        y = (now - d).days / 365.25
        return y if y > 0.1 else None
    except Exception:
        return None


def compute_ratios(channel: dict, now: datetime | None = None) -> dict:
    """Eight axes per channel. Values are None when not computable
    (missing launched_at for spy/vpy/pace_change; tiny season catalog
    for the season-based axes).

    Most axes use SEASON data so older channels' lifetime accumulation
    doesn't distort the picture — what matters is what the channel is
    doing right now, in the same time window as everyone else."""
    s = int(channel.get("subscriber_count") or 0)
    v = int(channel.get("video_count") or 0)
    a = _age_years(channel, now)

    # Season aggregates from the channels table (populated daily).
    s_long  = int(channel.get("season_long_videos")  or 0)
    s_short = int(channel.get("season_short_videos") or 0)
    s_live  = int(channel.get("season_live_videos")  or 0)
    s_total = s_long + s_short + s_live

    s_views    = (int(channel.get("season_long_views")  or 0)
                  + int(channel.get("season_short_views") or 0)
                  + int(channel.get("season_live_views")  or 0))
    s_likes    = int(channel.get("season_likes") or 0)
    s_comments = int(channel.get("season_comments") or 0)

    # Sample-size gates — small denominators give noisy ratios.
    has_season = s_total >= MIN_SEASON_VIDEOS_FOR_RATES
    has_share  = s_total >= MIN_SEASON_VIDEOS_FOR_SHARE
    has_engmt  = has_season and s_views > 0

    # Season VPV / VPS — the "what's working RIGHT NOW" axes.
    season_vpv = (s_views / s_total) if has_season else None
    season_vps = (s_views / s) if (has_season and s > 0) else None

    # Engagement rate (proportion, 0–1, typically 0.001–0.05).
    engagement = ((s_likes + s_comments) / s_views) if has_engmt else None

    # Pace change: current cadence ÷ lifetime cadence. >1 = ramping up.
    pace_change = None
    if a and s_total > 0:
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            season_start = datetime.fromisoformat(SEASON_START_ISO).replace(tzinfo=timezone.utc)
            season_days = max((now - season_start).days, 1)
            season_pace = (s_total / season_days) * 365  # videos/year at season pace
            lifetime_pace = v / a                          # videos/year over lifetime
            if lifetime_pace > 0:
                pace_change = season_pace / lifetime_pace
        except Exception:
            pass

    return {
        "vps":          season_vps,             # season views ÷ subs (current activity)
        "vpv":          season_vpv,             # season views ÷ season videos (current hit rate)
        "spv":          s / max(v, 1),          # subs ÷ videos (lifetime — subs are intrinsically lifetime)
        "spy":          (s / a) if a else None, # growth velocity (already age-normalized)
        "vpy":          (v / a) if a else None, # cadence (already age-normalized)
        "shorts_share": (s_short / s_total) if has_share else None,
        "engagement":   engagement,
        "pace_change":  pace_change,
    }


def _log10_safe(x):
    if x is None or x <= 0:
        return None
    return math.log10(x)


def _mad(values):
    """Median Absolute Deviation — robust spread measure. Returns 1.0 on
    degenerate input so we never divide by zero."""
    if not values:
        return 1.0
    m = statistics.median(values)
    md = statistics.median([abs(v - m) for v in values])
    return md or 1.0


def size_bucket(channel: dict) -> str:
    s = int(channel.get("subscriber_count") or 0)
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= s < hi:
            return name
    return "Tiny"


def _transform(axis: str, value):
    """Per-axis pre-scoring transform. Log10 for ratio axes, identity
    for proportion axes (shorts_share is already 0-1)."""
    if value is None:
        return None
    if axis in LOG_AXES:
        return _log10_safe(value)
    return value  # already in a comparable scale


def build_peer_reference(peer_channels: list[dict]) -> dict:
    """Given a peer set, compute (median, MAD) for each axis after the
    appropriate transform. Skips axes where < 3 peers have a valid value."""
    if len(peer_channels) < 3:
        return {}
    rs = [compute_ratios(c) for c in peer_channels]
    ref = {}
    for axis in ALL_AXES:
        vs = [_transform(axis, r[axis]) for r in rs]
        vs = [v for v in vs if v is not None]
        if len(vs) < 3:
            continue
        ref[axis] = (statistics.median(vs), _mad(vs))
    return ref


def z_scores(channel: dict, ref: dict) -> dict[str, float]:
    """z-score per axis using the matching transform + MAD scaling."""
    r = compute_ratios(channel)
    out = {}
    for axis, (med, m) in ref.items():
        x = _transform(axis, r[axis])
        if x is None:
            continue
        out[axis] = (x - med) / (1.4826 * m) if m else 0.0
    return out


def tags_from_z(zs: dict[str, float], threshold: float = Z_THRESHOLD) -> list[tuple[str, float, str]]:
    """Convert z-scores to [(axis, z, label), …] for every axis above threshold."""
    out = []
    for axis, z in zs.items():
        if abs(z) < threshold:
            continue
        pos, neg = TAG_VOCAB[axis]
        out.append((axis, z, pos if z > 0 else neg))
    out.sort(key=lambda t: -abs(t[1]))
    return out


def compute_channel_profile(channel: dict, all_clubs: list[dict]) -> dict:
    """Full profile for one channel against its league + size cohort.

    Returns:
      {
        "ratios":   {axis: value, …},
        "league":   {"name": "Serie A", "z": {axis: z, …}, "tags": [(axis, z, label), …]},
        "size":     {"bucket": "Small", "z": {…}, "tags": […]},
        "tag_count": <int — total flagged across both lenses>,
      }
    """
    lg = COUNTRY_TO_LEAGUE.get((channel.get("country") or "").upper())
    bucket = size_bucket(channel)

    league_peers = [c for c in all_clubs
                    if COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper()) == lg
                    and c.get("entity_type") == "Club"]
    size_peers = [c for c in all_clubs
                  if size_bucket(c) == bucket
                  and c.get("entity_type") == "Club"]

    league_ref = build_peer_reference(league_peers)
    size_ref = build_peer_reference(size_peers)

    league_z = z_scores(channel, league_ref)
    size_z = z_scores(channel, size_ref)
    league_tags = tags_from_z(league_z)
    size_tags = tags_from_z(size_z)

    return {
        "ratios": compute_ratios(channel),
        "league": {"name": lg, "z": league_z, "tags": league_tags},
        "size":   {"bucket": bucket, "z": size_z, "tags": size_tags},
        "tag_count": len(league_tags) + len(size_tags),
    }


def compute_all_profiles(all_channels: list[dict]) -> dict[str, dict]:
    """Bulk compute for every Club. Returns {channel_id: profile}."""
    clubs = [c for c in all_channels
             if c.get("entity_type") == "Club"
             and COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())]
    return {c["id"]: compute_channel_profile(c, all_channels) for c in clubs}


# ──────────────────────────────────────────────────────────────
# AI sentence — turn tags + ratios into one human-readable line
# ──────────────────────────────────────────────────────────────

PROFILE_SENTENCE_PROMPT = """\
You write one-sentence channel profiles for a YouTube football analytics site.

You will be given: a club name, its league, its size bucket, six structural
ratios (Views/Sub, Views/Video, Subs/Video, Subs/Year, Videos/Year, Shorts share),
the club's strongest z-score and which axis it fired on, and a list of
"tags" that triggered.

CRITICAL — make every sentence unique
- Pick the ONE most striking dimension and write about THAT, not a summary
  of all tags. The strongest_z field tells you which to anchor on.
- Anchor the sentence in a specific NUMBER from the data (the actual ratio
  value, the percentage, the year, the absolute count) — never abstract
  hand-waving like "audience outpacing content" or "engagement curve".
- VARY the structure. Don't always start with the club name. Don't end every
  sentence with a noun phrase. Do not use the words "outpacing", "lags",
  "engagement curve", "shorts momentum", or "shouting into the void".
- Mention the league or size bucket only when it sharpens the contrast
  (e.g. "for a Tiny club", "compared to Serie A peers"). Don't tack on
  context that doesn't add anything.

Tone
- Smart, dry, observational. Like a sports-section feature writer who
  reads the upload feed all day.
- 25 words max. Aim for 18.

Examples of the voice (different shapes):
- "Como's 419K subs on a 4-year-old channel is unusual — they ride
  promotion-era momentum the content output hasn't caught up with."
- "Newcastle posts roughly 130 videos a year, well below the Premier
  League norm, but each one clears 160K views on average."
- "Real Madrid's per-video yield sits at 470K views — three times the
  La Liga median, classic flagship economics."
- "Every Real Sociedad upload fights for daylight: 13K videos in the
  feed, but vps barely cracks 80."

If the club has no tags, return an empty string."""


def generate_profile_sentence(channel: dict, profile: dict, log=print) -> str | None:
    """Call Claude for a 1-sentence profile description. Returns None on
    failure or if no tags fired (nothing interesting to say)."""
    import os
    if not (profile["league"]["tags"] or profile["size"]["tags"]):
        return ""

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        log("[profile_sentence] ANTHROPIC_API_KEY missing")
        return None

    try:
        import anthropic
    except ImportError:
        log("[profile_sentence] anthropic package not installed")
        return None

    import json, time
    r = profile["ratios"]
    a = _age_years(channel)

    # Find the strongest single z-score across both lenses — that's
    # the angle the model should anchor the sentence on.
    strongest = None
    for lens_name in ("league", "size"):
        for axis, z, label in profile[lens_name]["tags"]:
            if strongest is None or abs(z) > abs(strongest["z"]):
                strongest = {"axis": axis, "axis_label": AXIS_LABEL[axis],
                             "tag": label, "z": round(z, 2),
                             "lens": lens_name}

    payload = {
        "name": channel.get("name", "?"),
        "league": profile["league"]["name"],
        "size_bucket": profile["size"]["bucket"],
        "subscriber_count": int(channel.get("subscriber_count") or 0),
        "video_count": int(channel.get("video_count") or 0),
        "total_views": int(channel.get("total_views") or 0),
        "channel_age_years": round(a, 1) if a else None,
        "ratios": {
            "views_per_sub": round(r.get("vps") or 0, 1),
            "views_per_video": int(r.get("vpv") or 0),
            "subs_per_video": round(r.get("spv") or 0, 1),
            "subs_per_year": int(r.get("spy") or 0),
            "videos_per_year": round(r.get("vpy") or 0, 1),
            "shorts_share_pct": round((r.get("shorts_share") or 0) * 100, 1)
                                if r.get("shorts_share") is not None else None,
        },
        "strongest_z": strongest,
        "tags_vs_league": [t[2] for t in profile["league"]["tags"]],
        "tags_vs_size_cohort": [t[2] for t in profile["size"]["tags"]],
    }

    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=120,
                temperature=0.75,  # higher than other AI notes — we want variety across 60+ clubs
                system=PROFILE_SENTENCE_PROMPT,
                messages=[{"role": "user",
                           "content": json.dumps(payload, ensure_ascii=False)}],
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            log(f"[profile_sentence] Claude API error: {e}")
            return None
        except Exception as e:
            log(f"[profile_sentence] error: {e}")
            return None
    else:
        return None

    text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    return text or None
