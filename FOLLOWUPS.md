# FOLLOWUPS

Deferred ideas surfaced during launch prep. Not blocking launch — revisit
after.

## Video description attributes (Phase 2) — PARKED on cost decision

Phase 1 shipped (raw `description` captured + backfilled, 65k rows; see
commit ef26f84). Phase 2 = derive `description_meta` (content_type,
sponsor vs internal_promo, match metadata, language, etc.) via a tuned
Haiku pass. **Fully designed & prototype-validated, NOT built** — parked
pending go/no-go on a **~$50 one-time** AI spend (~$5/mo recurring).
Full implementable spec + prompt + cost analysis:
**`DESCRIPTION_META_PHASE2.md`**.

## AI notes

- **Verify AI note numerical accuracy.** Add a sanity-check layer
  that re-validates the figures the model cites against the payload
  before persisting the note. Today's anti-BS guard only catches
  invented scores. Numbers like "+206.4M views" or "35% of total" go
  in untouched. Concrete idea: after `generate_*_vibe`, run a regex
  pass over the note to extract numeric claims (X%, X views, X
  uploads), match each against the payload's actual values, and
  reject if any are off by >5%. Same rejection path as the score
  guard.

## Data quality

- **90-day retention on video_snapshots + video_daily_deltas** —
  size-control. (Earlier convo: parked.)

- **Investigate weekly_refresh April 19 → May 3 silent breakage**
  + add a 7-day heartbeat alert. (Earlier convo: parked.)

- **DROP video_catalog table** — legacy, 89,987 rows, no readers.
  (Earlier convo: "leave it for now".)

- **CET captured_date policy** for the other daily_* crons
  (federations / players / other_clubs / women_clubs / wc2026). The
  top-5 cron now uses `intended_capture_date`; the satellite crons
  still UTC-bucket.

## Diagnostics / tooling

- **Extend resnap_channel_views.py + interpolate_frozen_runs.py to
  cover the WC2026 cohort.** Today the toolkit is top-5 only. Same
  shape (62 channels instead of 101). Surface via `--cohort wc2026`
  flag.
