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
# Scotland subdivision flag
_SCT_FLAG = "\U0001F3F4\U000E0067\U000E0062\U000E0073\U000E0063\U000E0074\U000E007F"

# Country name → ISO-3166 alpha-2 code (only the names we actually store
# in channels.country — feel free to extend). Lower-cased on lookup.
_NAME_TO_ISO = {
    "united states": "US", "usa": "US",
    "canada": "CA", "mexico": "MX", "haiti": "HT", "panama": "PA",
    "curaçao": "CW", "curacao": "CW",
    "argentina": "AR", "brazil": "BR", "uruguay": "UY",
    "ecuador": "EC", "colombia": "CO", "paraguay": "PY",
    "england": "ENG", "scotland": "SCT", "wales": "WLS",
    "france": "FR", "germany": "DE", "spain": "ES", "italy": "IT",
    "portugal": "PT", "netherlands": "NL", "belgium": "BE",
    "croatia": "HR", "switzerland": "CH", "norway": "NO",
    "austria": "AT", "czechia": "CZ", "czech republic": "CZ",
    "bosnia and herzegovina": "BA", "sweden": "SE",
    "türkiye": "TR", "turkey": "TR",
    "morocco": "MA", "egypt": "EG", "algeria": "DZ", "ghana": "GH",
    "ivory coast": "CI", "côte d'ivoire": "CI",
    "tunisia": "TN", "senegal": "SN", "south africa": "ZA",
    "dr congo": "CD", "cape verde": "CV",
    "iran": "IR", "iraq": "IQ", "saudi arabia": "SA",
    "jordan": "JO", "qatar": "QA", "uzbekistan": "UZ",
    "japan": "JP", "south korea": "KR", "korea": "KR",
    "australia": "AU", "new zealand": "NZ",
    "world": "",  # FIFA — no national flag
}


def _iso_to_flag(code: str) -> str:
    """Convert a 2-letter ISO-3166 alpha-2 country code to its flag emoji
    via the regional-indicator-symbol Unicode block. Returns "" for empty
    or invalid input. Special-cases the subdivision flags for England/
    Scotland (which are tag sequences, not regional indicators)."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    if code == "ENG":
        return _ENG_FLAG
    if code == "SCT":
        return _SCT_FLAG
    if len(code) != 2 or not code.isalpha():
        return ""
    # 🇦 = U+1F1E6 = ord('A') + 0x1F1A5
    return "".join(chr(0x1F1A5 + ord(c)) for c in code)


def flag_for_channel(channel: dict) -> str:
    """Resolve a channel's country to a flag emoji.

    Reads `channel.country` and accepts either a 2-letter ISO code
    ("DE", "BR", "EN") or a full country name ("Germany", "Brazil",
    "England") since the channels table holds a mix. Returns "" when
    no flag can be determined (e.g. the FIFA "World" channel).
    """
    raw = (channel or {}).get("country") or ""
    s = raw.strip()
    if not s:
        return ""
    # Try as-is first (covers ISO codes already)
    iso = _NAME_TO_ISO.get(s.lower())
    if iso is not None:
        return _iso_to_flag(iso)
    # Already a 2-letter code that wasn't in the name map (e.g. "IT")
    return _iso_to_flag(s)


# Back-compat: the existing _COUNTRY_FLAG dict is preserved for callers
# (League rows in channel_badge below) — populated from the resolver
# but only for the ISO-code form to keep the original semantics intact.
_COUNTRY_FLAG = {
    "IT": "\U0001F1EE\U0001F1F9",  # 🇮🇹
    "EN": _ENG_FLAG,
    "GB": _ENG_FLAG,
    "ES": "\U0001F1EA\U0001F1F8",  # 🇪🇸
    "DE": "\U0001F1E9\U0001F1EA",  # 🇩🇪
    "FR": "\U0001F1EB\U0001F1F7",  # 🇫🇷
    "US": "\U0001F1FA\U0001F1F8",  # 🇺🇸
}


def flag_span(flag: str, size: int = 14) -> str:
    """Standard rendering wrapper for a flag emoji inside a table row.

    Same fixed-width box dimensions as dual_dot() so columns stay
    aligned regardless of which marker fills the cell. Critically does
    NOT force a small font-size — that was breaking the England /
    Scotland subdivision-tag sequences which need the table's natural
    font (14px+) to render. The flag emoji inherits the table cell's
    font-size and centers itself in the box.
    """
    return (
        f'<span style="display:inline-block;width:{size}px;'
        f"text-align:center;line-height:1\">{flag}</span>"
    )


def channel_badge(channel: dict, color_map: dict | None, dual_map: dict | None,
                  size: int = 14) -> str:
    """Return the right marker for a row in a channel table.

    - League channels (entity_type='League') get their country flag.
    - Federation channels (entity_type='Federation') get their country
      flag too (resolved via flag_for_channel — accepts both ISO codes
      and full country names).
    - Everything else (Club / Player / GoverningBody / OtherClub /
      WomenClub) gets the standard concentric two-color dot.

    Wraps the marker in a fixed-size box so column widths stay aligned
    whether the cell holds a flag or a dot.
    """
    et = (channel or {}).get("entity_type")
    if et in ("League", "Federation"):
        flag = flag_for_channel(channel) if et == "Federation" else \
               _COUNTRY_FLAG.get(((channel.get("country") or "")).upper())
        if flag:
            return flag_span(flag, size)
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
    # Center the inner dot via transform-translate (sub-pixel safe) — using
    # absolute top/left math at half-pixel offsets caused asymmetric
    # anti-aliasing where the inner dot looked off-center on some screens.
    valign = "vertical-align:middle;" if inline else ""
    return (
        f'<span style="display:inline-block;width:{size}px;height:{size}px;'
        f'border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);'
        f'position:relative;box-sizing:border-box;{valign}flex-shrink:0">'
        f'<span style="display:block;width:{inner}px;height:{inner}px;'
        f'border-radius:50%;background:{c2};position:absolute;'
        f'top:50%;left:50%;transform:translate(-50%,-50%)"></span>'
        f'</span>'
    )
