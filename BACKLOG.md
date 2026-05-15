# Backlog — long-term issues parked for later

Things we know we'll need to address but aren't urgent. Each entry has
a status and a sensible trigger so we can pick it up at the right time
(not too early, not the day it breaks).

---

## 🔴 Promotion / relegation cycle (every July)

**What:** At the end of every European season, 3 teams from each of
the five top flights are relegated and 3 are promoted from the
division below. That means up to **15 NEW club YouTube channels per
season** to research + add, and up to **15 channels** that move out
of the Top-5 scope but whose history we want to keep.

**Open questions to answer before July 2026:**
- Do relegated clubs' channels stay tracked (so we can show "former
  Top-5 club" data) or are they marked inactive?
- New promoted teams: who decides the canonical YouTube channel?
  Manual research + admin-add seems unavoidable.
- How do we handle stats columns like `season_video_count` for a
  channel that played one season in the league and one outside? Do
  we keep both, only the most recent, or annotate per-season?
- Cron scripts (daily_refresh, hourly_rss) filter by league via
  `COUNTRY_TO_LEAGUE`. Need a per-channel "active leagues" flag or
  per-season membership table.

**Proposed model (sketch — flesh out before July):**
- New table `channel_league_seasons(channel_id, league, season, joined_at, left_at)`
  records league membership per season.
- "Currently in Top-5" check becomes: any row for `season=current`.
- Admin UI: Channel Management page gets a "promote/relegate" form
  that adds/closes a row for a chosen (channel, league, season).
- Pages keep their current filters but route through a helper
  `get_channels_in_league(league, season=current)` instead of
  `get_all_channels()` + python filter on `country`.

**Trigger to act:** Mid-May 2026 — promotion/relegation decided by end
of season (typically late May). Build the schema + admin form before
the first promoted club gets added in late June / July.

**Status:** Parked, recurring (every season).

---

## 🟢 Season rollover for the Top-5 leagues — DONE (May 2026)

Both the data layer AND the UI strings now route through dynamic
helpers — every "Season 25/26" / "since 2025-08-01" reference in the
app pulls from `LEAGUE_SEASONS` (or the helpers below) and switches to
26/27 automatically on **1 July 2026**.

**Data layer:**

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

**UI / display strings — also DONE:**

Every previously-hardcoded "25/26" / "2025-08-01" / "2025/26" string
now goes through one of the helpers above:

- `app.py:101` — sidebar entry uses `f"Season ({_csl()})"` and a
  season-neutral `url_path="season"`. File renamed
  `views/3_Season_2526.py` → `views/3_Season.py`.
- `views/3_Season.py:33` — title is `f"Season ({_csl()})"`.
- `views/0_Home.py` — narrative paragraph uses `.format()` with
  `_csl_home()`.
- `views/2_Clubs.py` — chart cutoff date uses
  `get_season_since(channel=...)`; the "See Season (xx/yy)"
  cross-link uses `_csl_clubs()`.
- `views/6_AI_Analysis.py` — subheader uses `_csl_ai()`.
- `views/13_Players.py / 14_Federations.py / 15_Other_Clubs.py /
  17_Women.py` — footer caption uses `get_season_since(league=...)`.
- `src/ai_analysis.py` — LLM prompts pull the label + start date from
  the helpers.
- `src/database.py` — 6 `since=...` default args switched to sentinel
  pattern (`None` → resolve to `DEFAULT_SEASON_START` at call time).
- `src/dashboard_cache.py` / `src/profile.py` — module-level
  `DURATION_SEASON_SINCE` / `SEASON_START_ISO` derived from
  `DEFAULT_SEASON_START`.

**Verified on 2026-05-15:** every consumer returns "25/26" /
"2025-08-01" today (no visible regression); simulated 2026-07-01
returns "26/27" / "2026-07-01" everywhere.

**Status:** Complete. No further action needed until ~July 2027 when
27/28 needs adding to `LEAGUE_SEASONS` — at that point just append
one tuple per league and everything updates.

---

## Convention

When picking up a parked item:
1. Read this file top-to-bottom for context.
2. Move the item to a temporary "in progress" section at the top.
3. After landing the change, delete the entry (git history preserves
   the original notes).
