-- v25: Top-5 season membership + app settings (season rollover 25/26 → 26/27).
--
-- Goal: make "which clubs are in the Top-5 cohort" a per-SEASON fact so we
-- can (a) roll promoted clubs in / relegated clubs out cleanly at the
-- season boundary, and (b) keep historic seasons reconstructable later.
--
-- SCOPE: Top-5 leagues ONLY for now (not WC2026 / NFL / F1 / Players /
-- Other / Women). Those keep their existing cohort logic untouched.
--
-- DESIGN (see also the rollover plan):
--   * season_channels is the SOURCE OF TRUTH for DISPLAY membership per
--     season. The live pages show clubs whose row matches the ACTIVE
--     season. league is stored per (season, club) so historic league
--     grouping no longer depends on the channel's current country.
--   * channels.is_active + entity_type remain the TRACKING switch — the
--     cron scripts keep snapshotting every Club channel regardless of
--     season membership, so a promoted club added now accrues history
--     before it's shown.
--   * app_settings.active_top5_season is an OPTIONAL override for the
--     active season; when unset the app falls back to the date-driven
--     current_season_label_safe() (flips 25/26 → 26/27 on 2026-07-01).
--     No public picker — admin/override only.
--
-- SAFETY INVARIANT: backfill season_channels so the 25/26 membership ==
-- exactly today's 96 clubs. Then switching the pages to read this table
-- is a no-op until the active label flips on 2026-07-01.

CREATE TABLE IF NOT EXISTS season_channels (
    season      text NOT NULL,                 -- e.g. '25/26', '26/27'
    channel_id  uuid NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    league      text NOT NULL,                 -- e.g. 'Premier League'
    PRIMARY KEY (season, channel_id)
);

CREATE INDEX IF NOT EXISTS idx_season_channels_season  ON season_channels (season);
CREATE INDEX IF NOT EXISTS idx_season_channels_channel ON season_channels (channel_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key         text PRIMARY KEY,
    value       text,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
