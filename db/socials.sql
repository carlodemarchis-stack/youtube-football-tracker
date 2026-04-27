-- Adds a nullable JSONB blob to channels for social platform links.
-- Populated by scripts/backfill_socials.py from Wikidata. Safe to re-run.
ALTER TABLE channels ADD COLUMN IF NOT EXISTS socials JSONB DEFAULT '{}'::jsonb;
