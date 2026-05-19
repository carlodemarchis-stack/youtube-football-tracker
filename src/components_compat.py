"""Drop-in replacement for ``streamlit.components.v1`` → ``st.iframe``.

``st.components.v1.html`` is deprecated in Streamlit 1.56 and removed in
the release after 2026-06-01. ``st.iframe`` (added in 1.56) embeds a raw
HTML string via the SAME ``srcdoc`` mechanism, so:

  * embedded ``<script>`` still executes (our sortable .ac-table /
    .wc-tbl tables and the dot/timeline renderers depend on this),
  * the iframe always has ``scrolling=True`` (Streamlit hardcodes it),
    matching every call site that passed ``scrolling=True``,
  * default ``width='stretch'`` == the old full-container width.

So routing the old ``components.html(html, height=H, scrolling=...)``
calls to ``st.iframe(html, height=H)`` is behaviour-preserving. This
module mirrors the old ``components.v1`` surface (``.html`` and
``.iframe``) so call sites only need their import line swapped:

    import streamlit.components.v1 as components
    →
    from src import components_compat as components
"""
from __future__ import annotations

import streamlit as st

# components.v1.html's historical default height when unspecified.
_LEGACY_DEFAULT_HEIGHT = 150


def html(html: str, width: int | None = None, height: int | None = None,
         scrolling: bool = False, **_ignore):
    """``components.v1.html`` shim. ``scrolling`` is accepted for
    signature compatibility but is a no-op: ``st.iframe`` always
    enables scrolling, which is what every ``scrolling=True`` call
    wanted and is harmless for the ``scrolling=False`` ones (heights
    are tuned to fit, so nothing actually overflows)."""
    # st.iframe requires height > 0 (validate_height raises on <= 0).
    # components.v1.html accepted height=0 for invisible "run this JS"
    # injectors (e.g. app.py's video-popup overlay). Clamp to 1px —
    # imperceptible, and the srcdoc JS still executes.
    h = max(1, int(height)) if height is not None else _LEGACY_DEFAULT_HEIGHT
    if width is not None:
        return st.iframe(html, width=int(width), height=h)
    return st.iframe(html, height=h)


def iframe(src: str, width: int | None = None, height: int | None = None,
           scrolling: bool = False, **_ignore):
    """``components.v1.iframe`` shim (URL src). Same mapping as ``html``;
    kept so the import swap is total even if a caller used it."""
    h = int(height) if height is not None else _LEGACY_DEFAULT_HEIGHT
    if width is not None:
        return st.iframe(src, width=int(width), height=h)
    return st.iframe(src, height=h)
