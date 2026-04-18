-- v15: Precomputed season breakdown stats on channels table
-- Run in Supabase SQL Editor

ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_long_views     BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_short_views    BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_live_views     BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_long_videos    INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_short_videos   INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_live_videos    INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_long_dur_avg   INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_short_dur_avg  INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_likes          BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_comments       BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_long_likes     BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_short_likes    BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_live_likes     BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_long_comments  BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_short_comments BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_live_comments  BIGINT DEFAULT 0;
