from __future__ import annotations

import atexit
import datetime as _dt
import os
import re
import sys
import threading
from collections import defaultdict

import isodate
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# Pre-import here at module load (NOT inside the atexit hook) — supabase
# pulls in httpx which registers its own atexit handlers on first import.
# Doing that during interpreter shutdown raises "can't register atexit
# after shutdown" and silently drops every flush. See the misleading
# "[quota_log] flush failed (table missing?)" symptom this used to print.
from supabase import create_client as _sb_create_client

from .quota_alert import send_ntfy


# YouTube Data API quota resets at midnight Pacific Time (America/Los_Angeles).
# Anchoring our day boundary to PT keeps our buckets aligned with Google's.
try:
    from zoneinfo import ZoneInfo  # py 3.9+
    _PT_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # pragma: no cover — extremely old Python
    _PT_TZ = None  # type: ignore[assignment]


def quota_date_iso() -> str:
    """The current YouTube-quota day, formatted YYYY-MM-DD.
    Today in America/Los_Angeles — NOT in UTC — so our buckets roll
    over at the same instant as Google's daily counter."""
    if _PT_TZ is not None:
        return _dt.datetime.now(_PT_TZ).date().isoformat()
    # Fallback: assume PT = UTC-8 (close enough during PST; off by 1h
    # during DST shoulders but we never had zoneinfo missing in prod).
    return (_dt.datetime.utcnow() - _dt.timedelta(hours=8)).date().isoformat()


# ─── Quota tracking (Approach A from the design doc) ──────────────
# Each YouTubeClient counts units consumed per key during its lifetime.
# A single atexit hook flushes the accumulated counters to the
# youtube_quota_log table as one upsert per (date, key_tail, script).
# The cost table covers the methods we actually call. Anything not in
# the table defaults to 1 unit — that's the official YouTube default
# for cheap list endpoints.
# Reference: https://developers.google.com/youtube/v3/determine_quota_cost
_METHOD_COST: dict[str, int] = {
    "channels.list":      1,
    "playlistItems.list": 1,
    "videos.list":        1,
    "search.list":      100,
}

_QUOTA_RE_RESOURCE = re.compile(r"/youtube/v3/([A-Za-z0-9_]+)")


def _infer_method(req) -> str:
    """Pull a 'channels.list' style identifier out of an HttpRequest by
    looking at its uri and HTTP method. We use it both for cost
    accounting and to attribute calls in the quota log."""
    try:
        uri = getattr(req, "uri", "") or ""
        m = _QUOTA_RE_RESOURCE.search(uri)
        resource = m.group(1) if m else "unknown"
        verb = (getattr(req, "method", "") or "GET").upper()
        action = {"GET": "list", "POST": "insert",
                  "PUT": "update", "DELETE": "delete"}.get(verb, "list")
        return f"{resource}.{action}"
    except Exception:
        return "unknown"


def _detect_script_name() -> str:
    """'streamlit' for Streamlit views, basename of argv[0] for cron
    scripts, 'unknown' as a fallback. Used as a third axis in the
    quota log so we can see WHICH job ate the budget."""
    try:
        # Streamlit sets these on import; cheaper than importing streamlit
        if os.environ.get("STREAMLIT_SERVER_PORT") or "streamlit" in sys.argv[0].lower():
            return "streamlit"
        argv0 = sys.argv[0] if sys.argv else ""
        if argv0:
            base = os.path.basename(argv0)
            # Strip .py for readability
            return base[:-3] if base.endswith(".py") else base
    except Exception:
        pass
    return "unknown"


# Process-wide counters. Keyed by key_tail (last 4 chars), value is a
# small dict the atexit flusher writes out. We carry script name on
# the bucket too so two scripts in the same process (rare, but happens
# in tests) don't collide. We also dedupe by date in case a process
# straddles UTC midnight.
class _QuotaCounters:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[tuple, dict] = {}
        # key: (date_str, key_tail, script)
        # val: {"units":int, "calls":int, "rotations_from":int,
        #       "rotations_to":int, "first_used_at":dt, "last_used_at":dt}

    def _bucket(self, key_tail: str, script: str) -> dict:
        date = quota_date_iso()
        k = (date, key_tail, script)
        b = self._buckets.get(k)
        if b is None:
            now = _dt.datetime.utcnow()
            b = {
                "date": date, "key_tail": key_tail, "script": script,
                "units": 0, "calls": 0,
                "rotations_from": 0, "rotations_to": 0,
                "first_used_at": now, "last_used_at": now,
            }
            self._buckets[k] = b
        return b

    def record_call(self, key_tail: str, script: str, units: int) -> None:
        with self._lock:
            b = self._bucket(key_tail, script)
            b["units"] += units
            b["calls"] += 1
            b["last_used_at"] = _dt.datetime.utcnow()

    def record_rotation(self, from_tail: str, to_tail: str, script: str) -> None:
        with self._lock:
            self._bucket(from_tail, script)["rotations_from"] += 1
            self._bucket(to_tail,   script)["rotations_to"]   += 1

    def snapshot_and_clear(self) -> list[dict]:
        with self._lock:
            rows = list(self._buckets.values())
            self._buckets = {}
            return rows


