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
from src.cached_db import (
    get_all_channels as _cached_channels,
    get_last_fetch_time as _cached_last_fetch,
)
from src.analytics import fmt_num, kpi_row
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
all_channels = get_global_channels() or _cached_channels(db)
color_map = get_global_color_map() or {}
dual = get_global_color_map_dual() or {}

# Profiles are computed against ALL clubs (peer references stable across
# scope changes). Heavy work is microseconds — no caching needed beyond
# Streamlit's per-process state.
profiles = _prof.compute_all_profiles(all_channels)


# ── AI profile sentence ──────────────────────────────────────
# Reads first from dashboard_cache (pre-generated nightly by the
# rebuild_all cron). Falls back to live Claude call only when the
# cache miss — that path is also Streamlit-cached for 1h.
from src import dashboard_cache as _dc

@st.cache_data(ttl=3600, show_spinner=False)
def _profile_sentence(channel_id: str) -> str | None:
    """Get the pre-generated profile sentence for a club, or generate
    on demand if cache miss. Returns empty string for typical clubs
    (no tags fired). None on failure / no Anthropic key."""
    # Try cache first
    try:
        row = _dc.read(db, "profile_sentence", channel_id)
        if row and row.get("payload", {}).get("text"):
            return row["payload"]["text"]
    except Exception:
        pass
    # Fallback: live generate (most clubs already cached, this only
    # fires for a single newly-flagged channel between cron runs)
    chan = next((c for c in all_channels if c["id"] == channel_id), None)
    if not chan:
        return None
    return _prof.generate_profile_sentence(chan, profiles[channel_id])
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
        for axis in ("vps", "vpv", "spv", "spy", "vpy", "shorts_share", "engagement", "pace_change"):
            if axis not in spotlight:
                continue
            c, lens, z, label = spotlight[axis]
            badge = channel_badge(c, color_map, dual, 14)
            cp = profiles[c["id"]]
            lens_label = (f"vs {cp['league']['name']}") if lens == "league" else \
                         f"vs {cp['size']['bucket']} clubs"
            cards += f"""
            <div style="flex:0 1 calc(33.33% - 8px);box-sizing:border-box;
                       background:#1a1c24;border-left:3px solid #58A6FF;
                       border-radius:6px;padding:10px 12px;
                       font-family:'Source Sans Pro',sans-serif">
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
        # Up to 8 cards in a 3-per-row grid → 1–3 rows. Each card ~120px
        # tall + 10px gap. Pad a bit so nothing clips.
        n = len(spotlight)
        rows = (n + 2) // 3
        components.html(
            f"""<div style="display:flex;gap:10px;flex-wrap:wrap">{cards}</div>""",
            height=rows * 130 + 20,
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

    # AI-generated 1-sentence profile (silently no-op without ANTHROPIC_API_KEY)
    _sent = _profile_sentence(g_club["id"])
    if _sent:
        st.markdown(
            f'<div style="font-style:italic;color:#cccccc;line-height:1.6;'
            f'border-left:3px solid #58A6FF;padding:8px 14px;margin:6px 0 14px 0;'
            f'background:#1a1c24;border-radius:4px">'
            f'<span style="color:#888;font-size:11px;font-weight:600;letter-spacing:0.5px;'
            f'text-transform:uppercase;font-style:normal">🤖 Profile read</span>'
            f'<div style="margin-top:4px">{_sent}</div></div>',
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

    # 8 metrics in 2 rows of 4 — keeps each cell legible at typical viewports.
    AXES_ORDER = ("vps", "vpv", "spv", "spy", "vpy", "shorts_share", "engagement", "pace_change")

    def _format_metric(axis: str, val):
        if val is None:
            return "—"
        if axis == "shorts_share":
            return f"{val * 100:.0f}% Shorts"
        if axis == "engagement":
            return f"{val * 100:.2f}%"
        if axis == "pace_change":
            return f"{val:.2f}×"   # >1 = ramping up, <1 = slowing
        if val >= 1000:
            return fmt_num(int(val))
        return f"{val:.1f}"

    for row_start in (0, 4):
        st.markdown(kpi_row([
            (_prof.AXIS_LABEL[axis], _format_metric(axis, r.get(axis)))
            for axis in AXES_ORDER[row_start:row_start + 4]
        ]), unsafe_allow_html=True)
    st.stop()


# ──────────────────────────────────────────────────────────────
# All flagged channels table
# ──────────────────────────────────────────────────────────────
st.subheader("Profile breakdown")
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
        sentence = _profile_sentence(c["id"]) or ""
        sentence_html = (
            f'<div style="font-size:12px;color:#aaa;font-style:italic;'
            f'line-height:1.45;margin-top:4px;max-width:520px">{sentence}</div>'
        ) if sentence else ""
        rows_html += f"""<tr>
            <td style="padding:8px 12px;vertical-align:top">{badge}</td>
            <td style="padding:8px 12px;vertical-align:top">
              <div style="font-weight:600;color:#FAFAFA">{c["name"]}</div>
              <div style="font-size:11px;color:#888">
                {p["league"]["name"]} · {p["size"]["bucket"]} · {fmt_num(subs)} subs
              </div>
              {sentence_html}
            </td>
            <td style="padding:8px 12px;vertical-align:top">{league_tags}</td>
            <td style="padding:8px 12px;vertical-align:top">{size_tags}</td>
            <td style="padding:8px 12px;vertical-align:top;text-align:right;font-weight:600;color:#58A6FF">{p["tag_count"]}</td>
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
    """, height=len(flagged) * 95 + 80, scrolling=False)


# ──────────────────────────────────────────────────────────────
# Tag glossary (collapsible)
# ──────────────────────────────────────────────────────────────
with st.expander("📖 Tag glossary — what each label means"):
    st.markdown("""
