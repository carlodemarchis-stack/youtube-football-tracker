from __future__ import annotations

import os

import streamlit as st
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num, yt_popup_js
from src.filters import get_global_color_map, get_global_filter, get_global_channels, get_channels_for_filter, get_league_for_channel, render_page_subtitle
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG, get_season_since
from src.auth import require_premium

load_dotenv()
require_premium()

st.title("Compare")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

# Apply global filter to narrow the club pool
g_league, g_club = get_global_filter()
_daily_updated = db.get_last_fetch_time("daily")
render_page_subtitle("Side-by-side club comparison", updated_raw=_daily_updated)
if g_league:
    filtered_channels = get_channels_for_filter(all_channels, g_league)
else:
    filtered_channels = all_channels

clubs = [ch for ch in filtered_channels if ch.get("entity_type") != "League"]

if not clubs:
    st.warning("No clubs loaded yet.")
    st.stop()

# If a specific club is selected, pre-select it
if g_club:
    st.session_state["_compare_clubs"] = [g_club["name"]]

club_names = sorted([ch["name"] for ch in clubs])

# ── Preset buttons ────────────────────────────────────────────
st.subheader("Quick Comparisons")
col1, col2, col3, col4, col5 = st.columns(5)
preset = None
with col1:
    if st.button("Serie A Big 3"):
        preset = ["Juventus", "Inter", "AC Milan"]
with col2:
    if st.button("Top 5 by Subs"):
        top5 = sorted(clubs, key=lambda c: c.get("subscriber_count", 0), reverse=True)[:5]
        preset = [c["name"] for c in top5]
with col3:
    if st.button("Top 5 by Views"):
        top5 = sorted(clubs, key=lambda c: c.get("total_views", 0), reverse=True)[:5]
        preset = [c["name"] for c in top5]
with col4:
    if st.button("Top 5 by Season Views"):
        top5 = sorted(clubs, key=lambda c: int(c.get("season_views") or 0), reverse=True)[:5]
        preset = [c["name"] for c in top5]
with col5:
    if st.button("Bottom 5 by Subs"):
        bot5 = sorted(clubs, key=lambda c: c.get("subscriber_count", 0))[:5]
        preset = [c["name"] for c in bot5]

if preset:
    st.session_state["_compare_clubs"] = preset

# ── Club selector ─────────────────────────────────────────────
default = st.session_state.get("_compare_clubs", [])
default = [n for n in default if n in club_names]
selected = st.multiselect("Select 2-5 clubs to compare", club_names, default=default, max_selections=5)
st.session_state["_compare_clubs"] = selected

if len(selected) < 2:
    st.info("Select at least 2 clubs to compare.")
    st.stop()

# ── Get data ──────────────────────────────────────────────────
compare_channels = [ch for ch in clubs if ch["name"] in selected]
color_map = get_global_color_map()

# ── Pie charts ────────────────────────────────────────────────
st.subheader("Side by Side")
comp_df = pd.DataFrame(compare_channels)
comp_df = comp_df.sort_values("subscriber_count", ascending=False)
comp_df["_season_views"] = comp_df.apply(lambda r: int(r.get("season_views") or 0), axis=1)

