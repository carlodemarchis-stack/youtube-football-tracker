"""Single source of truth for the app's custom-HTML color palette.

Streamlit's *native* widgets (sidebar, inputs, charts chrome) are themed
via .streamlit/config.toml's [theme] block. But everything we render
through st.markdown(..., unsafe_allow_html=True) and components.html(...)
— KPI cards, the WC2026 tables, the Home leaderboards, the Trends
charts — uses raw hex literals that the config theme cannot reach
(components.html renders in isolated iframes, so global CSS never
touches it). This module centralizes those literals.

How to reskin: set the YTFT_THEME env var to a palette name below, or
edit/add a palette here. The default ("dark") values are intentionally
*byte-for-byte the historical hardcoded hex*, so adopting this module
anywhere is a visual no-op until a value is actually changed — that's
the contract that keeps the rollout regression-free.

Only structural tokens live here (background / text / border / accent /
semantic colors). Team brand colors (club reds/blues) are deliberately
NOT themed — they must stay accurate regardless of skin.
"""
from __future__ import annotations

import os

# name -> {token: hex}. "dark" == today's exact literals.
_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
        "BG":            "#0E1117",  # app / iframe background
        "SURFACE":       "#1A1C24",  # cards, table row hover
        "SURFACE_2":     "#22252E",  # raised hover
        "BORDER":        "#262730",  # row separators / chart gridlines
        "BORDER_STRONG": "#444444",  # thead underline
        "TEXT":          "#FAFAFA",  # primary text
        "MUTED":         "#888888",  # secondary text
        "MUTED_2":       "#AAAAAA",  # tertiary text
        "WHITE":         "#FFFFFF",  # inner dot / hard contrast
        "ACCENT":        "#636EFA",  # primary accent (indigo)
        "LINK":          "#58A6FF",  # links / first KPI card
        "POS":           "#00CC96",  # positive / gains (green)
        "NEG":           "#EF553B",  # negative / losses (red)
        "WARN":          "#FFA15A",  # warnings / pre-beta banner (orange)
        "PURPLE":        "#AB63FA",
        "CYAN":          "#19D3F3",
    },
}

_NAME = os.getenv("YTFT_THEME", "dark").strip().lower()
C: dict[str, str] = _PALETTES.get(_NAME, _PALETTES["dark"])

# Module-level convenience names: `from src.theme import TEXT, BORDER, ...`
BG            = C["BG"]
SURFACE       = C["SURFACE"]
SURFACE_2     = C["SURFACE_2"]
BORDER        = C["BORDER"]
BORDER_STRONG = C["BORDER_STRONG"]
TEXT          = C["TEXT"]
MUTED         = C["MUTED"]
MUTED_2       = C["MUTED_2"]
WHITE         = C["WHITE"]
ACCENT        = C["ACCENT"]
LINK          = C["LINK"]
POS           = C["POS"]
NEG           = C["NEG"]
WARN          = C["WARN"]
PURPLE        = C["PURPLE"]
CYAN          = C["CYAN"]

# KPI card accent cycle — was analytics.KPI_PALETTE (same order/values).
KPI_PALETTE: tuple[str, ...] = (LINK, WARN, POS, NEG, PURPLE, ACCENT, CYAN)

# Categorical cycle for charts / per-channel colors — was
# analytics.CHANNEL_PALETTE (same order/values; first six map to tokens).
CHANNEL_PALETTE: list[str] = [
    ACCENT, NEG, POS, PURPLE, WARN,
    CYAN, "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
    "#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF",
]
