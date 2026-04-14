-- Run this in Supabase SQL Editor
-- Adds video_catalog table for storing ALL videos (compact)

CREATE TABLE IF NOT EXISTS video_catalog (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    youtube_video_id TEXT UNIQUE NOT NULL,
    channel_id UUID REFERENCES channels(id) ON DELETE CASCADE,
    title TEXT,
    published_at TIMESTAMPTZ,
    duration_seconds INT DEFAULT 0,
    view_count BIGINT DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_catalog_channel_id ON video_catalog(channel_id);
CREATE INDEX IF NOT EXISTS idx_catalog_view_count ON video_catalog(view_count DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_published_at ON video_catalog(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_youtube_id ON video_catalog(youtube_video_id);
