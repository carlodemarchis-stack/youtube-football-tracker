-- v16: Lifetime per-format view aggregates on channels table
-- Run in Supabase SQL Editor.
--
-- We already have lifetime per-format COUNTS (long_form_count,
-- shorts_count, live_count) but no per-format VIEWS at the channel
-- level. Adding 3 BIGINT columns so the Channels page can show a
-- Views split (Long / Shorts / Live) without per-page aggregation.
-- Filled by daily_refresh.py on each run (Supabase-only work, no
-- extra YouTube API calls).

ALTER TABLE channels ADD COLUMN IF NOT EXISTS lifetime_long_views  BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS lifetime_short_views BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS lifetime_live_views  BIGINT DEFAULT 0;
