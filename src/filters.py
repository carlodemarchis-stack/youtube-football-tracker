from __future__ import annotations

import streamlit as st
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG


# ── Inclusion-based cohort filters (single source of truth) ──────
#
# The OLD pattern (~25 inlined blocklist tuples across views/) was
# anti-fragile: any new entity_type (NFL, F1, future cricket / G3
# racing / women-league-2 / …) silently leaked into the Top-5 league
# UX until 25 spots were each updated by hand.
#
# The NEW pattern is opt-IN: a channel only enters the Top-5 cohort
# if its entity_type is explicitly in the allowlist. New types are
# excluded by default — they can never leak.
#
# Two helpers, named honestly:
#   - is_club()         → Club channels only (no League).
#   - is_top5_cohort()  → Club + League channels (the headline cohort
#                         that powers /home, /clubs, /season,
#                         /top-videos, /latest, /viral, /trends, etc.).
#
# Both also exclude WC2026-tagged rows (CONVENTIONS §10 isolation).

# Allowlist source of truth — adding a new core entity_type means
# touching ONE line here, not 25 inline tuples across views/.
_CLUB_TYPES = ("Club",)
_TOP5_COHORT_TYPES = ("Club", "League")

# Legacy blocklist — kept only because src/dashboard_cache.py still
# uses it for some SQL-side `.not.in_()` filters. New code should use
# is_top5_cohort() / is_club() instead.
_NON_CLUB_TYPES = ("League", "Player", "Federation", "GoverningBody",
                   "OtherClub", "WomenClub", "NFL", "F1")


