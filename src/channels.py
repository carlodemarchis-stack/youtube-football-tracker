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
}

SPORTS = ["Football", "Basketball", "Tennis", "Formula 1", "MotoGP", "Rugby", "Volleyball", "Cycling", "Other"]
ENTITY_TYPES = ["Club", "League", "Federation", "Player", "Media", "Other"]


def seed_channels(db):
    """Insert default channels if the DB is empty."""
    existing = db.get_all_channels()
    if existing:
        return
    for ch in SERIE_A_CHANNELS:
        db.add_channel(ch)
