-- Run this in Supabase SQL Editor
-- Adds channel_insights table for AI-generated analysis

CREATE TABLE IF NOT EXISTS channel_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id UUID REFERENCES channels(id) ON DELETE CASCADE UNIQUE,
    insights_json JSONB NOT NULL,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    model TEXT DEFAULT 'claude-haiku-4-5-20251001'
);