Each axis is a structural ratio. A tag fires when a channel sits
≥1.5 *median-absolute-deviations* (log-scaled) from peer median, on
either lens. The first tag fires when the ratio is **above** peers,
the second when it's **below**.
""")

    st.markdown("---")
    st.markdown("### 1. Season VPS — current activity per sub")
    st.caption("Season views ÷ total subscribers. How much each sub watches in the *current* season.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**👀 Active audience**  *(high)*  \n"
            "Each sub is watching a meaningful amount of season content. The fanbase shows up "
            "for new posts."
        )
    with g2:
        st.markdown(
            "**😴 Dormant audience**  *(low)*  \n"
            "Subs collected but barely watching this season. Common for legacy channels whose "
            "audience doesn't refresh on new content."
        )

    st.markdown("---")
    st.markdown("### 2. Season VPV — current per-upload hit rate")
    st.caption("Season views ÷ season videos. The current-season hit rate, fair to all clubs.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**🎯 Current hits**  *(high)*  \n"
            "Each season upload lands well above peer median. Right strategy executing now."
        )
    with g2:
        st.markdown(
            "**❄️ Cold streak**  *(low)*  \n"
            "Season uploads underperform peers per piece. Could be over-producing, off-strategy, "
            "or audience saturation."
        )

    st.markdown("---")
    st.markdown("### 3. SPV — Subs per Video  *(audience-to-output)*")
    st.caption("Subscribers ÷ video count. Audience size relative to content shipped.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**📺 Audience > output**  *(high)*  \n"
            "Sub base much larger than content output suggests. Legacy giants (Real, Barça) "
            "OR clubs that suddenly got popular (Como post-promotion, viral teams)."
        )
    with g2:
        st.markdown(
            "**💪 Output > audience**  *(low)*  \n"
            "Posts way more than the audience size warrants. Production-heavy strategy "
            "that hasn't (yet) translated to subs — ambitious investment in content."
        )

    st.markdown("---")
    st.markdown("### 4. SPY — Subs per Year  *(growth velocity)*")
    st.caption("Subscribers ÷ channel age in years. Audience acquisition rate over lifetime.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**🚀 Fast-growing**  *(high)*  \n"
            "Acquiring subs faster than peers. Either a young channel ramping up or an "
            "older channel with recent acceleration. A 'rising story' tag."
        )
    with g2:
        st.markdown(
            "**🐢 Stagnant growth**  *(low)*  \n"
            "Sub growth flatlined or never took off. Older channels with low yearly intake — "
            "could indicate disinterest, language barriers, or limited reach."
        )

    st.markdown("---")
    st.markdown("### 5. VPY — Videos per Year  *(cadence)*")
    st.caption("Video count ÷ channel age in years. Sustained output rate.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**📡 Steady producer**  *(high)*  \n"
            "Posts a high volume year after year. Disciplined publishing operation, "
            "usually backed by a dedicated content team."
        )
    with g2:
        st.markdown(
            "**🔇 Low cadence**  *(low)*  \n"
            "Posts much less than peers. Could be understaffed, a curated-quality choice, "
            "or a smaller club without a content operation."
        )

    st.markdown("---")
    st.markdown("### 6. SHARE — Format mix  *(strategic choice)*")
    st.caption("Season Shorts ÷ total season videos. The TikTok-vs-traditional decision.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**⚡ Shorts-first**  *(high)*  \n"
            "60%+ of season uploads are Shorts. Mobile-first / vertical-clip strategy. "
            "Often newer or strategy-shifted clubs."
        )
    with g2:
        st.markdown(
            "**🎬 Long-form focused**  *(low)*  \n"
            "Most of the season catalog is Long videos. Traditional highlights, press "
            "conferences, longer programming. Often legacy operations slower to shift to Shorts."
        )
    # getattr fallback in case Streamlit Cloud has a stale module cache
    # mid-deploy — fall back to the older constant name (or its value).
    _min_share = getattr(_prof, "MIN_SEASON_VIDEOS_FOR_SHARE",
                         getattr(_prof, "MIN_SEASON_VIDEOS", 20))
    st.caption(f"Computed only when season catalog ≥ {_min_share} videos "
               "(below that, single uploads swing the percentage too much).")

    st.markdown("---")
    st.markdown("### 7. Engagement rate  *(season quality)*")
    st.caption("(Season likes + comments) ÷ season views. What share of viewers actually react.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**💬 Engaged community**  *(high)*  \n"
            "Viewers like and comment at a rate well above peers. The audience is participating, "
            "not just consuming."
        )
    with g2:
        st.markdown(
            "**🤐 Silent viewers**  *(low)*  \n"
            "High views but minimal reactions. Algorithm-driven traffic without an active community, "
            "or content that doesn't invite response."
        )

    st.markdown("---")
    st.markdown("### 8. Pace change  *(cadence trend)*")
    st.caption("Current season cadence ÷ lifetime average cadence. >1 = ramping up, <1 = slowing.")
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(
            "**📈 Ramping up**  *(high)*  \n"
            "Current output rate is well above the channel's historical average. New strategy, "
            "post-promotion surge, or expanded content team."
        )
    with g2:
        st.markdown(
            "**🐌 Slowing down**  *(low)*  \n"
            "Posting much less than the channel's career average. Resource cuts, strategy shift, "
            "or reduced fixture coverage."
        )

    st.markdown("---")
    st.markdown("### Common patterns when tags combine")
    st.markdown("""
