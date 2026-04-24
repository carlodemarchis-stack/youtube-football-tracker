-- Add nullable date-of-birth column to channels (used by Players feature only).
-- Safe to re-run.
ALTER TABLE channels ADD COLUMN IF NOT EXISTS dob DATE;
