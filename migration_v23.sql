-- v23: Paid-promotion disclosure flag on videos.
--
-- YouTube's videos.list API exposes
-- `paidProductPlacementDetails.hasPaidProductPlacement` — a boolean
-- the creator sets when a video carries the "Includes paid promotion"
-- disclosure (brand sponsorship / paid product placement). The part
-- is free (0 extra quota) and returns via plain API key.
--
-- We fetch it on every video detail pull (src.youtube_api
-- get_video_details / _parse_video_item) and store it here so the
-- Top Videos / Season tables can render a "💰 Sponsored" badge.
--
-- NULLable + default FALSE: rows ingested before this migration read
-- as not-sponsored until their next nightly re-stat refreshes the
-- flag. The disclosure is rare in football content (~0.6% of top
-- videos) so when the badge shows it's a meaningful, low-noise signal.

ALTER TABLE videos
    ADD COLUMN IF NOT EXISTS has_paid_promotion BOOLEAN NOT NULL DEFAULT FALSE;
