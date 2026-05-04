#!/usr/bin/env python3
"""Static audit for risky Supabase reads that could silently truncate at 1000 rows.

The bug we keep tripping over: `db.client.table('X').select(...).execute()`
returns at most 1000 rows. PostgreSQL filters down BEFORE the limit is
imposed, so a query that *should* return 2,500 rows happily returns 1,000
of them and silently undercounts.

This script walks every .py file in the repo and flags every chained
SELECT call that ends in `.execute()` without one of:
  • `_fetch_all(...)`         — uses our pagination helper
  • `.limit(N)`               — explicit cap, intentional
  • `.range(...)`             — manual pagination
  • `.single()` / `.maybe_single()` — at most 1 row
  • `head=True` / `count=`    — count-only request, no rows returned
  • `.eq("id", ...)`          — primary-key lookup (always 1 row)

Run from repo root:
    python3 scripts/audit_supabase_pagination.py

Exit code 0 = clean. Non-zero = at least one risky call found.

Wire into CI / pre-commit if you want a hard gate.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}
SKIP_FILES = {"audit_supabase_pagination.py"}  # don't audit ourselves

# Lines that mean "this query is safe."
SAFE_MARKERS = (
    "_fetch_all(",
    ".limit(",
    ".range(",
    ".single(",
    ".maybe_single(",
    "head=True",
    "count=",
    'on_conflict=',     # insert/upsert returning at most the rows we sent
)

# Tables we know stay well below 1000 rows by their nature. Reads against
# these are safe even without explicit pagination.
SAFE_TABLES = {
    "channels",          # ~150 rows total
    "user_profiles",     # admin-managed list, small
    "channel_insights",  # one row per channel, ~150
}

# Patterns that bound a query to "at most a few rows" by filter shape.
SAFE_FILTER_PATTERNS = (
    '.in_("id"',  '.in_(\'id\'',                    # bounded by id list
    '.in_("video_id"',  '.in_(\'video_id\'',
    '.ilike("name"',  '.ilike(\'name\'',            # name lookups, ≤10 hits
)

# Identify the start of a chain: `.table("name")` or `.from_("name")`.
TABLE_START = re.compile(r"\b(?:client|db\.client|self\.client)\.(?:table|from_)\(")


def is_select_chain(snippet: str) -> bool:
    """Returns True if the chain looks like a SELECT (vs insert/update/delete/upsert)."""
    return ".select(" in snippet


def is_safe(snippet: str) -> bool:
    """Returns True if the chain has any of the SAFE_MARKERS."""
    return any(m in snippet for m in SAFE_MARKERS)


def is_pk_lookup(snippet: str) -> bool:
    """Returns True if the chain narrows by a unique key (id, youtube_video_id, …)."""
    pk_filters = [
        '.eq("id"',  '.eq(\'id\'',
        '.eq("youtube_video_id"',  '.eq(\'youtube_video_id\'',
        '.eq("user_id"',  '.eq(\'user_id\'',
        '.eq("email"',  '.eq(\'email\'',
        '.eq("handle"',  '.eq(\'handle\'',
        '.eq("youtube_channel_id"',  '.eq(\'youtube_channel_id\'',
    ]
    return any(p in snippet for p in pk_filters)


def is_safe_table(snippet: str) -> bool:
    """Returns True if the chain reads from a table we know stays small."""
    for t in SAFE_TABLES:
        if f'.table("{t}")' in snippet or f".table('{t}')" in snippet:
            return True
        if f'.from_("{t}")' in snippet or f".from_('{t}')" in snippet:
            return True
    return False


def is_bounded_filter(snippet: str) -> bool:
    """Returns True if the filter shape bounds the result naturally."""
    return any(p in snippet for p in SAFE_FILTER_PATTERNS)


def find_chains(text: str) -> list[tuple[int, str]]:
    """Walk the file char-by-char, grouping every multi-line query
    chain that ends in `.execute()`. Returns [(start_line, snippet)]."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        m = TABLE_START.search(text, i)
        if not m:
            break
        start = m.start()
        # Find the matching closing of the .execute() — we walk forward
        # and capture the chain until we hit `.execute()` or a blank line
        # gap of 2+ newlines (chain ended without execute → not our concern).
        j = m.end()
        depth = 1  # we're inside the opening paren of .table(
        while j < n and depth > 0:
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        # Now j points after the closing of .table(...). Continue capturing
        # method chain until .execute() or blank-line or non-chain char.
        chain_end = j
        while chain_end < n:
            # Skip whitespace within the chain (allows method chains across lines)
            while chain_end < n and text[chain_end] in " \t\n\r":
                chain_end += 1
            if chain_end >= n or text[chain_end] != ".":
                break
            # Found a .method( — capture method name + matching parens
            chain_end += 1
            name_start = chain_end
            while chain_end < n and (text[chain_end].isalnum() or text[chain_end] == "_"):
                chain_end += 1
            method = text[name_start:chain_end]
            # Skip whitespace before paren
            while chain_end < n and text[chain_end] in " \t":
                chain_end += 1
            if chain_end < n and text[chain_end] == "(":
                p_depth = 1
                chain_end += 1
                while chain_end < n and p_depth > 0:
                    c2 = text[chain_end]
                    if c2 == "(":
                        p_depth += 1
                    elif c2 == ")":
                        p_depth -= 1
                    chain_end += 1
            if method == "execute":
                snippet = text[start:chain_end]
                line_no = text[:start].count("\n") + 1
                out.append((line_no, snippet))
                break
        i = chain_end
    return out


def audit_file(path: Path) -> list[tuple[int, str]]:
    """Returns list of (line_no, snippet) for risky reads in this file."""
    try:
        text = path.read_text()
    except Exception:
        return []
    risky = []
    for line_no, snippet in find_chains(text):
        if not is_select_chain(snippet):
            continue  # writes / upserts: no row cap concern
        if is_safe(snippet):
            continue
        if is_pk_lookup(snippet):
            continue
        if is_safe_table(snippet):
            continue
        if is_bounded_filter(snippet):
            continue
        risky.append((line_no, snippet.strip()))
    return risky


def main() -> int:
    risky_total: list[tuple[Path, int, str]] = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if not f.endswith(".py") or f in SKIP_FILES:
                continue
            p = Path(root) / f
            for line_no, snippet in audit_file(p):
                risky_total.append((p.relative_to(REPO), line_no, snippet))

    if not risky_total:
        print("✅ No unpaginated Supabase SELECTs found.")
        return 0

    print(f"⚠️  Found {len(risky_total)} potentially-risky read(s) "
          "(SELECT without _fetch_all / limit / range / count / PK filter):\n")
    for path, line_no, snippet in risky_total:
        # Trim the snippet to ~3 lines for readability
        first_lines = "\n".join(snippet.splitlines()[:5])
        if len(snippet.splitlines()) > 5:
            first_lines += "\n  …"
        print(f"  {path}:{line_no}")
        print("  " + first_lines.replace("\n", "\n  "))
        print()

    print("To fix each one, pick the right approach:")
    print("  • Use src.database._fetch_all(query)  for unbounded reads")
    print("  • Add .limit(N)                       for intentional caps")
    print("  • Add .eq('id', ...)                  for PK lookups")
    print("  • Add head=True, count='exact'        for count-only queries")
    return 1


if __name__ == "__main__":
    sys.exit(main())
