-- Reddit integration — isolated schema, safe to drop if feature is killed.
-- Run once in the Supabase SQL Editor, then seed with reddit_seed.sql.

create extension if not exists pgcrypto;

create table if not exists reddit_subreddits (
  id uuid primary key default gen_random_uuid(),
  channel_id uuid references channels(id) on delete cascade,
  subreddit text unique not null,
  is_official boolean default false,
  subscribers int default 0,
  active_users int default 0,
  description text,
  title text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  last_fetched timestamptz
);

create table if not exists reddit_posts (
  id uuid primary key default gen_random_uuid(),
  subreddit_id uuid references reddit_subreddits(id) on delete cascade,
  reddit_post_id text unique not null,
  title text,
  author text,
  url text,
  permalink text,
  flair text,
  score int default 0,
  num_comments int default 0,
  upvote_ratio real,
  is_video boolean default false,
  thumbnail text,
  created_at timestamptz,
  fetched_at timestamptz default now()
);

create index if not exists idx_reddit_posts_subreddit_created
  on reddit_posts (subreddit_id, created_at desc);
create index if not exists idx_reddit_posts_subreddit_score
  on reddit_posts (subreddit_id, score desc);

create table if not exists reddit_snapshots (
  id uuid primary key default gen_random_uuid(),
  subreddit_id uuid references reddit_subreddits(id) on delete cascade,
  captured_date date not null,
  subscribers int,
  active_users int,
  posts_24h int,
  total_comments_24h int,
  unique (subreddit_id, captured_date)
);

-- Auto-update `updated_at` on row change
create or replace function reddit_touch_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists reddit_subreddits_touch on reddit_subreddits;
create trigger reddit_subreddits_touch
  before update on reddit_subreddits
  for each row execute function reddit_touch_updated_at();
