from __future__ import annotations

SERIE_A_CHANNELS = [
    {"name": "Juventus", "handle": "@juventus", "youtube_channel_id": "UCLzKhsxrExAC6yAdtZ-BOWw", "sport": "Football", "entity_type": "Club", "country": "Italy"},
    {"name": "AC Milan", "handle": "@ACMilan", "youtube_channel_id": "UCKcx1uK38H4AOkmfv4ywlrg", "sport": "Football", "entity_type": "Club", "country": "Italy"},
    {"name": "Inter Milan", "handle": "@inter", "youtube_channel_id": "UCvXzEblUa0cfny4HAJ_ZOWw", "sport": "Football", "entity_type": "Club", "country": "Italy"},
    {"name": "AS Roma", "handle": "@asroma", "youtube_channel_id": "UCLttSYJ6kPtlcurY96kXkQw", "sport": "Football", "entity_type": "Club", "country": "Italy"},
    {"name": "Serie A", "handle": "@seriea", "youtube_channel_id": "UCBJeMCIeLQos7wacox4hmLQ", "sport": "Football", "entity_type": "League", "country": "Italy"},
]

COUNTRY_TO_LEAGUE = {
    "IT": "Serie A",
    "GB": "Premier League",
    "EN": "Premier League",
    "ES": "La Liga",
    "DE": "Bundesliga",
    "FR": "Ligue 1",
    "US": "MLS",
    "Italy": "Serie A",
    "England": "Premier League",
    "Spain": "La Liga",
    "Germany": "Bundesliga",
    "France": "Ligue 1",
    "United States": "MLS",
}

LEAGUE_FLAG = {
    "Serie A": "\U0001F1EE\U0001F1F9",
    # England flag (subdivision tag) instead of UK flag — Premier League is
    # specifically English, and Welsh/Scottish/NI clubs play in their own
    # leagues. Some older fonts may render this as a generic black flag;
    # all modern OS/browser fonts (2023+) handle the GB-ENG tag sequence.
    "Premier League": "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F",
    "La Liga": "\U0001F1EA\U0001F1F8",
    "Bundesliga": "\U0001F1E9\U0001F1EA",
    "Ligue 1": "\U0001F1EB\U0001F1F7",
    "MLS": "\U0001F1FA\U0001F1F8",
}


# Brand colors for the 5 league channels. Used for per-league bar
# charts where each bar is one league (one value, no format split).
# Picked to match each league's official primary brand color while
# staying visually distinct on a dark background.
LEAGUE_COLOR = {
    "Serie A":        "#008FD7",  # Lega Serie A azure
    "Premier League": "#3D195B",  # PL purple
    "La Liga":        "#EE8707",  # LaLiga orange (current branding)
    "Bundesliga":     "#D3010C",  # DFL red
    "Ligue 1":        "#091B59",  # Ligue 1 navy
    "MLS":            "#001F47",  # MLS navy
}

# Brighter, chart-friendly variants of LEAGUE_COLOR. The brand-spec colors
# for Premier League (#3D195B) and Ligue 1 (#091B59) are too dark to read
# on the dark Streamlit theme — bars and lines disappear into the bg.
# Use these for charts only; keep LEAGUE_COLOR for chips/badges/anywhere
# the brand spec matters more than legibility.
LEAGUE_COLOR_CHART = {
    "Serie A":        "#008FD7",  # already bright enough
    "Premier League": "#9B6AC9",  # lighter PL purple
    "La Liga":        "#EE8707",  # already bright enough
    "Bundesliga":     "#D3010C",  # already bright enough
    "Ligue 1":        "#5C7AC9",  # lighter Ligue 1 navy
    "MLS":            "#5C82C9",  # lighter MLS navy
}


