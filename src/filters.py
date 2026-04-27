from __future__ import annotations

import streamlit as st
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG


# Entity types that should never appear in the main League/Club UX.
# Players live on their own page and are excluded from everything else
# to keep that feature isolated and killable without ripple effects.
_NON_CLUB_TYPES = ("League", "Player", "Federation")


def is_club(ch: dict) -> bool:
    """True if a channel belongs in the club/league UX (not a Player,
    not the league channel itself)."""
    return ch.get("entity_type") not in _NON_CLUB_TYPES


def _sync_query_params(league: str, club: str):
    """Silently update URL bar so filter survives browser reload."""
    target = {}
    if league != "All Leagues":
        target["league"] = league
    if club not in ("All Clubs", "All Clubs + League"):
        target["club"] = club
    elif club == "All Clubs + League":
        target["club"] = club
    # Only write if something actually changed (avoids rerun loop)
    current = {k: st.query_params.get(k) for k in ("league", "club") if st.query_params.get(k)}
    if current != target:
        st.query_params.clear()
        if target:
            st.query_params.update(target)


def render_header_filter(channels: list[dict]) -> tuple[str | None, dict | None]:
    """Render cascading league/club filter. Returns (league_name, channel_dict_or_None)."""

    # Build league list from clubs only — leagues, players and federations
    # have their own pages and would leak country codes (AF/AS/BR/EU/WW…)
    # into the dropdown otherwise.
    leagues = {}
    for ch in channels:
        if not is_club(ch):
            continue
        country = ch.get("country", "")
        league = COUNTRY_TO_LEAGUE.get(country, country)
        if league:
            leagues.setdefault(league, []).append(ch)

    league_names = sorted(leagues.keys())
    league_options = ["All Leagues"] + league_names

    # Restore from URL on fresh session OR when query params changed externally
    _qp_league = st.query_params.get("league", "All Leagues")
    _qp_club = st.query_params.get("club", "All Clubs")
    if "_filter_league" not in st.session_state:
        st.session_state["_filter_league"] = _qp_league
        st.session_state["_filter_club"] = _qp_club
    elif st.session_state.get("_filter_qp_league") != _qp_league or st.session_state.get("_filter_qp_club") != _qp_club:
        # Query params changed externally (e.g. clicked a row link) — apply them
        st.session_state["_filter_league"] = _qp_league
        st.session_state["_filter_club"] = _qp_club
    st.session_state["_filter_qp_league"] = _qp_league
    st.session_state["_filter_qp_club"] = _qp_club

    # Ensure stored value is still valid
    if st.session_state["_filter_league"] not in league_options:
        st.session_state["_filter_league"] = "All Leagues"

    col1, col2 = st.columns(2)

    with col1:
        def _fmt_league(name):
            f = LEAGUE_FLAG.get(name, "")
            return f"{f} {name}" if f else name

        selected_league = st.selectbox(
            "League",
            league_options,
            index=league_options.index(st.session_state["_filter_league"]),
            key="_widget_league",
            format_func=_fmt_league,
        )
        st.session_state["_filter_league"] = selected_league

    if selected_league == "All Leagues":
        st.session_state["_filter_club"] = "All Clubs"
        _sync_query_params(selected_league, "All Clubs")
        # Secondary scope dropdown
        scope_options = ["Overall", "Leagues only", "All clubs"]
        if "_filter_scope" not in st.session_state:
            st.session_state["_filter_scope"] = st.query_params.get("scope", "Overall")
        if st.session_state["_filter_scope"] not in scope_options:
            st.session_state["_filter_scope"] = "Overall"
        with col2:
            selected_scope = st.selectbox(
                "Scope",
                scope_options,
                index=scope_options.index(st.session_state["_filter_scope"]),
                key="_widget_scope",
            )
            st.session_state["_filter_scope"] = selected_scope
        # Persist scope in URL
        if selected_scope != "Overall":
            st.query_params["scope"] = selected_scope
        elif "scope" in st.query_params:
            del st.query_params["scope"]
        return None, None
    else:
        st.session_state["_filter_scope"] = "Overall"
        if "scope" in st.query_params:
            del st.query_params["scope"]

    # Club dropdown
    league_channels = leagues.get(selected_league, [])
    clubs = [ch for ch in league_channels if is_club(ch)]
    has_league_channel = any(ch.get("entity_type") == "League" for ch in league_channels)
    clubs.sort(key=lambda c: c.get("subscriber_count", 0), reverse=True)

    club_options = ["All Clubs"]
    if has_league_channel:
        club_options.append("All Clubs + League")
    club_options += [ch["name"] for ch in clubs]

    # Ensure stored club is still valid for this league
    if st.session_state["_filter_club"] not in club_options:
        st.session_state["_filter_club"] = "All Clubs"

    with col2:
        selected_club = st.selectbox(
            "Club",
            club_options,
            index=club_options.index(st.session_state["_filter_club"]),
            key="_widget_club",
        )
        st.session_state["_filter_club"] = selected_club

    _sync_query_params(selected_league, selected_club)

    if selected_club == "All Clubs":
        st.session_state["_filter_include_league"] = False
        return selected_league, None

    if selected_club == "All Clubs + League":
        st.session_state["_filter_include_league"] = True
        return selected_league, None

    st.session_state["_filter_include_league"] = False
    club_dict = next((ch for ch in clubs if ch["name"] == selected_club), None)
    return selected_league, club_dict


