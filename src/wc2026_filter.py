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

Z1 also carries an optional `sub_scope`:
  "All"     → everyone (default)
  "Teams"   → 48 nation channels only (excludes FIFA + the 6 confeds)
  "Confeds" → FIFA + 6 confederation governing bodies only
"""
from __future__ import annotations

import streamlit as st

# Stable display order; any unexpected confederation is appended.
_CONF_ORDER = ["UEFA", "CONMEBOL", "CONCACAF", "CAF", "AFC", "OFC", "FIFA"]
# session_state mirrors of the URL truth
_K_CONF = "_wc2026_confed"
_K_TEAM = "_wc2026_team"
_K_SCOPE = "_wc2026_scope"
# the URL query-param names (the durable store)
_QP_CONF = "wc_confed"
_QP_TEAM = "wc_team"
_QP_SCOPE = "wc_scope"

# Z1 sub-scope options. "All" = everyone (default), "Teams" = exclude
# FIFA + the 6 confed governing bodies, "Confeds" = only them.
_SCOPE_OPTIONS = ["All", "Teams", "Confeds"]
_SCOPE_LABEL = {
    "All": "All channels",
    "Teams": "Teams only",
    "Confeds": "Confederations only",
}


def _wc(c: dict) -> dict:
    return (c.get("competitions") or {}).get("wc2026") or {}


def confed_of(c: dict) -> str:
    return _wc(c).get("confederation") or "Other"


def team_of(c: dict) -> str:
    return _wc(c).get("team") or ""


def _sync_qp(confed: str, team: str, sub_scope: str) -> None:
    """Mirror the active selection into the URL so it survives page
    navigation + reload. Only our three keys are touched (never a blanket
    clear — other params like ?view=feed must be left alone), and only
    when something changed (avoids a rerun loop). Same contract as
    src.filters._sync_query_params."""
    target: dict[str, str] = {}
    if confed and confed != "All":
        target[_QP_CONF] = confed
    if team and team != "All":
        target[_QP_TEAM] = team
    if sub_scope and sub_scope != "All":
        target[_QP_SCOPE] = sub_scope
    current = {k: st.query_params.get(k)
               for k in (_QP_CONF, _QP_TEAM, _QP_SCOPE)
               if st.query_params.get(k)}
    if current != target:
        for k in (_QP_CONF, _QP_TEAM, _QP_SCOPE):
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
    _qp_scope = st.query_params.get(_QP_SCOPE, "All")
    if _K_CONF not in st.session_state:
        st.session_state[_K_CONF] = _qp_conf
        st.session_state[_K_TEAM] = _qp_team
        st.session_state[_K_SCOPE] = _qp_scope
    elif (st.session_state.get("_wc2026_qp_conf") != _qp_conf
          or st.session_state.get("_wc2026_qp_team") != _qp_team
          or st.session_state.get("_wc2026_qp_scope") != _qp_scope):
        st.session_state[_K_CONF] = _qp_conf
        st.session_state[_K_TEAM] = _qp_team
        st.session_state[_K_SCOPE] = _qp_scope
    st.session_state["_wc2026_qp_conf"] = _qp_conf
    st.session_state["_wc2026_qp_team"] = _qp_team
    st.session_state["_wc2026_qp_scope"] = _qp_scope

    if st.session_state[_K_CONF] not in conf_options:
        st.session_state[_K_CONF] = "All"
    if st.session_state.get(_K_SCOPE) not in _SCOPE_OPTIONS:
        st.session_state[_K_SCOPE] = "All"

    # Two columns, always — D2's contents flip based on D1, mirroring
    # the Top-5 global filter (Z1 → 'Overall / Leagues only / All clubs',
    # Z2 → club picker). Saves a column, no disabled placeholders.
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

    if confed is None:
        # Z1 — D2 is the sub-scope picker. The team key is reset so
        # navigating to a confederation later starts clean.
        st.session_state[_K_TEAM] = "All"
        team = None
        with c2:
            sel_scope = st.selectbox(
                "Show", _SCOPE_OPTIONS,
                index=_SCOPE_OPTIONS.index(st.session_state[_K_SCOPE]),
                key="_wc2026_widget_scope",
                format_func=lambda v: _SCOPE_LABEL.get(v, v),
            )
            st.session_state[_K_SCOPE] = sel_scope
    else:
        # Z2/Z3 — D2 is the team picker, sub-scope is implicit so it's
        # snapped back to "All" (mirrors the way Top-5 clears its
        # scope when leaving 'All Leagues').
        st.session_state[_K_SCOPE] = "All"
        teams = sorted({team_of(c) for c in wc_channels
                        if confed_of(c) == confed and team_of(c)})
        team_options = ["All"] + teams
        # Reset team when its confederation changed or the stored value
        # is no longer valid (mirrors _league_changed reset).
        if (st.session_state.get(_K_TEAM) not in team_options
                or _conf_changed):
            st.session_state[_K_TEAM] = "All"
        with c2:
            sel_team = st.selectbox(
                "Team", team_options,
                index=team_options.index(st.session_state[_K_TEAM]),
                key="_wc2026_widget_team",
            )
            st.session_state[_K_TEAM] = sel_team
            team = None if sel_team == "All" else sel_team

    _sync_qp(sel_conf, st.session_state[_K_TEAM],
             st.session_state[_K_SCOPE])
    # Publish the normalised selection for the pages to read (same
    # render-in-app.py → read-in-page split as the core filter:
    # render_header_filter → get_global_filter).
    st.session_state["_wc2026_sel_confed"] = confed
    st.session_state["_wc2026_sel_team"] = team
    st.session_state["_wc2026_sel_scope"] = st.session_state[_K_SCOPE]
    return confed, team


def get_wc2026_filter() -> tuple[str | None, str | None]:
    """Read the active WC2026 (confederation, team) — set by
    render_wc2026_filter() in app.py. Mirrors filters.get_global_filter.
    """
    return (st.session_state.get("_wc2026_sel_confed"),
            st.session_state.get("_wc2026_sel_team"))


def get_wc2026_sub_scope() -> str:
    """Read the active Z1 sub-scope: "All" | "Teams" | "Confeds".
    Only meaningful when both confed and team are None — anywhere else
    it's snapped to "All" by render_wc2026_filter()."""
    return st.session_state.get("_wc2026_sel_scope") or "All"


