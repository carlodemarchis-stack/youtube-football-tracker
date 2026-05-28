# NFL section — v0 design doc

Living plan. Goal: ship a thin first surface for NFL data, with
architecture that grows into top-5-level complexity without rewriting.

---

## v0 scope (the only thing we build now)

- **33 channels in the DB**: the 32 franchises + the official NFL HQ
  channel (@NFL). All flat — no AFC/NFC grouping or division grouping
  in the UI.
- **Channel-level only**: subs, total views, total videos, last-fetched.
  **No video ingestion, no per-video snapshots, no per-video deltas.**
- **One hidden page** at URL `/nfl` (or similar), not in the sidebar
  nav. Reachable only by direct link.
- A daily channel-stats cron, isolated from top-5 / WC2026.

Total new files: ~4 (one-off import, daily cron, page, optional
filter stub). Total touched existing files: ~5–7 — each a one-line
addition to an entity_type exclusion tuple.

---

## Forward-compatibility hooks (we lay these now so v1/v2 doesn't hurt)

These cost almost nothing to do up-front but would be expensive to
retrofit later. Each is a deliberate architecture echo of how the
top-5 cohort or WC2026 already works.

### 1. Isolation key + cohort metadata
- New `entity_type = "NFL"` for all 33 channels.
- `competitions.nfl = {conference: "AFC|NFC|—", division: "North|East|South|West|—"}` stored on every channel even though the v0 page doesn't render it.
  - Reason: the data is already verified in `data/nfl_teams.json`, costs nothing to keep, and gives us a free door to:
    - Group/filter by conference or division later.
    - Build an NFL filter widget like `src/wc2026_filter.py` later.
    - Power per-conference / per-division leaderboards.

### 2. Exclusion lists, updated once
Add `"NFL"` everywhere these patterns appear:
- `daily_refresh.py`, `hourly_rss.py`, `weekly_refresh.py` — entity_type exclusion tuple.
- `dashboard_cache.py` cache builders — same.
- `src/filters.py` — top-5 cohort filter.
- Other isolated crons (daily_players / daily_other_clubs / daily_women_clubs / daily_wc2026) — make sure they each ignore NFL too.
- `src/usage.py` excluded admin/page sets if needed.

**One-line additions; trivial; do them all at v0 so spillover never happens.**

### 3. Cron file naming
- `scripts/daily_nfl.py` — channel snapshots (v0).
- Reserve names: `scripts/hourly_nfl.py` (future RSS for new videos), `scripts/weekly_refresh_nfl.py` (future — or just include NFL in the main weekly).
- Each new NFL cron mirrors the shape of the WC2026 equivalent so the operational mental model is identical.

### 4. Page file naming + numbering
Reserve the `20*_NFL_*.py` range:
- `views/20_NFL.py` — All Channels (v0).
- `views/20a_NFL_Daily_Recap.py` — future.
- `views/20b_NFL_Trends.py` — future.
- `views/20c_NFL_Latest.py` — future.
- `views/20d_NFL_Viral.py` — future.
- `views/20e_NFL_OneHits.py` — future.
- Mirrors the `18*_WC2026_*.py` numbering. Predictable.

### 5. Cache namespace
Future dashboard_cache rows will live under:
- `nfl` cache name + `scope_all()` for the All Channels payload (mirrors `wc2026` cache).
- `nfl_trends`, `nfl_one_hits`, etc. — mirrors the `wc2026_trends` / `season_*` patterns.

### 6. Filter stub
`src/nfl_filter.py` doesn't need to exist in v0, but when it does it should follow the exact shape of `src/wc2026_filter.py` (Conference → Team), so the page-rendering code at scoped views looks identical.

### 7. Hidden-page mechanism
Page **registered as a `st.Page` in `app.py`** but NOT added to any nav group dict. Routable by URL, invisible in the sidebar. When ready, drop it into a new "🏈 NFL" group dict in app.py — one line.

---

## Build order (v0)

| Step | File | Notes |
|---|---|---|
| 1 | `scripts/import_nfl.py` | One-off. Reads `data/nfl_teams.json` + fetches the @NFL channel id, inserts/upserts 33 rows into `channels` with `entity_type="NFL"` + `competitions.nfl = {…}`. Fetches initial subs/views/videos in the same pass. |
| 2 | Exclusion-list additions (5–7 small edits across existing files) | The safety net — protects all top-5 surfaces and crons. |
| 3 | `scripts/daily_nfl.py` | Daily channel snapshot. 33 API units/day. |
| 4 | `views/20_NFL.py` | Hidden page. Sortable table + small KPI row. |
| 5 | `app.py` | Register the page (no nav-group entry). |
| 6 | Schedule the cron on Railway | Same way as `daily_wc2026`. |

Runtime impact when shipped: **zero for any existing user** — page invisible, exclusion lists keep top-5 untouched.

---

## Future expansion path (the road from v0 → top-5 parity)

Each step a separate decision, with the previous step's data validating the next.

1. **v0 (now)** — channel-only, flat, hidden page. See above.
2. **v1** — surface the conference/division grouping in the page; expose to a new "🏈 NFL" sidebar group; still no videos.
3. **v2** — hourly RSS ingestion for new videos; populate `videos` table for NFL channels; first per-video page (NFL Latest).
4. **v3** — per-video snapshots for ≤30d videos (same `video_snapshots` mechanism, just include NFL channel ids); NFL Viral page.
5. **v4** — full-season cache (`nfl_one_hits`, `nfl_trends`); Viral / One-Hit Wonders / Daily Recap.
6. **v5** — `nfl_filter.py` (Conference → Team), Z2/Z3 sub-app scoping, parity with WC2026.

Each version takes weeks (mostly running data) before validating the next — same arc WC2026 took.

---

## Risks (plain-language)

| Risk | What it means | v0 mitigation |
|---|---|---|
| Spillover into top-5 surfaces | NFL teams appear in Daily Recap, Trends, Season leaderboards | Brand-new `entity_type = "NFL"` + explicit add to ~5 exclusion lists |
| Cron ingests video data we don't want yet | DB grows, weekly_refresh tries to fetch 1000s of NFL videos | v0 cron ONLY writes channel + snapshot. Never touches `videos` table. |
| YouTube API quota | A separate quota line item | 33 channels × daily = ~33 units. Vs 10k/key/day. Non-issue. |
| Page accidentally goes public | Someone sees a half-built surface | Not in nav. URL-only. Easy to lock down further (admin-only flag) if needed. |
| Hard-to-undo rollback | Locked into NFL forever | All ~5 file edits are one-liners. Delete the entity_type + the rows + the page + the cron — and you're back where you started. |
| Forward-compat over-engineering | Building infra that's never used | We're storing free metadata + reserving naming conventions, NOT building unused crons or pages. |

---

## Decisions log (fill as we go)

- *(empty — capture each design choice as it lands)*
