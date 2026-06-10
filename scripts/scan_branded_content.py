"""Pass-1 detection of editorial/branded content (Tier 2): videos where
a sponsor is the *reason for / featured in* the video — "Penalty
Challenge by Audi", Pepsi UCL ceremony, EA Sports reaction vids —
as opposed to ambient shirt/stadium sponsorship that's on every clip.

This is HIGH PRECISION, not high recall — it seeds a candidate set we
clean over multiple passes. Two signal sources:

  1. YouTube's has_paid_promotion flag (creator-disclosed → confirmed).
  2. Explicit disclosure phrases in title/description, multilingual
     (EN/ES/IT/DE/FR/PT Latin phrases + KO/JA/AR keyword presence) —
     this is how we catch UNTAGGED branded content the flag misses.

Writes branded_content_candidates (video_id, signals[], brand_guess,
score, lang, has_paid_flag). reviewed/is_branded are for the cleaning
passes. Idempotent: re-running upserts; safe to re-run after editing
the patterns to refine the set.

Keyset-paginates the whole videos table (id ascending) so it never
hits the large-offset statement timeout.

Usage:
  python3 scripts/scan_branded_content.py
  python3 scripts/scan_branded_content.py --limit 5000   # cap for testing
"""
from __future__ import annotations

import argparse
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


# ── Multilingual disclosure patterns ─────────────────────────────
# Each entry: (signal_name, compiled_regex, score, captures_brand).
# Latin-script phrases capture the brand in group(1); CJK/Arabic
# keyword markers just flag presence (brand sits in non-Latin text we
# don't parse in pass 1).
_BRAND = r"([^\n.,;:|!?\(\)\[\]]{2,40})"

_PATTERNS = [
    ("presented_by", re.compile(
        r"(?:presented by|presentado por|presentada por|presentato da|"
        r"pr[aä]sentiert von|pr[ée]sent[ée]e? par|apresentado por)\s+"
        + _BRAND, re.I), 3, True),
    ("partnership", re.compile(
        r"(?:in partnership with|en partenariat avec|in collaborazione con|"
        r"in zusammenarbeit mit|em parceria com|en colaboraci[oó]n con|"
        r"em colabora[cç][aã]o com|in collaboration with)\s+"
        + _BRAND, re.I), 3, True),
    ("powered_by", re.compile(
        r"(?:powered by|propuls[ée] par|mit freundlicher unterst[uü]tzung von)"
        r"\s+" + _BRAND, re.I), 3, True),
    ("sponsored_by", re.compile(
        r"(?:sponsored by|patrocinad[oa] por|patrocinad[oa] pela?|"
        r"gesponser?t von|sponsoris[ée] par|sponsorizzato da)\s+"
        + _BRAND, re.I), 3, True),
    ("brought_by", re.compile(
        r"(?:brought to you by|gracias a nuestro patrocinador)\s+"
        + _BRAND, re.I), 3, True),
    ("promo_code", re.compile(
        r"(?:promo code|use code|c[oó]digo promocional|c[oó]digo|"
        r"gutscheincode|code promo|codice sconto)\s+([A-Z0-9]{3,18})",
        re.I), 2, True),
    # Non-Latin keyword presence (no brand capture in pass 1).
    ("cjk_provided", re.compile(
        r"(협찬|제공|提供|協賛|スポンサー)"), 2, False),
    ("ar_sponsor", re.compile(
        r"(برعاية|بالتعاون مع|برعايه)"), 2, False),
]

# Junk brand captures to discard (boilerplate that follows a phrase).
_BRAND_BLOCKLIST = re.compile(
    r"^(the|our|us|you|your|a|an|this|that|all|และ|el|la|los|le|les)\b",
    re.I)


def _scan(title: str, desc: str):
    """Return (signals, brand_guess, text_score) for one video."""
    blob = (title or "") + " \n " + (desc or "")
    signals = []
    brand = None
    score = 0
    for name, rx, pts, caps in _PATTERNS:
        m = rx.search(blob)
        if not m:
            continue
        signals.append(name)
        score += pts
        if caps and brand is None:
            g = (m.group(1) or "").strip(" -–—:·|")
            if g and not _BRAND_BLOCKLIST.match(g):
                brand = g[:60]
    return signals, brand, score


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    sb_url = os.environ.get("SUPABASE_URL", "").strip()
    sb_key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
              or os.environ.get("SUPABASE_KEY", "").strip())
    if not (sb_url and sb_key):
        log("FATAL: missing SUPABASE_URL / SUPABASE_(SERVICE_)KEY")
        return 1
    db = Database(sb_url, sb_key)

    PAGE = 1000
    last_id = "00000000-0000-0000-0000-000000000000"
    seen = 0
    candidates = 0
    sig_counts: Counter = Counter()
    while True:
        rows = (db.client.table("videos")
                .select("id,channel_id,title,description,language,"
                        "has_paid_promotion")
                .gt("id", last_id).order("id", desc=False)
                .limit(PAGE).execute().data) or []
        if not rows:
            break
        batch = []
        for r in rows:
            signals, brand, tscore = _scan(
                r.get("title") or "", r.get("description") or "")
            paid = bool(r.get("has_paid_promotion"))
            if not paid and tscore < 2:
                continue  # not a candidate
            score = tscore + (5 if paid else 0)
            if paid:
                signals = signals + ["youtube_flag"]
            for s in signals:
                sig_counts[s] += 1
            batch.append({
                "video_id": r["id"],
                "channel_id": r.get("channel_id"),
                "has_paid_flag": paid,
                "signals": signals,
                "brand_guess": brand,
                "score": score,
                "lang": r.get("language"),
            })
        if batch:
            db.client.table("branded_content_candidates").upsert(
                batch, on_conflict="video_id").execute()
            candidates += len(batch)
        seen += len(rows)
        last_id = rows[-1]["id"]
        if seen % 10000 == 0 or len(rows) < PAGE:
            log(f"  {seen:>7,} scanned · {candidates:>5} candidates")
        if args.limit and seen >= args.limit:
            log(f"  reached --limit {args.limit}")
            break

    log(f"\nDone — {seen:,} videos scanned, {candidates} candidates.")
    log("Signal breakdown:")
    for s, n in sig_counts.most_common():
        log(f"  {s:16s} {n:>6,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
