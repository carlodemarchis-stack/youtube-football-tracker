"""One-shot: add Premier League clubs (country=EN)."""
from __future__ import annotations
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(__file__))

from src.database import Database
from src.youtube_api import YouTubeClient

CLUBS = [
    ("Arsenal FC", "arsenal"),
    ("Aston Villa FC", "avfcofficial"),
    ("AFC Bournemouth", "AFCBournemouth"),
    ("Brentford FC", "BrentfordFC"),
    ("Brighton & Hove Albion", "OfficialBHAFC"),
    ("Burnley FC", "burnleyofficial"),
    ("Chelsea FC", "chelseafc"),
    ("Crystal Palace FC", "OfficialCPFC"),
    ("Everton FC", "everton"),
    ("Fulham FC", "FulhamFC"),
    ("Leeds United", "LeedsUnitedOfficial"),
    ("Liverpool FC", "LiverpoolFC"),
    ("Manchester City", "mancity"),
    ("Manchester United", "manutd"),
    ("Newcastle United", "NUFC"),
    ("Nottingham Forest", "NottinghamForestFC"),
    ("Sunderland AFC", "sunderlandafc"),
    ("Tottenham Hotspur", "TottenhamHotspur"),
    ("West Ham United", "westhamunited"),
    ("Wolverhampton Wanderers", "wolves"),
]

def main():
    db = Database(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    yt = YouTubeClient(os.environ["YOUTUBE_API_KEY"])
    for name, handle in CLUBS:
        info = yt.resolve_handle(handle)
        if not info:
            print(f"[FAIL] {name} (@{handle}) — not found")
            continue
        existing = db.get_channel_by_youtube_id(info["youtube_channel_id"])
        if existing:
            print(f"[SKIP] {name} — already in DB ({info['youtube_channel_id']})")
            continue
        db.add_channel({
            "youtube_channel_id": info["youtube_channel_id"],
            "name": name,
            "handle": "@" + handle,
            "sport": "Football",
            "entity_type": "Club",
            "country": "EN",
            "is_active": True,
        })
        print(f"[ADD ] {name} ({info['youtube_channel_id']})")

if __name__ == "__main__":
    main()
