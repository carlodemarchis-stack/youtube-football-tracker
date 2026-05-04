"""Save the X regional handles discovered during the Apr 2026 re-sweep
focused on Juventus, Liverpool, Man City — driven by the gap vs the
external benchmark in reference/social_benchmark_2026.md.

Writes to:
- channels.socials.x_regional (list of full URLs)
- follower_snapshots rows with platform = 'x_<lang>' and follower_count
"""
from __future__ import annotations
import os, sys, json
from datetime import datetime, timezone
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from src.database import Database  # noqa: E402

# (channel name pattern, [(handle, lang_code, follower_count), ...])
# NOTE: Juventus/Liverpool/Man City already saved on 2026-04-30. Chelsea on
# 2026-05-01. Only run entries here on subsequent invocations or
# follower_snapshots will duplicate.
DISCOVERIES_PRIOR_SAVED = {
    # 2026-05-02 fifth wave already saved — kept here as audit trail.
    # Re-running would duplicate follower_snapshot rows.
}

DISCOVERIES = {
    # 2026-05-03 sixth wave — the 5 LEAGUE channels themselves (Other
    # Social shows clubs + leagues; we'd skipped the league regionals).
    "LALIGA EA SPORTS": [
        ("LaLigaEN",   "en",  2_700_000),
        ("LaLigaArab", "ar",  2_100_000),
        ("LaLigaFRA",  "fr",    950_900),
        ("LaLigaJP",   "jp",    138_200),
        ("LaLigaID",   "id",    217_700),
        ("LaLigaTH",   "th",     16_100),
        ("LaLigaBRA",  "br",    271_500),
        ("LaLigaTR",   "tr",     32_400),
    ],
    "Premier League": [
        ("PLinArabic", "ar",   902_300),
        ("PLforIndia", "in",   157_800),
        ("PLinUSA",    "us",   328_800),
    ],
    "Bundesliga": [
        ("bundesliga_EN", "en", 1_500_000),
        ("bundesliga_JP", "jp",    67_100),
    ],
    "Serie A": [
        ("SerieA_EN", "en", 540_900),
        ("SerieA_AR", "ar", 523_000),
        ("SerieA_BR", "br",  36_500),
        ("SerieA_ES", "es", 114_400),
        ("SerieA_ID", "id",  38_000),
        ("SerieA_JP", "jp",  22_900),
    ],
    "Ligue 1 McDonald’s": [
        ("Ligue1_ENG",  "en", 1_200_000),
        ("Ligue1_ESP",  "es",   389_200),
        ("Ligue1_POR",  "pt",   177_600),
        ("Ligue1_Arab", "ar",   331_400),
    ],
}

# Older waves (saved 2026-05-02 fifth wave) — keep for audit trail only.
_OLDER = {
    # 2026-05-02 fifth wave (rest of Bundesliga + Serie A + Ligue 1 + La Liga)
    "1. FC Union Berlin": [
        ("fcunion_en", "en", 37_500),
        ("fcunion_es", "es", 24_700),
    ],
    "TSG Hoffenheim": [
        ("tsghoffenheimEN", "en", 42_500),
    ],
    "1. FSV Mainz 05": [
        ("mainz05en", "en", 37_200),
        ("mainz05_jp", "jp", 25_800),
        ("mainz05_kr", "kr",  1_489),
    ],
    "FC Augsburg": [
        ("FCA_World", "en", 46_800),
    ],
    "Werder Bremen": [
        ("werderbremen_EN", "en", 35_900),
        ("werderbremenES",  "es", 15_000),
    ],
    "Parma Calcio 1913": [
        ("ParmaCalcio_en", "en", 31_200),
        ("ParmaCalcioJPN", "jp",  3_773),
    ],
    "AS MONACO": [
        ("AS_Monaco_EN", "en", 114_700),
        ("AS_Monaco_ES", "es", 108_900),
        ("AS_Monaco_BR", "br",  40_900),
        ("AS_Monaco_JP", "jp",  35_800),
    ],
    "OGC Nice": [
        ("ogcnice_eng", "en", 16_700),
    ],
    "Racing Club de Strasbourg Alsace": [
        ("RCSA_English", "en", 72_100),
        ("RCSA_Deutsch", "de",  2_941),
    ],
    "RCD Espanyol de Barcelona": [
        ("RCDEspanyol_EN", "en", 5_716),
    ],
    "Club Atlético Osasuna": [
        ("Osasuna_en",   "en", 10_700),
        ("Osasuna_eus",  "eus", 6_530),
        ("Osasuna_Arab", "ar",  4_018),
    ],
    "RC Celta": [
        ("CeltaEnglish", "en", 14_500),
        ("CeltaArab",    "ar",  9_192),
        ("CeltaJapan",   "jp",    323),
    ],
    "Girona FC": [
        ("GironaFC_ENGL", "en",  9_715),
        ("GironaFC_ES",   "es",  5_221),
        ("GironaFC_Arab", "ar", 20_400),
    ],
    "Levante UD": [
        ("LevanteUD_val", "val", 2_047),
    ],
    "Getafe C.F.": [
        ("getafecfen",   "en", 37_300),
        ("getafecfarab", "ar",  7_184),
    ],
}

