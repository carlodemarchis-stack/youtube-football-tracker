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
