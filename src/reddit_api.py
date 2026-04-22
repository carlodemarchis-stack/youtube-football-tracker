"""Reddit API wrapper — public JSON endpoints only, no OAuth.

Reddit's public `.json` endpoints serve all read-only public data with just
a User-Agent header. No client_id / client_secret / registration needed.

Anonymous rate limit is ~60 requests/minute. Our hourly run does ~34
requests total (17 subreddits × 2 endpoints), so we're well under.

Env vars required:
    REDDIT_USER_AGENT   (optional — defaults to a sensible UA string)
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


_DEFAULT_UA = "ytft-research/0.1 (public data only)"
_TIMEOUT = 15


_last_error: str = ""


def last_error() -> str:
    return _last_error


def _get_json(path: str) -> dict | None:
    """Fetch a Reddit .json endpoint. Returns parsed JSON or None on failure.
    Stores error reason in module-level `_last_error` for diagnostics."""
    global _last_error
    ua = os.environ.get("REDDIT_USER_AGENT", _DEFAULT_UA)
    url = f"https://www.reddit.com{path}"
    req = Request(url, headers={
        "User-Agent": ua,
        "Accept": "application/json",
    })
    try:
        with urlopen(req, timeout=_TIMEOUT) as resp:
            _last_error = ""
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        _last_error = f"HTTP {e.code}"
        return None
    except (URLError, TimeoutError) as e:
        _last_error = f"network: {e}"
        return None
    except json.JSONDecodeError as e:
        _last_error = f"invalid JSON: {e}"
        return None
    except Exception as e:
        _last_error = f"error: {e}"
        return None


def _post_from_json(sub_name: str, child_data: dict) -> RedditPost | None:
    """Convert a Reddit listing child's data dict into a RedditPost."""
    pid = child_data.get("id")
    if not pid:
        return None
    created_utc = child_data.get("created_utc") or 0
    try:
        created_iso = datetime.fromtimestamp(float(created_utc), tz=timezone.utc).isoformat()
    except Exception:
        created_iso = ""
    thumb = child_data.get("thumbnail") or ""
    if not thumb.startswith("http"):
        thumb = ""
    author = child_data.get("author") or "[deleted]"
    return RedditPost(
        reddit_post_id=pid,
        subreddit=sub_name,
        title=(child_data.get("title") or "")[:500],
        author=author,
        url=child_data.get("url") or "",
        permalink=f"https://reddit.com{child_data.get('permalink', '')}"
            if child_data.get("permalink") else "",
        flair=child_data.get("link_flair_text") or "",
        score=int(child_data.get("score") or 0),
        num_comments=int(child_data.get("num_comments") or 0),
        upvote_ratio=float(child_data.get("upvote_ratio") or 0.0),
        is_video=bool(child_data.get("is_video")),
        thumbnail=thumb,
        created_at=created_iso,
    )


class RedditClient:
    """Simple read-only client using Reddit's public .json endpoints."""

    def __init__(self, *_args, **_kwargs):
        # Args accepted for API compatibility with praw-based version; unused.
        pass

    def get_subreddit_info(self, name: str) -> SubredditInfo | None:
        """Fetch subscribers, active users, description for a subreddit."""
        data = _get_json(f"/r/{name}/about.json")
        if not data or "data" not in data:
            return None
        d = data["data"]
        return SubredditInfo(
            subreddit=d.get("display_name") or name,
            subscribers=int(d.get("subscribers") or 0),
            active_users=int(d.get("active_user_count") or 0),
            description=(d.get("public_description") or "")[:500],
            title=d.get("title") or "",
            over18=bool(d.get("over18")),
        )

    def get_recent_posts(self, name: str, limit: int = 25) -> list[RedditPost]:
        """Fetch the latest N posts from a subreddit (newest first)."""
        data = _get_json(f"/r/{name}/new.json?limit={min(100, max(1, limit))}")
        if not data or "data" not in data:
            return []
        out: list[RedditPost] = []
        for child in data["data"].get("children", []):
            post = _post_from_json(name, child.get("data", {}))
            if post:
                out.append(post)
        return out

    def get_top_post_24h(self, name: str) -> RedditPost | None:
        """Highest-scoring post in the last 24h."""
        data = _get_json(f"/r/{name}/top.json?t=day&limit=1")
        if not data or "data" not in data:
            return None
        children = data["data"].get("children", [])
        if not children:
            return None
        return _post_from_json(name, children[0].get("data", {}))
