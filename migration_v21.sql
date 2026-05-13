-- v21: Tag channels with the competitions they participate in.
--
-- The pattern is many-to-many (one federation channel like
-- France can map to Euro 2024 + WC 2026 + Nations League), but
-- a JSONB column gives us that without a separate join table —
-- queries use the ? existence operator:
--
--   SELECT * FROM channels WHERE competitions ? 'wc2026';
--
-- Per-competition metadata (group, confederation, etc.) lives
-- inside the JSON value, e.g.
--
--   competitions = {
--     "wc2026": {"group": "A", "confederation": "CONCACAF", "team": "Mexico"},
--     "euro2028": {...}
--   }

ALTER TABLE channels
    ADD COLUMN IF NOT EXISTS competitions JSONB NOT NULL DEFAULT '{}'::jsonb;

-- GIN index so the `competitions ? 'wc2026'` lookup is fast.
CREATE INDEX IF NOT EXISTS idx_channels_competitions
    ON channels USING GIN (competitions);
