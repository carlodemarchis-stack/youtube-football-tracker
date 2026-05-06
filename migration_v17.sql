-- v17: Per-video language detection
-- Run in Supabase SQL Editor.
--
-- Adds two columns to `videos`:
--   language        ISO 639-1 lowercase (e.g. 'it', 'en', 'es')
--   language_source how we determined it ('youtube', 'detected', 'prior')
-- Source preference order, when filling:
--   1. YouTube API: snippet.defaultAudioLanguage > snippet.defaultLanguage
--      → language_source='youtube' (most reliable)
--   2. lingua-py title detection (with channel-country prior)
--      → language_source='detected'
--   3. Channel country fallback
--      → language_source='prior'

ALTER TABLE videos
    ADD COLUMN IF NOT EXISTS language        TEXT,
    ADD COLUMN IF NOT EXISTS language_source TEXT;

-- Index for fast per-language filtering / aggregations
CREATE INDEX IF NOT EXISTS idx_videos_language ON videos(language);
