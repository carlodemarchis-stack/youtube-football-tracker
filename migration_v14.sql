-- v14: Precomputed top-100 stats on channels table for instant reads
-- Run in Supabase SQL Editor

ALTER TABLE channels ADD COLUMN IF NOT EXISTS top100_views        BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS top100_avg_age_days REAL DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS top100_avg_dur_s    INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS top100_long_pct     INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS top1_views          BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS top1_published_at   TIMESTAMPTZ;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS top1_dur_s          INT DEFAULT 0;

ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top100_views        BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top100_avg_age_days REAL DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top100_avg_dur_s    INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top100_long_pct     INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top1_views          BIGINT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top1_published_at   TIMESTAMPTZ;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_top1_dur_s          INT DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS season_video_count         INT DEFAULT 0;
