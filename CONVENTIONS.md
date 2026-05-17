# CONVENTIONS — standardized UX/style templates

The single source of truth for how this app looks and behaves. Every
UI change is checked against this before it's done; when a new
convention emerges it's added here (don't let it drift). Exceptions are
allowed but must be deliberate and noted in the relevant PR/commit.

**These are defaults, not handcuffs.** A specific page may deviate when
the deviation serves that page's meaning — consistency must never
destroy what a page is trying to say. When unsure whether a difference
is intentional, **ask before standardizing it away**.

Status legend: ✅ enforced via shared code · 📝 documented, applied by hand

---

## 1. Visual identity & color  ✅ `src/theme.py`, `src/dot.py`

- **All colors come from `src/theme.py`** — no hex literals in new code
  (views or `src/`). A reskin = edit `theme.py` only.
- **Format is fixed everywhere**: Long = blue (`ACCENT`), Shorts =
  green (`POS`), Live = orange (`WARN`). Same three colors, same
  meaning, on every page — charts, tables, legends, KPI splits. Never
  reassigned.
- **Channel identity is consistent everywhere**: a channel is always
  shown with its own marker — **double-dot** (its two brand colors)
  for clubs, **flag** for leagues / federations / countries — and its
  own color stays the same across every chart / table / page (via
  `dot.py` + `theme.CHANNEL_PALETTE`). A club looks the same on every
  screen.
- **Metric cards** (the KPI bar) cycle a fixed accent set
  (`theme.KPI_PALETTE`) so each stat reads distinct — card styling
  only, not identity.
- Brand colors (club/league/country) are data, not theme — the one
  allowed exception to "no hex / theme-only".

---

## 2. Metric vocabulary & order  📝

- **Canonical order** wherever these appear together (KPI bar, chart
  rows, table column groups):
  **Views → Videos → Views/Video → Likes → Comments → Engagement**.
  A subset keeps the relative order (never Likes before Views).
- **Fixed labels** — same noun everywhere, no synonyms:
  `Views`, `Videos`, `Views/Video`, `Likes`, `Comments`,
  `Engagement Rate`. ("Avg Views/Video" is fine as a card label; the
  metric is still "Views/Video".)
- **Canonical metric emoji** (used as the KPI-label prefix, §4 — same
  emoji = same metric on every page):
  👁️ Views · 🎬 Videos · 🎯 Views/Video · ❤️ Likes · 💬 Comments ·
  ⚡ Engagement · 👥 Subscribers · 📺 Long/Shorts/Live · 🔥 Top/most ·
  📡 entity/channel count · ⏱️ age · 🚀 momentum/pace.
  **Views/Sub** reuses 🎯 (it's the same per-X ratio family as
  Views/Video — a deliberate grouping, not a new metric).
- **Domain pages outside the YouTube-metric vocabulary** (Digital
  Footprint web-perf, Quota Monitor API keys, Outliers profile axes)
  keep emoji-prefixed KPI labels but with **page-appropriate icons**
  (e.g. ⚡ LCP · 📖 Wiki · ⭐ rating · ♿ a11y · 🔍 SEO · ✅ best-practices
  · 🔑 API key) — a deliberate exemption from the metric map above, not
  drift. The §4 "lead with an emoji" rule still applies; only the
  symbol set differs because the metric isn't a YouTube metric.
- **Format split (Long / Shorts / Live)** — order and colors (§1) are
  always fixed. Presentation has **two sanctioned forms; pick per page
  by whether per-format sorting adds value**:
  - **3 separate columns** (Long-form / Shorts / Live), each sortable —
    when the page is a sortable stats table and ranking by a single
    format is useful (e.g. WC2026 All Channels).
  - **1 composite column** `L / S / Li`, non-sortable — when it's a
    compact at-a-glance breakdown, not a ranking axis (e.g. Trends
    movers, Home leaderboards).
  Never split the order or recolor; only the column layout varies.

---

## 3. Numbers, deltas & dates  📝

- **Numbers** via `analytics.fmt_num` (K/M/B) — e.g. `9.3B`, `145.4K`.
  Exception: Quota Monitor shows exact integers on purpose.
- **Deltas**: no leading `+` on positives (a gain is obvious); keep
  `-` on negatives; color `POS` / `NEG` / `MUTED` (zero).
  Exception: **Daily Recap keeps the leading `+`** — it's a pure
  deltas page (every number is a change, not a total), so the sign is
  signal, not noise. Deliberate; do not "fix".
- **Dates** via `analytics.fmt_date`. **No fuzzy relative time in
  titles/headers** ("18h ago", "yesterday →"). Relative time is OK
  only in a subtitle/caption (e.g. "updated 15m ago").