def _is_gov_body_channel(c: dict) -> bool:
    """A channel that represents a governing body itself, not a member
    nation. Detected by ``wc2026.team == wc2026.confederation`` (FIFA
    in FIFA, UEFA in UEFA, …) — catches BOTH the main governing-body
    channels (entity_type=GoverningBody) AND alt channels like FIFA+
    (entity_type=Federation, role=federation, but still representing
    FIFA, not a member nation)."""
    w = _wc(c)
    t = (w.get("team") or "").strip()
    cf = (w.get("confederation") or "").strip()
    return t != "" and t == cf


def scope_wc2026(channels: list[dict], confed: str | None,
                 team: str | None,
                 sub_scope: str | None = None) -> list[dict]:
    """Apply the (confederation, team) selection to a channel list, plus
    the Z1 sub-scope when it's set. ``sub_scope`` defaults to whatever
    the filter last published, so existing call sites don't need to be
    updated to pass it explicitly."""
    out = channels
    if confed is not None:
        out = [c for c in out if confed_of(c) == confed]
    if team is not None:
        out = [c for c in out if team_of(c) == team]
    # Apply the Z1 sub-scope only when we're actually at Z1 (no confed
    # / team narrowing already). At Z2/Z3 the user's pick implies one
    # or the other — applying both would surprise.
    if sub_scope is None:
        sub_scope = get_wc2026_sub_scope()
    if confed is None and team is None:
        if sub_scope == "Teams":
            out = [c for c in out if not _is_gov_body_channel(c)]
        elif sub_scope == "Confeds":
            out = [c for c in out if _is_gov_body_channel(c)]
    return out


def scope_label(confed: str | None, team: str | None,
                sub_scope: str | None = None) -> str:
    """Human label for the active scope (for subtitles/headers)."""
    if team:
        return team
    if confed:
        return confed
    if sub_scope is None:
        sub_scope = get_wc2026_sub_scope()
    if sub_scope == "Teams":
        return "All nations"
    if sub_scope == "Confeds":
        return "FIFA + confederations"
    return "All confederations"
