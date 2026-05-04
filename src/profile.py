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
    "vps": ("🌱 Small but loyal",       "💧 Disengaged subs"),
    "vpv": ("🚀 Punching above weight", "📉 High volume, low yield"),
    "spv": ("📺 Audience > output",     "💪 Output > audience"),
    "spy": ("🚀 Fast-growing",          "🐢 Stagnant growth"),
    "vpy": ("📡 Steady producer",       "🔇 Low cadence"),
}

AXIS_LABEL = {
    "vps": "Views / Subscriber",
    "vpv": "Views / Video",
    "spv": "Subs / Video",
    "spy": "Subs / Year",
    "vpy": "Videos / Year",
}

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
    """Five structural ratios for one channel. Values are None when not
    computable (missing launched_at for spy / vpy)."""
    s = int(channel.get("subscriber_count") or 0)
    v = int(channel.get("video_count") or 0)
    t = int(channel.get("total_views") or 0)
    a = _age_years(channel, now)
    return {
        "vps": t / max(s, 1),
        "vpv": t / max(v, 1),
        "spv": s / max(v, 1),
        "spy": (s / a) if a else None,
        "vpy": (v / a) if a else None,
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


def build_peer_reference(peer_channels: list[dict]) -> dict:
    """Given a peer set, compute (median, MAD) for log10 of each ratio.
    Skips axes where < 3 peers have a valid value (too noisy)."""
    if len(peer_channels) < 3:
        return {}
    rs = [compute_ratios(c) for c in peer_channels]
    ref = {}
    for axis in ("vps", "vpv", "spv", "spy", "vpy"):
        vs = [_log10_safe(r[axis]) for r in rs]
        vs = [v for v in vs if v is not None]
        if len(vs) < 3:
            continue
        ref[axis] = (statistics.median(vs), _mad(vs))
    return ref


def z_scores(channel: dict, ref: dict) -> dict[str, float]:
    """z-score per axis using log-transform + MAD scaling."""
    r = compute_ratios(channel)
    out = {}
    for axis, (med, m) in ref.items():
        x = _log10_safe(r[axis])
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
