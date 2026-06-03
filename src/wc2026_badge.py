"""Unified channel-badge resolver for every WC2026 surface.

Before this module the WC2026 pages each made their own choice for the
row-marker — some called src.dot.channel_badge directly (which fell
back to the channel row's `color`/`color2` columns, mostly empty), some
maintained their own TEAM_FLAG / _CONF_DUAL dicts inline. Output was
inconsistent: the All Channels page showed flags + dual-dots correctly,
Latest / Viral / Daily Recap / All-time Top showed plain accent dots.

This file is the single source of truth for what a WC2026 channel's
badge looks like.

Rules
  - GoverningBody (FIFA, UEFA, CONMEBOL, CONCACAF, CAF, AFC, OFC):
    a concentric dual-dot in the body's brand colours. UEFA renders
    outer red + inner blue, matching its official mark.
  - Team channels (everything else in the WC2026 set): the team's
    flag emoji — including England's GB-ENG subdivision tag sequence,
    which is U+1F3F4 + U+E0067 U+E0062 U+E0065 U+E006E U+E0067 + U+E007F
    rather than the UK flag, because England qualified as a separate
    federation.
  - Fallback (unrecognised team / governing body): plain accent dot.
"""
from __future__ import annotations

from src.dot import dual_dot, flag_span
from src import theme as _T


# Confederation + FIFA brand colours. Outer (c1) is the dominant brand
# colour, inner (c2) is the accent.
_CONF_DUAL: dict[str, tuple[str, str]] = {
    "FIFA":     ("#326295", "#FFFFFF"),   # FIFA blue + white
    "UEFA":     ("#C8102E", "#003F87"),   # outer red + inner blue (matches UEFA brand)
    "CONMEBOL": ("#003F87", "#F4C300"),   # blue + yellow
    "CONCACAF": ("#F26522", "#1E73BE"),   # orange + blue
    "CAF":      ("#006B3F", "#FCD116"),   # green + yellow
    "AFC":      ("#F0A91A", "#005A36"),   # gold + green
    "OFC":      ("#0073CF", "#FFFFFF"),   # Pacific blue + white
}

# Flat single-colour map (first of each pair) for chart palettes that
# expect a single colour per group.
CONF_COLOR: dict[str, str] = {k: v[0] for k, v in _CONF_DUAL.items()}


# Country/territory flag emoji per qualifying team. England and Scotland
# use the ISO 3166-2 subdivision tag sequence (the GB-ENG / GB-SCT
# encoding) so they render correctly with their own flags rather than
# the Union Jack.
TEAM_FLAG: dict[str, str] = {
    # CONCACAF
    "Mexico": "🇲🇽", "United States": "🇺🇸", "Canada": "🇨🇦",
    "Haiti": "🇭🇹", "Panama": "🇵🇦", "Curaçao": "🇨🇼",
    # CONMEBOL
    "Argentina": "🇦🇷", "Brazil": "🇧🇷", "Uruguay": "🇺🇾",
    "Ecuador": "🇪🇨", "Colombia": "🇨🇴", "Paraguay": "🇵🇾",
    # UEFA
    "England": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "France": "🇫🇷", "Germany": "🇩🇪", "Spain": "🇪🇸",
    "Portugal": "🇵🇹", "Netherlands": "🇳🇱", "Belgium": "🇧🇪",
    "Croatia": "🇭🇷", "Switzerland": "🇨🇭", "Norway": "🇳🇴",
    "Scotland": "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F",
    "Austria": "🇦🇹", "Czechia": "🇨🇿",
    "Bosnia and Herzegovina": "🇧🇦", "Sweden": "🇸🇪", "Türkiye": "🇹🇷",
    # CAF
    "Morocco": "🇲🇦", "Egypt": "🇪🇬", "Algeria": "🇩🇿",
    "Ghana": "🇬🇭", "Ivory Coast": "🇨🇮", "Tunisia": "🇹🇳",
    "Senegal": "🇸🇳", "South Africa": "🇿🇦",
    "DR Congo": "🇨🇩", "Cape Verde": "🇨🇻",
    # AFC
    "Iran": "🇮🇷", "Iraq": "🇮🇶", "Saudi Arabia": "🇸🇦",
    "Jordan": "🇯🇴", "Qatar": "🇶🇦", "Uzbekistan": "🇺🇿",
    "Japan": "🇯🇵", "South Korea": "🇰🇷",
    # OFC
    "Australia": "🇦🇺", "New Zealand": "🇳🇿",
}


def conf_marker(name: str, size: int = 14) -> str:
    """Dual-dot for a governing body, sized to match a table row."""
    c1, c2 = _CONF_DUAL.get(name, (_T.ACCENT, _T.WHITE))
    return dual_dot(c1, c2, size, inline=True)


def wc2026_badge(channel: dict, size: int = 14) -> str:
    """Return the standard WC2026 row marker for ``channel``.

    Resolves in three steps:
      1. If the channel is FIFA or a confederation governing body
         (entity_type == 'GoverningBody'), render its brand dual-dot
         using the channel's `name` field as the lookup key.
      2. Otherwise treat it as a team channel and look up its flag
         from TEAM_FLAG, keyed on `competitions.wc2026.team` (the
         canonical team name as stored at import time).
      3. Fallback: plain accent dot.
    """
    ch = channel or {}
    et = ch.get("entity_type")
    name = (ch.get("name") or "").strip()
    wc = (ch.get("competitions") or {}).get("wc2026") or {}
    team = (wc.get("team") or "").strip()

    if et == "GoverningBody":
        # The channel name IS the body name for FIFA / UEFA / CONMEBOL /
        # … as imported by scripts/import_wc2026.py. If we ever rename a
        # channel, also tolerate a confederation hint from `wc.confederation`.
        key = name if name in _CONF_DUAL else (wc.get("confederation") or "")
        if key in _CONF_DUAL:
            return conf_marker(key, size)

    flag = TEAM_FLAG.get(team)
    if flag:
        return flag_span(flag, size)

    return dual_dot(_T.ACCENT, _T.WHITE, size, inline=True)
