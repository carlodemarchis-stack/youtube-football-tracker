-- v9: Add live_count for past live streams (UULV playlist)
ALTER TABLE channels ADD COLUMN IF NOT EXISTS live_count integer DEFAULT 0;
