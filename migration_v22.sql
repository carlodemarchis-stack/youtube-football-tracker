-- v22: Per-day quota usage log for YouTube API keys.
--
-- Approach A from the design doc: each YouTubeClient process counts
-- units consumed per key during its run and flushes a single upsert
-- per (date, key_tail, script) bucket at process exit (atexit). The
-- admin Quota Monitor page reads this table to show today's usage,
-- 14-day history, and rotation events per key.
--
-- We index by the last 4 chars of the key (key_tail) rather than the
-- full key so the table can be safely viewed without exposing secrets.
-- key_tail uniqueness is good enough across the ~5 keys we run.
--
-- script: where the calls came from — basename of sys.argv[0] for
-- cron-style scripts, "streamlit" for interactive views, or "unknown".

CREATE TABLE IF NOT EXISTS youtube_quota_log (
    date            date        NOT NULL,
    key_tail        text        NOT NULL,
    script          text        NOT NULL DEFAULT 'unknown',
    units           bigint      NOT NULL DEFAULT 0,
    calls           bigint      NOT NULL DEFAULT 0,
    rotations_from  int         NOT NULL DEFAULT 0,
    rotations_to    int         NOT NULL DEFAULT 0,
    first_used_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (date, key_tail, script)
);

CREATE INDEX IF NOT EXISTS idx_youtube_quota_log_date
    ON youtube_quota_log (date DESC);
CREATE INDEX IF NOT EXISTS idx_youtube_quota_log_key_tail
    ON youtube_quota_log (key_tail);
