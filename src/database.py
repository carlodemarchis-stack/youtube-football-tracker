from __future__ import annotations

import time
from datetime import datetime, timezone

from supabase import create_client, Client


def _retry(fn, retries=3, delay=2):
    """Retry a function on connection errors."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(delay * (attempt + 1))


_PAGE_SIZE = 1000  # Supabase PGRST_MAX_ROWS default


def _fetch_all(query_builder):
    """Paginate through Supabase results to bypass the 1000-row server limit."""
    all_rows = []
    offset = 0
    while True:
        resp = query_builder.range(offset, offset + _PAGE_SIZE - 1).execute()
        rows = resp.data or []
        all_rows.extend(rows)
        if len(rows) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return all_rows


class Database:
    def __init__(self, url: str, key: str):
        self.client: Client = create_client(url, key)

    # ── Channels ──────────────────────────────────────────────

    def upsert_channel(self, channel_data: dict) -> dict:
        row = {
            "youtube_channel_id": channel_data["youtube_channel_id"],
            "name": channel_data["name"],
            "handle": channel_data.get("handle", ""),
            "subscriber_count": channel_data.get("subscriber_count", 0),
            "total_views": channel_data.get("total_views", 0),
            "video_count": channel_data.get("video_count", 0),
            "last_fetched": datetime.now(timezone.utc).isoformat(),
        }
        if channel_data.get("launched_at"):
            row["launched_at"] = channel_data["launched_at"]
        if "long_form_count" in channel_data:
            row["long_form_count"] = channel_data["long_form_count"]
        if "shorts_count" in channel_data:
            row["shorts_count"] = channel_data["shorts_count"]
        if "live_count" in channel_data:
            row["live_count"] = channel_data["live_count"]
        resp = (
            self.client.table("channels")
            .upsert(row, on_conflict="youtube_channel_id")
            .execute()
        )
        return resp.data[0] if resp.data else {}

    def snapshot_channel(self, channel_db_id: str, stats: dict):
        """Append a daily snapshot row. Unique on (channel_id, captured_date)
        so re-runs on the same day no-op via upsert."""
        today = datetime.now(timezone.utc).date().isoformat()
        row = {
            "channel_id": channel_db_id,
            "captured_date": today,
            "subscriber_count": stats.get("subscriber_count", 0),
            "total_views": stats.get("total_views", 0),
            "video_count": stats.get("video_count", 0),
            "long_form_count": stats.get("long_form_count", 0),
            "shorts_count": stats.get("shorts_count", 0),
            "live_count": stats.get("live_count", 0),
        }
        try:
            self.client.table("channel_snapshots").upsert(
                row, on_conflict="channel_id,captured_date"
            ).execute()
        except Exception as e:
            # Don't fail the refresh if snapshot table isn't there yet
            print(f"snapshot_channel skipped: {e}")

    # ── Video snapshots ──────────────────────────────────────
    def snapshot_videos_batch(self, rows: list[dict]) -> int:
        """Bulk upsert rows into video_snapshots.
        rows: list of dicts with keys video_id, view_count, like_count, comment_count.
        Returns number of rows upserted. Fails silently if table missing."""
        if not rows:
            return 0
        today = datetime.now(timezone.utc).date().isoformat()
        payload = [{
            "video_id": r["video_id"],
            "captured_date": today,
            "view_count": int(r.get("view_count", 0) or 0),
            "like_count": int(r.get("like_count", 0) or 0),
            "comment_count": int(r.get("comment_count", 0) or 0),
        } for r in rows]
        total = 0
        try:
            for i in range(0, len(payload), 500):
                batch = payload[i:i+500]
                self.client.table("video_snapshots").upsert(
                    batch, on_conflict="video_id,captured_date"
                ).execute()
                total += len(batch)
        except Exception as e:
            print(f"snapshot_videos_batch skipped: {e}")
        return total

    def get_video_snapshots_for_date(self, captured_date: str) -> list[dict]:
        resp = (
            self.client.table("video_snapshots")
            .select("*")
            .eq("captured_date", captured_date)
            .execute()
        )
        return resp.data or []

    def get_video_snapshots_range(self, since_date: str, until_date: str | None = None) -> list[dict]:
        q = (
            self.client.table("video_snapshots")
            .select("*")
            .gte("captured_date", since_date)
        )
        if until_date:
            q = q.lte("captured_date", until_date)
        resp = q.order("captured_date", desc=False).execute()
        return resp.data or []

    def get_season_video_rows(self, since: str = "2025-08-01") -> list[dict]:
        """All videos with published_at >= since (minimal columns).
        Used by daily cron to decide which videos to snapshot.
        Paginates to bypass Supabase's default 1000-row limit."""
        all_rows: list[dict] = []
        page_size = 1000
        offset = 0
        while True:
            resp = (
                self.client.table("videos")
                .select("id,youtube_video_id,channel_id,published_at")
                .gte("published_at", since)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = resp.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return all_rows

    def get_all_video_rows(self) -> list[dict]:
        """Every video in the DB (minimal cols). Paginated."""
        all_rows: list[dict] = []
        page_size = 1000
        offset = 0
        while True:
            resp = (
                self.client.table("videos")
                .select("id,youtube_video_id")
                .range(offset, offset + page_size - 1)
                .execute()
            )
            batch = resp.data or []
            all_rows.extend(batch)
            if len(batch) < page_size:
                break
            offset += page_size
        return all_rows

    def get_all_snapshots(self, since_date: str | None = None) -> list[dict]:
        """All snapshots, optionally from a given ISO date (YYYY-MM-DD)."""
        q = self.client.table("channel_snapshots").select("*")
        if since_date:
            q = q.gte("captured_date", since_date)
        resp = q.order("captured_date", desc=False).execute()
        return resp.data or []

    def get_channel_snapshots(self, channel_db_id: str, limit: int = 365) -> list[dict]:
        resp = (
            self.client.table("channel_snapshots")
            .select("*")
            .eq("channel_id", channel_db_id)
            .order("captured_date", desc=False)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    def get_all_channels(self) -> list[dict]:
        resp = self.client.table("channels").select("*").execute()
        return resp.data or []

    def get_channel_by_youtube_id(self, yt_id: str) -> dict | None:
        resp = (
            self.client.table("channels")
            .select("*")
            .eq("youtube_channel_id", yt_id)
            .execute()
        )
        return resp.data[0] if resp.data else None

    # ── Videos ────────────────────────────────────────────────

    def get_known_video_ids(self, channel_db_id: str) -> set[str]:
        """Return set of youtube_video_id already stored for this channel."""
        resp = (
            self.client.table("videos")
            .select("youtube_video_id")
            .eq("channel_id", channel_db_id)
            .execute()
        )
        return {r["youtube_video_id"] for r in (resp.data or [])}

    def get_top_video_ids_for_channel(self, channel_db_id: str, limit: int = 100) -> list[str]:
        """Return youtube_video_ids of the current top N by views for a channel."""
        resp = (
            self.client.table("videos")
            .select("youtube_video_id")
            .eq("channel_id", channel_db_id)
            .order("view_count", desc=True)
            .limit(limit)
            .execute()
        )
        return [r["youtube_video_id"] for r in (resp.data or [])]

    def upsert_videos(self, videos: list[dict], channel_db_id: str):
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for v in videos:
            row = {
                "youtube_video_id": v["youtube_video_id"],
                "channel_id": channel_db_id,
                "title": v.get("title", ""),
                "published_at": v.get("published_at"),
                "view_count": v.get("view_count", 0),
                "like_count": v.get("like_count", 0),
                "comment_count": v.get("comment_count", 0),
                "duration_seconds": v.get("duration_seconds", 0),
                "category": v.get("category", "Other"),
                "thumbnail_url": v.get("thumbnail_url", ""),
                "last_updated": now,
            }
            if "format" in v:
                row["format"] = v["format"]
            rows.append(row)
        # Batch upsert in chunks of 50
        for i in range(0, len(rows), 50):
            batch = rows[i : i + 50]
            _retry(lambda b=batch: self.client.table("videos").upsert(
                b, on_conflict="youtube_video_id"
            ).execute())

    # ── Video Catalog (all videos, compact) ─────────────────────

    def upsert_catalog_batch(self, videos: list[dict], channel_db_id: str):
        """Bulk upsert compact video records to video_catalog."""
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for v in videos:
            rows.append({
                "youtube_video_id": v["youtube_video_id"],
                "channel_id": channel_db_id,
                "title": v.get("title", ""),
                "published_at": v.get("published_at"),
                "duration_seconds": v.get("duration_seconds", 0),
                "view_count": v.get("view_count", 0),
                "last_updated": now,
            })
        for i in range(0, len(rows), 50):
            batch = rows[i : i + 50]
            _retry(lambda b=batch: self.client.table("video_catalog").upsert(
                b, on_conflict="youtube_video_id"
            ).execute())

    def get_catalog_video_ids(self, channel_db_id: str) -> set[str]:
        """Return set of youtube_video_ids already in catalog for this channel."""
        query = (
            self.client.table("video_catalog")
            .select("youtube_video_id")
            .eq("channel_id", channel_db_id)
        )
        return {r["youtube_video_id"] for r in _fetch_all(query)}

    def get_catalog_by_channel(self, channel_db_id: str) -> list[dict]:
        query = (
            self.client.table("video_catalog")
            .select("*")
            .eq("channel_id", channel_db_id)
            .order("view_count", desc=True)
        )
        return _fetch_all(query)

    def get_full_catalog(self) -> list[dict]:
        query = (
            self.client.table("video_catalog")
            .select("*, channels(name, handle)")
            .order("view_count", desc=True)
        )
        return _fetch_all(query)

    # ── Videos (top N, full detail) ───────────────────────────

    def get_top_videos(self, limit: int = 100, channel_id: str | None = None) -> list[dict]:
        query = (
            self.client.table("videos")
            .select("*, channels(name, handle)")
            .order("view_count", desc=True)
            .limit(limit)
        )
        if channel_id:
            query = query.eq("channel_id", channel_id)
        resp = query.execute()
        return resp.data or []

    def get_videos_by_channel(self, channel_id: str) -> list[dict]:
        query = (
            self.client.table("videos")
            .select("*")
            .eq("channel_id", channel_id)
            .order("view_count", desc=True)
        )
        return _fetch_all(query)

    def get_season_videos_by_channel(self, channel_id: str, since: str = "2025-08-01") -> list[dict]:
        """Get all videos for a channel published on or after `since`, ordered by views desc."""
        query = (
            self.client.table("videos")
            .select("*")
            .eq("channel_id", channel_id)
            .gte("published_at", since)
            .order("view_count", desc=True)
        )
        return _fetch_all(query)

    def get_all_videos(self) -> list[dict]:
        query = (
            self.client.table("videos")
            .select("*, channels(name, handle)")
            .order("view_count", desc=True)
        )
        return _fetch_all(query)

    # ── Channel Insights (AI) ────────────────────────────────

    def save_insights(self, channel_id: str, insights: dict, model: str = "claude-sonnet-4-20250514"):
        import json
        row = {
            "channel_id": channel_id,
            "insights_json": json.dumps(insights),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": model,
        }
        self.client.table("channel_insights").upsert(
            row, on_conflict="channel_id"
        ).execute()

    def get_insights(self, channel_id: str) -> dict | None:
        resp = (
            self.client.table("channel_insights")
            .select("*")
            .eq("channel_id", channel_id)
            .execute()
        )
        if not resp.data:
            return None
        row = resp.data[0]
        if isinstance(row.get("insights_json"), str):
            import json
            row["insights_json"] = json.loads(row["insights_json"])
        return row

    # ── Channel Management ────────────────────────────────────

    def add_channel(self, data: dict) -> dict:
        row = {
            "youtube_channel_id": data["youtube_channel_id"],
            "name": data["name"],
            "handle": data.get("handle", ""),
            "sport": data.get("sport", "Football"),
            "entity_type": data.get("entity_type", "Club"),
            "country": data.get("country", ""),
            "is_active": data.get("is_active", True),
            "color": data.get("color", ""),
            "color2": data.get("color2", ""),
        }
        resp = (
            self.client.table("channels")
            .upsert(row, on_conflict="youtube_channel_id")
            .execute()
        )
        return resp.data[0] if resp.data else {}

    def update_channel(self, channel_id: str, data: dict) -> dict:
        resp = (
            self.client.table("channels")
            .update(data)
            .eq("id", channel_id)
            .execute()
        )
        return resp.data[0] if resp.data else {}

    def delete_channel(self, channel_id: str):
        self.client.table("channels").delete().eq("id", channel_id).execute()

    def get_active_channels(self) -> list[dict]:
        resp = (
            self.client.table("channels")
            .select("*")
            .eq("is_active", True)
            .execute()
        )
        return resp.data or []

    # ── User Profiles ─────────────────────────────────────────

    def upsert_user_profile(self, email: str, name: str, role: str = "viewer") -> dict:
        row = {
            "user_id": email,
            "email": email,
            "display_name": name,
            "role": role,
            "last_login": datetime.now(timezone.utc).isoformat(),
        }
        resp = (
            self.client.table("user_profiles")
            .upsert(row, on_conflict="email")
            .execute()
        )
        return resp.data[0] if resp.data else {}

    def get_user_profile(self, email: str) -> dict | None:
        resp = (
            self.client.table("user_profiles")
            .select("*")
            .eq("email", email)
            .execute()
        )
        return resp.data[0] if resp.data else None

    def set_user_role(self, email: str, role: str):
        self.client.table("user_profiles").update({"role": role}).eq("email", email).execute()

    def get_all_users(self) -> list[dict]:
        resp = (
            self.client.table("user_profiles")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        return resp.data or []

    # ── AI Usage Tracking ──────────────────────────────────────

    def log_ai_usage(self, email: str, input_tokens: int, output_tokens: int, model: str):
        self.client.table("ai_usage").insert({
            "user_email": email,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": model,
        }).execute()
        # Update running total on user profile
        profile = self.get_user_profile(email)
        if profile:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            reset_date = (profile.get("ai_budget_reset") or "")[:10]
            if reset_date != today:
                # New day — reset counter
                self.client.table("user_profiles").update({
                    "ai_tokens_used": input_tokens + output_tokens,
                    "ai_budget_reset": today,
                }).eq("email", email).execute()
            else:
                new_total = (profile.get("ai_tokens_used") or 0) + input_tokens + output_tokens
                self.client.table("user_profiles").update({
                    "ai_tokens_used": new_total,
                }).eq("email", email).execute()

    def get_ai_budget(self, email: str) -> tuple[int, int]:
        """Return (tokens_used_today, daily_budget). Budget 0 = unlimited."""
        profile = self.get_user_profile(email)
        if not profile:
            return 0, 50000
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        reset_date = (profile.get("ai_budget_reset") or "")[:10]
        used = profile.get("ai_tokens_used", 0) if reset_date == today else 0
        budget = profile.get("ai_token_budget", 50000)
        return used, budget

    # ── Fetch History ─────────────────────────────────────────

    def log_fetch(self, channels_updated: int, videos_fetched: int, status: str = "success", error_message: str = ""):
        self.client.table("fetch_history").insert({
            "channels_updated": channels_updated,
            "videos_fetched": videos_fetched,
            "status": status,
            "error_message": error_message,
        }).execute()

    def get_fetch_history(self, limit: int = 20) -> list[dict]:
        resp = (
            self.client.table("fetch_history")
            .select("*")
            .order("fetched_at", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []
