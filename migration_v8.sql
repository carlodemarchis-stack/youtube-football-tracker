-- v8: Add long-form / shorts split
-- Channel-level counts
ALTER TABLE channels ADD COLUMN IF NOT EXISTS long_form_count integer DEFAULT 0;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS shorts_count integer DEFAULT 0;

-- Video-level format indicator: 'long' or 'short'
ALTER TABLE videos ADD COLUMN IF NOT EXISTS format text DEFAULT 'long';
