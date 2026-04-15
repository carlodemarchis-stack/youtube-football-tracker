-- Migration v11: video_snapshots
-- Daily point-in-time snapshots of per-video counters (season videos only).
-- One row per (video, day). Re-running the cron on the same day upserts.

create table if not exists video_snapshots (
    id uuid primary key default gen_random_uuid(),
    video_id uuid not null references videos(id) on delete cascade,
    captured_date date not null default (now() at time zone 'utc')::date,
    captured_at timestamptz not null default now(),
    view_count bigint default 0,
    like_count bigint default 0,
    comment_count bigint default 0,
    unique (video_id, captured_date)
);

create index if not exists idx_video_snapshots_video_date
    on video_snapshots (video_id, captured_date desc);

create index if not exists idx_video_snapshots_date
    on video_snapshots (captured_date desc);
