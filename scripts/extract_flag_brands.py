"""Pass-4: extract the sponsor brand for YouTube-FLAG-ONLY candidates.

The flag tells us a video is sponsored but never names the brand, and
these videos rarely use "presented by X" phrasing — instead the sponsor
sits in an @mention (@axa, @lidlgb), a possessive (Paulaner's), or a
"with X". Because the video is ALREADY confirmed sponsored, pulling the
named commercial entity is high-precision — we just have to exclude
the football channels (opponents) and people (players/coaches).

For each flag-only candidate (has_paid_flag=true, brand_canonical NULL)
we extract the first non-football @mention / possessive, then map it
through CANON (curated) to a clean brand. Unmapped tokens (people,
junk, untracked) are left NULL. Writes brand_canonical + sets
llm_confidence='flag+mention'.

Re-run after the scanner picks up new videos.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database


def log(m: str) -> None:
    print(m, flush=True)


_AT = re.compile(r'@([A-Za-z][A-Za-z0-9_.]{2,30})')
_POSS = re.compile(r"\b([A-Z][A-Za-z0-9&]{2,20})['’]s\b")

# token (lowercase, @stripped) -> canonical brand. Extend as new
# sponsors appear. Unmapped → left NULL (people / clubs / junk).
CANON = {
    "mcdonald": "McDonald's", "enterpriseuk": "Enterprise",
    "emirates": "Emirates", "fly_emirates": "Emirates", "emiratesfa": "Emirates",
    "lidlgb": "Lidl", "justeattakeaway": "Just Eat", "flixbusde": "FlixBus",
    "amazonmex": "Amazon", "aramco": "Aramco", "easportsfc": "EA Sports FC",
    "easports": "EA Sports", "ea_fifa": "EA Sports FC",
    "bankofamerica": "Bank of America", "hankook": "Hankook",
    "hyundai_kor": "Hyundai", "hyundai": "Hyundai", "play_efootball": "eFootball",
    "playstation": "PlayStation", "brinkhoff": "Brinkhoff's",
    "snapdragon": "Snapdragon", "marriottbonvoy": "Marriott Bonvoy",
    "etihad": "Etihad", "gillette": "Gillette", "jpperformance": "JP Performance",
    "trulyhardseltzer": "Truly", "att": "AT&T", "coinmerce": "Coinmerce",
    "powerade": "Powerade", "curacaotourism": "Curaçao Tourism",
    "coca": "Coca-Cola", "magentasport": "MagentaSport", "laufenn_global": "Laufenn",
    "bitburger": "Bitburger", "axa": "AXA", "pepsiglobal": "Pepsi",
    "niveamenuk": "Nivea Men", "paulaner": "Paulaner", "bmw": "BMW",
    "visa": "Visa", "adidas": "adidas", "nike": "Nike", "puma": "PUMA",
    "konami": "Konami", "heineken": "Heineken", "qatarairways": "Qatar Airways",
    "budweiser": "Budweiser", "castrol": "Castrol", "lufthansa": "Lufthansa",
    "telekom": "Deutsche Telekom", "rewe": "REWE", "aldi": "Aldi",
    "hisense": "Hisense", "tiktok": "TikTok", "spotify": "Spotify",
    "amazonprime": "Amazon Prime", "uber": "Uber", "ubereats": "Uber Eats",
    "deliveroo": "Deliveroo", "betway": "Betway", "stake": "Stake",
    "cazoo": "Cazoo", "standardchartered": "Standard Chartered",
    "socios": "Socios", "crypto": "Crypto.com", "binance": "Binance",
    "coinbase": "Coinbase", "redbull": "Red Bull", "monster": "Monster",
    "gatorade": "Gatorade", "mastercard": "Mastercard", "paypal": "PayPal",
    "westernunion": "Western Union", "sap": "SAP", "samsung": "Samsung",
    "oppo": "OPPO", "tcl": "TCL", "subway": "Subway", "kfc": "KFC",
    "expedia": "Expedia", "booking": "Booking.com", "airbnb": "Airbnb",
    "hellofresh": "HelloFresh",
}


def _football_handles(db) -> set:
    out = set()
    for c in db.get_all_channels():
        h = (c.get("handle") or "").lstrip("@").lower()
        if h:
            out.add(h)
    out |= {"arsenal", "chelseafc", "mancity", "liverpoolfc", "avfcofficial",
            "officialbhafc", "tottenhamhotspur", "wolves", "nufc", "lcfc",
            "westham", "everton", "fulhamfc", "realmadrid", "fcbarcelona",
            "acmilan", "inter", "juventus", "asroma", "fcbayern",
            "borussiadortmund", "bvb09", "rbleipzig", "psg"}
    return out


def _extract(title: str, desc: str, fb: set) -> str | None:
    t = title or ""
    blob = t + " \n " + (desc or "")[:250]
    for src in (t, blob):
        for m in _AT.findall(src):
            if m.lower() not in fb:
                return m
    for m in _POSS.findall(t):
        if m.lower() not in fb and m.lower() not in ("today", "match", "team"):
            return m
    return None


def main() -> int:
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (sb_url and sb_key):
        log("FATAL: missing SUPABASE_URL / SUPABASE_(SERVICE_)KEY")
        return 1
    db = Database(sb_url, sb_key)
    fb = _football_handles(db)

    rows = []
    off = 0
    while True:
        r = (db.client.table("branded_content_candidates").select("video_id")
             .eq("has_paid_flag", True).is_("brand_canonical", "null")
             .range(off, off + 999).execute().data) or []
        rows += r
        if len(r) < 1000:
            break
        off += 1000
    ids = [x["video_id"] for x in rows]
    log(f"{len(ids)} flag-only candidates without a brand")

    upd = []
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        vids = (db.client.table("videos")
                .select("id,title,description").in_("id", chunk)
                .execute().data) or []
        for v in vids:
            tok = _extract(v.get("title") or "", v.get("description") or "", fb)
            if not tok:
                continue
            can = CANON.get(tok.lstrip("@").lower())
            if not can:
                continue
            upd.append({"video_id": v["id"], "brand_canonical": can,
                        "brand_guess": "@" + tok, "is_branded": True,
                        "reviewed": True, "llm_confidence": "flag+mention"})

    for i in range(0, len(upd), 200):
        db.client.table("branded_content_candidates").upsert(
            upd[i:i + 200], on_conflict="video_id").execute()
    log(f"Wrote brand for {len(upd)} flag-only videos.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
