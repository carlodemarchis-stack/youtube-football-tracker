-- Pre-computed per-video daily deltas.
-- Populated by scripts/daily_refresh.py after video_snapshots are inserted,
-- and by scripts/backfill_video_deltas.py for historical dates.
-- Reading this table replaces the "fetch two days of video_snapshots + diff in
-- Python" pattern used by the Daily Recap "Most watched" section.

create table if not exists video_daily_deltas (
  video_id        uuid not null,
  channel_id      uuid not null,
  captured_date   date not null,
  view_delta      int  default 0,   -- view_count on `captured_date` minus previous snapshot
  view_count      int  default 0,   -- snapshot value on `captured_date`
  prev_captured_date date,          -- the date we diffed against (usually captured_date - 1)
  primary key (video_id, captured_date)
);

-- Indexes for the two hot query patterns:
--   1. "Top-N on date X globally" / "for these channels":
--        where captured_date = X [and channel_id in (...)] order by view_delta desc limit N
create index if not exists idx_vdd_date_delta
  on video_daily_deltas (captured_date, view_delta desc);

create index if not exists idx_vdd_channel_date_delta
  on video_daily_deltas (channel_id, captured_date, view_delta desc);