# Main-handle corrections (this run only).
MAIN_HANDLE_FIXES = {
    "Ligue 1 McDonald’s": "https://x.com/Ligue1",  # was Ligue1UberEats
}

URL = lambda h: f"https://x.com/{h}"


def main():
    sb_url = os.environ["SUPABASE_URL"]
    sb_key = os.environ["SUPABASE_KEY"]
    db = Database(sb_url, sb_key)

    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date().isoformat()

    # Apply main-handle corrections first (clubs with wrong/regional X
    # in socials.x — fix the field, no snapshot row).
    for name_pat, new_url in (MAIN_HANDLE_FIXES if 'MAIN_HANDLE_FIXES' in globals() else {}).items():
        res = (db.client.table("channels")
               .select("id,name,socials")
               .ilike("name", f"%{name_pat}%")
               .execute())
        rows = [r for r in (res.data or [])
                if "women" not in r.get("name", "").lower()
                and "frauen" not in r.get("name", "").lower()]
        if not rows:
            continue
        rows.sort(key=lambda r: len(r["name"]))
        ch = rows[0]
        socials = ch.get("socials") or {}
        old = socials.get("x")
        if old == new_url:
            continue
        socials["x"] = new_url
        db.client.table("channels").update({"socials": socials}).eq("id", ch["id"]).execute()
        print(f"[fix] {ch['name']}: x main {old} -> {new_url}")

    for name_pat, accounts in DISCOVERIES.items():
        # Find the channel
        res = (db.client.table("channels")
               .select("id,name,socials")
               .ilike("name", f"%{name_pat}%")
               .execute())
        rows = [r for r in (res.data or [])
                if r.get("name", "").lower() not in ("liverpool fc women",)
                and "women" not in r.get("name", "").lower()
                and "academy" not in r.get("name", "").lower()
                and "u-" not in r.get("name", "").lower()]
        if not rows:
            print(f"[skip] no channel for '{name_pat}'")
            continue
        if len(rows) > 1:
            # pick the most-followed / shortest name as the senior club
            rows.sort(key=lambda r: len(r["name"]))
        ch = rows[0]
        cid = ch["id"]
        socials = ch.get("socials") or {}
        existing_regional = list(socials.get("x_regional") or [])
        new_urls = [URL(h) for (h, _, _) in accounts]
        merged = existing_regional + [u for u in new_urls if u not in existing_regional]
        socials["x_regional"] = merged

        upd = (db.client.table("channels")
               .update({"socials": socials})
               .eq("id", cid)
               .execute())
        print(f"[ok ] {ch['name']}: x_regional now has {len(merged)} url(s)")

        # Insert follower_snapshots rows for each new account
        for handle, lang, count in accounts:
            platform = f"x_{lang}"
            payload = {
                "channel_id": cid,
                "platform": platform,
                "follower_count": count,
                "captured_at": now,
                "source": f"x.com/{handle}",
                "notes": f"@{handle} (regional X account, re-sweep 2026-04-30)",
            }
            try:
                db.client.table("follower_snapshots").insert(payload).execute()
                print(f"        + snapshot {platform} @{handle} = {count:,}")
            except Exception as e:
                print(f"        ! snapshot fail {platform} @{handle}: {e}")


if __name__ == "__main__":
    main()