_COUNTERS = _QuotaCounters()


def _maybe_send_daily_summary(sb, today_pt: str) -> None:
    """If this is the first flush of today (PT), send an ntfy summary
    of yesterday's totals. De-duped via an __alert sentinel row written
    into youtube_quota_log itself — composite-PK conflict makes the
    'I'll send the alert' decision atomic across concurrent processes."""
    yesterday_pt = (_dt.date.fromisoformat(today_pt)
                    - _dt.timedelta(days=1)).isoformat()
    # Try to claim the slot. .insert() (not upsert) — on duplicate PK
    # we lose the race and the other process will send (or already did).
    try:
        sb.table("youtube_quota_log").insert({
            "date": yesterday_pt,
            "key_tail": "__alert",
            "script": "summary",
            "units": 0, "calls": 0,
            "rotations_from": 0, "rotations_to": 0,
        }).execute()
    except Exception:
        return  # already sent (or table missing) — nothing to do

    # We won the slot — aggregate yesterday's real rows and notify.
    try:
        r = (sb.table("youtube_quota_log").select("*")
             .eq("date", yesterday_pt)
             .neq("key_tail", "__alert").execute())
        rows = r.data or []
        if not rows:
            return  # nothing logged yesterday, skip
        from collections import defaultdict
        by_tail = defaultdict(lambda: {"units": 0, "calls": 0})
        for row in rows:
            t = row.get("key_tail")
            by_tail[t]["units"] += int(row.get("units") or 0)
            by_tail[t]["calls"] += int(row.get("calls") or 0)

        # Map key_tail → env var name for the message (only the names
        # that are configured in this process's env)
        env_label: dict[str, str] = {}
        for env_name in ("YOUTUBE_API_KEY", "YOUTUBE_API_KEY_DAILY",
                          "YOUTUBE_API_KEY_HEAVY",
                          "YOUTUBE_API_KEY_INTERACTIVE",
                          "YOUTUBE_API_KEY_BACKUP"):
            v = os.environ.get(env_name, "").strip()
            if v:
                env_label[v[-4:]] = env_name

        total_units = sum(b["units"] for b in by_tail.values())
        total_calls = sum(b["calls"] for b in by_tail.values())
        # Compose plain-text body (ntfy renders newlines)
        lines = [
            f"📊 YT quota summary for {yesterday_pt} (PT)",
            f"Total: {total_units:,} units · {total_calls:,} calls",
            "",
        ]
        # Stable order: by env-var declaration
        order = {tail: i for i, tail in enumerate(env_label.keys())}
        for tail, b in sorted(by_tail.items(),
                                key=lambda kv: (order.get(kv[0], 999),
                                                -kv[1]["units"])):
            label = env_label.get(tail, f"unknown(…{tail})")
            pct = min(100, int(round(b["units"] * 100 / 10000)))
            lines.append(f"• {label}: {b['units']:,} u / {pct}% · {b['calls']:,} calls")
        send_ntfy(
            "\n".join(lines),
            title=f"YT quota - {yesterday_pt}",
            priority="default",
            tags="bar_chart,youtube",
        )
    except Exception as e:
        try:
            print(f"[quota_log] daily-summary send failed: {e}", file=sys.stderr)
        except Exception:
            pass


def _flush_quota_log() -> None:
    """atexit hook: best-effort flush of accumulated counters to the
    youtube_quota_log table. Never raises — alerting must not break
    process shutdown. Skipped silently if Supabase env or the table
    isn't available (e.g. pre-migration)."""
    rows = _COUNTERS.snapshot_and_clear()
    if not rows:
        return
    try:
        url = os.environ.get("SUPABASE_URL", "").strip()
        # Prefer service-role key (bypasses RLS) but accept anon for
        # local dev — the table has a permissive write policy in v22.
        key = (os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
               or os.environ.get("SUPABASE_KEY", "").strip())
        if not url or not key:
            return
        sb = _sb_create_client(url, key)

        # If this flush spans into a new PT day relative to yesterday's
        # data, fire one summary notification (atomic via PK conflict).
        _maybe_send_daily_summary(sb, quota_date_iso())

        # We upsert by (date, key_tail, script). The per-process bucket
        # already aggregates within a run, but multiple processes will
        # write to the same composite key — so we read-modify-write to
        # accumulate across runs in the same day.
        for r in rows:
            existing = sb.table("youtube_quota_log").select("*").match({
                "date": r["date"],
                "key_tail": r["key_tail"],
                "script": r["script"],
            }).execute()
            prev = (existing.data or [{}])[0]
            payload = {
                "date":  r["date"],
                "key_tail": r["key_tail"],
                "script": r["script"],
                "units":           int(prev.get("units", 0)) + r["units"],
                "calls":           int(prev.get("calls", 0)) + r["calls"],
                "rotations_from":  int(prev.get("rotations_from", 0)) + r["rotations_from"],
                "rotations_to":    int(prev.get("rotations_to", 0)) + r["rotations_to"],
                "first_used_at":   prev.get("first_used_at") or r["first_used_at"].isoformat(),
                "last_used_at":    r["last_used_at"].isoformat(),
            }
            sb.table("youtube_quota_log").upsert(payload).execute()
    except Exception as e:
        # Table may not exist yet (pre-migration) — don't crash exit.
        try:
            print(f"[quota_log] flush failed (table missing?): {e}",
                  file=sys.stderr)
        except Exception:
            pass


