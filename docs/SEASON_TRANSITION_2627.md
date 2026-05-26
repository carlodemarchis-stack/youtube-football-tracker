# Season transition — 25/26 → 26/27

Target date for the cut-over: **2026-07-01**. Living doc — decisions get
recorded here as we make them.

---

## 1. What auto-rolls on July 1, no work needed

`LEAGUE_SEASONS` (`src/channels.py`) already has the `26/27` window
defined for all five leagues (start `2026-07-01`, end `2027-07-01`). On
July 1 these flip automatically:

- **Sidebar label** — "Season (25/26)" → "Season (26/27)"
  (via `current_season_label_safe()`).
- **`get_season_since()`** — returns `2026-07-01` instead of
  `2025-08-01`. Everything downstream that uses it follows.
- **`daily_refresh`** — only ingests videos published on/after
  `2026-07-01` from then on.
- **Per-page "season" filters** — Season page, Season Top, Season AI
  notes, Daily Recap "season totals", etc. all switch to 26/27 data.
- **Rolling-window pages** (30-Day Trends, Latest, Viral) — unaffected,
  they're season-agnostic by construction.
- **All-time / All-Time Top** — cumulative, unaffected.

## 2. Decisions to make

### 2a. Promoted clubs (add 3 per league, 15 total)

3 clubs promoted into each top-5 league for 26/27. We need to:

- **Roster source of truth** — final promotions confirmed by league:
  - Premier League: confirmed after Championship playoff final (late May).
  - Serie A: confirmed after Serie B playoff (late May / early June).
  - La Liga: confirmed after Segunda playoff (mid-June).
  - Bundesliga: 2 automatic + 1 playoff winner (late May).
  - Ligue 1: 2 automatic + 1 playoff winner (late May / early June).
- **Onboard before July 1**, so the cron picks them up from day one.
  Channel add via `views/8_Channel_Management.py` (`country` = the
  league's country → `COUNTRY_TO_LEAGUE` maps them in automatically).
- **Track from now or from July 1?** — *Decision needed.* See §3.

### 2b. Relegated clubs (remove 3 per league, 15 total)

The losing 3 in each league. Options for each:

| Option | Effect | Pro | Con |
|---|---|---|---|
| **A. Keep in top-5 cohort** | Stays in PL/Serie A pages | History intact | Wrong — they're not in the league anymore |
| **B. Move to "Other Clubs"** | Existing `entity_type = "OtherClub"` cohort | Honest about league status; still tracked | Disappears from Top 5 surfaces |
| **C. Mark inactive, stop snapshotting** | Channel row stays for history but no new ingestion | Saves quota | Pages need to handle "inactive" |
| **D. Delete the channel row** | Gone from everywhere | Cleanest DB | Loses all historical data |

**Recommended: B.** Reflects reality (relegated club is no longer in the
league) without losing data. Implementation = change `entity_type` from
`Club` to `OtherClub` and let existing routing do the rest.

### 2c. Historical-season access (do we expose 25/26 data after July 1?)

Once `get_season_since()` returns `2026-07-01`, the Season page and
friends won't show any 25/26 data. We have three options:

| Option | Effort | UX |
|---|---|---|
| **A. Drop 25/26 access** | Zero — it's the default | Old season gone |
| **B. Add a season picker** | Medium — wire `get_season_range(league, season)` into the Season page header | Users can browse past seasons |
| **C. Freeze a 25/26 read-only archive page** | Medium — snapshot the Season cache row, render as static | Less flexible than B, simpler than B |

**Recommended: B**, but ship it just before/after July 1 so we don't
delay the cut-over. Until then, *all* season pages default to current.

### 2d. Data retention / Supabase weight

Tables that grow with time:

| Table | Approx size today | Growth/season | Notes |
|---|---|---|---|
| `videos` | ~70k rows | +~70k/season | Cumulative — keep |
| `channel_snapshots` | ~6k rows (≈40d × 150 channels) | ~55k/year | Tiny; keep |
| `video_snapshots` | ~2M rows ↑ | ~2M/season | **The heavy one** |
| `video_daily_deltas` | ~2M rows ↑ | ~2M/season | **The heavy one** |

For 26/27 we keep ingesting `video_snapshots` / `video_daily_deltas` for
videos in their 30-day post-publish window. The 25/26 rows stop growing
naturally (their videos all aged past 30 days), but they don't auto-prune.

**Options to keep Supabase lean:**

| Option | Effort | Storage | Trade-off |
|---|---|---|---|
| **A. Do nothing** | Zero | +2M rows/year | Manageable for years before it bites |
| **B. Prune `video_*` older than 30 days post-publish** | Small — one SQL job after season end | Cuts ~95% of the snapshot rows | We lose per-video daily history beyond 30d — but those are already not displayed anywhere |
| **C. Move 25/26 snapshot rows to an `archive_*` table** | Medium | Same total size, but compresses out of main | Adds joining complexity to any historical UI |

**Recommended: B.** Daily-delta history beyond 30 days post-publish is
already unused (the snapshot window is 30 days). Pruning is safe and
gives a clean foundation for 26/27. Drop the SQL into a one-off script.

### 2e. WC2026 cohort interaction

The 26/27 season starts **July 1**. The World Cup runs **June 11 → July
19, 2026** — so for the first 2½ weeks of the new top-5 season, the
World Cup is still on. No code interaction (cohorts are isolated by
`entity_type` + `competitions.wc2026`), just worth noting for narrative.

## 3. Implementation plan (sequenced)

### Phase 0 — now (late May)
- [ ] Lock the decisions in §2 (one round of Qs to user).
- [ ] Save this doc — *done.*

### Phase 1 — promotions confirmed (rolling, end of May → mid-June)
- [ ] Identify final 3 promoted clubs per league as each playoff lands.
- [ ] Add their YouTube channels via Channel Management.
  - `country` = league's country (so `COUNTRY_TO_LEAGUE` picks them up
    automatically — no code change needed).
  - `entity_type` = `Club`.
