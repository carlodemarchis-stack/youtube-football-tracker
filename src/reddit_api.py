"""Reddit API wrapper (read-only). Isolated from the YouTube side.

Uses PRAW in read-only mode: only client_id + client_secret + user_agent.
No username/password needed — we only read public subreddit data.

Env vars required:
    REDDIT_CLIENT_ID
    REDDIT_CLIENT_SECRET
    REDDIT_USER_AGENT    (e.g. "ytft-research/0.1 by u/your_username")
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable

try:
    import praw
    from prawcore.exceptions import NotFound, Forbidden, Redirect, ResponseException
except ImportError:  # Let import fail lazily so the rest of the app still loads
    praw = None  # type: ignore
    NotFound = Forbidden = Redirect = ResponseException = Exception  # type: ignore


@dataclass
class SubredditInfo:
    subreddit: str
    subscribers: int = 0
    active_users: int = 0
    description: str = ""
    title: str = ""
    over18: bool = False


@dataclass
class RedditPost:
    reddit_post_id: str
    subreddit: str
    title: str = ""
    author: str = ""
    url: str = ""
    permalink: str = ""
    flair: str = ""
    score: int = 0
    num_comments: int = 0
    upvote_ratio: float = 0.0
    is_video: bool = False
    thumbnail: str = ""
    created_at: str = ""  # ISO8601 UTC


class RedditClient:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 user_agent: str | None = None):
        if praw is None:
            raise RuntimeError("praw is not installed. Add 'praw>=7.7.0' to requirements.txt.")
        self.client = praw.Reddit(
            client_id=client_id or os.environ.get("REDDIT_CLIENT_ID", ""),
            client_secret=client_secret or os.environ.get("REDDIT_CLIENT_SECRET", ""),
            user_agent=user_agent or os.environ.get("REDDIT_USER_AGENT", "ytft-research/0.1"),
            check_for_async=False,
        )
        self.client.read_only = True

    # ── Subreddit metadata ───────────────────────────────────
    def get_subreddit_info(self, name: str) -> SubredditInfo | None:
        """Fetch subscribers, active users, description for a subreddit.
        Returns None if the subreddit doesn't exist / is private / banned."""
        try:
            s = self.client.subreddit(name)
            # Accessing any attribute triggers a fetch
            _ = s.id
            return SubredditInfo(
                subreddit=s.display_name,
                subscribers=int(getattr(s, "subscribers", 0) or 0),
                active_users=int(getattr(s, "active_user_count", 0) or 0),
                description=(getattr(s, "public_description", "") or "")[:500],
                title=getattr(s, "title", "") or "",
                over18=bool(getattr(s, "over18", False)),
            )
        except (NotFound, Forbidden, Redirect, ResponseException):
            return None
        except Exception:
            return None

    # ── Posts ────────────────────────────────────────────────
    def get_recent_posts(self, name: str, limit: int = 25) -> list[RedditPost]:
        """Fetch the latest N posts from a subreddit (newest first)."""
        try:
            sub = self.client.subreddit(name)
            out: list[RedditPost] = []
            for post in sub.new(limit=limit):
                created_iso = datetime.fromtimestamp(
                    post.created_utc, tz=timezone.utc
                ).isoformat() if getattr(post, "created_utc", None) else ""
                out.append(RedditPost(
                    reddit_post_id=post.id,
                    subreddit=name,
                    title=(post.title or "")[:500],
                    author=str(post.author) if post.author else "[deleted]",
                    url=post.url or "",
                    permalink=f"https://reddit.com{post.permalink}" if post.permalink else "",
                    flair=post.link_flair_text or "",
                    score=int(post.score or 0),
                    num_comments=int(post.num_comments or 0),
                    upvote_ratio=float(post.upvote_ratio or 0.0),
                    is_video=bool(post.is_video),
                    thumbnail=(post.thumbnail or "") if str(post.thumbnail).startswith("http") else "",
                    created_at=created_iso,
                ))
            return out
        except (NotFound, Forbidden, Redirect, ResponseException):
            return []
        except Exception:
            return []

    def get_top_post_24h(self, name: str) -> RedditPost | None:
        """Highest-scoring post in the last 24h."""
        try:
            sub = self.client.subreddit(name)
            for post in sub.top(time_filter="day", limit=1):
                created_iso = datetime.fromtimestamp(
                    post.created_utc, tz=timezone.utc
                ).isoformat() if getattr(post, "created_utc", None) else ""
                return RedditPost(
                    reddit_post_id=post.id,
                    subreddit=name,
                    title=(post.title or "")[:500],
                    author=str(post.author) if post.author else "[deleted]",
                    url=post.url or "",
                    permalink=f"https://reddit.com{post.permalink}" if post.permalink else "",
                    flair=post.link_flair_text or "",
                    score=int(post.score or 0),
                    num_comments=int(post.num_comments or 0),
                    upvote_ratio=float(post.upvote_ratio or 0.0),
                    is_video=bool(post.is_video),
                    thumbnail=(post.thumbnail or "") if str(post.thumbnail).startswith("http") else "",
                    created_at=created_iso,
                )
        except Exception:
            return None
        return None
