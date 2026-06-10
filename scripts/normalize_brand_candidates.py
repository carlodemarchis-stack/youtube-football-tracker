"""Pass-2 cleaning of branded_content_candidates: normalize the raw
brand_guess captures into branded_content_candidates.brand_norm so the
same sponsor groups under one key.

Rule-based, no LLM (that's a later pass). Strips @handles, trailing
hashtags/emoji/pipes, and leading articles (der/die/the/el/la/…);
does NOT split camelCase (brands are often intentionally camelCased —
NordVPN, CommBank, eToro). Drops single-token junk captures (articles,
stopwords) to NULL. Idempotent: re-run after editing the rules.

Usage:
  python3 scripts/normalize_brand_candidates.py
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database


def log(m: str) -> None:
    print(m, flush=True)


_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF←-⇿⬀-⯿️‍⭐]+")
_JUNK = {"der", "die", "das", "the", "a", "an", "el", "la", "los", "las",
         "le", "les", "il", "lo", "gli", "o", "os", "fútbol", "futbol",
         "football", "our", "your", "this", "new", "all", "de", "du",
         "des", "da", "di", "i", "y", "e", "&", "l"}
_LEAD_ART = re.compile(
    r'^(der|die|das|the|el|la|los|las|le|les|il|lo|gli|i|o|os|a|an|l)\s+',
    re.I)


def normalize_brand(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().lstrip('@')
    s = re.split(r'\s*[#|•\n]', s, 1)[0]          # cut trailing noise
    s = _EMOJI.sub('', s).strip(" -–—:·.,'\"!?")
    s = _LEAD_ART.sub('', s).strip()               # drop leading article
    s = re.sub(r'\s+', ' ', s).strip()
    if not s or s.lower() in _JUNK or len(s) < 2:
        return None
    return s


def main() -> int:
    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (sb_url and sb_key):
        log("FATAL: missing SUPABASE_URL / SUPABASE_(SERVICE_)KEY")
        return 1
    db = Database(sb_url, sb_key)

    rows = []
    off = 0
    while True:
        r = (db.client.table("branded_content_candidates")
             .select("video_id,brand_guess")
             .range(off, off + 999).execute().data) or []
        rows += r
        if len(r) < 1000:
            break
        off += 1000
    log(f"Normalizing {len(rows)} candidates…")

    updates = []
    nulled = 0
    seen: Counter = Counter()
    for r in rows:
        bn = normalize_brand(r.get("brand_guess"))
        if bn is None:
            nulled += 1
        else:
            seen[bn.lower()] += 1
        updates.append({"video_id": r["video_id"], "brand_norm": bn})

    for i in range(0, len(updates), 200):
        db.client.table("branded_content_candidates").upsert(
            updates[i:i + 200], on_conflict="video_id").execute()

    log(f"Done — {len(updates)} rows, {nulled} brand_norm=NULL, "
        f"{len(seen)} distinct brands.")
    log("Top brands:")
    for b, n in seen.most_common(15):
        log(f"  {n:>4}  {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
