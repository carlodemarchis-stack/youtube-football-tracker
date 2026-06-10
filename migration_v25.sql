-- v25: Incremental branded-content scanning marker.
--
-- scripts/scan_branded_content.py runs INCREMENTAL by default — it only
-- processes videos that haven't been scanned yet (branded_scanned_at
-- IS NULL), marking each page as it goes. New videos (inserted by the
-- ingestion crons with branded_scanned_at NULL) get picked up by the
-- next weekly run; the full 187k catalogue is no longer re-scanned
-- every time. Pass --full to force a complete re-scan.
--
-- The partial index keeps "find the unscanned videos" instant once the
-- bulk of the catalogue is marked.

ALTER TABLE videos ADD COLUMN IF NOT EXISTS branded_scanned_at timestamptz;

-- Build AFTER back-filling branded_scanned_at on existing rows, so the
-- partial index covers only the small unscanned set:
--   CREATE INDEX CONCURRENTLY idx_videos_unscanned
--       ON videos (id) WHERE branded_scanned_at IS NULL;
