-- v20: Add owned-and-operated digital identity fields to channels.
--
-- These fields support the "Digital Footprint" Lab page (and the
-- yearly collector that builds its data). They are intentionally
-- NULLable: the long tail of clubs in the DB don't have these
-- mapped yet, and the page renders gracefully on missing values.
--
--   website        — canonical owned-and-operated URL of the
--                    club / league. Free-form text so it can hold
--                    the locale-specific landing page (e.g.
--                    "https://www.juventus.com/en/") rather than
--                    just a bare domain.
--   wikipedia_slug — the article slug on en.wikipedia.org used to
--                    fetch language editions, article size and
--                    pageview counts. Stored as the URL slug, e.g.
--                    "Juventus_FC".

ALTER TABLE channels
    ADD COLUMN IF NOT EXISTS website TEXT,
    ADD COLUMN IF NOT EXISTS wikipedia_slug TEXT;

-- Helpful for the collector that filters by "have we got a website yet?"
CREATE INDEX IF NOT EXISTS idx_channels_website
    ON channels (website) WHERE website IS NOT NULL;
