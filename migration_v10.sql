-- Migration v10: channel_snapshots
-- Daily point-in-time snapshots of channel-level counters.
-- One row per (channel, day). Re-running refresh the same day upserts.

create table if not exists channel_snapshots (
    id uuid primary key default gen_random_uuid(),
    channel_id uuid not null references channels(id) on delete cascade,
    captured_date date not null default (now() at time zone 'utc')::date,
    captured_at timestamptz not null default now(),
    subscriber_count bigint default 0,
    total_views bigint default 0,
    video_count int default 0,
    long_form_count int default 0,
    shorts_count int default 0,
    live_count int default 0,
    unique (channel_id, captured_date)
);

create index if not exists idx_channel_snapshots_channel_date
    on channel_snapshots (channel_id, captured_date desc);