# ─── Season config — multi-season aware (data layer only) ───────────
#
# LEAGUE_SEASONS is the source of truth for "which leagues run a
# proper August → July season, and when each historical / current /
# future season starts and ends." Only the five European top-flight
# leagues qualify — MLS runs Feb → Dec on a calendar year so it
# doesn't fit this model and is deliberately absent from the dict.
# Pages that want to be "season-aware" check is_season_aware(league)
# first; MLS and anything not in this dict falls through to the legacy
# LEAGUE_SEASON_START flat-date behaviour below (used for video
# ingestion floors etc.).
#
# Each value is an ordered list of (season_label, start_iso, end_iso).
# Append a new tuple in August when the next season starts — every
# helper below picks the right one for the current calendar date.
#
# End dates set to 1 July (≈ post-Champions-League-final + 1 month
# of pre-season buffer). Adjust per-league if needed.
LEAGUE_SEASONS: dict[str, list[tuple[str, str, str]]] = {
    "Serie A": [
        ("25/26", "2025-08-01", "2026-07-01"),
        ("26/27", "2026-07-01", "2027-07-01"),
    ],
    "Premier League": [
        ("25/26", "2025-08-01", "2026-07-01"),
        ("26/27", "2026-07-01", "2027-07-01"),
    ],
    "La Liga": [
        ("25/26", "2025-08-01", "2026-07-01"),
        ("26/27", "2026-07-01", "2027-07-01"),
    ],
    "Bundesliga": [
        ("25/26", "2025-08-01", "2026-07-01"),
        ("26/27", "2026-07-01", "2027-07-01"),
    ],
    "Ligue 1": [
        ("25/26", "2025-08-01", "2026-07-01"),
        ("26/27", "2026-07-01", "2027-07-01"),
    ],
}

# Set form for fast membership tests (e.g. is_season_aware()).
SEASON_AWARE_LEAGUES: frozenset[str] = frozenset(LEAGUE_SEASONS.keys())


# ─── Legacy flat-date config (still used as an ingestion floor) ─────
#
# Kept for backward compat — daily_refresh / hourly_rss etc. read this
# via get_season_since() to know how far back to ingest videos. For
# the season-aware leagues it's derived from LEAGUE_SEASONS' current
# season so we have a single source of truth; MLS and any future
# additions keep their explicit date.
def _current_season_start(league: str) -> str | None:
    """Internal: today's active season start for a season-aware league."""
    seasons = LEAGUE_SEASONS.get(league)
    if not seasons:
        return None
    from datetime import date as _date
    today_iso = _date.today().isoformat()
    # Pick the season whose [start, end) bracket contains today; fall
    # through to the latest entry if today is past every defined end
    # (means we need to extend LEAGUE_SEASONS — backlog reminder).
    for _, start, end in seasons:
        if start <= today_iso < end:
            return start
    return seasons[-1][1]  # past all → use last end as a sentinel; caller will get fresh data


LEAGUE_SEASON_START: dict[str, str] = {
    **{lg: _current_season_start(lg) or "2025-08-01"
       for lg in LEAGUE_SEASONS},
    # Non-season-aware leagues: explicit, won't auto-roll.
    "MLS": "2025-02-01",
}

# Fallback for unknown leagues. Derived from Serie A's active season
# (every European top flight shares the same window) so it auto-rolls
# at the boundary — was hardcoded "2025-08-01" before. Falls back to
# the last defined start if today is past every defined season; that
# situation means LEAGUE_SEASONS needs extending.
DEFAULT_SEASON_START: str = (
    _current_season_start("Serie A") or "2025-08-01"
)


# ─── Public helpers ─────────────────────────────────────────────────


def is_season_aware(league: str) -> bool:
    """True for the five European top flights, False for MLS / unknown."""
    return league in SEASON_AWARE_LEAGUES


def list_seasons(league: str) -> list[str]:
    """All defined season labels for a league, oldest first. Empty for
    non-season-aware leagues."""
    return [s for s, _, _ in LEAGUE_SEASONS.get(league, [])]


def get_season_range(league: str, season: str) -> tuple[str, str] | None:
    """Return (start_iso, end_iso) for an explicit (league, season) pair,
    or None if either isn't known."""
    for s, start, end in LEAGUE_SEASONS.get(league, []):
        if s == season:
            return (start, end)
    return None


