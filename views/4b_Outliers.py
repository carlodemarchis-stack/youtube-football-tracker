"""Outliers — channels whose profile sits unusually far from peers.

Each club is compared against:
  1. Its league (Serie A / Premier League / La Liga / Bundesliga / Ligue 1)
  2. Its size cohort (Tiny / Small / Mid / Giant by sub count, across leagues)

The two lenses tell different stories — sometimes they agree (strongest
signal), sometimes they disagree (the club is normal-for-its-size but
weird-for-its-league, or vice versa). Both are surfaced.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.filters import (
    get_global_filter, get_global_channels, get_global_color_map,
    get_global_color_map_dual, render_page_subtitle,
)
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG
from src.dot import channel_badge
from src.auth import require_login
from src import profile as _prof

load_dotenv()
require_login()

st.title("🎯 Outliers")
render_page_subtitle(
    "Clubs whose profile sits unusually far from their peers.",
    caveat=("Two lenses: against league peers, and against similar-sized "
            "clubs across leagues. Tags fire at |z| ≥ 1.5."),
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()
color_map = get_global_color_map() or {}
dual = get_global_color_map_dual() or {}

# Profiles are computed against ALL clubs (peer references stable across
# scope changes). Heavy work is microseconds — no caching needed beyond
# Streamlit's per-process state.
profiles = _prof.compute_all_profiles(all_channels)
clubs_only = [c for c in all_channels
              if c.get("entity_type") == "Club"
              and COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper())]

# Apply the global filter to control which clubs the page shows. Peer
# stats stay computed against the full set so a Serie-A-only view still
# uses the same Serie A medians.
g_league, g_club = get_global_filter()
if g_club:
    visible = [c for c in clubs_only if c["id"] == g_club["id"]]
elif g_league:
    visible = [c for c in clubs_only
               if COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper()) == g_league]
else:
    visible = clubs_only


# ──────────────────────────────────────────────────────────────
# Spotlight panel: most-outlier club per axis (one card per axis,
# the largest |z| in EITHER lens wins). Hidden in single-club view.
# ──────────────────────────────────────────────────────────────
def _largest_z_per_axis():
    """Returns {axis: (club, lens_name, z, label)} — the most extreme
    club per axis across both lenses, scoped to `visible`."""
    out: dict = {}
    for c in visible:
        p = profiles.get(c["id"])
        if not p:
            continue
        for lens_name in ("league", "size"):
            for axis, z, label in p[lens_name]["tags"]:
                cur = out.get(axis)
                if cur is None or abs(z) > abs(cur[2]):
                    out[axis] = (c, lens_name, z, label)
    return out


if not g_club and visible:
    st.subheader("✨ Spotlight")
    spotlight = _largest_z_per_axis()
    if spotlight:
        # Render as 5 small cards in a row (one per axis we have a hit for).
        cards = ""
        for axis in ("vps", "vpv", "spv", "spy", "vpy"):
            if axis not in spotlight:
                continue
            c, lens, z, label = spotlight[axis]
            badge = channel_badge(c, color_map, dual, 14)
            cp = profiles[c["id"]]
            lens_label = (f"vs {cp['league']['name']}") if lens == "league" else \
                         f"vs {cp['size']['bucket']} clubs"
            cards += f"""
            <div style="flex:1 1 0;min-width:180px;background:#1a1c24;
                       border-left:3px solid #58A6FF;border-radius:6px;
                       padding:10px 12px;font-family:'Source Sans Pro',sans-serif">
              <div style="font-size:11px;color:#888;text-transform:uppercase;
                         letter-spacing:0.5px;margin-bottom:4px">
                {_prof.AXIS_LABEL[axis]}
              </div>
              <div style="font-size:14px;font-weight:600;color:#FAFAFA;margin-bottom:6px">
                {label}
              </div>
              <div style="display:flex;align-items:center;gap:6px;font-size:13px;color:#FAFAFA">
                {badge} <span>{c["name"]}</span>
              </div>
              <div style="font-size:11px;color:#888;margin-top:4px">
                z = {z:+.1f} · {lens_label}
              </div>
            </div>
            """
        components.html(
            f"""<div style="display:flex;gap:10px;flex-wrap:wrap">{cards}</div>""",
            height=170,
        )
    else:
        st.caption("No outliers in scope.")


# ──────────────────────────────────────────────────────────────
# Single-club detail card — when filter selects one club
# ──────────────────────────────────────────────────────────────
if g_club:
    p = profiles.get(g_club["id"])
    if not p:
        st.info("No profile data for this channel.")
        st.stop()

    badge = channel_badge(g_club, color_map, dual, 18)
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;font-size:18px;'
        f'font-weight:600;margin:8px 0">{badge}{g_club["name"]} '
        f'<span style="color:#888;font-weight:400;font-size:14px">'
        f'· {p["league"]["name"]} · {p["size"]["bucket"]} ({fmt_num(g_club.get("subscriber_count") or 0)} subs)'
        f'</span></div>',
        unsafe_allow_html=True,
    )

    if not p["league"]["tags"] and not p["size"]["tags"]:
        st.success("✅ This channel sits within ±1.5σ of its peers on every axis "
                   "in both lenses — typical profile.")
    else:
        c1, c2 = st.columns(2)
        for col, lens, header in ((c1, "league", f"vs {p['league']['name']} peers"),
                                   (c2, "size",   f"vs {p['size']['bucket']} clubs (across leagues)")):
            with col:
                st.markdown(f"**{header}**")
                tags = p[lens]["tags"]
                if not tags:
                    st.caption("Within ±1.5σ on every axis.")
                else:
                    for axis, z, label in tags:
                        st.markdown(
                            f'<div style="background:#1a1c24;border-left:3px solid #58A6FF;'
                            f'padding:6px 10px;margin:4px 0;border-radius:4px">'
                            f'<span style="font-weight:600">{label}</span> '
                            f'<span style="color:#888;font-size:12px">'
                            f'· {_prof.AXIS_LABEL[axis]} · z={z:+.1f}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

    st.markdown("---")
    st.subheader("Ratios")
    r = p["ratios"]
    rcols = st.columns(5)
    for i, axis in enumerate(("vps", "vpv", "spv", "spy", "vpy")):
        val = r.get(axis)
        if val is None:
            label = "—"
        elif val >= 1000:
            label = fmt_num(int(val))
        else:
            label = f"{val:.1f}"
        rcols[i].metric(_prof.AXIS_LABEL[axis], label)
    st.stop()


# ──────────────────────────────────────────────────────────────
# All flagged channels table
# ──────────────────────────────────────────────────────────────
st.subheader("All flagged channels")
st.caption(f"{len(visible)} clubs in scope · sorted by total tag count")

# Filter to clubs with at least one tag
flagged = []
for c in visible:
    p = profiles.get(c["id"])
    if not p or p["tag_count"] == 0:
        continue
    flagged.append((c, p))
flagged.sort(key=lambda cp: -cp[1]["tag_count"])

if not flagged:
    st.info("No flagged outliers in this scope.")
else:
    rows_html = ""
    for c, p in flagged:
        badge = channel_badge(c, color_map, dual, 14)
        league_tags = " ".join(
            f'<span style="display:inline-block;background:#1a1c24;border:1px solid #2a2d36;'
            f'padding:2px 8px;border-radius:10px;font-size:11px;color:#FAFAFA;'
            f'margin:2px 4px 2px 0;white-space:nowrap" title="{_prof.AXIS_LABEL[axis]} · z={z:+.1f}">'
            f'{label}</span>'
            for axis, z, label in p["league"]["tags"]
        ) or '<span style="color:#666;font-size:11px">—</span>'
        size_tags = " ".join(
            f'<span style="display:inline-block;background:#1a1c24;border:1px solid #2a2d36;'
            f'padding:2px 8px;border-radius:10px;font-size:11px;color:#FAFAFA;'
            f'margin:2px 4px 2px 0;white-space:nowrap" title="{_prof.AXIS_LABEL[axis]} · z={z:+.1f}">'
            f'{label}</span>'
            for axis, z, label in p["size"]["tags"]
        ) or '<span style="color:#666;font-size:11px">—</span>'
        subs = int(c.get("subscriber_count") or 0)
        rows_html += f"""<tr>
            <td style="padding:8px 12px">{badge}</td>
            <td style="padding:8px 12px">
              <div style="font-weight:600;color:#FAFAFA">{c["name"]}</div>
              <div style="font-size:11px;color:#888">
                {p["league"]["name"]} · {p["size"]["bucket"]} · {fmt_num(subs)} subs
              </div>
            </td>
            <td style="padding:8px 12px">{league_tags}</td>
            <td style="padding:8px 12px">{size_tags}</td>
            <td style="padding:8px 12px;text-align:right;font-weight:600;color:#58A6FF">{p["tag_count"]}</td>
        </tr>"""

    components.html(f"""
    <style>
      .out-tbl {{ width:100%; border-collapse:collapse; font-size:14px;
                  color:#FAFAFA; font-family:"Source Sans Pro",sans-serif; }}
      .out-tbl th {{ padding:8px 12px; border-bottom:2px solid #444;
                     text-align:left; font-weight:600; }}
      .out-tbl td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
      .out-tbl tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="out-tbl">
      <thead><tr>
        <th></th>
        <th>Club</th>
        <th>vs League</th>
        <th>vs Size cohort</th>
        <th style="text-align:right"># tags</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
    """, height=len(flagged) * 60 + 80, scrolling=False)


# ──────────────────────────────────────────────────────────────
# How this works (collapsible)
# ──────────────────────────────────────────────────────────────
with st.expander("How this works"):
    st.markdown("""
**Five structural ratios** are computed per channel:

- **VPS** — Views per Subscriber (engagement)
- **VPV** — Views per Video (per-upload yield)
- **SPV** — Subs per Video (audience-to-output)
- **SPY** — Subs per Year (growth velocity)
- **VPY** — Videos per Year (cadence)

Each ratio is **log-transformed** before scoring (audience numbers span
4 orders of magnitude in a single league — without log, every giant
would always look extreme).

**z-score** = how many median-absolute-deviations away from the peer
median this club's log-ratio sits. Tags fire at |z| ≥ 1.5.

**Two lenses** run in parallel:
- **vs League peers** — the structural / cultural context
- **vs Size cohort** — strategic context, fixes the "giants are always weird" issue

A club with the same tag in both lenses (e.g. Como is both 📺 in Serie A
*and* 📺 vs Small clubs) is a strong signal. Disagreement is interesting
too — tells you the club is unusual in one frame but normal in another.

Profiles recompute live on every page load — all data sits on the
`channels` table, microseconds of math.
""")