def get_global_filter() -> tuple[str | None, dict | None]:
    """Read the global filter from session state (set by app.py)."""
    return st.session_state.get("_global_league"), st.session_state.get("_global_club")


def get_global_channels() -> list[dict]:
    """Get all channels from session state."""
    return st.session_state.get("_global_channels", [])


def get_include_league() -> bool:
    """Whether the user selected 'All Clubs + League' in the filter."""
    return st.session_state.get("_filter_include_league", False)


def get_filter_description() -> str:
    """Return a short human-readable description of the current filter state."""
    league, club = get_global_filter()
    if club:
        return f"{club['name']} only"
    if league:
        include_lg = get_include_league()
        flag = LEAGUE_FLAG.get(league, "")
        lg_label = f"{flag} {league}" if flag else league
        if include_lg:
            return f"{lg_label} incl. league channel"
        return lg_label
    # All leagues
    scope = get_all_leagues_scope()
    if scope == "Leagues only":
        return "league channels only"
    if scope == "All clubs":
        return "all clubs across leagues"
    return "all leagues"


def render_page_subtitle(content: str, updated_raw: str | None = None,
                         caveat: str | None = None) -> None:
    """Render a consistent one-line subtitle: content · filter · updated.

    Args:
        content: What this page shows (e.g. "Top 100 most viewed videos").
        updated_raw: ISO timestamp for "updated Xh ago". If None, uses
                     max(last_fetched) from global channels.
        caveat: Optional second-line caveat (e.g. season date disclaimer).
    """
    from src.analytics import fmt_date

    filter_desc = get_filter_description()

    if updated_raw is None:
        all_ch = get_global_channels()
        updated_raw = max((c.get("last_fetched") or "" for c in all_ch), default="") if all_ch else ""

    parts = [content, filter_desc]
    if updated_raw:
        parts.append(f"updated {fmt_date(updated_raw)}")

    st.caption(" · ".join(parts))
    if caveat:
        st.caption(caveat)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02X}{g:02X}{b:02X}"


def _luminance(h: str) -> float:
    r, g, b = _hex_to_rgb(h)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _lighten_color(h: str, target_lum: float = 80.0) -> str:
    """Lighten a color so it's visible on a dark background (~#0E1117).
    Blends toward white until it reaches target luminance."""
    r, g, b = _hex_to_rgb(h)
    lum = _luminance(h)
    if lum >= target_lum:
        return h
    # Blend toward a lighter version
    factor = min((target_lum - lum) / (255 - lum + 1), 0.65)
    r2 = int(r + (255 - r) * factor)
    g2 = int(g + (255 - g) * factor)
    b2 = int(b + (255 - b) * factor)
    return _rgb_to_hex(r2, g2, b2)


def get_global_color_map() -> dict[str, str]:
    """Return a color map (name → chart-safe color) for charts.

    Dark colors (low luminance) are lightened so they're visible on
    Streamlit's dark background.
    """
    colors = _load_colors()
    return {name: _lighten_color(c[0]) for name, c in colors.items()}


def get_global_color_map_dual() -> dict[str, tuple[str, str]]:
    """Return a color map (name → (primary, secondary)) for dual-color dots."""
    return _load_colors()


