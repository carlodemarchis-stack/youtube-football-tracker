-- Run this in your Supabase SQL Editor to create the tables

-- Channels table
CREATE TABLE IF NOT EXISTS channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    youtube_channel_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    handle TEXT,
    subscriber_count BIGINT DEFAULT 0,
    total_views BIGINT DEFAULT 0,
    video_count INT DEFAULT 0,
    last_fetched TIMESTAMPTZ
);

-- Videos table
CREATE TABLE IF NOT EXISTS videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    youtube_video_id TEXT UNIQUE NOT NULL,
    channel_id UUID REFERENCES channels(id) ON DELETE CASCADE,
    title TEXT,
    published_at TIMESTAMPTZ,
    view_count BIGINT DEFAULT 0,
    like_count BIGINT DEFAULT 0,
    comment_count BIGINT DEFAULT 0,
    duration_seconds INT DEFAULT 0,
    category TEXT DEFAULT 'Other',
    thumbnail_url TEXT,
    last_updated TIMESTAMPTZ DEFAULT NOW()
);

-- Fetch history table
CREATE TABLE IF NOT EXISTS fetch_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    channels_updated INT DEFAULT 0,
    videos_fetched INT DEFAULT 0,
    status TEXT DEFAULT 'success',
    error_message TEXT
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_videos_channel_id ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_view_count ON videos(view_count DESC);
CREATE INDEX IF NOT EXISTS idx_videos_published_at ON videos(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_youtube_id ON videos(youtube_video_id);
