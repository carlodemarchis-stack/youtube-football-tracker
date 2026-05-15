# Backlog — long-term issues parked for later

Things we know we'll need to address but aren't urgent. Each entry has
a status and a sensible trigger so we can pick it up at the right time
(not too early, not the day it breaks).

---

## 🟡 Season rollover for the Top-5 leagues (Aug 2026)

**Data layer — DONE (May 2026):**

`src/channels.py` now owns multi-season data via:

- `LEAGUE_SEASONS: dict[str, list[(label, start_iso, end_iso)]]`
  — full window per (league, season). Contains 25/26 and 26/27 for
  every Top-5 European league. **MLS is intentionally absent** because
  its Feb-Dec calendar season doesn't match this model.
- `SEASON_AWARE_LEAGUES` — frozenset of leagues with proper seasons
- `get_current_season_label(league=None, today_iso=None)` — picks the
  active season for today's date
- `get_season_range(league, season)` → `(start, end)`
- `list_seasons(league)` → all labels, oldest first
- `is_season_aware(league)` → bool
- `get_season_since(channel|league, season=None)` — extended with a
  `season` kwarg. Two-arg call form unchanged so every existing
  caller keeps working. When 26/27 starts, callers automatically
  switch over with zero code change.
- `LEAGUE_SEASON_START` retained as a flat-date legacy dict; the
  five European entries are now DERIVED from `LEAGUE_SEASONS` so
  there's exactly one place to bump (just add a new tuple per
  league when 27/28 is needed).

To add a future season: append `("27/28", "2027-08-01", "2028-07-01")`
to each league's list in `LEAGUE_SEASONS`. That's it. The helpers,
`LEAGUE_SEASON_START`, and `get_season_since()` all derive from the
same source.

**UI / display strings — STILL PENDING:**

These still hard-code "25/26" or "2025-08-01" in user-visible copy
and won't auto-update when the new season starts:

1. `app.py:101` — sidebar title `Season (25/26)` + url_path
   `season-2526`
2. `views/3_Season_2526.py:33` — page title + filename itself
3. `views/3b_Season_Top_Videos.py` — sibling page, check for hard-codes
4. `views/0_Home.py:410` — narrative copy mentions the season
5. `views/2_Clubs.py:1193,1407` — chart caption + cross-link copy
6. `views/6_AI_Analysis.py:239,245` — subheader "Season 25/26"
7. `views/14_Federations.py:464`, `views/15_Other_Clubs.py:460`,
   `views/17_Women.py:460`, `views/13_Players.py:538` — footer
   captions "since 2025-08-01"
8. `src/ai_analysis.py:42,122` — LLM prompts that hard-code "2025/26"
9. `src/database.py:330` — `get_season_video_rows(since="2025-08-01")`
   default arg (defensive — every caller passes explicitly)

**Suggested rollover approach (when ~July 2026):**

- Walk the list above; replace literal "25/26" with
  `get_current_season_label()` and replace literal "2025-08-01" with
  `get_season_since(...)`.
- Rename `views/3_Season_2526.py` → `views/3_Season.py` so the
  filename stops carrying a year (the page title comes from the
  helper).
- Bump the sidebar `st.Page(title=...)` and `url_path` to a
  season-neutral form (e.g. `title="Season", url_path="season"`).

**Trigger to act:** Around 1 July 2026 — gives us a month before
clubs start pre-season uploads to test the rollover end-to-end.

**Status:** Foundation laid (data). UI rollover parked until ~July 2026.

---

## Convention

When picking up a parked item:
1. Read this file top-to-bottom for context.
2. Move the item to a temporary "in progress" section at the top.
3. After landing the change, delete the entry (git history preserves
   the original notes).