def is_wc2026(ch: dict) -> bool:
    """True if the channel is tagged for the WC2026 feature.

    WC2026 lives on its own pages and — exactly like Players /
    Federations — must be excluded from every core league/club view,
    aggregate, leaderboard, Latest / Top / Season / Daily Recap
    (CONVENTIONS §10). This is **tag-based**, so isolation holds even
    if a WC2026 channel isn't a Federation/GoverningBody (today they
    all are, so this is currently a no-op — it exists so adding
    WC2026 *videos* to the shared tables can't leak into core
    surfaces now or in future)."""
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

    # Build league bucket: clubs + the league channel itself.
    # Skip players / federations / women / other-clubs (they have their own
    # pages and would leak country codes like AF/AS/BR/WW into the dropdown).
    leagues = {}
    for ch in channels:
        is_league_ch = ch.get("entity_type") == "League"
        if not is_club(ch) and not is_league_ch:
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
        # Detect a league change so we can reset the club selector to
        # the new league's preferred default below. Without this, the
        # previously-stored "All Clubs" sticks even when the new league
        # would otherwise default to "All Clubs + League".
        _league_changed = (
            st.session_state.get("_filter_league") != selected_league
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
        # Display label: "Overall" is internally the same value but the
        # label "All clubs + Leagues" makes the parallel with the per-
        # league "All Clubs + League" option explicit. URL/session state
        # still uses "Overall" so existing bookmarks keep working.
        _scope_label = {
            "Overall": "All Clubs + Leagues",
            "Leagues only": "Leagues only",
            "All clubs": "All Clubs",
        }
        with col2:
            selected_scope = st.selectbox(
                "Scope",
                scope_options,
                index=scope_options.index(st.session_state["_filter_scope"]),
                key="_widget_scope",
                format_func=lambda v: _scope_label.get(v, v),
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
    league_channel = next((ch for ch in league_channels
                           if ch.get("entity_type") == "League"), None)
    has_league_channel = league_channel is not None
    clubs.sort(key=lambda c: c.get("subscriber_count", 0), reverse=True)

    # Order:
    #   1. "All Clubs + League" — default when a league is selected (matches
    #      what people actually want — clubs AND the league channel together)
    #   2. "All Clubs" — clubs only, no league channel
    #   3. The league channel itself (single-entity)
    #   4. Each club, by subscribers desc
    club_options = []
    if has_league_channel:
        club_options.append("All Clubs + League")
    club_options.append("All Clubs")
    if has_league_channel:
        # The league channel itself as a selectable single-entity option.
        # Stored as the bare channel name; identified on read-back by
        # comparing to league_channel["name"] directly.
        club_options.append(league_channel["name"])
    club_options += [ch["name"] for ch in clubs]

    # Ensure stored club is still valid for this league. Falls back to
    # the first option (which is "All Clubs + League" when there's a
    # league channel, otherwise "All Clubs"). Also reset on league
    # change so a sticky "All Clubs" from one league (or from the
    # All-Leagues view, which forces "All Clubs") doesn't override the
    # new league's preferred default.
    if (st.session_state["_filter_club"] not in club_options
            or _league_changed):
        st.session_state["_filter_club"] = club_options[0]

    league_flag = LEAGUE_FLAG.get(selected_league, "")
    league_channel_name = league_channel["name"] if has_league_channel else None

    def _fmt_club(name: str) -> str:
        # Only the league-channel option carries the country flag; clubs
        # render as plain names. The "All ..." rows are scope toggles.
        if has_league_channel and name == league_channel_name and league_flag:
            return f"{league_flag} {name}"
        return name

    with col2:
        selected_club = st.selectbox(
            "Club / League channel",
            club_options,
            index=club_options.index(st.session_state["_filter_club"]),
            key="_widget_club",
            format_func=_fmt_club,
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
    # League-channel single selection: strip the prefix and return the
    # league channel record as the "club" value (every page treats the
    # second tuple element as "the single channel I'm filtered to").
    if has_league_channel and selected_club == league_channel["name"]:
        return selected_league, league_channel
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
    return "all clubs + league channels"


def render_page_subtitle(content: str, updated_raw: str | None = None,
                         caveat: str | None = None, *,
                         show_filter: bool = True) -> None:
    """Render a consistent one-line subtitle: content · filter · updated.

    Args:
        content: What this page shows (e.g. "Top 100 most viewed videos").
        updated_raw: ISO timestamp for "updated Xh ago". If None, uses
                     max(last_fetched) from global channels.
        caveat: Optional second-line caveat (e.g. season date disclaimer).
        show_filter: include the global-filter description segment.
                     Pass False on standalone, filter-less pages
                     (Players / Other Clubs / Women) where the inherited
                     "all clubs + league channels" string is nonsensical.
    """
    from src.analytics import fmt_date

    if updated_raw is None:
        all_ch = get_global_channels()
        updated_raw = max((c.get("last_fetched") or "" for c in all_ch), default="") if all_ch else ""

    parts = [content]
    if show_filter:
        parts.append(get_filter_description())
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


# Inclusion-based: only Club + League channels get chart colors
# generated/persisted by `_load_colors`. Everyone else (Players /
# Federations / NFL / F1 / WC2026 / future cohorts) lives on their
# own pages and picks colors there — generating colors for them
# would flood the DB with UPDATEs for channels that will never be
# plotted on a shared chart. Mirrors the cohort allowlist above.
_CHART_COLOR_TYPES = frozenset(_TOP5_COHORT_TYPES)


@st.cache_data(ttl=1800, show_spinner=False)
def _load_colors() -> dict[str, tuple[str, str]]:
    """Map channel name → (primary, secondary) colors for ALL channels.

    Two-tier behaviour to avoid the 8-second-freeze trap:
      1. Read whatever's already in `ch.color` / `ch.color2` for every
         channel, regardless of entity_type. Federations, Players,
         OtherClubs etc. show up here with their existing colors —
         the per-row dual-dot badges on those pages render correctly.
      2. Auto-assign + DB-persist a color ONLY for Club/League rows
         that don't have one yet. Other entity types fall back to a
         neutral grey in the consumer when missing — they don't get
         randomized palette entries assigned at page-render time
         (that was the source of the WC2026 slowdown).

    Cached at 30-min TTL.
    """
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
    # Channels that genuinely need a palette slot (only Club/League)
    needs_color: list[dict] = []

    for ch in all_channels:
        if ch.get("color"):
            c1 = ch["color"]
            c2 = ch.get("color2") or "#FFFFFF"
            color_map[ch["name"]] = (c1, c2)
            used_colors.add(c1)
        elif ch.get("entity_type") in _CHART_COLOR_TYPES:
            # Only Club/League rows trigger the auto-assign + DB write
            needs_color.append(ch)
        # Else: Player/Federation/etc. without color — leave out of the
        # map; the consumer falls back to neutral grey (#888) or its
        # own per-entity default.

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
        # Overall: Club + League channels, excluding Players /
        # Federations / NFL / F1 / WC2026 (each has its own page).
        return [ch for ch in channels if is_top5_cohort(ch)]
    return [
        ch for ch in channels
        if is_top5_cohort(ch)
        and COUNTRY_TO_LEAGUE.get(ch.get("country", ""), ch.get("country", "")) == league
    ]


def get_league_for_channel(ch: dict) -> str:
    """Get league name for a channel."""
    return COUNTRY_TO_LEAGUE.get(ch.get("country", ""), ch.get("country", ""))


def render_club_header(channel: dict, all_channels: list[dict]) -> None:
    """Render a channel's name with the standard marker (dual-dot for
    clubs, country flag for league channels) and inline subscriber
    ranks. Works for both clubs and league channels — ranks are only
    computed against the clubs cohort, so a league channel just gets
    its flag + name with no rank suffix."""
    from src.dot import channel_badge

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

    # Marker before the name: dual-dot for clubs, country flag for the
    # league channel (channel_badge handles both via entity_type).
    color_map = get_global_color_map() or {}
    dual_map = get_global_color_map_dual() or {}
    badge = channel_badge(channel, color_map, dual_map, 18)

    # Link the name to the channel's YouTube page (handle preferred,
    # fallback to canonical /channel/<id> URL). Inherit color so the
    # link doesn't get the default Streamlit blue.
    handle = (channel.get("handle") or "").lstrip("@").strip()
    yt_id = channel.get("youtube_channel_id") or ""
    if handle:
        yt_url = f"https://www.youtube.com/@{handle}"
    elif yt_id:
        yt_url = f"https://www.youtube.com/channel/{yt_id}"
    else:
        yt_url = ""
    name_html = (
        f"<a href='{yt_url}' target='_blank' rel='noopener' "
        f"style='color:inherit;text-decoration:none' "
        f"onmouseover=\"this.style.textDecoration='underline'\" "
        f"onmouseout=\"this.style.textDecoration='none'\">{channel['name']}</a>"
        if yt_url else channel["name"]
    )

    st.markdown(
        f"<h3 style='margin:0;display:flex;align-items:center;gap:10px'>"
        f"<span style='display:inline-flex;align-items:center'>{badge}</span>"
        f"<span>{name_html}{rank_html}</span></h3>",
        unsafe_allow_html=True,
    )


def render_league_header(league_name: str,
                         channels_in_scope: list[dict] | None = None,
                         extra_suffix: str = "") -> None:
    """Render a league header in the same visual signature as
    `render_club_header` — flag + league name + grey suffix.

    suffix defaults to a club-count breakdown when channels_in_scope is
    provided ("20 clubs · 1 league channel"), or you can override with
    extra_suffix (e.g. "All Leagues" / "Leagues only · 5 channels").
    """
    flag = LEAGUE_FLAG.get(league_name, "")
    if extra_suffix:
        suffix = extra_suffix
    elif channels_in_scope is not None:
        n_clubs = sum(1 for c in channels_in_scope
                      if c.get("entity_type") not in ("League", "Player",
                                                      "Federation", "OtherClub",
                                                      "WomenClub"))
        n_lg = sum(1 for c in channels_in_scope
                   if c.get("entity_type") == "League")
        bits = [f"{n_clubs} clubs"]
        if n_lg:
            bits.append(f"{n_lg} league channel")
        suffix = " · ".join(bits)
    else:
        suffix = ""
    suffix_html = (
        f"<span style='color:#888;font-size:0.95rem;font-weight:400;margin-left:14px'>"
        f"{suffix}</span>" if suffix else ""
    )
    st.markdown(
        f"<h3 style='margin:0;display:flex;align-items:center;gap:10px'>"
        f"<span style='font-size:1.2em'>{flag}</span>"
        f"<span>{league_name}{suffix_html}</span></h3>",
        unsafe_allow_html=True,
    )
