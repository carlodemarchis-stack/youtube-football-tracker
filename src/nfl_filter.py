"""NFL filter — single channel dropdown rendered above the page title.

Mirrors the WC2026 filter pattern (src/wc2026_filter.py) but simpler:
one dropdown, no cascade. URL param `team=<name>` is the source of
truth, mirrored into session state so the selectbox stays in sync
across navigation. Page reads it via :func:`get_nfl_filter`.
"""
from __future__ import annotations

import streamlit as st

_QP_TEAM = "team"
_K_TEAM = "_nfl_team"


def get_nfl_filter() -> str | None:
    """Return the picked channel name, or ``None`` for "All channels"."""
    v = st.session_state.get(_K_TEAM) or st.query_params.get(_QP_TEAM)
    return v if v and v != "All" else None


def render_nfl_filter(nfl_channels: list[dict]) -> str | None:
    """Render the dropdown above the page title (called from app.py).
    Returns the picked channel name, or None for "All channels"."""
    if not nfl_channels:
        return None

    # HQ first, then franchises alphabetical — same order as the
    # legacy inline selector.
    def _is_hq(c) -> bool:
        return (
            (c.get("competitions") or {}).get("nfl") or {}
        ).get("conference", "—") == "—"

    options = ["All"] + (
        [c["name"] for c in nfl_channels if _is_hq(c)]
        + sorted(c["name"] for c in nfl_channels if not _is_hq(c))
    )

    # URL-param → session-state sync (same trick as WC2026).
    _qp = st.query_params.get(_QP_TEAM, "All")
    if _K_TEAM not in st.session_state:
        st.session_state[_K_TEAM] = _qp
    elif st.session_state.get("_nfl_qp_team") != _qp:
        st.session_state[_K_TEAM] = _qp
    st.session_state["_nfl_qp_team"] = _qp
    if st.session_state[_K_TEAM] not in options:
        st.session_state[_K_TEAM] = "All"

    cols = st.columns([1, 3])
    with cols[0]:
        st.markdown(
            "<div style='font-size:13px;color:#888;margin-top:8px'>"
            "🏈 NFL filter</div>",
            unsafe_allow_html=True,
        )
        sel = st.selectbox(
            "Channel",
            options,
            index=options.index(st.session_state[_K_TEAM]),
            key=_K_TEAM,
            label_visibility="collapsed",
        )

    # Mirror selection back into the URL so the filter survives reloads
    # and is shareable. Match WC2026's pattern.
    target = {_QP_TEAM: sel} if sel != "All" else {}
    current = {k: st.query_params.get(k)
               for k in (_QP_TEAM,) if st.query_params.get(k)}
    if current != target:
        if _QP_TEAM in st.query_params:
            del st.query_params[_QP_TEAM]
        if target:
            st.query_params.update(target)

    return None if sel == "All" else sel
