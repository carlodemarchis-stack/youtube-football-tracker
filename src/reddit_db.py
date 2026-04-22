"""Supabase helpers for the Reddit tables. Isolated from src/database.py
so the Reddit feature can be killed without touching YouTube-side code."""
from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timezone, date

from supabase import create_client, Client

from src.reddit_api import SubredditInfo, RedditPost


def _client() -> Client:
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY not set")
    return create_client(url, key)


# ── Subreddit CRUD ──────────────────────────────────────────
def get_tracked_subreddits() -> list[dict]:
    """All subreddits we're tracking, newest first."""
    resp = (
        _client().table("reddit_subreddits")
        .select("*")
        .order("subscribers", desc=True)
        .execute()
    )
    return resp.data or []


def get_subreddit_by_name(name: str) -> dict | None:
    resp = (
        _client().table("reddit_subreddits")
        .select("*")
        .ilike("subreddit", name)
        .limit(1)
        .execute()
    )
    return (resp.data or [None])[0]


def upsert_subreddit_stats(name: str, info: SubredditInfo) -> None:
    """Update subscribers/active_users/description for a subreddit."""
    _client().table("reddit_subreddits").update({
        "subscribers": info.subscribers,
        "active_users": info.active_users,
        "description": info.description,
        "last_fetched": datetime.now(timezone.utc).isoformat(),
    }).ilike("subreddit", name).execute()


def add_subreddit(subreddit: str, channel_id: str | None,
                  is_official: bool = False) -> dict:
    """Create a new tracked subreddit row. `subreddit` is the name without r/ prefix."""
    row = {
        "subreddit": subreddit,
        "channel_id": channel_id,
        "is_official": is_official,
    }
    resp = _client().table("reddit_subreddits").upsert(row, on_conflict="subreddit").execute()
    return (resp.data or [{}])[0]


def remove_subreddit(subreddit: str) -> None:
    _client().table("reddit_subreddits").delete().ilike("subreddit", subreddit).execute()


# ── Posts ──────────────────────────────────────────────────
def upsert_posts(subreddit_id: str, posts: list[RedditPost]) -> int:
    if not posts:
        return 0
    rows = []
    for p in posts:
        row = asdict(p)
        row.pop("subreddit", None)  # don't store the name, we have the FK
        row["subreddit_id"] = subreddit_id
        rows.append(row)
    # Chunk to avoid too-large upserts
    total = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i + 200]
        resp = _client().table("reddit_posts").upsert(chunk, on_conflict="reddit_post_id").execute()
        total += len(resp.data or [])
    return total


def get_recent_posts(subreddit_id: str, limit: int = 25) -> list[dict]:
    resp = (
        _client().table("reddit_posts")
        .select("*")
        .eq("subreddit_id", subreddit_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def get_top_post_today(subreddit_id: str) -> dict | None:
    """Highest-scoring post of the last 24 hours (based on created_at)."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    resp = (
        _client().table("reddit_posts")
        .select("*")
        .eq("subreddit_id", subreddit_id)
        .gte("created_at", since)
        .order("score", desc=True)
        .limit(1)
        .execute()
    )
    return (resp.data or [None])[0]


# ── Snapshots ──────────────────────────────────────────────
def add_snapshot(subreddit_id: str, subscribers: int, active_users: int,
                 posts_24h: int, total_comments_24h: int) -> None:
    today = date.today().isoformat()
    row = {
        "subreddit_id": subreddit_id,
        "captured_date": today,
        "subscribers": subscribers,
        "active_users": active_users,
        "posts_24h": posts_24h,
        "total_comments_24h": total_comments_24h,
    }
    _client().table("reddit_snapshots").upsert(
        row, on_conflict="subreddit_id,captured_date"
    ).execute()


def get_snapshots(subreddit_id: str, since_date: str | None = None) -> list[dict]:
    q = (
        _client().table("reddit_snapshots")
        .select("*")
        .eq("subreddit_id", subreddit_id)
        .order("captured_date", desc=False)
    )
    if since_date:
        q = q.gte("captured_date", since_date)
    resp = q.execute()
    return resp.data or []
