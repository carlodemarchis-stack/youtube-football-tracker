"""Shared Confederation → Team filter for the WC2026 sub-app.

Deliberately SEPARATE from the core global league/club filter
(app.py). WC2026 is isolated & killable (CONVENTIONS §10) — its
filter must not entangle the core one, so this is a self-contained
helper each WC2026 page calls at the top. The selection is held in
session state, so it persists as the user moves between the WC2026
pages ("global within the sub-app") without any app.py plumbing.

Two levels mirroring the core league→club model:
  Z1  confed None, team None  → all WC2026 channels
  Z2  confed set,  team None  → one confederation
  Z3  team set                → one team (incl. its alt channels)
"""
from __future__ import annotations

import streamlit as st

# Stable display order; any unexpected confederation is appended.
_CONF_ORDER = ["UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC", "FIFA"]
_K_CONF = "_wc2026_confed"
_K_TEAM = "_wc2026_team"


def _wc(c: dict) -> dict:
    return (c.get("competitions") or {}).get("wc2026") or {}


def confed_of(c: dict) -> str:
    return _wc(c).get("confederation") or "Other"


def team_of(c: dict) -> str:
    return _wc(c).get("team") or ""


def render_wc2026_filter(wc_channels: list[dict]) -> tuple[str | None, str | None]:
    """Render the Confederation / Team selectboxes and return the
    active ``(confederation, team)`` — ``None`` means "All". State is
    persisted via session-state widget keys so it carries across the
    WC2026 pages."""
    present = {confed_of(c) for c in wc_channels}
    confeds = [c for c in _CONF_ORDER if c in present]
    confeds += sorted(present - set(confeds) - {"Other"})

    c1, c2 = st.columns(2)
    with c1:
        conf_pick = st.selectbox("Confederation", ["All"] + confeds,
                                 key=_K_CONF)
    confed = None if conf_pick == "All" else conf_pick

    teams: list[str] = []
    if confed is not None:
        teams = sorted({team_of(c) for c in wc_channels
                        if confed_of(c) == confed and team_of(c)})

    # Dependent selectbox gotcha: if the persisted team is no longer a
    # valid option (confederation changed), drop the stale key BEFORE
    # the widget is created so it falls back to "All" cleanly.
    if st.session_state.get(_K_TEAM) not in (["All"] + teams):
        st.session_state.pop(_K_TEAM, None)
    with c2:
        if teams:
            team_pick = st.selectbox("Team", ["All"] + teams, key=_K_TEAM)
            team = None if team_pick == "All" else team_pick
        else:
            st.selectbox("Team", ["All"], disabled=True,
                         key="_wc2026_team_disabled")
            team = None
    return confed, team


def scope_wc2026(channels: list[dict], confed: str | None,
                 team: str | None) -> list[dict]:
    """Apply the (confederation, team) selection to a channel list."""
    out = channels
    if confed is not None:
        out = [c for c in out if confed_of(c) == confed]
    if team is not None:
        out = [c for c in out if team_of(c) == team]
    return out


def scope_label(confed: str | None, team: str | None) -> str:
    """Human label for the active scope (for subtitles/headers)."""
    if team:
        return team
    if confed:
        return confed
    return "All confederations"
