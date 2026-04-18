-- Migration v13: season_views on channels table
-- Precomputed sum of view_count for videos published since season start.
-- Updated by daily_refresh and hourly_rss so reads are instant.

ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_views BIGINT DEFAULT 0;
