-- Migration v12: actual_start_time for live videos
-- YouTube live streams have a published_at that reflects when they were *scheduled*,
-- which can be days before the actual broadcast. actual_start_time stores the real
-- broadcast time from liveStreamingDetails.actualStartTime (or scheduledStartTime
-- as fallback), so Latest Videos can sort live streams by when they actually aired.

ALTER TABLE videos ADD COLUMN IF NOT EXISTS actual_start_time TIMESTAMPTZ;

-- For sorting: use COALESCE(actual_start_time, published_at) to get the effective
-- "when did this content actually appear" timestamp.