def make_pie(data, val_col, title):
    fig = px.pie(
        data, values=val_col, names="name",
        color="name", color_discrete_map=color_map,
        title=title, hole=0.4,
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(showlegend=False, margin=dict(t=40, b=10, l=10, r=10), height=280)
    return fig

# Count season videos per channel
_season_vid_counts = {}
for ch in compare_channels:
    _sv = db.client.table("videos").select("id", count="exact") \
        .eq("channel_id", ch["id"]).gte("published_at", get_season_since(ch)).limit(1).execute()
    _season_vid_counts[ch["name"]] = _sv.count or 0
comp_df["_season_videos"] = comp_df["name"].map(_season_vid_counts)

pie_cols = st.columns(5)
with pie_cols[0]:
    st.plotly_chart(make_pie(comp_df, "subscriber_count", "Subscribers"), use_container_width=True)
with pie_cols[1]:
    st.plotly_chart(make_pie(comp_df, "total_views", "Total Views"), use_container_width=True)
with pie_cols[2]:
    st.plotly_chart(make_pie(comp_df, "video_count", "Videos"), use_container_width=True)
with pie_cols[3]:
    st.plotly_chart(make_pie(comp_df, "_season_views", "Season Views"), use_container_width=True)
with pie_cols[4]:
    st.plotly_chart(make_pie(comp_df, "_season_videos", "Season Videos"), use_container_width=True)

# ── Stats table ───────────────────────────────────────────────
rows = []
for ch in sorted(compare_channels, key=lambda c: c.get("subscriber_count", 0), reverse=True):
    avg = ch.get("total_views", 0) // max(ch.get("video_count", 1), 1)
    sv = int(ch.get("season_views") or 0)
    # Count season videos for this channel
    season_vids = db.client.table("videos").select("id", count="exact") \
        .eq("channel_id", ch["id"]).gte("published_at", get_season_since(ch)).limit(1).execute()
    s_vid_count = season_vids.count or 0
    s_avg = sv // max(s_vid_count, 1)
    rows.append({
        "Club": ch["name"],
        "Subscribers": fmt_num(ch.get("subscriber_count", 0)),
        "Total Views": fmt_num(ch.get("total_views", 0)),
        "Videos": fmt_num(ch.get("video_count", 0)),
        "Avg Views/Video": fmt_num(avg),
        "Season Views": fmt_num(sv),
        "Season Videos": fmt_num(s_vid_count),
        "Season Avg": fmt_num(s_avg),
    })
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── Top 10 videos from each club ──────────────────────────────
st.subheader("Top Videos Head-to-Head")
all_vids = []
for ch in compare_channels:
    vids = db.get_videos_by_channel(ch["id"])
    for v in vids[:10]:
        v["club_name"] = ch["name"]
        all_vids.append(v)

if all_vids:
    vdf = pd.DataFrame(all_vids).sort_values("view_count", ascending=False).reset_index(drop=True)
    vdf["rank"] = range(1, len(vdf) + 1)

    fig = px.bar(
        vdf, x="rank", y="view_count",
        color="club_name", color_discrete_map=color_map,
        hover_data=["title"],
        labels={"rank": "Rank", "view_count": "Views", "club_name": "Club"},
    )
    fig.update_layout(
        xaxis_title="Rank", yaxis_title="Views",
        legend_title="Club", margin=dict(t=20, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    import streamlit.components.v1 as components
    from src.filters import get_global_color_map_dual
    _dual = get_global_color_map_dual()
    _ch_by_name_cmp = {c["name"]: c for c in all_channels}
    _top = vdf.head(20)
    _rows = ""
    for i, r in enumerate(_top.itertuples(index=False), 1):
        yt_id = getattr(r, "youtube_video_id", "") or ""
        title = (getattr(r, "title", "") or "").replace("<", "&lt;").replace(">", "&gt;")
        club = getattr(r, "club_name", "") or ""
        _views = int(getattr(r, "view_count", 0) or 0)
        _likes = int(getattr(r, "like_count", 0) or 0)
        url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""
        title_cell = f'<a href="{url}" target="_blank" rel="noopener" style="color:#FAFAFA;text-decoration:none">{title}</a>' if url else title
        c1, c2 = _dual.get(club, (color_map.get(club, "#636EFA"), "#FFFFFF"))
        dot = f'<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:{c1};border:1px solid rgba(255,255,255,0.3);vertical-align:middle;position:relative"><span style="display:block;width:6px;height:6px;border-radius:50%;background:{c2};position:absolute;top:2px;left:2px"></span></span>'
        _ch_obj = _ch_by_name_cmp.get(club, {})
        _flag = LEAGUE_FLAG.get(COUNTRY_TO_LEAGUE.get((_ch_obj.get("country") or "").strip(), ""), "")
        # Format & theme
        _fmt_raw = (getattr(r, "format", "") or "").lower()
        _dur = int(getattr(r, "duration_seconds", 0) or 0)
        if _fmt_raw not in ("long", "short", "live"):
            _fmt_raw = "long" if _dur >= 60 else "short"
        _fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[_fmt_raw]
        _fmt_color = {"long": "#636EFA", "short": "#EF553B", "live": "#FFA15A"}[_fmt_raw]
        _cat = (getattr(r, "category", "") or "").replace("<", "&lt;")
        _cat_span = f' · <span style="color:#666">{_cat}</span>' if _cat and _cat != "Other" else ""
        _meta = f'{_flag} {dot} <span style="color:#AAA">{club}</span> · <span style="color:{_fmt_color}">{_fmt_label}</span>{_cat_span}'
        row_click = f'onclick="window.open(\'{url}\',\'_blank\',\'noopener\')" style="cursor:pointer"' if url else ''
        _rows += f"""<tr {row_click} data-views="{_views}" data-likes="{_likes}">
            <td style="padding:6px 10px;color:#888">{i}</td>
            <td style="padding:6px 10px">
              <div style="font-size:12px;margin-bottom:2px;white-space:nowrap">{_meta}</div>
              <div>{title_cell}</div>
            </td>
            <td style="padding:6px 10px;text-align:right">{fmt_num(_views)}</td>
            <td style="padding:6px 10px;text-align:right">{fmt_num(_likes)}</td>
        </tr>"""
    components.html(f"""
    <style>
      .cmp {{ width:100%;border-collapse:collapse;font-size:14px;color:#FAFAFA;font-family:"Source Sans Pro",sans-serif; }}
      .cmp th {{ padding:6px 10px;border-bottom:2px solid #444;text-align:left; }}
      .cmp td {{ border-bottom:1px solid #262730; }}
      .cmp tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="cmp"><thead><tr>
      <th>#</th><th>Video</th><th style="text-align:right">Views</th><th style="text-align:right">Likes</th>
    </tr></thead><tbody>{_rows}</tbody></table>
    {yt_popup_js()}
    """, height=len(_top) * 50 + 60, scrolling=False)
