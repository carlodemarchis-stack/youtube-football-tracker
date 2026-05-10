"""One-shot seeder for channels.website + channels.wikipedia_slug.

Run after migration_v20.sql. Populates ~50 clubs across the 5 leagues
where the website + wikipedia article are well-established. Skips any
channel whose website is already set (so it's safe to re-run).

Match key: youtube_channel_id (stable; resilient to handle / name
edits in the DB).

Usage:  python3 scripts/seed_channel_websites.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.database import Database


# Mapping is keyed by youtube_channel_id so a name/handle change
# doesn't break the seed. value = (website, wikipedia_slug).
SEED: dict[str, tuple[str, str]] = {
    # ── Serie A ──────────────────────────────────────────────────
    "UCLzKhsxrExAC6yAdtZ-BOWw": ("https://www.juventus.com/en/", "Juventus_FC"),
    "UCKcx1uK38H4AOkmfv4ywlrg": ("https://www.acmilan.com/en", "AC_Milan"),
    "UCvXzEblUa0cfny4HAJ_ZOWw": ("https://www.inter.it/en", "Inter_Milan"),
    "UCLttSYJ6kPtlcurY96kXkQw": ("https://www.asroma.com/en", "AS_Roma"),
    "UCBJeMCIeLQos7wacox4hmLQ": ("https://www.legaseriea.it/en", "Serie_A"),

    # The slate below is keyed by handle and resolved below — IDs
    # for the other ~95 channels aren't hard-coded here. Resolution
    # happens at runtime against the DB.
}

# Handle-keyed seed for the long tail. The seeder resolves these
# against the channels table at run time. handles are case-folded.
HANDLE_SEED: dict[str, tuple[str, str]] = {
    # ── Serie A (clubs not above) ────────────────────────────────
    "@officialsscnapoli": ("https://sscnapoli.it/en/", "SSC_Napoli"),
    "@sscnapoli": ("https://sscnapoli.it/en/", "SSC_Napoli"),
    "@officialsslazio": ("https://www.sslazio.it/en/", "SS_Lazio"),
    "@officialacffiorentina": ("https://www.acffiorentina.com/en", "ACF_Fiorentina"),
    "@atalantabc": ("https://www.atalanta.it/en/", "Atalanta_BC"),
    "@bolognafc1909": ("https://www.bolognafc.com/en/", "Bologna_FC_1909"),
    "@torinofc1906": ("https://www.torinofc.it/en/", "Torino_FC"),
    "@udinesecalcio1896": ("https://www.udinese.it/en/", "Udinese_Calcio"),
    "@hellasveronafc": ("https://www.hellasverona.it/en/", "Hellas_Verona_FC"),
    "@empolifc": ("https://www.empolifc.com/en/", "Empoli_F.C."),
    "@cagliaricalcio": ("https://www.cagliaricalcio.com/en/", "Cagliari_Calcio"),
    "@genoacfc1893": ("https://www.genoacfc.it/en", "Genoa_CFC"),
    "@genoacfc": ("https://www.genoacfc.it/en", "Genoa_CFC"),
    "@parmacalcio1913": ("https://www.parmacalcio1913.com/en/", "Parma_Calcio_1913"),
    "@uscremonese1903": ("https://www.uscremonese.it/en/", "U.S._Cremonese"),
    "@uslecceofficial": ("https://www.uslecce.it/en/", "U.S._Lecce"),
    "@comofootball": ("https://www.comofootball.com/en/", "Como_1907"),
    "@sassuoloofficial": ("https://www.sassuolocalcio.it/en/", "U.S._Sassuolo_Calcio"),
    # Pisa, Cesena, etc. left out — long tail to be filled via Admin UI.

    # ── Premier League (top + most of the rest) ─────────────────
    "@manchesterunited": ("https://www.manutd.com/", "Manchester_United_F.C."),
    "@manutd": ("https://www.manutd.com/", "Manchester_United_F.C."),
    "@mancity": ("https://www.mancity.com/", "Manchester_City_F.C."),
    "@mancityofficial": ("https://www.mancity.com/", "Manchester_City_F.C."),
    "@liverpoolfc": ("https://www.liverpoolfc.com/", "Liverpool_F.C."),
    "@chelseafc": ("https://www.chelseafc.com/en", "Chelsea_F.C."),
    "@arsenal": ("https://www.arsenal.com/", "Arsenal_F.C."),
    "@spursofficial": ("https://www.tottenhamhotspur.com/", "Tottenham_Hotspur_F.C."),
    "@tottenhamhotspur": ("https://www.tottenhamhotspur.com/", "Tottenham_Hotspur_F.C."),
    "@nufc": ("https://www.newcastleunited.com/", "Newcastle_United_F.C."),
    "@avfcofficial": ("https://www.avfc.co.uk/", "Aston_Villa_F.C."),
    "@astonvilla": ("https://www.avfc.co.uk/", "Aston_Villa_F.C."),
    "@westhamunited": ("https://www.whufc.com/", "West_Ham_United_F.C."),
    "@brighton": ("https://www.brightonandhovealbion.com/", "Brighton_%26_Hove_Albion_F.C."),
    "@bhafc": ("https://www.brightonandhovealbion.com/", "Brighton_%26_Hove_Albion_F.C."),
    "@brentfordfc": ("https://www.brentfordfc.com/", "Brentford_F.C."),
    "@everton": ("https://www.evertonfc.com/", "Everton_F.C."),
    "@evertonfc": ("https://www.evertonfc.com/", "Everton_F.C."),
    "@crystalpalace": ("https://www.cpfc.co.uk/", "Crystal_Palace_F.C."),
    "@cpfc": ("https://www.cpfc.co.uk/", "Crystal_Palace_F.C."),
    "@nottinghamforestfc": ("https://www.nottinghamforest.co.uk/", "Nottingham_Forest_F.C."),
    "@nffc": ("https://www.nottinghamforest.co.uk/", "Nottingham_Forest_F.C."),
    "@bournemouthfc": ("https://www.afcb.co.uk/", "A.F.C._Bournemouth"),
    "@afcbournemouth": ("https://www.afcb.co.uk/", "A.F.C._Bournemouth"),
    "@fulhamfc": ("https://www.fulhamfc.com/", "Fulham_F.C."),
    "@wolves": ("https://www.wolves.co.uk/", "Wolverhampton_Wanderers_F.C."),
    "@premierleague": ("https://www.premierleague.com/", "Premier_League"),

    # ── La Liga ─────────────────────────────────────────────────
    "@realmadrid": ("https://www.realmadrid.com/en", "Real_Madrid_CF"),
    "@fcbarcelona": ("https://www.fcbarcelona.com/en/", "FC_Barcelona"),
    "@atleticodemadrid": ("https://en.atleticodemadrid.com/", "Atlético_Madrid"),
    "@sevillafc": ("https://www.sevillafc.es/en", "Sevilla_FC"),
    "@realbetisbalompie": ("https://www.realbetisbalompie.es/en", "Real_Betis"),
    "@realbetis": ("https://www.realbetisbalompie.es/en", "Real_Betis"),
    "@valenciacf": ("https://www.valenciacf.com/en", "Valencia_CF"),
    "@athleticclub": ("https://www.athletic-club.eus/en", "Athletic_Bilbao"),
    "@realsociedad": ("https://www.realsociedad.eus/en", "Real_Sociedad"),
    "@villarrealcf": ("https://www.villarrealcf.es/en", "Villarreal_CF"),
    "@getafecf": ("https://www.getafecf.com/en", "Getafe_CF"),
    "@rcceltadevigo": ("https://rccelta.gal/en/", "Celta_Vigo"),
    "@rccelta": ("https://rccelta.gal/en/", "Celta_Vigo"),
    "@rcdmallorca": ("https://www.rcdmallorca.es/en/", "RCD_Mallorca"),
    "@laliga": ("https://www.laliga.com/en-GB", "La_Liga"),

    # ── Bundesliga ──────────────────────────────────────────────
    "@fcbayern": ("https://fcbayern.com/en", "FC_Bayern_Munich"),
    "@bvb": ("https://www.bvb.de/eng", "Borussia_Dortmund"),
    "@borussiadortmund": ("https://www.bvb.de/eng", "Borussia_Dortmund"),
    "@rbleipzig": ("https://rbleipzig.com/en/", "RB_Leipzig"),
    "@bayer04fussball": ("https://www.bayer04.de/en", "Bayer_04_Leverkusen"),
    "@bayer04": ("https://www.bayer04.de/en", "Bayer_04_Leverkusen"),
    "@eintrachtfrankfurt": ("https://www.eintracht.de/en/", "Eintracht_Frankfurt"),
    "@borussia": ("https://www.borussia.de/en/", "Borussia_Mönchengladbach"),
    "@vfbstuttgart": ("https://www.vfb.de/en/", "VfB_Stuttgart"),
    "@werderbremen": ("https://www.werder.de/en/", "SV_Werder_Bremen"),
    "@vflwolfsburg": ("https://www.vfl-wolfsburg.de/en/", "VfL_Wolfsburg"),
    "@scfreiburg": ("https://www.scfreiburg.com/en/", "SC_Freiburg"),
    "@1fcunionberlin": ("https://www.fc-union-berlin.de/en/", "1._FC_Union_Berlin"),
    "@unionberlin": ("https://www.fc-union-berlin.de/en/", "1._FC_Union_Berlin"),
    "@1fsvmainz05": ("https://www.mainz05.de/en/", "1._FSV_Mainz_05"),
    "@mainz05": ("https://www.mainz05.de/en/", "1._FSV_Mainz_05"),
    "@hoffenheim": ("https://www.tsg-hoffenheim.de/en/", "TSG_1899_Hoffenheim"),
    "@fcaugsburg": ("https://www.fcaugsburg.de/en/", "FC_Augsburg"),
    "@bundesliga": ("https://www.bundesliga.com/en", "Bundesliga"),

    # ── Ligue 1 ─────────────────────────────────────────────────
    "@psg": ("https://en.psg.fr/", "Paris_Saint-Germain_F.C."),
    "@parissg": ("https://en.psg.fr/", "Paris_Saint-Germain_F.C."),
    "@om": ("https://www.om.fr/en", "Olympique_de_Marseille"),
    "@olympiquedemarseille": ("https://www.om.fr/en", "Olympique_de_Marseille"),
    "@ol": ("https://www.ol.fr/en", "Olympique_Lyonnais"),
    "@olympiquelyonnais": ("https://www.ol.fr/en", "Olympique_Lyonnais"),
    "@asmonaco": ("https://www.asmonaco.com/en/", "AS_Monaco_FC"),
    "@losclive": ("https://www.losc.fr/en/", "Lille_OSC"),
    "@losc": ("https://www.losc.fr/en/", "Lille_OSC"),
    "@staderennaisfc": ("https://www.staderennais.com/en/", "Stade_Rennais_F.C."),
    "@ogcnice": ("https://www.ogcnice.com/en/", "OGC_Nice"),
    "@rclens": ("https://www.rclens.fr/en", "RC_Lens"),
    "@stadebrestois29": ("https://www.stadebrestois.com/en/", "Stade_Brestois_29"),
    "@strasbourg": ("https://www.rcstrasbourgalsace.fr/en/", "RC_Strasbourg_Alsace"),
    "@rcsa": ("https://www.rcstrasbourgalsace.fr/en/", "RC_Strasbourg_Alsace"),
    "@toulousefc": ("https://www.toulousefc.com/en/", "Toulouse_FC"),
    "@asseofficiel": ("https://www.asse.fr/en/", "AS_Saint-Étienne"),
    "@asse": ("https://www.asse.fr/en/", "AS_Saint-Étienne"),
    "@ligue1": ("https://www.ligue1.com/", "Ligue_1"),
}


def main() -> int:
    # Prefer the service-role key for writes — RLS (migration_v18)
    # blocks UPDATE for the anon key, which silently returns
    # `data: []` and looks like success.
    url = os.environ.get("SUPABASE_URL", "")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "")
           or os.environ.get("SUPABASE_KEY", ""))
    if not (url and key):
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY (or SUPABASE_KEY) "
              "not set", file=sys.stderr)
        return 1

    db = Database(url, key)
    chs = db.get_all_channels()

    # Build resolution: by youtube_channel_id first (highest trust),
    # then by handle (case-folded, with-or-without leading @).
    by_yt = {ch.get("youtube_channel_id"): ch for ch in chs if ch.get("youtube_channel_id")}
    by_handle: dict[str, dict] = {}
    for ch in chs:
        h = (ch.get("handle") or "").strip().lower()
        if not h:
            continue
        if not h.startswith("@"):
            h = "@" + h
        by_handle[h] = ch

    updates: list[tuple[dict, str, str, str]] = []

    # Pass 1: youtube_channel_id matches
    for yt_id, (website, wiki) in SEED.items():
        ch = by_yt.get(yt_id)
        if not ch:
            continue
        if ch.get("website") and ch.get("wikipedia_slug"):
            continue  # already filled
        updates.append((ch, website, wiki, "yt_id"))

    # Pass 2: handle matches (skip if already queued from pass 1)
    queued_ids = {u[0]["id"] for u in updates}
    for handle, (website, wiki) in HANDLE_SEED.items():
        ch = by_handle.get(handle)
        if not ch or ch["id"] in queued_ids:
            continue
        if ch.get("website") and ch.get("wikipedia_slug"):
            continue
        updates.append((ch, website, wiki, "handle"))
        queued_ids.add(ch["id"])

    if not updates:
        print("Nothing to update — every targeted channel already has both fields.")
        return 0

    print(f"Updating {len(updates)} channels…")
    ok = 0
    for ch, website, wiki, via in updates:
        try:
            db.update_channel(ch["id"], {"website": website,
                                          "wikipedia_slug": wiki})
            ok += 1
            print(f"  ✓ {ch['name']:<32} ({via})  →  {website}")
        except Exception as e:
            print(f"  ✗ {ch['name']:<32}  {e}")

    # Coverage report
    chs2 = db.get_all_channels()
    core = [c for c in chs2 if c.get("entity_type") not in
            ("Player", "Federation", "OtherClub", "WomenClub")]
    with_web = sum(1 for c in core if (c.get("website") or "").strip())
    print(f"\nCoverage: {with_web}/{len(core)} core channels have website set "
          f"({100*with_web//len(core)}%).")
    print(f"Successfully updated: {ok}/{len(updates)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
