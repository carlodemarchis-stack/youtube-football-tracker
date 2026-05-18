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
# Bumped to v2 when og:image self-hosting was added — guarantees a
# redeploy re-patches even if an old (text-only) patched index.html
# somehow persisted in the container.
_MARKER = "<!-- ytft-og v2 -->"
# Optional social card, committed in the repo. If present it's copied
# into Streamlit's static dir at boot (served at <public>/og-card.png,
# same mechanism as /favicon.png) and a large-image card is emitted.
# If absent, falls back to the text-only summary card. Must be 1200x630.
_IMG_NAME = "og-card.png"
_IMG_REPO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", _IMG_NAME,
)


@st.cache_resource(show_spinner=False)
def inject_og_tags() -> bool:
    """Patch Streamlit's static index.html <head> with OG/Twitter meta.
    Runs once per server process (cache_resource). Returns True if the
    file ended up patched, False on any problem (never raises)."""
    try:
        static_dir = os.path.join(os.path.dirname(st.__file__), "static")
        idx = os.path.join(static_dir, "index.html")
        if not os.path.exists(idx) or not os.access(idx, os.W_OK):
            return False

        # Self-host the card: copy repo asset → Streamlit static dir so
        # it's reachable at an absolute URL crawlers can fetch. Re-copied
        # each boot (cheap, keeps it in sync with the committed asset).
        img_url = ""
        try:
            if os.path.exists(_IMG_REPO) and os.access(static_dir, os.W_OK):
                import shutil
                shutil.copyfile(_IMG_REPO,
                                os.path.join(static_dir, _IMG_NAME))
                img_url = f"{_PUBLIC_URL}/{_IMG_NAME}"
        except Exception:
            img_url = ""

        with open(idx, encoding="utf-8") as f:
            html = f.read()
        if _MARKER in html:
            return True  # already patched (idempotent)

        if img_url:
            _img_tags = (
                f'    <meta property="og:image" content="{img_url}" />\n'
                f'    <meta property="og:image:secure_url" content="{img_url}" />\n'
                f'    <meta property="og:image:type" content="image/png" />\n'
                f'    <meta property="og:image:width" content="1200" />\n'
                f'    <meta property="og:image:height" content="630" />\n'
                f'    <meta property="og:image:alt" content="{_TITLE}" />\n'
                f'    <meta name="twitter:image" content="{img_url}" />\n'
            )
            _tw_card = "summary_large_image"
        else:
            _img_tags = ""
            _tw_card = "summary"

        tags = (
            f'{_MARKER}\n'
            f'    <title>{_TITLE}</title>\n'
            f'    <meta name="description" content="{_DESC}" />\n'
            f'    <meta property="og:type" content="website" />\n'
            f'    <meta property="og:site_name" content="YouTube Football Tracker" />\n'
            f'    <meta property="og:title" content="{_TITLE}" />\n'
            f'    <meta property="og:description" content="{_DESC}" />\n'
            f'    <meta property="og:url" content="{_PUBLIC_URL}" />\n'
            f'{_img_tags}'
            f'    <meta name="twitter:card" content="{_tw_card}" />\n'
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
