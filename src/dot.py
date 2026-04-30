"""Concentric two-color "club dot" — consistent across every page.

Outer circle = primary color, inner concentric circle = secondary color.
A 1-px translucent border keeps the outer ring visible against any
background. Inner dot is mathematically centered (top/left = (size - inner)/2).

Two sizes:
- STANDARD (size=14, inner=7) — for tables, banners, leaderboards
- COMPACT  (size=10, inner=5) — for video cards / dense layouts

Use the helper instead of hand-rolling HTML so every page stays in sync.
"""
from __future__ import annotations


# EN/GB → England subdivision flag (Premier League is English)
_ENG_FLAG = "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"
_COUNTRY_FLAG = {
    "IT": "\U0001F1EE\U0001F1F9",  # 🇮🇹
    "EN": _ENG_FLAG,
    "GB": _ENG_FLAG,
    "ES": "\U0001F1EA\U0001F1F8",  # 🇪🇸
    "DE": "\U0001F1E9\U0001F1EA",  # 🇩🇪
    "FR": "\U0001F1EB\U0001F1F7",  # 🇫🇷
    "US": "\U0001F1FA\U0001F1F8",  # 🇺🇸
}


def channel_badge(channel: dict, color_map: dict | None, dual_map: dict | None,
                  size: int = 14) -> str:
    """Return the right marker for a row in a club/league table.

    - League channels (entity_type='League') get their country flag.
    - Everything else (Club + fallback) gets the standard concentric dot.

    Wraps the marker in a fixed-size box so column widths stay aligned
    whether the cell holds a flag or a dot.
    """
    if (channel or {}).get("entity_type") == "League":
        country = ((channel.get("country") or "")).upper()
        flag = _COUNTRY_FLAG.get(country)
        if flag:
            # Match the dot's box dimensions so league rows align
            return (
                f'<span style="display:inline-flex;align-items:center;'
                f'justify-content:center;width:{size}px;height:{size}px;'
                f'font-size:{max(size - 2, 10)}px;line-height:1">{flag}</span>'
            )
    name = (channel or {}).get("name", "")
    if dual_map and name in dual_map:
        c1, c2 = dual_map[name]
    elif color_map and name in color_map:
        c1, c2 = color_map[name], "#FFFFFF"
    else:
        c1, c2 = "#636EFA", "#FFFFFF"
    return dual_dot(c1, c2, size)


def dual_dot(c1: str, c2: str, size: int = 14, *, inline: bool = False) -> str:
    """Return the standardized concentric two-color dot HTML.

    Args:
        c1: Primary (outer) color hex like '#FF0000'.
        c2: Secondary (inner) color hex.
        size: Outer diameter in px. Inner is exactly size//2.
        inline: When True, adds vertical-align:middle so the dot sits on
            the text baseline (use in inline contexts; default for table
            cells where a containing flex/grid handles alignment).
    """
    inner = max(2, size // 2)
    offset = (size - inner) / 2
    # Inline-style 'border' makes the dot retain its outline on rows
    # with hover background changes.
    valign = "vertical-align:middle;" if inline else ""
    return (
        f'<span style="display:inline-block;width:{size}px;height:{size}px;'
        f'border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);'
        f'position:relative;{valign}flex-shrink:0">'
        f'<span style="display:block;width:{inner}px;height:{inner}px;'
        f'border-radius:50%;background:{c2};position:absolute;'
        f'top:{offset}px;left:{offset}px"></span>'
        f'</span>'
    )
