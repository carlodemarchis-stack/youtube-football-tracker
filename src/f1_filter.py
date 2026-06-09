"""F1 filter — single channel dropdown rendered above the page title.

Mirrors the NFL filter (src/nfl_filter.py). One dropdown:

  [All, Teams only, F1 HQ, <team1>, <team2>, …]

URL param `team=<name>` is the source of truth, mirrored into session
state so the selectbox stays in sync across navigation. Page reads
:func:`get_f1_filter` for the picked channel name and
:func:`is_f1_teams_only` to know whether the HQ should be dropped.
"""
from __future__ import annotations

import streamlit as st

_QP_TEAM = "team"
_K_TEAM = "_f1_team"
# Sentinel value that means "all teams, no HQ" — drops the F1 HQ
# channel from scope but stays in Z1 (multi-channel) mode.
_TEAMS_ONLY = "Teams only"


def get_f1_filter() -> str | None:
    """Return the picked channel name, or ``None`` for any multi-channel
    scope (All / Teams only)."""
    v = st.session_state.get(_K_TEAM) or st.query_params.get(_QP_TEAM)
    return v if v and v not in ("All", _TEAMS_ONLY) else None


def is_f1_teams_only() -> bool:
    """True when the user picked 'Teams only' — page should exclude
    the F1 HQ channel from its scope but stay in Z1 mode."""
    v = st.session_state.get(_K_TEAM) or st.query_params.get(_QP_TEAM)
    return v == _TEAMS_ONLY


def render_f1_filter(f1_channels: list[dict]) -> str | None:
    """Render the dropdown above the page title (called from app.py).
    Returns the picked channel name, or None for any multi-channel
    scope (All / Teams only)."""
    if not f1_channels:
        return None

    # HQ first, then teams alphabetical.
    def _is_hq(c) -> bool:
        return ((c.get("competitions") or {}).get("f1") or {}) \
            .get("role") == "hq"

    options = ["All", _TEAMS_ONLY] + (
        [c["name"] for c in f1_channels if _is_hq(c)]
        + sorted(c["name"] for c in f1_channels if not _is_hq(c))
    )

    # URL-param → session-state sync (same trick as WC2026 / NFL).
    _qp = st.query_params.get(_QP_TEAM, "All")
    if _K_TEAM not in st.session_state:
        st.session_state[_K_TEAM] = _qp
    elif st.session_state.get("_f1_qp_team") != _qp:
        st.session_state[_K_TEAM] = _qp
    st.session_state["_f1_qp_team"] = _qp
    if st.session_state[_K_TEAM] not in options:
        st.session_state[_K_TEAM] = "All"

    cols = st.columns([1, 3])
    with cols[0]:
        st.markdown(
            "<div style='font-size:13px;color:#888;margin-top:8px'>"
            "🏎️ F1 filter</div>",
            unsafe_allow_html=True,
        )
        sel = st.selectbox(
            "Channel",
            options,
            index=options.index(st.session_state[_K_TEAM]),
            key=_K_TEAM,
            label_visibility="collapsed",
        )

    # Mirror selection back into the URL so the filter survives
    # reloads and is shareable. Match WC2026 / NFL's pattern.
    target = {_QP_TEAM: sel} if sel != "All" else {}
    current = {k: st.query_params.get(k)
               for k in (_QP_TEAM,) if st.query_params.get(k)}
    if current != target:
        if _QP_TEAM in st.query_params:
            del st.query_params[_QP_TEAM]
        if target:
            st.query_params.update(target)

    # Multi-channel scopes (All / Teams only) both return None — the
    # page reads is_f1_teams_only() to know whether to drop HQ.
    return None if sel in ("All", _TEAMS_ONLY) else sel