- **Percentages**: 2 decimals for rates (`2.29%`); pie share labels
  as the chart already renders them.

---

## 4. KPI bar  ✅ `analytics.kpi_row()`

- Always via `analytics.kpi_row()` — never hand-built cards.
- **KPI labels lead with the canonical metric emoji** (§2 map) —
  e.g. `👁️ Δ Channel Views`, `🎬 New videos`. This is the rule, not
  decoration; the same emoji always means the same metric. Plain
  (emoji-less) KPI labels are the thing to fix, not the reverse.
- Metrics in canonical order (§2); accents from `theme.KPI_PALETTE`
  automatically (don't pass custom hex).
- Card = `(label, value)` or `(label, value, subtitle)`; subtitle is
  the small grey context line (rank "#1/18", a qualifier, etc.).
- Sits directly under the page title/subtitle, before charts/tables.
- Values `fmt_num`-formatted (§3); rates as `x.xx%`.

---

## 5. Charts  ✅ donuts `src/charts.py` · 📝 bars/lines

- **Title alignment depends on chart type** (never an in-chart
  plotly/altair title — always a caption above the chart):
  - **Pie/donut → centered**, via `charts.chart_title()`.
  - **Bar / line / everything else → left-aligned**, via
    `st.caption("👁️ …")`.
- **Donuts/pies after a KPI bar**: via `src/charts.py` —
  `DONUT_HEIGHT` (260) + `DONUT_MARGIN` (10 all sides), transparent
  bg, theme font. Slice order/colors per §1–§2.
- **Bars/lines** (📝): transparent paper/plot bg, theme font,
  gridlines `theme.BORDER`, axis titles empty unless they add real
  info, modest top margin (~30, no tall title band), legends
  horizontal/top when present.
- Per-channel series via `CHANNEL_PALETTE` / `get_channel_colors`;
  format series via the fixed Long/Shorts/Live colors (§1).

---

## 6. Tables  📝 (pattern is the rule, not a specific module)

Three **distinct table families** — keep each internally consistent;
never force one into another.

**A. Stats tables — canonical reference: the core Top-5 pages'
table.** The real template is the **"All-time" table in
`views/2_Clubs.py`** (`.ac-wrap` / `.ac-table`; the page formerly
titled "All Channels"), mirrored by Season's `ch-season`. That *pattern* is the standard for any ranked metric
table — **not** `src/wc_table.py` (which is just the WC2026 variant
and should itself match this, not the other way round). Its hallmarks:
- **Horizontal-scroll wrapper** (`overflow-x:auto` + a table
  `min-width`) — the table scrolls; columns are never squashed or
  dropped to fit.
- **Two-row grouped header**: a top row of `colspan` metric groups,
  each underlined in its group color (Views `#58A6FF`, Videos
  `#FFCA3A`, Views/Video `#AB63FA` — these group colors are
  themselves part of the standard), then a detail row of per-column
  headers. Use the group row when columns cluster; skip it when flat.
- **Per-column sortable**: `<th data-col data-type>` + `<td data-val>`
  (raw number / lowercased string, *not* display text), click toggles
  ▲/▼ with an `.active` highlight, sensible default sort column.
- **Identity cell** = marker (flag or dual-dot, §1) + name; name
  **links to the channel** (`youtube_url` → `@handle` →
  `/channel/<id>`).
- **Composite `L / S / Li`** cells non-sortable; numeric columns
  sortable (§2). Theme colors, row-hover, transparent bg.
- **The pattern is the rule, not any one module.** A hand-rolled
  table that already matches the "All-time" look is compliant —
  do **not** rewrite it just to share code (that's the churn we're
  avoiding). When building a *new* stats table or substantially
  reworking one anyway, copy the core pattern (or a shared helper that
  reproduces it exactly). `wc_table.py` is reconciled toward this
  reference over time, opportunistically — never page meaning first.

**B. Video lists — a different template, not a stats table.**
Thumbnail / video-row layouts (Latest Videos, the feed mosaic,
top-video lists). Do **not** retrofit them onto the stats-table
pattern.

**The only standardized part is the left cell** = thumbnail + a
**3-line stack** (in this order):
  1. **Channel** — marker (flag / dual-dot, §1+§7) + channel name.
  2. **Title** — bold, links to the video; may carry an inline flag
     (player / country) after the text.
  3. **Context line — case-dependent**: e.g. format chip (`Shorts`)
     · category · duration · date. Varies per page; the only line
     whose content changes by context.

**Everything else stays as each page already has it.** Right-side
columns, which attributes are columns vs. inline, sortability, a
rank `#` — all page-specific; **do not standardize, reorder, sort, or
"upgrade" them**. A *curated ranking* (Season Top, All-Time Top,
No.1) keeps its fixed columns and inline attributes and is **not**
click-sortable — the order is the point. A Latest-Videos-style
*explorer* may be sortable; that's its own choice, not a rule to
spread.

Whole row is click-to-watch (overlay / new tab).

**C. Side-by-side (50/50) pairs.** Two related tables on one row
(e.g. "biggest view gains" + "most videos published"). A sanctioned
layout for paired comparison; each half still follows its own family
(usually A). Equal columns; stack only if side-by-side harms
readability.

---

## 7. Identity markers  ✅ `src/dot.py`

- **Clubs** → dual-dot (two brand colors) via `dual_dot` /
  `channel_badge`.
- **Leagues / Federations / Countries / WC2026 teams** → flag via
  `flag_for_channel` / `flag_span` (England & Scotland use the
  subdivision tag-flags, not the UK flag).
- **Governing bodies** with no national flag → dual-dot fallback.
- Always through `src/dot.py` — never inline an emoji/flag/dot.
  Fixed marker-box size so columns align whether flag or dot.
- Same entity → same marker & color on every page (§1).

---

## 8. Page scaffolding  📝

Consistent vertical structure for every content page:
1. **Title** — `st.title()`, plain name (dynamic where needed, e.g.
   `Season ({label})`).
2. **Subtitle line** — one caption: scope + freshness, pattern
   `"<what> · <scope> · updated <Xm> ago"`.
3. **Qualifier caption** (optional) — e.g. "Stats cover videos
   published on/after …".
4. **KPI bar** (§4).
5. **Charts** (§5) / **Tables** (§6).
6. **Section headers** within the page → `st.subheader()` with a
   leading emoji ("📈 Daily trends", "🎬 …"), used consistently.
- "Others are isolated" note shown where tangential entities could be
  mistaken for league data.

---

## 9. Microcopy  📝

- "**day**", not "night" (we sample daily; users think in days).
- Gains have no `+`; losses keep `-` (§3).
- No fuzzy relative time in titles/headers (§3).
- Sentence case for captions/labels; Title Case for page titles &
  section headers.
- Precise scope ("all clubs + league channels", "48 teams + FIFA +
  6 confederations") over vague ("everything").
- Plain language over jargon; explain a caveat once where it matters
  (YouTube rounding, season cutoff, etc.).

---

## 10. Isolation rule (data integrity)  ✅ HARD RULE

- **Players, Federations, Other Clubs, Women, WC2026** are isolated by
  design: excluded from every league/club view, leaderboard,
  aggregate, All-Time Top, Latest Videos, Season Top, Compare. They
  live on their own pages only.
- Aggregations over clubs/leagues must filter out
  `entity_type in (Player, Federation, GoverningBody, OtherClub,
  WomenClub)` and `competitions.wc2026`-tagged channels.
- Each tangential type has its own isolated daily cron; pausing or
  killing one must not affect the main pipeline.
- Isolation is **tag-based** in `src/filters.py` (`is_wc2026()` wired
  into `is_club()` / `get_channels_for_filter()`), so it holds even if
  a WC2026 channel isn't a Federation/GoverningBody. Some view files
  still isolate by `entity_type`-only inline (Latest, Top Videos,
  Daily Recap `_non_player_ids`) — safe today (all WC2026 are
  Federation/GoverningBody) but a pending cleanup to make fully
  tag-robust.
- **WC2026 collects videos** (lightweight: `videos` +
  `video_snapshots` + `video_daily_deltas`, rolling 30-day window,
  scoped to WC2026 channels, no top100/season precompute). Because of
  the isolation above these never reach core surfaces. The per-video
  time-series can only be built going forward, hence collection runs
  continuously from now.
- **Players / Other Clubs / Women / Federations crons deliberately do
  NOT write `video_snapshots` / `video_daily_deltas`** — those pages
  show only channel stats + `season_*` + last-upload, so the per-video
  history had zero consumers and grew unbounded. The `get_video_details`
  fetch + `videos`-freshness upsert + `refresh_top100_stats` are kept
  (season numbers read live `videos.view_count`). Not an oversight —
  don't "restore" the snapshot writes.
- **Hard rule, not a style default** — never relax without explicit
  instruction.

---

## How to use this doc

- Before finishing any UI/data-presentation change, diff it against
  §1–§10. If it deviates, the deviation must be deliberate and called
  out in the commit message.
- When a new cross-page convention emerges, add it here in the same
  commit (don't let it drift, like the donut titles did).
- "Defaults, not handcuffs" (see top): page meaning wins over blind
  consistency — when unsure, ask before standardizing a difference
  away.
