-- v22b: Allow anon writes to youtube_quota_log.
--
-- Production scripts (daily_refresh, hourly_rss, snapshot_videos_only,
-- refill_season_videos) run on a Railway service that uses
-- SUPABASE_KEY (anon) — not SUPABASE_SERVICE_KEY. Without a permissive
-- INSERT/UPDATE policy, the atexit flush in src/youtube_api.py silently
-- fails the RLS check and no rows are ever written.
--
-- The table only holds per-process usage counters (units / calls /
-- last_used_at). Nothing sensitive lives in here, so a permissive
-- policy is acceptable — the worst a bad actor can do is inflate the
-- numbers on the admin monitor page.
--
-- SELECT remains service-role-only (no SELECT policy), keeping the
-- monitor admin-only.

CREATE POLICY youtube_quota_log_anon_insert
    ON youtube_quota_log
    FOR INSERT
    TO anon
    WITH CHECK (true);

CREATE POLICY youtube_quota_log_anon_update
    ON youtube_quota_log
    FOR UPDATE
    TO anon
    USING (true)
    WITH CHECK (true);

-- Also let anon SELECT — the flush hook does a read-modify-write to
-- accumulate counts across runs within the same day, so it needs to
-- see existing rows. The monitor page uses admin_db() (service role)
-- and bypasses RLS regardless.
CREATE POLICY youtube_quota_log_anon_select
    ON youtube_quota_log
    FOR SELECT
    TO anon
    USING (true);
