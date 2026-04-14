-- Migration v7: Add channel launch date
ALTER TABLE channels ADD COLUMN IF NOT EXISTS launched_at TIMESTAMPTZ;