def get_current_season_label(league: str | None = None,
                              today_iso: str | None = None) -> str | None:
    """The active season label for `league` (e.g. '25/26', '26/27').

    If `league` is None, returns the first matching label across the
    season-aware leagues (they're all aligned on the same window in
    practice). Returns None if today falls outside every defined
    season — that's a signal that LEAGUE_SEASONS needs extending.
    """
    from datetime import date as _date
    today_iso = today_iso or _date.today().isoformat()
    candidates = ([league] if league else list(LEAGUE_SEASONS.keys()))
    for lg in candidates:
        for s, start, end in LEAGUE_SEASONS.get(lg, []):
            if start <= today_iso < end:
                return s
    return None


def current_season_label_safe() -> str:
    """Best-effort current-season label for UI strings — never None.

    Falls back to the latest defined label if today is past every
    configured season (means LEAGUE_SEASONS needs extending; the page
    still renders rather than showing 'None'). Use this in sidebar
    titles, page headings, captions etc. — anywhere we display the
    season to a human and can't show empty.
    """
    cur = get_current_season_label()
    if cur:
        return cur
    # Fallback: any season-aware league's last defined label.
    for lg in LEAGUE_SEASONS:
        seasons = list_seasons(lg)
        if seasons:
            return seasons[-1]
    return ""


def get_season_since(channel: dict | None = None, league: str | None = None,
                     season: str | None = None) -> str:
    """Return the start date for a season — explicit `season` if given,
    otherwise the current season for `league` / `channel`.

    Backward-compat: the original two-arg form (channel-only or
    league-only, no `season`) still returns the current-season start
    so every existing caller keeps working. New code that wants a
    historical season just passes it in:
        get_season_since(league="Serie A", season="25/26")

    Falls through to LEAGUE_SEASON_START (and DEFAULT_SEASON_START)
    for MLS / unknown leagues so video ingestion never breaks.
    """
    # Resolve league from channel if not given.
    if league is None and channel:
        country = (channel.get("country") or "").upper()
        league = COUNTRY_TO_LEAGUE.get(country)
    # Explicit season lookup for season-aware leagues.
    if league and season and league in LEAGUE_SEASONS:
        rng = get_season_range(league, season)
        if rng:
            return rng[0]
    # Current-season lookup (the original behaviour).
    if league and league in LEAGUE_SEASONS:
        cur = get_current_season_label(league)
        if cur:
            rng = get_season_range(league, cur)
            if rng:
                return rng[0]
    # Legacy / non-season-aware leagues.
    return LEAGUE_SEASON_START.get(league or "", DEFAULT_SEASON_START)


def league_with_flag(name: str) -> str:
    """Return league name prefixed with its country flag emoji."""
    flag = LEAGUE_FLAG.get(name, "")
    return f"{flag} {name}" if flag else name

