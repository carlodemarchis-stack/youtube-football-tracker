# Video description attributes — Phase 2 spec (PARKED, pending go/no-go on AI spend)

Status as of 2026-05-23. **Phase 1 is done & shipped. Phase 2 is fully
designed and validated by prototype, but NOT built** — parked while
deciding whether the one-time AI cost (~$50) is worth it. This doc
captures everything so it can be implemented later with no re-work.

---

## Phase 1 — DONE & shipped (commit ef26f84)

- `videos.description` (text) + `videos.description_meta` (jsonb) columns
  added (manual ALTER, applied).
- `_parse_video_item` carries `snippet.description`; `upsert_videos`
  writes it → **all cohorts capture description on every new/re-fetched
  video, free** (rides along in the existing `videos.list` call).
- `scripts/backfill_descriptions.py` backfilled the raw text:
  **65,273 rows** (top-5, this season). ~1,300 quota units, 6 min.
  ~16% empty (mostly shorts — expected).
- `description_meta` is created but **NULL** — that's Phase 2.

Key design principle: **raw stored once → derive attributes offline,
re-runnable, zero re-fetch.**

---

## Phase 2 — derive `description_meta` (DESIGNED, NOT BUILT)

### Scope (decided)
- **long + live only**, non-empty description, **top-5**, **this season**.
  Exact count measured: **33,665 videos**.
- Shorts excluded: ~35% empty, thin bodies — descriptions are a
  long/live feature.

### Two extraction layers
- **L1 — deterministic (regex/string), free, re-runnable anytime:**
  per-channel footer learning → `clean_body`; hashtags; links +
  branded-shortener detection; `internal_promo` flags
  (store/membership/app/season_tickets); social-platform count;
  timestamps; scoreline regex.
- **L3 — one Haiku call per video** (the only thing that costs money):
  everything semantic/multilingual below.

### Final attribute schema (`description_meta` JSON)
```json
{
  "clean_body": "…footer stripped…",
  "content_type": "match_highlights",   // EXACTLY ONE, see list
  "tags": ["womens","season_recap"],    // 0+ overlays, see list
  "competition": "Champions League", "opponent": "Bayern Munich",
  "scoreline": "4-3", "matchday": 37, "season": "2025/26",
  "players_named": ["Arda Güler","Harry Kane"],
  "sponsor_name": "BingX",              // third-party only, or null
  "internal_promo": ["store","membership"],  // club's OWN commerce
  "language": "en", "target_market": ["KR","JP"],  // FOREIGN only
  "hashtags": ["#FCBarcelona"], "has_timestamps": true,
  "one_line_summary": "…",
  "_v": 1, "_engine": "haiku-4.5+regex"
}
```

**content_type (14, pick ONE — the dominant FORMAT):**
`match_highlights, goal_compilation, press_conference, interview,
podcast, documentary, behind_the_scenes, training, news_announcement,
tribute_farewell, matchday_promo, branded_content, awards, other`
- `branded_content` = video built around a THIRD-PARTY brand activation
  (sponsor quiz/challenge/feature).
- `awards` = player-of-the-year / awards ceremonies.

**tags (8, 0+ — orthogonal overlays, NEVER content_type):**
`womens, youth_academy, classic_rewind, community_csr, cultural_greeting,
transfer, season_recap, esports`

### Post-processing (deterministic, baked in after the LLM call)
1. **Sponsor blocklist** — null `sponsor_name` if it's a league/own
   brand. Set:
   `{ea sports, ea sports fc, mcdonald's, mcdonalds, enilive, laliga,
   la liga, premier league, bundesliga, serie a, ligue 1}`
   plus null if it contains the club's own name or any of
   `{store, shop, tienda, premium, " tv", fan club, membership, socios}`.
2. **content_type guard** — if the model returns a value not in the 14
   formats, coerce to `other` and push the value into `tags` if it's a
   known facet. (In testing the expanded enum stopped leaks; guard is a
   safety net.)
3. **target_market** — drop the competition's home country
   (`{la liga:ES, premier league:GB, serie a:IT, bundesliga:DE,
   ligue 1:FR}`), uppercase the rest. (Stops "Thai clip → [TH, ES]".)
4. **matchday** — force null when `content_type == matchday_promo`
   (multi-match live shows hallucinate a number).
5. Optional (decided: skip for now) — "branded_content = COMMERCIAL
   only; charity/nonprofit partner → community_csr tag." Currently a
   charity anthem with a nonprofit partner may read as branded_content;
   harmless since community_csr tag carries the truth.

### The tuned prompt (validated, ready to paste)
Model `claude-haiku-4-5`, `temperature=0`, `max_tokens=600`. Single user
message: HEAD + SCHEMA + "\n\nTITLE: " + title + "\n\nDESC:\n" +
description[:1400].

