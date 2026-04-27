-- Follower-count history across non-YouTube social platforms.
-- One row per (channel, platform, captured_at). New row each benchmark run.
-- Safe to re-run.
CREATE TABLE IF NOT EXISTS follower_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  follower_count BIGINT NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source TEXT DEFAULT 'manual',  -- 'manual' / 'chrome' / 'api' / etc.
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_followers_ch_platform_date
  ON follower_snapshots(channel_id, platform, captured_at DESC);
