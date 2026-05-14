from __future__ import annotations

import os
import sys

import isodate
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .quota_alert import send_ntfy


def _is_quota_exceeded(err: HttpError) -> bool:
    """Detect quotaExceeded / dailyLimitExceeded errors from the YouTube API."""
    try:
        if getattr(err.resp, "status", None) != 403:
            return False
        content = (err.content or b"").decode("utf-8", errors="ignore")
        return ("quotaExceeded" in content
                or "dailyLimitExceeded" in content
                or "rateLimitExceeded" in content)
    except Exception:
        return False


class YouTubeClient:
    # Undocumented playlist prefixes (replace "UC" at start of channel ID)
    PREFIX_UPLOADS = "UU"        # All uploads (default)
    PREFIX_LONG = "UULF"         # Long-form videos only
    PREFIX_SHORTS = "UUSH"       # Shorts only
    PREFIX_LIVE = "UULV"         # Past live streams (counted as long-form)
    PREFIX_POP_LONG = "UULP"     # Popular long-form (capped ~200)
    PREFIX_POP_SHORTS = "UUPS"   # Popular shorts (capped ~200)

    @staticmethod
    def _playlist_id(channel_id: str, prefix: str) -> str:
        """Derive a playlist ID from a channel ID by replacing the 'UC' prefix."""
        return prefix + channel_id[2:]

    def __init__(self, api_key: str, *, backups: list[str] | None = None):
        # Key pool: primary first, then any backups. On quota-exceeded
        # we rotate to the next key, fire one ntfy alert per rotation,
        # and retry the call transparently. When no backups are passed
        # explicitly we pick up YOUTUBE_API_KEY_BACKUP from the env so
        # existing call sites get failover for free.
        if backups is None:
            backups = []
            env_backup = os.environ.get("YOUTUBE_API_KEY_BACKUP", "").strip()
            if env_backup and env_backup != api_key:
                backups.append(env_backup)
        self._keys: list[str] = [api_key] + [k for k in backups if k]
        self._idx: int = 0
        self._alerts_fired: set[int] = set()  # to avoid duplicate alerts
        self.youtube = build(
            "youtube", "v3", developerKey=self._keys[self._idx])

    # ── Quota-failover plumbing ──────────────────────────────────────
    def _rotate_key(self, error: Exception) -> bool:
        """Swap to the next API key. Returns True if rotation happened,
        False if all keys are exhausted. Sends an ntfy alert on rotation."""
        if self._idx + 1 >= len(self._keys):
            # All exhausted — alert once, then let the error bubble up
            if "exhausted" not in self._alerts_fired:
                self._alerts_fired.add("exhausted")
                key_tail = self._keys[self._idx][-6:]
                send_ntfy(
                    f"🚨 YouTube API: ALL keys exhausted "
                    f"(last={key_tail}). Production reads are now failing.",
                    title="YTFT quota — ALL KEYS DEAD",
                    priority="urgent",
                    tags="rotating_light,youtube",
                )
            return False
        old_tail = self._keys[self._idx][-6:]
        self._idx += 1
        new_tail = self._keys[self._idx][-6:]
        # One alert per (key_index, rotation) so reruns don't spam
        marker = f"rotated_to_{self._idx}"
        if marker not in self._alerts_fired:
            self._alerts_fired.add(marker)
            send_ntfy(
                f"⚠️ YouTube API key …{old_tail} hit quota — "
                f"switched to backup …{new_tail}",
                title="YTFT quota — key rotated",
                priority="high",
                tags="warning,youtube",
            )
        # Rebuild the client against the new key
        self.youtube = build(
            "youtube", "v3", developerKey=self._keys[self._idx])
        print(f"[YouTubeClient] rotated key …{old_tail} → …{new_tail}",
              file=sys.stderr)
        return True

    def _call(self, make_request):
        """Execute make_request(self.youtube).execute() with quota failover.
        make_request is a callable that takes the current youtube client
        and returns an unexecuted HttpRequest. We rebuild the request after
        each rotation because the request is bound to the previous client."""
        while True:
            try:
                return make_request(self.youtube).execute()
            except HttpError as e:
                if _is_quota_exceeded(e) and self._rotate_key(e):
                    continue
                raise

    def resolve_handle(self, handle: str) -> dict | None:
        """Resolve a YouTube handle (e.g. @sscnapoli) to channel info."""
        handle = handle.strip().lstrip("@")
        resp = self._call(lambda yt: yt.channels().list(
            part="snippet,statistics,contentDetails",
            forHandle=handle,
        ))
        if not resp.get("items"):
            return None
        item = resp["items"][0]
        snippet = item["snippet"]
        stats = item["statistics"]
        return {
            "youtube_channel_id": item["id"],
            "name": snippet.get("title", ""),
            "handle": "@" + handle,
            "description": snippet.get("description", ""),
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "subscriber_count": int(stats.get("subscriberCount", 0)),
            "total_views": int(stats.get("viewCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
            "country": snippet.get("country", ""),
            "launched_at": snippet.get("publishedAt", ""),
        }

    def get_channel_stats(self, channel_id: str) -> dict:
        resp = self._call(lambda yt: yt.channels().list(
            part="snippet,statistics,contentDetails",
            id=channel_id,
        ))
        if not resp.get("items"):
            return {}
        item = resp["items"][0]
        stats = item["statistics"]
        snippet = item.get("snippet", {})
        result = {
            "youtube_channel_id": channel_id,
            "name": snippet.get("title", ""),
            "handle": snippet.get("customUrl", ""),
            "subscriber_count": int(stats.get("subscriberCount", 0)),
            "total_views": int(stats.get("viewCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
            "uploads_playlist_id": item["contentDetails"]["relatedPlaylists"]["uploads"],
            "launched_at": snippet.get("publishedAt", ""),
        }
        # Fetch long-form / shorts counts (2 cheap API calls)
        counts = self.get_format_counts(channel_id)
        result["long_form_count"] = counts["long"]
        result["shorts_count"] = counts["shorts"]
        result["live_count"] = counts["live"]
        return result

    def get_all_video_ids(self, uploads_playlist_id: str, max_results: int = 500) -> list[str]:
        """Fetch video IDs from uploads playlist (newest first)."""
        video_ids = []
        next_page = None
        while len(video_ids) < max_results:
            _pl_id = uploads_playlist_id
            _max = min(50, max_results - len(video_ids))
            _tok = next_page
            resp = self._call(lambda yt: yt.playlistItems().list(
                part="contentDetails",
                playlistId=_pl_id,
                maxResults=_max,
                pageToken=_tok,
            ))
            for item in resp.get("items", []):
                video_ids.append(item["contentDetails"]["videoId"])
            next_page = resp.get("nextPageToken")
            if not next_page:
                break
        return video_ids

    def get_recent_video_entries(self, uploads_playlist_id: str, max_results: int = 20) -> list[dict]:
        """Fetch the most recent N videos from a channel's uploads playlist.
        Returns list of {"video_id", "published", "title"} — same shape as the RSS feed.
        Costs 1 quota unit. Used as a drop-in replacement when the public RSS
        endpoint is down/deprecated."""
        try:
            _pl_id = uploads_playlist_id
            _max = max(1, min(50, max_results))
            resp = self._call(lambda yt: yt.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=_pl_id,
                maxResults=_max,
            ))
        except Exception:
            return []
        out = []
        for item in resp.get("items", []):
            cd = item.get("contentDetails", {}) or {}
            sn = item.get("snippet", {}) or {}
            vid = cd.get("videoId") or sn.get("resourceId", {}).get("videoId", "")
            if not vid:
                continue
            out.append({
                "video_id": vid,
                "published": cd.get("videoPublishedAt") or sn.get("publishedAt", ""),
                "title": sn.get("title", ""),
            })
        return out

    def get_video_ids_since(self, uploads_playlist_id: str, since: str) -> list[str]:
        """Fetch video IDs from uploads playlist published on or after `since` (ISO date, e.g. '2025-08-01').
        Uses playlistItems (1 unit/call) and stops when hitting older videos."""
        video_ids = []
        next_page = None
        while True:
            _pl_id = uploads_playlist_id
            _tok = next_page
            resp = self._call(lambda yt: yt.playlistItems().list(
                part="contentDetails",
                playlistId=_pl_id,
                maxResults=50,
                pageToken=_tok,
            ))
            for item in resp.get("items", []):
                published = item["contentDetails"].get("videoPublishedAt", "")
                if published < since:
                    return video_ids  # Hit older content, done
                video_ids.append(item["contentDetails"]["videoId"])
            next_page = resp.get("nextPageToken")
            if not next_page:
                break
        return video_ids

    def get_popular_video_ids(self, channel_id: str) -> dict[str, list[str]]:
        """Fetch popular video IDs using UULP (long) + UUPS (shorts) playlists.
        Returns {"long": [...], "shorts": [...]}. Each list up to ~200 IDs.
        Costs 1 unit/call instead of 100 for search().list()."""
        result = {"long": [], "shorts": []}
        for key, prefix in [("long", self.PREFIX_POP_LONG), ("shorts", self.PREFIX_POP_SHORTS)]:
            pl_id = self._playlist_id(channel_id, prefix)
            next_page = None
            while True:
                try:
                    _pl_id = pl_id
                    _tok = next_page
                    resp = self._call(lambda yt: yt.playlistItems().list(
                        part="contentDetails",
                        playlistId=_pl_id,
                        maxResults=50,
                        pageToken=_tok,
                    ))
                except Exception:
                    break  # Playlist may not exist for some channels
                for item in resp.get("items", []):
                    result[key].append(item["contentDetails"]["videoId"])
                next_page = resp.get("nextPageToken")
                if not next_page:
                    break
        return result

    def get_format_counts(self, channel_id: str) -> dict[str, int]:
        """Get format counts from auto-playlists (1 API call each).
        Returns {"long": N (UULF only), "shorts": N (UUSH), "live": N (UULV)}.
        Note: consumers should treat 'long + live' as long-form total."""
        counts = {"long": 0, "shorts": 0, "live": 0}
        for key, prefix in [("long", self.PREFIX_LONG), ("shorts", self.PREFIX_SHORTS), ("live", self.PREFIX_LIVE)]:
            pl_id = self._playlist_id(channel_id, prefix)
            try:
                _pl_id = pl_id
                resp = self._call(lambda yt: yt.playlistItems().list(
                    part="contentDetails", playlistId=_pl_id, maxResults=1,
                ))
                counts[key] = resp.get("pageInfo", {}).get("totalResults", 0)
            except Exception:
                pass
        return counts

    def get_video_ids_since_by_format(self, channel_id: str, since: str) -> dict[str, list[str]]:
        """Fetch season video IDs split by format using UULF + UUSH + UULV playlists.
        Walks each playlist newest-first, stops at cutoff date.
        Returns {"long": [...], "shorts": [...], "live": [...]}."""
        result = {"long": [], "shorts": [], "live": []}
        for key, prefix in [("long", self.PREFIX_LONG), ("shorts", self.PREFIX_SHORTS), ("live", self.PREFIX_LIVE)]:
            pl_id = self._playlist_id(channel_id, prefix)
            next_page = None
            while True:
                try:
                    _pl_id = pl_id
                    _tok = next_page
                    resp = self._call(lambda yt: yt.playlistItems().list(
                        part="contentDetails",
                        playlistId=_pl_id,
                        maxResults=50,
                        pageToken=_tok,
                    ))
                except Exception:
                    break
                for item in resp.get("items", []):
                    published = item["contentDetails"].get("videoPublishedAt", "")
                    if published < since:
                        break
                    result[key].append(item["contentDetails"]["videoId"])
                else:
                    next_page = resp.get("nextPageToken")
                    if not next_page:
                        break
                    continue
                break  # Inner loop hit cutoff, stop paging
        return result

    def get_most_popular_video_ids(self, channel_id: str, max_results: int = 100) -> list[str]:
        """DEPRECATED: Use get_popular_video_ids() instead. Kept for backwards compat.
        Fetch video IDs sorted by view count (most popular first). Costs 100 units/call."""
        video_ids = []
        next_page = None
        while len(video_ids) < max_results:
            _cid = channel_id
            _max = min(50, max_results - len(video_ids))
            _tok = next_page
            resp = self._call(lambda yt: yt.search().list(
                part="id",
                channelId=_cid,
                type="video",
                order="viewCount",
                maxResults=_max,
                pageToken=_tok,
            ))
            for item in resp.get("items", []):
                video_ids.append(item["id"]["videoId"])
            next_page = resp.get("nextPageToken")
            if not next_page:
                break
        return video_ids

    def get_video_details(self, video_ids: list[str], on_progress: callable = None) -> list[dict]:
        videos = []
        total = len(video_ids)
        for i in range(0, total, 50):
            batch = video_ids[i : i + 50]
            _ids = ",".join(batch)
            resp = self._call(lambda yt: yt.videos().list(
                part="snippet,statistics,contentDetails,liveStreamingDetails",
                id=_ids,
            ))
            for item in resp.get("items", []):
                # Defensive: skip items that came back without an id. This
                # has been seen in rare cases (deleted/private/region-locked
                # videos, malformed API responses) and used to leak NULL
                # youtube_video_id rows into snapshot upserts, which
                # subsequently violated NOT NULL on the videos table.
                if not item.get("id"):
                    continue
                stats = item.get("statistics", {})
                snippet = item.get("snippet", {})
                content = item.get("contentDetails", {})
                live_details = item.get("liveStreamingDetails", {})
                duration_str = content.get("duration", "PT0S")
                try:
                    duration = isodate.parse_duration(duration_str)
                    duration_sec = int(duration.total_seconds())
                except Exception:
                    duration_sec = 0
                # For live streams: use actualStartTime (when it aired),
                # fall back to scheduledStartTime, then None.
                actual_start = (
                    live_details.get("actualStartTime")
                    or live_details.get("scheduledStartTime")
                    or None
                )
                # YouTube-supplied language hints (frequently empty, but
                # when present they're the most reliable signal — beats
                # title-based heuristics).
                _yt_lang = (snippet.get("defaultAudioLanguage")
                            or snippet.get("defaultLanguage")
                            or None)
                videos.append({
                    "youtube_video_id": item["id"],
                    "title": snippet.get("title", ""),
                    "published_at": snippet.get("publishedAt"),
                    "actual_start_time": actual_start,
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)),
                    "comment_count": int(stats.get("commentCount", 0)),
                    "duration_seconds": duration_sec,
                    "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
                    # Raw value (e.g. "it", "en-GB"); will be normalized
                    # into the videos.language column by lang_detect.
                    "youtube_language": _yt_lang,
                })
            if on_progress:
                on_progress(min(i + 50, total), total)
        return videos
