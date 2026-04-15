from __future__ import annotations

import os
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.growth import group_by_channel, delta
from src.filters import get_global_color_map, get_global_color_map_dual
from src.channels import COUNTRY_TO_LEAGUE

load_dotenv()

st.title("YouTube Football Tracker")

# ── Leagues covered ─────────────────────────────────────────────
try:
    _chs = st.session_state.get("_global_channels") or []
    if not _chs:
        SUPABASE_URL = os.getenv("SUPABASE_URL", "")
        SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
        if SUPABASE_URL and SUPABASE_KEY:
            _chs = Database(SUPABASE_URL, SUPABASE_KEY).get_all_channels()
    _league_counts: dict[str, int] = {}
    for _c in _chs:
        if _c.get("entity_type") == "League":
            continue
        _lg = COUNTRY_TO_LEAGUE.get((_c.get("country") or "").upper(), _c.get("country") or "—")
        _league_counts[_lg] = _league_counts.get(_lg, 0) + 1
    if _league_counts:
        _parts = [f"**{lg}** ({n})" for lg, n in sorted(_league_counts.items(), key=lambda x: -x[1])]
        st.caption(f"Covering {len(_league_counts)} leagues: " + " · ".join(_parts))
except Exception:
    pass

# ── Biggest gainers this week (snapshot-driven) ─────────────────
try:
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    if SUPABASE_URL and SUPABASE_KEY:
        _db = Database(SUPABASE_URL, SUPABASE_KEY)
        _channels = _db.get_all_channels()
        _ch_by_id = {c["id"]: c for c in _channels}
        _since = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
        _snaps = _db.get_all_snapshots(since_date=_since)
        _by_ch = group_by_channel(_snaps)

        _gainers = []
        for cid, s in _by_ch.items():
            ch = _ch_by_id.get(cid)
            if not ch or ch.get("entity_type") == "League" or len(s) < 2:
                continue
            d7 = delta(s, "subscriber_count", 7)
            if d7 is None:
                continue
            _gainers.append({"name": ch["name"], "subs": s[-1].get("subscriber_count", 0) or 0, "d7": d7, "handle": ch.get("handle", "")})

        if _gainers:
            _gainers.sort(key=lambda g: g["d7"], reverse=True)
            top5 = _gainers[:5]
            color_map = get_global_color_map()
            dual = get_global_color_map_dual()
            st.subheader("🔥 Biggest gainers this week")
            rows = ""
            for i, g in enumerate(top5, 1):
                c1, c2 = dual.get(g["name"], (color_map.get(g["name"], "#636EFA"), "#FFFFFF"))
                dot = f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative"><span style="display:block;width:7px;height:7px;border-radius:50%;background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>'
                col = "#00CC96" if g["d7"] > 0 else ("#EF553B" if g["d7"] < 0 else "#888")
                sgn = "+" if g["d7"] >= 0 else ""
                rows += f"""<tr>
                    <td style="padding:6px 12px;color:#888">{i}</td>
                    <td style="padding:6px 12px">{dot}</td>
                    <td style="padding:6px 12px">{g['name']}</td>
                    <td style="padding:6px 12px;text-align:right">{fmt_num(g['subs'])}</td>
                    <td style="padding:6px 12px;text-align:right;color:{col}">{sgn}{fmt_num(g['d7'])}</td>
                </tr>"""
            components.html(f"""
            <style>
              .home-g {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                         font-family:"Source Sans Pro",sans-serif; }}
              .home-g th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
              .home-g td {{ border-bottom:1px solid #262730; }}
            </style>
            <table class="home-g"><thead><tr>
              <th>#</th><th></th><th>Club</th>
              <th style="text-align:right">Subscribers</th>
              <th style="text-align:right">Δ Subs 7d</th>
            </tr></thead><tbody>{rows}</tbody></table>
            """, height=len(top5) * 37 + 80, scrolling=False)
except Exception:
    pass  # Snapshot data may not exist yet; degrade silently
st.markdown(
    """
    Track YouTube stats for top leagues football clubs.

    ### Global filter
    At the top of every page you'll find a cascading filter that sets the zoom level
    for the whole site. Your selection persists as you navigate.

    - **All Leagues** — site-wide view. A secondary dropdown lets you pick the
      *scope*:
        - *Overall* — everything aggregated by league
        - *Leagues only* — just league channels
        - *All clubs* — all clubs across every league
    - **One League** — narrows to a single competition; a club dropdown lets you
      also pick *All Clubs* or *All Clubs + League channel*
    - **One Club** — focuses every page on a single channel

    ### Pages
    - **Channels** — league and club rankings with subscribers, views,
      videos (long vs shorts), and engagement metrics; charts adapt to your
      filter's zoom level
    - **Season 25/26** — current-season performance: videos published since
      August 2025, split by long-form vs Shorts
    - **Top Videos** — the top 100 most-viewed videos for your current
      selection, with ranks, years, and theme breakdown
    - **Compare** — pick any clubs across leagues and line them up side by side
    - **AI Analysis** — Claude-powered insights for the selected club
    """
)

st.markdown("---")
st.caption("Data sourced from YouTube Data API v3. Stats update on demand.")
