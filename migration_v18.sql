-- ─────────────────────────────────────────────────────────────────────────────
-- migration_v18: Row-Level Security + drop unused Reddit tables
-- ─────────────────────────────────────────────────────────────────────────────
-- Apply via Supabase Dashboard → SQL Editor → paste → Run.
-- Reversible: each block has a commented-out rollback at the bottom.
--
-- WHAT THIS DOES
--   1. Drops the three reddit_* tables (no longer used; page disabled).
--   2. Enables RLS on every remaining public-schema table.
--   3. Creates SELECT-only policies for the anon role on tables that the
--      public dashboard needs to read.
--   4. Locks user_profiles + ai_usage to admin-only (no anon access at all).
--   5. Allows anon read on fetch_history (powers the "last updated" indicator
--      on the public Home page — agreed Option A).
--
-- AFTER APPLYING
--   - The public dashboard (anon key) keeps working for read paths.
--   - All write paths still go through service_role (admin pages + crons),
--     which bypasses RLS — they keep working unchanged.
--   - Anon trying to INSERT/UPDATE/DELETE on any table → permission denied.
--   - Anon trying to SELECT from user_profiles, ai_usage → permission denied.
--
-- TEST AFTER RUNNING
--   curl -s "$SUPABASE_URL/rest/v1/user_profiles?select=*" \
--        -H "apikey: <anon-key>"
--   → should return [] or 401, NOT the user list.
-- ─────────────────────────────────────────────────────────────────────────────


-- ── Step 1: drop unused reddit tables ──────────────────────────────────────
-- The Reddit page is disabled in app.py (Reddit blocks cloud IPs); the cron
-- isn't running on Railway. Tables hold ~500 rows of stale scrape data.
DROP TABLE IF EXISTS reddit_posts       CASCADE;
DROP TABLE IF EXISTS reddit_snapshots   CASCADE;
DROP TABLE IF EXISTS reddit_subreddits  CASCADE;


-- ── Step 2: enable RLS on every remaining table ────────────────────────────
-- Once enabled, any role *without* an explicit policy gets denied. The
-- service_role key bypasses RLS entirely (Supabase platform behavior).
ALTER TABLE videos              ENABLE ROW LEVEL SECURITY;
ALTER TABLE channels            ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_snapshots   ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_snapshots     ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_daily_deltas  ENABLE ROW LEVEL SECURITY;
ALTER TABLE video_catalog       ENABLE ROW LEVEL SECURITY;
ALTER TABLE dashboard_cache     ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_insights    ENABLE ROW LEVEL SECURITY;
ALTER TABLE follower_snapshots  ENABLE ROW LEVEL SECURITY;
ALTER TABLE fetch_history       ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_usage            ENABLE ROW LEVEL SECURITY;


-- ── Step 3: anon SELECT policies for public-read tables ────────────────────
-- "USING (true)" = unconditionally allow read; we're treating these as
-- public datasets. No INSERT/UPDATE/DELETE policy → anon writes denied.
CREATE POLICY "anon_select_videos"             ON videos
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_channels"           ON channels
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_channel_snapshots"  ON channel_snapshots
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_video_snapshots"    ON video_snapshots
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_video_daily_deltas" ON video_daily_deltas
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_video_catalog"      ON video_catalog
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_dashboard_cache"    ON dashboard_cache
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_channel_insights"   ON channel_insights
    FOR SELECT TO anon USING (true);
CREATE POLICY "anon_select_follower_snapshots" ON follower_snapshots
    FOR SELECT TO anon USING (true);

-- fetch_history: anon read allowed (powers the "last updated …" indicator
-- on the public Home page; the Latest page also uses MAX(published_at) on
-- videos, but the Home indicator currently reads from this table). Cron
-- summary text is operational but not sensitive.
CREATE POLICY "anon_select_fetch_history"      ON fetch_history
    FOR SELECT TO anon USING (true);


-- ── Step 4: locked-down tables (no anon policy = no anon access) ───────────
-- user_profiles: emails + role assignments. PII — admin-only via service_role.
-- ai_usage:      LLM cost tracking. Operational — admin-only.
-- (No CREATE POLICY needed: RLS-enabled tables with no policy deny everything
-- to non-bypassing roles.)


-- ─────────────────────────────────────────────────────────────────────────────
-- ROLLBACK (uncomment + run if anything goes wrong)
-- ─────────────────────────────────────────────────────────────────────────────
-- ALTER TABLE videos              DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE channels            DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE channel_snapshots   DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE video_snapshots     DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE video_daily_deltas  DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE video_catalog       DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE dashboard_cache     DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE channel_insights    DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE follower_snapshots  DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE fetch_history       DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE user_profiles       DISABLE ROW LEVEL SECURITY;
-- ALTER TABLE ai_usage            DISABLE ROW LEVEL SECURITY;
-- (Reddit table drops are not reversible without restoring from backup.)
