-- Run this in Supabase SQL Editor to add Phase 2 columns/tables

-- Add categorization columns to channels
ALTER TABLE channels ADD COLUMN IF NOT EXISTS sport TEXT DEFAULT 'Football';
ALTER TABLE channels ADD COLUMN IF NOT EXISTS entity_type TEXT DEFAULT 'Club';
ALTER TABLE channels ADD COLUMN IF NOT EXISTS country TEXT DEFAULT '';
ALTER TABLE channels ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true;

-- User profiles table
CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT DEFAULT '',
    role TEXT DEFAULT 'viewer',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_login TIMESTAMPTZ DEFAULT NOW()
);
