-- Migration v5: Add persistent colors to channels
ALTER TABLE channels ADD COLUMN IF NOT EXISTS color TEXT DEFAULT '';
ALTER TABLE channels ADD COLUMN IF NOT EXISTS color2 TEXT DEFAULT '';
