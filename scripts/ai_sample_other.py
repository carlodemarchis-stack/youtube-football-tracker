#!/usr/bin/env python3
"""Sample 100 videos currently classified as 'Other' and ask Claude to
suggest categories. Helps us design new rules for the keyword classifier.

Env: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_KEY
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from src.database import Database

EXISTING = [
    "Highlights", "Full Match", "Full Match (Live)", "Live Stream",
    "Press Conference", "Interview", "Training",
    "Transfer & Signings", "Women's Football", "Academy & Youth",
    "Matchday", "Behind the Scenes", "Trailer & Promo", "Goals & Skills",
]

SYSTEM = f"""You classify football (soccer) YouTube video titles into themes.
Given a title, pick ONE category.

Prefer these existing categories when they fit:
{", ".join(EXISTING)}

If none fit well, invent a short new one (max 3 words). Don't be noisy — reuse
your own new categories across titles when the same concept recurs. Titles
come from top-flight club YouTube channels across Europe and the US."""

USER_TEMPLATE = """Classify each title. Reply with ONLY a JSON array where
each element is {{"i": index, "cat": "Category Name"}}. No prose.

Titles:
{body}"""


def main() -> int:
    sb_url = os.environ["SUPABASE_URL"].strip().strip('"').strip("'")
    sb_key = os.environ["SUPABASE_KEY"].strip().strip('"').strip("'")
    ak = os.environ["ANTHROPIC_API_KEY"].strip()

    db = Database(sb_url, sb_key)
    # Pull a bunch of 'Other' rows, sample 100 at random
    rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            db.client.table("videos")
            .select("id,title,duration_seconds,format")
            .eq("category", "Other")
            .range(offset, offset + 999)
            .execute()
            .data or []
        )
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
        if len(rows) > 10000:  # enough to sample from
            break

    print(f"Loaded {len(rows)} 'Other' rows — sampling 100")
    random.seed(42)
    sample = random.sample(rows, min(100, len(rows)))

    body = "\n".join(f'{i}. "{v["title"]}"' for i, v in enumerate(sample))
    client = anthropic.Anthropic(api_key=ak)
    msg = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": USER_TEMPLATE.format(body=body)}],
    )
    text = msg.content[0].text.strip()
    # Strip potential ```json fences
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")

    try:
        parsed = json.loads(text)
    except Exception as e:
        print("Parse failed:", e)
        print(text[:2000])
        return 1

    from collections import Counter
    cats = Counter(p["cat"] for p in parsed)
    print("\n── Distribution from Claude ──")
    for c, n in cats.most_common():
        print(f"  {n:>3}  {c}")

    print("\n── Sample per category ──")
    by_cat: dict[str, list[str]] = {}
    for p in parsed:
        title = sample[p["i"]]["title"]
        by_cat.setdefault(p["cat"], []).append(title)
    for c, titles in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"\n[{c}]  ({len(titles)})")
        for t in titles[:5]:
            print(f"  • {t[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
