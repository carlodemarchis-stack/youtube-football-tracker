from __future__ import annotations

import json
import anthropic


MODEL = "claude-sonnet-4-20250514"


def _format_video_lines(videos: list[dict]) -> str:
    """Build compact video list for the prompt."""
    lines = []
    for i, v in enumerate(videos, 1):
        date = (v.get("published_at") or "")[:10]
        dur = v.get("duration_seconds", 0)
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "?"
        fmt = (v.get("format") or "long").lower()
        lines.append(
            f"{i}. \"{v.get('title', '')}\" | views: {v.get('view_count', 0):,} | "
            f"likes: {v.get('like_count', 0):,} | date: {date} | duration: {dur_str} | format: {fmt}"
        )
    return "\n".join(lines)


def analyze_channel_videos(
    api_key: str,
    videos: list[dict],
    channel_name: str,
    season_videos: list[dict] | None = None,
    channel_stats: dict | None = None,
) -> dict:
    """Use Claude to analyze a channel's videos and extract insights."""

    top100_text = _format_video_lines(videos)

    # Season videos section
    season_section = ""
    if season_videos:
        season_text = _format_video_lines(season_videos[:50])
        season_section = f"""

--- SEASON 2025/26 VIDEOS (up to 50, by views) ---
These are videos published since August 2025 (current season):
{season_text}
"""

    # Channel stats section
    stats_section = ""
    if channel_stats:
        subs = channel_stats.get("subscriber_count", 0) or 0
        views = channel_stats.get("total_views", 0) or 0
        vcount = channel_stats.get("video_count", 0) or 0
        sv = channel_stats.get("season_views", 0) or 0
        s_long = channel_stats.get("season_long_videos", 0) or 0
        s_short = channel_stats.get("season_short_videos", 0) or 0
        s_live = channel_stats.get("season_live_videos", 0) or 0
        s_long_v = channel_stats.get("season_long_views", 0) or 0
        s_short_v = channel_stats.get("season_short_views", 0) or 0
        s_live_v = channel_stats.get("season_live_views", 0) or 0
        stats_section = f"""

--- CHANNEL STATS ---
Subscribers: {subs:,}
Total views (all time): {views:,}
Total videos: {vcount:,}
Season views (since Aug 2025): {sv:,}
Season breakdown: {s_long} long ({s_long_v:,} views) | {s_short} shorts ({s_short_v:,} views) | {s_live} live ({s_live_v:,} views)
"""

    prompt = f"""Analyze the YouTube channel "{channel_name}".
{stats_section}
--- TOP 100 ALL-TIME VIDEOS (by views) ---
{top100_text}
{season_section}
Return a JSON object (no markdown, no code fences, just raw JSON) with these keys:

1. "summary": 2-3 sentence overall analysis of what makes this channel's content successful.

2. "main_issues": array of 3-5 strategic issues or weaknesses you identify.
   Each: {{"issue": str, "severity": "high"|"medium"|"low", "detail": str, "recommendation": str}}
   Be specific and actionable. Examples: over-reliance on one player, declining upload frequency,
   poor Shorts strategy, seasonal content gaps, low engagement despite high views, etc.
   Sort by severity (high first).

3. "key_figures": array of objects identifying people/players who appear in multiple video titles.
   Each: {{"name": str, "video_count": int, "total_views": int, "avg_views": int, "notable_videos": [str]}}
   Sort by total_views descending.

4. "themes": array of content themes you identify (be specific, not generic).
   Each: {{"theme": str, "count": int, "avg_views": int, "examples": [str]}}
   Sort by count descending.

5. "surprise_hits": array of 3-5 videos that are unexpectedly popular for their type.
   Each: {{"title": str, "views": int, "why_surprising": str}}

6. "era_analysis": array grouping TOP 100 videos by time period.
   Each: {{"period": str, "video_count": int, "avg_views": int, "trend": str}}

7. "format_insights": object analyzing video length patterns across TOP 100.
   {{"shorts_count": int, "shorts_avg_views": int, "long_count": int, "long_avg_views": int, "live_count": int, "live_avg_views": int, "optimal_duration": str, "observation": str}}

8. "custom_themes": Classify EVERY TOP 100 video into exactly one category SPECIFIC to this channel.

   CLASSIFICATION PRIORITY (apply in this order):
   a) FORMAT FIRST: If a video is clearly a specific format (challenge, quiz, reaction, behind-the-scenes, vlog, training session, press conference, anthem/music, tutorial), assign it to a FORMAT-based theme.
   b) PLAYER THEME only if dominant: Only create a player-specific theme if that player appears in 8+ videos in the top 100 AND is the clear subject of the video.
   c) CONTENT TYPE for the rest: Match highlights, transfer announcements, fan content, compilations, etc.

   Do NOT use generic names like "Other Content" or "Miscellaneous". Every theme must be descriptive.
   Only create categories with 3+ videos.
   Return: Array of {{"theme": str, "count": int, "avg_views": int}}
   Sort by count descending.

9. "video_themes": Map EVERY TOP 100 video rank (1-based) to its custom_theme name.
   Return as object: {{"1": "theme name", "2": "theme name", ...}} for all {len(videos)} videos.
   Use the EXACT same theme names as in custom_themes above.
   IMPORTANT: The counts here MUST match custom_themes.

"""

    if season_videos:
        prompt += f"""10. "season_overview": High-level season (2025/26) summary. Object with:
   {{"total_videos": int, "total_views": int, "avg_views_per_video": int,
    "comparison_to_alltime": str, "momentum": "rising"|"stable"|"declining",
    "season_insight": str}}
   "comparison_to_alltime" should compare season avg views to top-100 avg views and explain what changed.
   "season_insight" is a 2-sentence strategic takeaway about current direction.

11. "season_themes": Content themes in season videos (same logic as top-100 themes but for season only).
   Array of {{"theme": str, "count": int, "avg_views": int, "examples": [str]}}
   Sort by count descending. Highlight themes that are NEW vs top 100 (emerging directions).

12. "season_key_figures": People/players featured in season video titles.
   Each: {{"name": str, "video_count": int, "total_views": int, "avg_views": int}}
   Sort by total_views descending.

13. "season_best_performers": The 5 best-performing season videos (by views).
   Each: {{"title": str, "views": int, "format": str, "why_it_worked": str}}

14. "season_format_insights": Format breakdown for season videos.
   {{"shorts_count": int, "shorts_avg_views": int, "long_count": int, "long_avg_views": int,
    "live_count": int, "live_avg_views": int, "format_shift": str}}
   "format_shift" compares the season format mix to the top-100 format mix — what changed?

15. "season_monthly": Monthly breakdown of season output.
   Array of {{"month": str, "video_count": int, "total_views": int, "avg_views": int, "note": str}}
   "note" is a short observation about that month (e.g. "transfer window spike", "mid-season dip").
   Sort chronologically.

"""

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # Strip markdown code fences
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[1]
    if response_text.endswith("```"):
        response_text = response_text.rsplit("```", 1)[0]
    response_text = response_text.strip()

    # Try parsing, if it fails try to fix common issues
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        # Truncated JSON - try to find the last valid closing brace
        for i in range(len(response_text) - 1, 0, -1):
            if response_text[i] == '}':
                try:
                    return json.loads(response_text[:i + 1])
                except json.JSONDecodeError:
                    continue
        raise
