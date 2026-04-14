from __future__ import annotations

import re
from datetime import datetime, timezone


CHANNEL_PALETTE = [
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
    "#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF",
]


def get_channel_colors(channel_names: list[str]) -> dict[str, str]:
    """Return a consistent color map for channel names."""
    return {name: CHANNEL_PALETTE[i % len(CHANNEL_PALETTE)] for i, name in enumerate(sorted(channel_names))}


def fmt_num(n: int | float) -> str:
    """Format numbers: 1.2B, 78.4M, 14.5K, or 999."""
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

import pandas as pd


# ── Theme Detection ───────────────────────────────────────────

THEME_PATTERNS = {
    "Match Highlights": r"highlight|gol|goal|sintesi|recap|match|partita|vs\.?|derby",
    "Goals & Skills": r"best goal|top goal|skill|dribbl|trick|compilation",
    "Behind the Scenes": r"behind the scene|backstage|dietro le quinte|tunnel cam|vlog",
    "Training": r"training|allenamento|practice|session|warm.?up",
    "Press Conference": r"press conference|conferenza|presser|pre.?match|post.?match.*conf",
    "Transfer News": r"transfer|mercato|signing|welcome|ufficiale|official",
    "Interviews": r"interview|intervista|exclusive|talks|speaks",
    "Matchday": r"matchday|gameday|giornata|live|lineup|starting",
}


def detect_theme(title: str) -> str:
    title_lower = title.lower()
    for theme, pattern in THEME_PATTERNS.items():
        if re.search(pattern, title_lower):
            return theme
    return "Other"


def classify_videos(videos: list[dict]) -> list[dict]:
    for v in videos:
        v["category"] = detect_theme(v.get("title", ""))
    return videos


# ── Stats Computation ─────────────────────────────────────────

def compute_tier_stats(df: pd.DataFrame, current_year: int | None = None) -> dict:
    if current_year is None:
        current_year = datetime.now(timezone.utc).year

    if df.empty:
        return {"top_10": {}, "top_50": {}, "top_100": {}, "current_year": {}}

    df = df.copy()
    if "published_at" in df.columns:
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
        now = pd.Timestamp.now(tz="UTC")
        df["age_days"] = (now - df["published_at"]).dt.days

    def tier_summary(subset: pd.DataFrame) -> dict:
        result = {
            "count": len(subset),
            "avg_views": int(subset["view_count"].mean()) if len(subset) > 0 else 0,
            "avg_likes": int(subset["like_count"].mean()) if len(subset) > 0 else 0,
            "avg_comments": int(subset["comment_count"].mean()) if len(subset) > 0 else 0,
        }
        if "age_days" in subset.columns and len(subset) > 0:
            result["avg_age_days"] = int(subset["age_days"].mean())
            result["avg_age_years"] = round(result["avg_age_days"] / 365.25, 1)
        return result

    stats = {
        "top_10": tier_summary(df.head(10)),
        "top_50": tier_summary(df.head(50)),
        "top_100": tier_summary(df.head(100)),
    }

    if "published_at" in df.columns:
        current_year_videos = df[df["published_at"].dt.year == current_year]
        current_year_in_top100 = df.head(100)
        current_year_in_top100 = current_year_in_top100[
            current_year_in_top100["published_at"].dt.year == current_year
        ]
        stats["current_year"] = {
            "total_videos_this_year": len(current_year_videos),
            "in_top_100": len(current_year_in_top100),
            "positions": current_year_in_top100.index.tolist() if len(current_year_in_top100) > 0 else [],
            "avg_views": int(current_year_videos["view_count"].mean()) if len(current_year_videos) > 0 else 0,
        }

    return stats


def compute_theme_distribution(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "category" not in df.columns:
        return pd.DataFrame(columns=["category", "count", "pct"])
    dist = df["category"].value_counts().reset_index()
    dist.columns = ["category", "count"]
    dist["pct"] = (dist["count"] / dist["count"].sum() * 100).round(1)
    return dist


def compute_channel_comparison(channels: list[dict]) -> pd.DataFrame:
    if not channels:
        return pd.DataFrame()
    df = pd.DataFrame(channels)
    if "video_count" in df.columns and "total_views" in df.columns:
        df["avg_views_per_video"] = (df["total_views"] / df["video_count"].replace(0, 1)).astype(int)
    return df.sort_values("total_views", ascending=False)