def _load_colors() -> dict[str, tuple[str, str]]:
    """Load or auto-assign both primary and secondary colors for all channels."""
    from src.analytics import CHANNEL_PALETTE
    from src.channels import TEAM_COLORS
    all_channels = get_global_channels()
    if not all_channels:
        import os
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if url and key:
            from src.database import Database
            db = Database(url, key)
            all_channels = db.get_all_channels()

    color_map: dict[str, tuple[str, str]] = {}
    used_colors: set[str] = set()
    needs_color: list[dict] = []

    for ch in all_channels:
        if ch.get("color"):
            c1 = ch["color"]
            c2 = ch.get("color2") or "#FFFFFF"
            color_map[ch["name"]] = (c1, c2)
            used_colors.add(c1)
        else:
            needs_color.append(ch)

    if needs_color:
        import os
        from src.database import Database
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        db = Database(url, key) if url and key else None

        available = [c for c in CHANNEL_PALETTE if c not in used_colors]
        if not available:
            available = list(CHANNEL_PALETTE)

        for i, ch in enumerate(sorted(needs_color, key=lambda c: c.get("name", ""))):
            team = TEAM_COLORS.get(ch.get("name", ""))
            if team and team[0] not in used_colors:
                c1, c2 = team
            else:
                c1 = available[i % len(available)]
                c2 = "#FFFFFF"
            color_map[ch["name"]] = (c1, c2)
            used_colors.add(c1)
            ch["color"] = c1
            ch["color2"] = c2
            if db:
                db.update_channel(ch["id"], {"color": c1, "color2": c2})

    return color_map


def get_all_leagues_scope() -> str:
    """When league == 'All Leagues', returns 'Overall' | 'Leagues only' | 'All clubs'."""
    return st.session_state.get("_filter_scope", "Overall")


def get_channels_for_filter(channels: list[dict], league: str | None) -> list[dict]:
    """Return channels matching the current league filter (and All-Leagues scope)."""
    if league is None:
        scope = get_all_leagues_scope()
        if scope == "Leagues only":
            return [ch for ch in channels if ch.get("entity_type") == "League"]
        if scope == "All clubs":
            return [ch for ch in channels if is_club(ch)]
        # Overall: exclude Players + Federations (own pages) but keep leagues
        return [ch for ch in channels
                if ch.get("entity_type") not in ("Player", "Federation")]
    return [
        ch for ch in channels
        if ch.get("entity_type") not in ("Player", "Federation")
        and COUNTRY_TO_LEAGUE.get(ch.get("country", ""), ch.get("country", "")) == league
    ]


def get_league_for_channel(ch: dict) -> str:
    """Get league name for a channel."""
    return COUNTRY_TO_LEAGUE.get(ch.get("country", ""), ch.get("country", ""))


def render_club_header(channel: dict, all_channels: list[dict]) -> None:
    """Render a club's name with inline subscriber ranks (league + overall)."""
    clubs = [c for c in all_channels if is_club(c)]
    clubs.sort(key=lambda c: c.get("subscriber_count", 0), reverse=True)
    overall_rank = next((i + 1 for i, c in enumerate(clubs) if c["id"] == channel["id"]), None)
    overall_total = len(clubs)
    ch_league = COUNTRY_TO_LEAGUE.get((channel.get("country") or "").upper(), "")
    peers = [c for c in clubs if COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper(), "") == ch_league]
    peers.sort(key=lambda c: c.get("subscriber_count", 0), reverse=True)
    league_rank = next((i + 1 for i, c in enumerate(peers) if c["id"] == channel["id"]), None)
    league_total = len(peers)

    bits = []
    if league_rank and ch_league:
        bits.append(f"#{league_rank}/{league_total} in {ch_league}")
    if overall_rank:
        bits.append(f"#{overall_rank}/{overall_total} overall")
    _raw_launched = (channel.get("launched_at") or "")[:10]
    if _raw_launched:
        try:
            from datetime import datetime as _dt
            _ld = _dt.strptime(_raw_launched, "%Y-%m-%d")
            bits.append(f"launched {_ld.strftime('%b %Y')}")
        except Exception:
            bits.append(f"launched {_raw_launched}")
    rank_html = (
        f"<span style='color:#888;font-size:0.95rem;font-weight:400;margin-left:14px'>"
        f"{' · '.join(bits)}</span>" if bits else ""
    )
    st.markdown(
        f"<h3 style='margin:0'>{channel['name']}{rank_html}</h3>",
        unsafe_allow_html=True,
    )