HEAD = `You classify official football-club YouTube descriptions (any language).\n\n`

SCHEMA (verbatim):
```
Return ONLY a JSON object (no prose, no fence):
- content_type: EXACTLY ONE of ["match_highlights","goal_compilation","press_conference",
  "interview","podcast","documentary","behind_the_scenes","training","news_announcement",
  "tribute_farewell","matchday_promo","branded_content","awards","other"]
    branded_content = video built around a THIRD-PARTY brand activation (quiz/challenge/
      feature with a sponsor). awards = player-of-the-year / awards ceremonies.
- tags: array (0+) from ["womens","youth_academy","classic_rewind","community_csr",
  "cultural_greeting","transfer","season_recap","esports"]  (overlays, NOT formats —
   community_csr / cultural_greeting are ALWAYS tags, never content_type)
- competition, opponent, scoreline, season: string or null
- matchday: integer ONLY if an explicit round number is stated; else null
- players_named: array of strings
- sponsor_name: a THIRD-PARTY external brand featured in THIS video, or null.
  NOT the club's own store/membership/app, NOT a league title (EA SPORTS, McDonald's).
- internal_promo: array (0+) from ["store","membership","app","season_tickets"]
- language: ISO-639-1 string
- target_market: array of FOREIGN ISO country codes deliberately targeted; else []
- one_line_summary: string (<=15 words, English)
A contract signing/extension is news_announcement (+tag transfer), NOT tribute_farewell.
```
JSON parsing: strip a leading ```json fence if present, then json.loads.

---

## Cost analysis (the open decision)

Measured: avg output ~218 tok/call; real input ~785 tok/call (full
prompt). Haiku 4.5 pricing **assumed** $1/MTok in, $5/MTok out — VERIFY
before spend.

| Item | Cost |
|---|---|
| **One-time backfill** (33,665) | **~$63** (~$47 with prompt caching) |
| **Monthly incremental** (~2–4k new long+live) | **~$3–6/run** → ~$40–70/yr |

- One-time is **idempotent/resumable** (stamp `_v:1`, skip done) — never
  re-paid unless the prompt version is bumped deliberately.
- L1 re-derivation is **always free**; only L3 costs.
- Output dominates cost; caching helps input only.
- Cheaper lever if wanted: AI-tag only videos above a view threshold
  (skip low-view academy/reserve tail) → roughly halves volume.

### Decisions locked
1. Scope = long+live, top-5, this season. ✅
2. Going-forward cadence = **monthly batch** (not a daily cron). ✅
3. content_type enriched with `branded_content` + `awards` (Option B). ✅
4. Charity-vs-branded nuance = leave for now. ✅
5. **Go/no-go on the ~$50 one-time spend = STILL PENDING.** ← the blocker

---

## Implementation plan (when greenlit)

1. `src/description_meta.py`:
   - L1 fns (footer learner, clean_body, hashtags, links, internal_promo,
     timestamps, scoreline).
   - `extract_meta_ai(title, description)` → Haiku call + JSON parse.
   - post-processing (blocklist, guard, market scrub, matchday null).
   - `build_description_meta(video) -> dict` combining L1+L3, stamps `_v`.
   - Constants: CONTENT_TYPES, TAGS_OK, SPONSOR_BLOCKLIST, OWN_BRAND,
     COMP_COUNTRY (all in this doc).
2. `scripts/backfill_description_meta.py`:
   - long+live, top-5, this season; reads stored `description` (no
     YouTube re-fetch); writes `description_meta`.
   - `--dry-run`, `--limit`, `--since`; resumable (skip rows at `_v:1`).
   - modest concurrency + retry; uses ANTHROPIC_API_KEY.
3. Monthly: same script, run by hand or a monthly cron — only processes
   rows missing `description_meta` (i.e. new videos).
4. NOT in scope here (separate later features this DATA enables):
   commercialization-index page, content-mix charts, feeding clean_body
   into the existing AI vibe notes.

## Validation evidence (already done in prototype, temp scripts deleted)
- 197-sample cross-league read (DE/EN/ES/FR/IT, clubs + leagues):
  confirmed body+footer structure, footer per-channel constant, content
  taxonomy, multilingual + Asia-localization patterns.
- L3 extractor run across 8 languages: content_type + facets, match
  metadata, sponsor vs internal_promo all clean.
- Blocklist: EA SPORTS / McDonald's / own-brand scrubbed; BingX,
  Coca-Cola, CMA CGM, MG Motor, Swizzels, Aktion Mensch survived.
- Determinism @ temp=0: primary fields stable; rare facet-tag flip on
  ambiguous content → extract-once-and-store.
- branded_content/awards + guard: no leaks; purpose categories correctly
  stay as tags on a real format.
```
