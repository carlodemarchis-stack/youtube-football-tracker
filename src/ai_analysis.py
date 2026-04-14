from __future__ import annotations

import json
import anthropic


MODEL = "claude-sonnet-4-20250514"


def analyze_channel_videos(api_key: str, videos: list[dict], channel_name: str) -> dict:
    """Use Claude to analyze a channel's top videos and extract insights."""

    # Build compact video list for the prompt
    video_lines = []
    for i, v in enumerate(videos, 1):
        date = (v.get("published_at") or "")[:10]
        dur = v.get("duration_seconds", 0)
        dur_str = f"{dur // 60}:{dur % 60:02d}" if dur else "?"
        video_lines.append(
            f"{i}. \"{v.get('title', '')}\" | views: {v.get('view_count', 0):,} | "
            f"likes: {v.get('like_count', 0):,} | date: {date} | duration: {dur_str}"
        )
    video_text = "\n".join(video_lines)

    prompt = f"""Analyze the top {len(videos)} most popular YouTube videos for the channel "{channel_name}".

Here are the videos ranked by views:
{video_text}

Return a JSON object (no markdown, no code fences, just raw JSON) with these keys:

1. "key_figures": array of objects identifying people/players who appear in multiple video titles.
   Each: {{"name": str, "video_count": int, "total_views": int, "avg_views": int, "notable_videos": [str]}}
   Sort by total_views descending.

2. "themes": array of content themes you identify (be specific, not generic).
   Each: {{"theme": str, "count": int, "avg_views": int, "examples": [str]}}
   Sort by count descending.

3. "surprise_hits": array of 3-5 videos that are unexpectedly popular for their type.
   Each: {{"title": str, "views": int, "why_surprising": str}}

4. "era_analysis": array grouping videos by time period.
   Each: {{"period": str, "video_count": int, "avg_views": int, "trend": str}}

5. "format_insights": object analyzing video length patterns.
   {{"shorts_count": int, "shorts_avg_views": int, "long_count": int, "long_avg_views": int, "optimal_duration": str, "observation": str}}
   (shorts = under 60 seconds)

6. "summary": 2-3 sentence overall analysis of what makes this channel's top content successful.

7. "custom_themes": Classify EVERY video into exactly one category SPECIFIC to this channel.

   CLASSIFICATION PRIORITY (apply in this order):
   a) FORMAT FIRST: If a video is clearly a specific format (challenge, quiz, reaction, behind-the-scenes, vlog, training session, press conference, anthem/music, tutorial), assign it to a FORMAT-based theme. E.g. "Challenges & Games", "Behind the Scenes", "Music & Anthems".
   b) PLAYER THEME only if dominant: Only create a player-specific theme (e.g. "Ronaldo Era") if that player appears in 8+ videos in the top 100 AND is the clear subject of the video (not just mentioned). A video titled "Ronaldo scores amazing goal" = Ronaldo theme. A video titled "Top 10 goals" that includes a Ronaldo goal = NOT Ronaldo theme, it's a compilation.
   c) CONTENT TYPE for the rest: Match highlights, transfer announcements, fan content, compilations, etc.

   Do NOT use generic names like "Other Content" or "Miscellaneous". Every theme must be descriptive.
   Only create categories with 3+ videos.
   Return: Array of {{"theme": str, "count": int, "avg_views": int}}
   Sort by count descending.

8. "video_themes": Map EVERY video rank (1-based) to its custom_theme name.
   Return as object: {{"1": "theme name", "2": "theme name", ...}} for all {len(videos)} videos.
   Use the EXACT same theme names as in custom_themes above.
   IMPORTANT: The counts here MUST match custom_themes. Build video_themes first, then derive custom_themes counts from it.
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
