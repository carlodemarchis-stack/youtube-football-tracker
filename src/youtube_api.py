from __future__ import annotations

import isodate
from googleapiclient.discovery import build


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

    def __init__(self, api_key: str):
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def resolve_handle(self, handle: str) -> dict | None:
        """Resolve a YouTube handle (e.g. @sscnapoli) to channel info."""
        handle = handle.strip().lstrip("@")
        resp = self.youtube.channels().list(
            part="snippet,statistics,contentDetails",
            forHandle=handle,
        ).execute()
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
        resp = self.youtube.channels().list(
            part="snippet,statistics,contentDetails",
            id=channel_id,
        ).execute()
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
            resp = self.youtube.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=min(50, max_results - len(video_ids)),
                pageToken=next_page,
            ).execute()
            for item in resp.get("items", []):
                video_ids.append(item["contentDetails"]["videoId"])
            next_page = resp.get("nextPageToken")
            if not next_page:
                break
        return video_ids

    def get_video_ids_since(self, uploads_playlist_id: str, since: str) -> list[str]:
        """Fetch video IDs from uploads playlist published on or after `since` (ISO date, e.g. '2025-08-01').
        Uses playlistItems (1 unit/call) and stops when hitting older videos."""
        video_ids = []
        next_page = None
        while True:
            resp = self.youtube.playlistItems().list(
                part="contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=50,
                pageToken=next_page,
            ).execute()
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
                    resp = self.youtube.playlistItems().list(
                        part="contentDetails",
                        playlistId=pl_id,
                        maxResults=50,
                        pageToken=next_page,
                    ).execute()
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
                resp = self.youtube.playlistItems().list(
                    part="contentDetails", playlistId=pl_id, maxResults=1,
                ).execute()
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
                    resp = self.youtube.playlistItems().list(
                        part="contentDetails",
                        playlistId=pl_id,
                        maxResults=50,
                        pageToken=next_page,
                    ).execute()
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
            resp = self.youtube.search().list(
                part="id",
                channelId=channel_id,
                type="video",
                order="viewCount",
                maxResults=min(50, max_results - len(video_ids)),
                pageToken=next_page,
            ).execute()
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
            resp = self.youtube.videos().list(
                part="snippet,statistics,contentDetails,liveStreamingDetails",
                id=",".join(batch),
            ).execute()
            for item in resp.get("items", []):
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
                })
            if on_progress:
                on_progress(min(i + 50, total), total)
        return videos
