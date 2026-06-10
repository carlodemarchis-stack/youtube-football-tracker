"""Pure-Python cohort filters (no Streamlit) — safe to import from
unattended scripts running in slim Python containers (Railway crons
that don't ship Streamlit in their image).

This module exists because src/filters.py uses Streamlit at import
time (the @st.cache_data decorator on _load_colors), which crashes
any cron that imports from it. The cohort predicates themselves are
pure Python and need no UI deps — so they live here and src/filters.py
re-exports them for backwards compatibility.

Single source of truth for "which channels count as the Top-5
cohort", "which channels are clubs", and "which channels are
WC2026-tagged". Inclusion-based: any new entity_type is excluded by
default — see docs/CONVENTIONS.md §10."""
from __future__ import annotations


# Allowlist constants — adding a new core entity_type means touching
# ONE line here, not ~30 inline tuples across views/ and scripts/.
_CLUB_TYPES = ("Club",)
_TOP5_COHORT_TYPES = ("Club", "League")


def is_wc2026(ch: dict) -> bool:
    """True if the channel is tagged for the WC2026 feature.

    WC2026 lives on its own pages and — exactly like Players /
    Federations — must be excluded from every core league/club view,
    aggregate, leaderboard, Latest / Top / Season / Daily Recap
    (CONVENTIONS §10). Tag-based, so isolation holds even if a
    WC2026 channel isn't a Federation/GoverningBody."""
    return bool((ch.get("competitions") or {}).get("wc2026"))


def is_club(ch: dict) -> bool:
    """True only for Club entities (no Leagues, no Players, no
    Federations, no NFL/F1/Women/Other). Use this when the surface
    aggregates per-club (a leaderboard, a club picker, etc.)."""
    return (ch.get("entity_type") in _CLUB_TYPES
            and not is_wc2026(ch))


def is_top5_cohort(ch: dict) -> bool:
    """True for the core Top-5 cohort: Club + League channels, no
    WC2026 tag. Use this for any surface that shows "everything in
    the Top-5 universe" — Home, the All-Channels table on /clubs,
    /latest, /viral, /trends, /season, /top-videos, etc.

    Inclusion-based on purpose: any new entity_type (cricket teams,
    F2/F3, women-league-2, …) is excluded by default, so it can
    never silently leak into the headline UX. Onboard a new type by
    adding it to ``_TOP5_COHORT_TYPES`` above."""
    return (ch.get("entity_type") in _TOP5_COHORT_TYPES
            and not is_wc2026(ch))
