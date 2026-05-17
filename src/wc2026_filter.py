"""Shared Confederation → Team filter for the WC2026 sub-app.

Deliberately SEPARATE from the core global league/club filter
(app.py): WC2026 is isolated & killable (CONVENTIONS §10), so this is
a self-contained helper each WC2026 page calls at the top.

Cross-page persistence uses the SAME proven mechanism as the core
`render_header_filter` (src/filters.py) — the one that took days to
get right: the URL query params are the durable store (they survive
`st.navigation` page switches; widget-key session_state does NOT),
mirrored into session_state, with the selectbox driven by an explicit
`index=` and the URL written back only when the value changes (so no
rerun loop). Do not "simplify" this to `selectbox(key=...)` — that is
exactly the version that failed to persist page-to-page.

Two levels mirroring league→club:
  Z1  confed None, team None  → all WC2026 channels
  Z2  confed set,  team None  → one confederation
  Z3  team set                → one team (incl. its alt channels)
"""
from __future__ import annotations

import streamlit as st

# Stable display order; any unexpected confederation is appended.
_CONF_ORDER = ["UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC", "FIFA"]
# session_state mirrors of the URL truth
_K_CONF = "_wc2026_confed"
_K_TEAM = "_wc2026_team"
# the URL query-param names (the durable store)
_QP_CONF = "wc_confed"
_QP_TEAM = "wc_team"


def _wc(c: dict) -> dict:
    return (c.get("competitions") or {}).get("wc2026") or {}


def confed_of(c: dict) -> str:
    return _wc(c).get("confederation") or "Other"


def team_of(c: dict) -> str:
    return _wc(c).get("team") or ""


def _sync_qp(confed: str, team: str) -> None:
    """Mirror the active selection into the URL so it survives page
    navigation + reload. Only our two keys are touched (never a blanket
    clear — other params like ?view=feed must be left alone), and only
    when something changed (avoids a rerun loop). Same contract as
    src.filters._sync_query_params."""
    target: dict[str, str] = {}
    if confed and confed != "All":
        target[_QP_CONF] = confed
    if team and team != "All":
        target[_QP_TEAM] = team
    current = {k: st.query_params.get(k)
               for k in (_QP_CONF, _QP_TEAM) if st.query_params.get(k)}
    if current != target:
        for k in (_QP_CONF, _QP_TEAM):
            if k in st.query_params:
                del st.query_params[k]
        if target:
            st.query_params.update(target)


def render_wc2026_filter(wc_channels: list[dict]) -> tuple[str | None, str | None]:
    """Render the Confederation / Team selectboxes and return the
    active ``(confederation, team)`` — ``None`` means "All". Persists
    across WC2026 pages via the URL (see module docstring)."""
    present = {confed_of(c) for c in wc_channels}
    confeds = [c for c in _CONF_ORDER if c in present]
    confeds += sorted(present - set(confeds) - {"Other"})
    conf_options = ["All"] + confeds

    # Restore from URL on a fresh session OR when the query params
    # changed externally (e.g. landed via a deep link / page switch).
    _qp_conf = st.query_params.get(_QP_CONF, "All")
    _qp_team = st.query_params.get(_QP_TEAM, "All")
    if _K_CONF not in st.session_state:
        st.session_state[_K_CONF] = _qp_conf
        st.session_state[_K_TEAM] = _qp_team
    elif (st.session_state.get("_wc2026_qp_conf") != _qp_conf
          or st.session_state.get("_wc2026_qp_team") != _qp_team):
        st.session_state[_K_CONF] = _qp_conf
        st.session_state[_K_TEAM] = _qp_team
    st.session_state["_wc2026_qp_conf"] = _qp_conf
    st.session_state["_wc2026_qp_team"] = _qp_team

    if st.session_state[_K_CONF] not in conf_options:
        st.session_state[_K_CONF] = "All"

    c1, c2 = st.columns(2)
    with c1:
        sel_conf = st.selectbox(
            "Confederation", conf_options,
            index=conf_options.index(st.session_state[_K_CONF]),
            key="_wc2026_widget_confed",
        )
        _conf_changed = st.session_state.get(_K_CONF) != sel_conf
        st.session_state[_K_CONF] = sel_conf
    confed = None if sel_conf == "All" else sel_conf

    teams: list[str] = []
    if confed is not None:
        teams = sorted({team_of(c) for c in wc_channels
                        if confed_of(c) == confed and team_of(c)})
    team_options = ["All"] + teams
    # Reset team when its confederation changed or the stored value is
    # no longer valid (mirrors the core filter's _league_changed reset).
    if st.session_state.get(_K_TEAM) not in team_options or _conf_changed:
        st.session_state[_K_TEAM] = "All"

    with c2:
        if teams:
            sel_team = st.selectbox(
                "Team", team_options,
                index=team_options.index(st.session_state[_K_TEAM]),
                key="_wc2026_widget_team",
            )
            st.session_state[_K_TEAM] = sel_team
            team = None if sel_team == "All" else sel_team
        else:
            st.selectbox("Team", ["All"], index=0, disabled=True,
                         key="_wc2026_widget_team_disabled")
            st.session_state[_K_TEAM] = "All"
            team = None

    _sync_qp(sel_conf, st.session_state[_K_TEAM])
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