# Official team colors (primary, secondary)
# Used as default when auto-assigning channel colors
TEAM_COLORS = {
    # Serie A — (primary, secondary)
    "Juventus": ("#000000", "#D4A843"),
    "AC Milan": ("#E3001B", "#000000"),
    "Inter Milan": ("#0068A8", "#000000"),
    "AS Roma": ("#C8102E", "#F5A623"),
    "SSC Napoli": ("#12A0D7", "#FFFFFF"),
    "SS Lazio": ("#87CEEB", "#FFFFFF"),
    "ACF Fiorentina": ("#6A1B9A", "#FFFFFF"),
    "Atalanta": ("#1E3A5F", "#000000"),
    "Torino FC": ("#8B1A2B", "#FFFFFF"),
    "Bologna FC 1909": ("#1A2D5A", "#C8102E"),
    "US Sassuolo": ("#00A651", "#000000"),
    "Udinese Calcio": ("#000000", "#F0F0F0"),
    "Hellas Verona FC": ("#FFDD00", "#003DA5"),
    "US Salernitana 1919": ("#8B0000", "#FFFFFF"),
    "US Lecce": ("#FFD700", "#C8102E"),
    "Empoli FC": ("#005BA6", "#FFFFFF"),
    "Cagliari Calcio": ("#C8102E", "#003DA5"),
    "Genoa CFC": ("#9E1B34", "#003DA5"),
    "Frosinone Calcio": ("#FFD100", "#003DA5"),
    "Monza": ("#CE1126", "#FFFFFF"),
    "Como Football": ("#003DA5", "#FFFFFF"),
    "Parma Calcio 1913": ("#FFDD00", "#003DA5"),
    "Venezia FC": ("#F47920", "#1E5631"),
    "US Cremonese": ("#CC0000", "#808080"),
    "Serie A": ("#008FD7", "#1D1D1B"),
    # Premier League
    "Manchester United": ("#DA291C", "#FFE500"),
    "Manchester City": ("#6CABDD", "#FFFFFF"),
    "Liverpool FC": ("#C8102E", "#FFFFFF"),
    "Chelsea FC": ("#034694", "#FFFFFF"),
    "Arsenal": ("#EF0107", "#FFFFFF"),
    "Tottenham Hotspur": ("#132257", "#FFFFFF"),
    "Newcastle United": ("#241F20", "#F0F0F0"),
    "Aston Villa": ("#670E36", "#95BFE5"),
    "West Ham United": ("#7A263A", "#1BB1E7"),
    "Brighton & Hove Albion": ("#0057B8", "#FFFFFF"),
    "Premier League": ("#3D195B", "#00FF87"),
    # La Liga
    "FC Barcelona": ("#A50044", "#004D98"),
    "Real Madrid": ("#FEBE10", "#FFFFFF"),
    "Atletico Madrid": ("#CB3524", "#FFFFFF"),
    "Real Sociedad": ("#1461AB", "#FFFFFF"),
    "Real Betis": ("#00954C", "#FFFFFF"),
    "Sevilla FC": ("#F43333", "#FFFFFF"),
    "La Liga": ("#FF4B44", "#2A2A2A"),
    # Bundesliga
    "Bayern Munich": ("#DC052D", "#FFFFFF"),
    "Borussia Dortmund": ("#FDE100", "#000000"),
    "RB Leipzig": ("#DD0741", "#FFFFFF"),
    "Bayer Leverkusen": ("#E32221", "#000000"),
    "Bundesliga": ("#D20515", "#FFFFFF"),
    # Ligue 1
    "Paris Saint-Germain": ("#004170", "#E30613"),
    "Olympique de Marseille": ("#2FAEE0", "#FFFFFF"),
    "Olympique Lyonnais": ("#1A3C8E", "#E30613"),
    "AS Monaco": ("#C8102E", "#FFFFFF"),
    "Ligue 1": ("#DDE524", "#091C3E"),
    # MLS
    "LA Galaxy": ("#00245D", "#FFD200"),
    "LAFC": ("#C39E6D", "#000000"),
    "Inter Miami": ("#F7B5CD", "#000000"),
    "Atlanta United": ("#80000A", "#A19060"),
    "MLS": ("#000000", "#FFFFFF"),
    # Governing bodies — logo-derived primary + secondary colors.
    # Used by channel_badge to render a dual-dot marker on Z3 / WC2026.
    "FIFA":     ("#326295", "#FFFFFF"),  # FIFA blue + white
    "UEFA":     ("#C8102E", "#003F87"),  # UEFA red + blue
    "AFC":      ("#F0A91A", "#005A36"),  # orange + green
    "CAF TV":   ("#006B3F", "#FCD116"),  # CAF green + pan-African yellow
    "Concacaf": ("#F26522", "#1E73BE"),  # CONCACAF orange + blue
    "CONMEBOL": ("#003F87", "#F4C300"),  # CONMEBOL blue + sun yellow
    "OFC":      ("#0073CF", "#FFFFFF"),  # OFC Pacific blue + white
}

SPORTS = ["Football", "Basketball", "Tennis", "Formula 1", "MotoGP", "Rugby", "Volleyball", "Cycling", "Other"]
ENTITY_TYPES = ["Club", "League", "Federation", "GoverningBody",
                "Player", "Media", "Other"]


def seed_channels(db):
    """Insert default channels if the DB is empty."""
    existing = db.get_all_channels()
    if existing:
        return
    for ch in SERIE_A_CHANNELS:
        db.add_channel(ch)
