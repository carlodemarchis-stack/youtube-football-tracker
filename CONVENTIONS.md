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
- **Dates** via `analytics.fmt_date`. **No fuzzy relative time in
  titles/headers** ("18h ago", "yesterday →"). Relative time is OK
  only in a subtitle/caption (e.g. "updated 15m ago").
- **Percentages**: 2 decimals for rates (`2.29%`); pie share labels
  as the chart already renders them.

---

## 4. KPI bar  ✅ `analytics.kpi_row()`

- Always via `analytics.kpi_row()` — never hand-built cards.
- Metrics in canonical order (§2); accents from `theme.KPI_PALETTE`
  automatically (don't pass custom hex).
- Card = `(label, value)` or `(label, value, subtitle)`; subtitle is
  the small grey context line (rank "#1/18", a qualifier, etc.).
- Sits directly under the page title/subtitle, before charts/tables.
- Values `fmt_num`-formatted (§3); rates as `x.xx%`.

---

## 5. Charts  ✅ donuts `src/charts.py` · 📝 bars/lines

- **Donuts/pies after a KPI bar**: via `src/charts.py` —
  `chart_title()` centered caption above (never an in-chart plotly
  title), `DONUT_HEIGHT` (260) + `DONUT_MARGIN` (10 all sides),
  transparent bg, theme font. Slice order/colors per §1–§2.
- **Bars/lines** (📝): transparent paper/plot bg, theme font,
  gridlines `theme.BORDER`, axis titles empty unless they add real
  info, modest top margin (~30, no tall title band), legends
  horizontal/top when present.
- One concept per chart; the label is a centered caption, not a
  plotly title — consistent with donuts.
- Per-channel series via `CHANNEL_PALETTE` / `get_channel_colors`;
  format series via the fixed Long/Shorts/Live colors (§1).

---

## 6. Tables  ✅ stats `src/wc_table.py` · 📝 others

Three **distinct table families** — keep each internally consistent;
never force one into another.

**A. Stats tables — canonical: the Top-5 "All Channels" style.**
The default for any ranked metric table (All Channels, WC2026,
Others, etc.):
- **Horizontally scrollable when wide** (the `.wc-wrap` wrapper) —
  never squash or drop columns to fit.
- **Sortable**: clickable headers with ▲/▼, `data-val` raw sort keys
  (number / lowercased string, *not* the display text),
  numeric-vs-string aware, a sensible default sort column.
- **Grouped / dual header row when needed** — e.g. a metric group
  ("Views/Video") spanning its Long/Shorts/Live sub-columns. Supported
  when the page's data warrants it; don't add it gratuitously.
- **Identity cell** = marker (flag or dual-dot, §1) + name, and the
  **name links to the channel** (`youtube_url` → `@handle` →
  `/channel/<id>`).
- **Composite `L / S / Li`** cells non-sortable; numeric metric
  columns sortable (§2).
- Theme colors only; header / hover / active styling from the shared
  component.
- Implemented via `src/wc_table.py`. Hand-rolled stats tables converge
  onto it **opportunistically** — only when already editing that page,
  never at the cost of that page's meaning (ask if unsure).

**B. Video lists — a different template, not a stats table.**
Thumbnail / video-row layouts (Latest Videos, the feed mosaic,
top-video lists). Keep their own consistent format (thumbnail, title,
channel marker, inline metrics, click-to-watch). Do **not** retrofit
them onto the stats-table component.

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