| Pattern | Tags | Story |
|---|---|---|
| **The Como** | 📺 + 🚀 + 💧 | Audience exploded (often post-promotion), content & engagement haven't caught up |
| **The Sunderland** | 🌱 alone | Small but real community |
| **The legacy giant** | 📺 + 🚀 + sometimes 💧 | Real / Barça / Bayern / Juve — big audience, fast cumulative growth, sometimes weak per-sub engagement |
| **The volume laggard** | 📉 + 💪 (+ 🐢) | Posts a lot, doesn't get views, audience small |
| **The quality club** | 🚀 + 🔇 | Few videos, each one lands |

Two-lens agreement is the strongest signal — Como tagging in BOTH "vs Serie A"
AND "vs Small clubs" means the pattern isn't a league quirk, it's genuinely
unusual. Disagreement is interesting too: a club that's normal-for-its-size
but weird-for-its-league is making a strategic choice that diverges from its market.
""")

    st.markdown("---")
    st.markdown("### How the math works")
    st.markdown("""
Each ratio is **log-transformed** before scoring — audience numbers span 4
orders of magnitude in a single league, so without log every giant would
always look extreme.

**z-score** = how many median-absolute-deviations away from the peer median
this club's log-ratio sits. We use MAD (not stdev) so a few extreme clubs
don't distort the spread for everyone else. Tags fire at **|z| ≥ 1.5**.

**Two lenses** run in parallel:
- **vs League peers** — structural / cultural context (same competition)
- **vs Size cohort** — strategic context (Tiny / Small / Mid / Giant by sub count)

Profiles recompute live on every page load — all data sits on the
`channels` table, microseconds of math.
""")
