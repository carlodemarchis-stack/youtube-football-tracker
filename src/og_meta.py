"""Server-side social-preview (Open Graph / Twitter Card) meta tags.

Why this exists / why it's done this way: Streamlit has no native OG
support, and `st.markdown`/`components.html` inject into the page
*body* — link-unfurl crawlers (LinkedIn, Slack, X, newsletters) don't
run JS and only read the server-rendered HTML <head>. The only thing
that actually makes a shared link preview work for a Streamlit app is
patching Streamlit's static index.html <head> on the server.

`inject_og_tags()` does that idempotently, guarded by @st.cache_resource
so it runs exactly once per server process. On a Railway redeploy the
container restarts with a fresh (unpatched) index.html and this re-runs,
so it self-heals. Fully defensive — any failure is swallowed; the app
must never break over a meta tag.

NOTE: og:image is intentionally omitted — a proper 1200×630 card image
needs to be created + hosted first. Title + description + url already
give LinkedIn/newsletter a real preview (vs. a bare URL); the image is
a follow-up asset, not a blocker.
"""
from __future__ import annotations

import os

import streamlit as st

_PUBLIC_URL = "https://ytft.aguywithascarf.com"
_TITLE = "YouTube Football Tracker — A Guy With A Scarf"
_DESC = (
    "Track YouTube performance for 100+ football clubs across Europe's "
    "top 5 leagues — daily recaps, season trends, all-time leaderboards "
    "— plus the road to the FIFA World Cup 2026."
)
_MARKER = "<!-- ytft-og -->"


@st.cache_resource(show_spinner=False)
def inject_og_tags() -> bool:
    """Patch Streamlit's static index.html <head> with OG/Twitter meta.
    Runs once per server process (cache_resource). Returns True if the
    file ended up patched, False on any problem (never raises)."""
    try:
        idx = os.path.join(os.path.dirname(st.__file__), "static",
                           "index.html")
        if not os.path.exists(idx) or not os.access(idx, os.W_OK):
            return False
        with open(idx, encoding="utf-8") as f:
            html = f.read()
        if _MARKER in html:
            return True  # already patched (idempotent)

        tags = (
            f'{_MARKER}\n'
            f'    <title>{_TITLE}</title>\n'
            f'    <meta name="description" content="{_DESC}" />\n'
            f'    <meta property="og:type" content="website" />\n'
            f'    <meta property="og:site_name" content="YouTube Football Tracker" />\n'
            f'    <meta property="og:title" content="{_TITLE}" />\n'
            f'    <meta property="og:description" content="{_DESC}" />\n'
            f'    <meta property="og:url" content="{_PUBLIC_URL}" />\n'
            f'    <meta name="twitter:card" content="summary" />\n'
            f'    <meta name="twitter:title" content="{_TITLE}" />\n'
            f'    <meta name="twitter:description" content="{_DESC}" />'
        )

        # Replace Streamlit's default <title> with our marker+tags so
        # the static title is meaningful too. Fall back to inserting
        # before </head> if the default title isn't found verbatim.
        if "<title>Streamlit</title>" in html:
            html = html.replace("<title>Streamlit</title>", tags, 1)
        elif "</head>" in html:
            html = html.replace("</head>", f"    {tags}\n  </head>", 1)
        else:
            return False

        with open(idx, "w", encoding="utf-8") as f:
            f.write(html)
        return True
    except Exception:
        return False
