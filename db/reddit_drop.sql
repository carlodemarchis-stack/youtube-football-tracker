-- Kill switch: completely remove the Reddit feature from the DB.
-- Pair with: delete views/12_Reddit.py, src/reddit_api.py, src/reddit_db.py,
-- scripts/hourly_reddit.py and the nav entry in app.py.

drop trigger if exists reddit_subreddits_touch on reddit_subreddits;
drop function if exists reddit_touch_updated_at();
drop table if exists reddit_snapshots cascade;
drop table if exists reddit_posts cascade;
drop table if exists reddit_subreddits cascade;
