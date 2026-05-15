# Backlog — long-term issues parked for later

Things we know we'll need to address but aren't urgent. Each entry has
a status and a sensible trigger so we can pick it up at the right time
(not too early, not the day it breaks).

---

## 🟡 Season rollover for the Top-5 leagues (Aug 2026)

**What:** Every Top-5-leagues page is anchored to the 2025-26 European
football season. The current-season cutoff is hardcoded as
`2025-08-01`. When 2026-27 starts (≈ early August 2026), every "Season"
view will keep showing the OLD season's videos unless we update the
constants — or, better, switch to a season-resolving helper.

**Where it lives (so we don't miss anything):**

1. `src/channels.py`
   - `LEAGUE_SEASON_START` — the dict of per-league start dates
   - `DEFAULT_SEASON_START = "2025-08-01"`
   - `get_season_since()` — already abstracts the lookup, just needs
     the dict bumped
2. `src/database.py:330` — `get_season_video_rows(since="2025-08-01")`
   default arg (every caller passes explicitly, so this is just
   defensive)
3. `src/ai_analysis.py:42,122` — prompt strings that name the season
   "2025/26"
4. `app.py:101` — sidebar title `Season (25/26)` + url_path
   `season-2526`
5. `views/3_Season_2526.py:33` — page title hard-coded
6. `views/3b_Season_Top_Videos.py` — sibling page, may have hard-coded
   strings too
7. `views/0_Home.py:410` — narrative copy mentions the season
8. `views/2_Clubs.py:1193,1407` — chart caption + cross-link copy
9. `views/6_AI_Analysis.py:239,245` — subheader "Season 25/26"
10. `views/14_Federations.py`, `views/15_Other_Clubs.py`,
    `views/17_Women.py`, `views/13_Players.py` — each have a footer
    caption "since 2025-08-01"

**Suggested approach when the time comes:**

- Make `get_season_since()` return the **upcoming** season once the
  new one has started: i.e. add a "season label" helper that returns
  `("2026-27", "26/27", "2026-08-01")` and reference it everywhere
  instead of hard-coding strings.
- Rename `views/3_Season_2526.py` → `views/3_Season_2627.py` (or
  drop the year from the filename: `views/3_Season.py` with a
  computed title) so we don't have to rename a file every August.
- Same for the sidebar entry — derive the label from
  `get_current_season_label()`.

**Trigger to act:** Around 1 July 2026 — gives us a month before
clubs start pre-season uploads to test the rollover end-to-end.

**Status:** Parked — not blocking anything until late summer 2026.

---

## Convention

When picking up a parked item:
1. Read this file top-to-bottom for context.
2. Move the item to a temporary "in progress" section at the top.
3. After landing the change, delete the entry (git history preserves
   the original notes).