- [ ] Decision (§3a-i): **start tracking now or wait for July 1?**
  - **Track now** (recommended): cleaner sub/view-count baseline + the
    same daily snapshot history their incumbent peers already have.
    The Season page won't show them until July 1 (their `published_at`
    is < `2026-07-01`).

### Phase 2 — relegations confirmed (same window)
- [ ] Identify final 3 relegated clubs per league.
- [ ] **Don't change anything yet.** Wait until July 1 to flip their
      `entity_type` so the 25/26 season pages remain accurate.

### Phase 3 — July 1 cut-over day
- [ ] Flip the 15 relegated clubs: `entity_type = "OtherClub"`.
  (One SQL UPDATE; they'll reappear on the Other Clubs page.)
- [ ] Verify all top-5 surfaces now show 26/27 channels only.
- [ ] (Optional) Add release-note entry.

### Phase 4 — after July 1 (weeks 1-2)
- [ ] Ship the **season picker** on the Season page (§2c-B).
  Reads the season list from `LEAGUE_SEASONS`, defaults to current,
  lets the user select 25/26 to browse past data.
- [ ] Write the **prune script** for `video_snapshots` /
      `video_daily_deltas` rows >30 days past their video's `published_at`
      (§2d-B). Run once as a manual one-off; keep the script in
      `scripts/` for future seasons.

### Phase 5 — Aug 2027 forward
- [ ] Append `27/28` to `LEAGUE_SEASONS` when the next season's dates
      are known. Same pattern repeats annually.

## 4. Risks / things to watch

- **Late-confirmed playoff winners** — La Liga / Ligue 1 / Bundesliga
  playoff finals can land mid-June. Don't pre-add channels for clubs
  that haven't actually won yet.
- **Channel rename / handle change** — relegated clubs sometimes rebrand
  their YouTube during the off-season. Our `channels` table keys on the
  `youtube_channel_id`, which is stable, so renames are absorbed
  automatically on the next refresh.
- **Cohort sub-aggregation** — once the relegated 15 move to `OtherClub`,
  league-level "total views" etc. on 26/27 pages will only sum the 26/27
  cohort. Historical Season pages (via the picker, when built) should
  still join on the per-row league at publish time, not the current
  `entity_type`. *Open detail to confirm at implementation time.*

## 5. Decisions log (fill in as we go)

- *(empty — decisions go here as the user confirms each §2 question)*
