-- ─────────────────────────────────────────────────────────────────────────────
-- migration_v19: indexes on like_count + comment_count for Season Top
-- ─────────────────────────────────────────────────────────────────────────────
-- Apply via Supabase Dashboard → SQL Editor → paste → Run.
--
-- WHY
--   The Season Top page issues 3 queries per zoom:
--     ORDER BY view_count    DESC LIMIT 100   ← already fast (indexed)
--     ORDER BY like_count    DESC LIMIT 5     ← TIMES OUT at Z1 (no index)
--     ORDER BY comment_count DESC LIMIT 5     ← TIMES OUT at Z1 (no index)
--   With a 100+ channel WHERE channel_id IN (…) clause and ~30k season
--   rows, Postgres has to fall back to a full scan + sort.
--
-- WHAT
--   Two btree indexes that let the planner do an index-ordered scan
--   under WHERE published_at >= … AND channel_id IN (…). Composite over
--   (published_at, <metric>) so they're useful both with and without
--   the channel IN filter.
--
-- COST
--   Each index ~10–20 MB on a videos table this size. Build is ~5–15s,
--   non-locking (CREATE INDEX CONCURRENTLY would also work but isn't
--   needed at this size).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_videos_published_like
  ON videos (published_at DESC, like_count DESC);

CREATE INDEX IF NOT EXISTS idx_videos_published_comments
  ON videos (published_at DESC, comment_count DESC);


-- Rollback (uncomment + run if needed):
-- DROP INDEX IF EXISTS idx_videos_published_like;
-- DROP INDEX IF EXISTS idx_videos_published_comments;