atexit.register(_flush_quota_log)


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
        self._script: str = _detect_script_name()
        self.youtube = build(
            "youtube", "v3", developerKey=self._keys[self._idx])

    def _current_tail(self) -> str:
        return self._keys[self._idx][-4:]

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
                    title="YTFT quota - ALL KEYS DEAD",
                    priority="urgent",
                    tags="rotating_light,youtube",
                )
            return False
        old_tail6 = self._keys[self._idx][-6:]
        old_tail4 = self._keys[self._idx][-4:]
        self._idx += 1
        new_tail6 = self._keys[self._idx][-6:]
        new_tail4 = self._keys[self._idx][-4:]
        old_tail, new_tail = old_tail6, new_tail6
        # Quota-log: record rotation on both sides
        _COUNTERS.record_rotation(old_tail4, new_tail4, self._script)
        # One alert per (key_index, rotation) so reruns don't spam
        marker = f"rotated_to_{self._idx}"
        if marker not in self._alerts_fired:
            self._alerts_fired.add(marker)
            send_ntfy(
                f"⚠️ YouTube API key …{old_tail} hit quota — "
                f"switched to backup …{new_tail}",
                title="YTFT quota - key rotated",
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
            req = make_request(self.youtube)
            method = _infer_method(req)
            cost = _METHOD_COST.get(method, 1)
            try:
                resp = req.execute()
            except HttpError as e:
                if _is_quota_exceeded(e) and self._rotate_key(e):
                    continue
                raise
            # Record only on success — failed calls don't consume quota
            # (HttpError responses are billed but very rare in practice).
            _COUNTERS.record_call(self._current_tail(), self._script, cost)
            return resp

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

    def _parse_video_item(self, item: dict) -> dict | None:
        """Parse a single videos.list() item dict into our flat schema.
        Returns None if the item lacks an id (deleted/private/region-locked
        or malformed). Extracted so retry passes can reuse the parsing."""
        if not item.get("id"):
            return None
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
        actual_start = (
            live_details.get("actualStartTime")
            or live_details.get("scheduledStartTime")
            or None
        )
        _yt_lang = (snippet.get("defaultAudioLanguage")
                    or snippet.get("defaultLanguage")
                    or None)
        return {
            "youtube_video_id": item["id"],
            "title": snippet.get("title", ""),
            "description": snippet.get("description", "") or "",
            "published_at": snippet.get("publishedAt"),
            "actual_start_time": actual_start,
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
            "duration_seconds": duration_sec,
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            "youtube_language": _yt_lang,
        }

    def get_video_details(
        self,
        video_ids: list[str],
        on_progress: callable = None,
        retries: int = 1,
    ) -> list[dict]:
        """Fetch video details in 50-id batches.

        When YouTube returns fewer items than requested in a batch (transient
        API hiccups, rate-limit weirdness, intermittent server errors), we
        retry the missing IDs up to `retries` times. Structurally
        unavailable IDs (deleted/private/scheduled/region-locked) will
        keep returning empty and quietly drop out — that's expected.

        retries=1 is the default and lifts per-night coverage from
        ~91% to ~99%; set retries=0 to disable.
        """
        videos: list[dict] = []
        returned_ids: set[str] = set()
        total = len(video_ids)

        def _fetch_batch(batch: list[str]) -> None:
            _ids = ",".join(batch)
            resp = self._call(lambda yt: yt.videos().list(
                part="snippet,statistics,contentDetails,liveStreamingDetails",
                id=_ids,
            ))
            for item in resp.get("items", []):
                parsed = self._parse_video_item(item)
                if parsed is None:
                    continue
                if parsed["youtube_video_id"] in returned_ids:
                    continue
                returned_ids.add(parsed["youtube_video_id"])
                videos.append(parsed)

        for i in range(0, total, 50):
            batch = video_ids[i : i + 50]
            _fetch_batch(batch)
            if on_progress:
                on_progress(min(i + 50, total), total)

        # Retry pass(es) for IDs that didn't come back. Most of these are
        # structurally unavailable and will stay missing; a few are
        # transient and recover on a second look.
        for _attempt in range(retries):
            missing = [vid for vid in video_ids if vid not in returned_ids]
            if not missing:
                break
            recovered_before = len(returned_ids)
            for j in range(0, len(missing), 50):
                _fetch_batch(missing[j : j + 50])
            # If a retry pass recovered nothing, no point in further attempts.
            if len(returned_ids) == recovered_before:
                break

        return videos

