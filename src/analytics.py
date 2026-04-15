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
# Multi-language keyword patterns. Layered classifier uses title,
# duration_seconds, and format. Rules checked in priority order; first
# match wins. Patterns are pre-compiled for speed.

_THEME_RULES: list[tuple[str, "re.Pattern"]] = [
    # Highlights (all common languages) — must check BEFORE Full Match so
    # "Juventus vs Inter | Highlights" classifies correctly even if duration
    # is long (some clubs upload 15-min cuts).
    ("Highlights", re.compile(
        r"highlight|sintesi|resumen|zusammenfassung|r[ée]sum[ée]|"
        r"hoogtepunten|melhores momentos|\bgoles del partido\b"
    )),
    # Press conference
    ("Press Conference", re.compile(
        r"press\s?conference|press\s?conf|presser|conferenza\s?stampa|"
        r"rueda de prensa|pressekonferenz|conf[ée]rence de presse|coletiva"
    )),
    # Interview
    ("Interview", re.compile(
        r"interview|intervista|entrevista|entretien|interviu|\bparla\b|\bhabla\b"
    )),
    # Training
    ("Training", re.compile(
        r"\btraining\b|allenamento|entrenamiento|entra[îi]nement|treino|\bprep\b|warm.?up"
    )),
    # Transfer / Welcome / Signing
    ("Transfer & Signings", re.compile(
        r"\bwelcome\b|signing|ufficiale|oficial|offiziell|officiel|"
        r"\bmercato\b|\bfichaje\b|transfer|transfer[êe]ncia|unveil|presentazione|presentaci[oó]n"
    )),
    # Women's football (flag before Academy since both can appear)
    ("Women's Football", re.compile(
        r"\bwomen\b|femminil|femenin|femenil|\bfrauen\b|f[ée]minin"
    )),
    # Academy / youth
    ("Academy & Youth", re.compile(
        r"\bacademy\b|primavera|\bu\s?1[5-9]\b|\bu\s?2[0-3]\b|"
        r"\bcantera\b|jugend|jeunes|giovanili|juvenil|youth"
    )),
    # Matchday prep / preview
    ("Matchday", re.compile(
        r"matchday|gameday|giornata|d[ií]a de partido|spieltag|jour de match|"
        r"pre\s?match|pre.?game|\blineup\b|starting\s?xi|convocat|"
        r"teamnews|team\s?news"
    )),
    # Behind the scenes / inside
    ("Behind the Scenes", re.compile(
        r"behind the scene|dietro le quinte|detr[aá]s de|hinter den kulissen|"
        r"coulisses|\bvlog\b|\binside\b|backstage|tunnel cam|bts\b"
    )),
    # Trailer / promo
    ("Trailer & Promo", re.compile(
        r"\btrailer\b|\bpromo\b|\bteaser\b|\bpreview\b|anteprima|avance|vorschau"
    )),
    # Goals & skills (lower priority — many titles mention "goal")
    ("Goals & Skills", re.compile(
        r"\bbest goal|\btop goal|\bskill\b|dribbl|nutmeg|\btrick\b|\bassist\b|\bsave\b|\bparata\b|"
        r"compilation|\bfree.?kick\b|\bpunizione\b|golazo|gola[çc]o"
    )),
]

# Match-pattern: "Team A vs Team B" / "Team A - Team B" / "Team A v Team B"
_MATCH_PATTERN = re.compile(r"\bv(s\.?)?\b|\s[-–—]\s|\bvs\b")


def detect_theme(
    title: str,
    duration_seconds: int | None = None,
    format_: str | None = None,
) -> str:
    """Classify a video into a theme. Uses title + duration + format signals.

    Priority:
      1. Highlights keyword → Highlights
      2. Keyword-based rules in priority order
      3. Duration/format fallbacks:
         - live ≥ 60 min → Full Match (Live)
         - live < 60 min → Live Stream
         - VOD ≥ 80 min + match-pattern in title → Full Match
         - everything else → Other
    """
    t = (title or "").lower()

    # 1. Keyword rules in order
    for theme, rx in _THEME_RULES:
        if rx.search(t):
            return theme

    # 2. Duration / format fallbacks
    dur = duration_seconds or 0
    fmt = (format_ or "").lower()
    if fmt == "live":
        if dur >= 3600:
            return "Full Match (Live)"
        return "Live Stream"
    if dur >= 4800 and _MATCH_PATTERN.search(t):
        return "Full Match"

    return "Other"


def classify_videos(videos: list[dict]) -> list[dict]:
    for v in videos:
        v["category"] = detect_theme(
            v.get("title", ""),
            v.get("duration_seconds"),
            v.get("format"),
        )
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
