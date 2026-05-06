from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

from src.database import Database
from src.analytics import compute_channel_comparison, compute_tier_stats, compute_theme_distribution, fmt_num, yt_popup_js, CATEGORY_COLORS
from src.filters import get_global_filter, get_global_channels, get_channels_for_filter, get_league_for_channel, get_include_league, get_global_color_map, get_global_color_map_dual, get_all_leagues_scope, render_page_subtitle
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG, league_with_flag
from src.auth import require_login
from src.dot import dual_dot, channel_badge

load_dotenv()
require_login()

st.title("Top 100 Videos")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

if not all_channels:
    st.warning("No channel data yet.")
    st.stop()

league, club = get_global_filter()

_daily_updated = db.get_last_fetch_time("daily")
render_page_subtitle("Top 100 most viewed videos all time", updated_raw=_daily_updated)


# Top-N is all we ever need for charts/tables on this page.
@st.cache_data(ttl=300)
def _load_top_videos_global(limit: int = 500):
    if hasattr(db, "get_top_videos"):
        return db.get_top_videos(limit=limit)
    return db.get_all_videos()[:limit]


@st.cache_data(ttl=300)
def _load_top_videos_for_channels(channel_ids: tuple, limit: int = 500):
    if hasattr(db, "get_top_videos_in_channels"):
        return db.get_top_videos_in_channels(list(channel_ids), limit=limit)
    return [v for v in db.get_all_videos() if v.get("channel_id") in set(channel_ids)][:limit]


# ── Get videos based on filter ────────────────────────────────
if club:
    # One club
    from src.filters import render_club_header
    render_club_header(club, all_channels)
    with st.spinner(f"Loading {club['name']} videos…"):
        raw_videos = db.get_videos_by_channel(club["id"])
    if not raw_videos:
        st.warning(f"No videos for {club['name']} yet.")
        st.stop()
    df = pd.DataFrame(raw_videos)
    df["channel_name"] = club["name"]
    color_field = "category"
    color_map = None
elif league:
    # One league — fetch top-N only for that league's channels
    league_channels = get_channels_for_filter(all_channels, league)
    from src.filters import render_league_header
    render_league_header(league, channels_in_scope=league_channels)
    ch_ids = tuple(ch["id"] for ch in league_channels)
    with st.spinner(f"Loading top videos for {league}…"):
        all_videos = _load_top_videos_for_channels(ch_ids, limit=500)
    df = pd.DataFrame(all_videos)
    if df.empty:
        df["channel_name"] = []
    else:
        df["channel_name"] = df["channels"].apply(lambda c: c["name"] if c else "Unknown")
    league_names = {ch["name"] for ch in league_channels}
    df = df[df["channel_name"].isin(league_names)] if not df.empty else df
    color_field = "channel_name"
    color_map = get_global_color_map()
else:
    # All leagues — fetch global top-N only
    scope = get_all_leagues_scope()
    with st.spinner("Loading top videos across all leagues…"):
        all_videos = _load_top_videos_global(limit=500)
    df = pd.DataFrame(all_videos)
    df["channel_name"] = df["channels"].apply(lambda c: c["name"] if c else "Unknown")
    ch_by_name = {ch["name"]: ch for ch in all_channels}
    # Always drop Players + Federations — they live on their own pages
    _exclude_isolated = {n for n, c in ch_by_name.items()
                         if c.get("entity_type") in ("Player", "Federation", "OtherClub", "WomenClub")}
    if _exclude_isolated:
        df = df[~df["channel_name"].isin(_exclude_isolated)]
    if scope == "Leagues only":
        keep = {n for n, c in ch_by_name.items() if c.get("entity_type") == "League"}
        df = df[df["channel_name"].isin(keep)]
        color_field = "channel_name"
        color_map = get_global_color_map()
    elif scope == "All clubs":
        keep = {n for n, c in ch_by_name.items() if c.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub")}
        df = df[df["channel_name"].isin(keep)]
        ch_to_league = {n: league_with_flag(get_league_for_channel(c)) for n, c in ch_by_name.items()}
        df["league"] = df["channel_name"].map(ch_to_league).fillna("Other")
        color_field = "league"
        color_map = None
    else:
        ch_to_league = {n: league_with_flag(get_league_for_channel(c)) for n, c in ch_by_name.items()}
        df["league"] = df["channel_name"].map(ch_to_league).fillna("Other")
        color_field = "league"
        color_map = None

if df.empty:
    st.warning("No video data for this selection.")
    st.stop()

# ── KPI line (computed up front so it sits above the main table) ─
_filtered_for_kpi = df.sort_values("view_count", ascending=False).head(100)
try:
    if club:
        _scope_channels = [club]
    elif league:
        _scope_channels = get_channels_for_filter(all_channels, league)
    else:
        _scope = get_all_leagues_scope()
        if _scope == "Leagues only":
            _scope_channels = [c for c in all_channels
                               if c.get("entity_type") == "League"]
        elif _scope == "All clubs":
            _scope_channels = [c for c in all_channels
                               if c.get("entity_type") not in
                                  ("League", "Player", "Federation",
                                   "OtherClub", "WomenClub")]
        else:
            _scope_channels = [c for c in all_channels
                               if c.get("entity_type") not in
                                  ("Player", "Federation",
                                   "OtherClub", "WomenClub")]
    _lifetime_views = sum(int(c.get("total_views") or 0) for c in _scope_channels)
    _top_views = int(_filtered_for_kpi["view_count"].sum())
    _avg_views = int(_filtered_for_kpi["view_count"].mean()) if len(_filtered_for_kpi) else 0
    _cutoff = int(_filtered_for_kpi["view_count"].min()) if len(_filtered_for_kpi) else 0
    _pct_lifetime = (_top_views / _lifetime_views * 100) if _lifetime_views else 0.0

    # Average age (years) of the top 100 — tells you how front- or
    # back-loaded the catalogue is.
    _now_utc_kpi = pd.Timestamp.now(tz="UTC")
    _ages_days = (_now_utc_kpi - pd.to_datetime(
        _filtered_for_kpi["published_at"], utc=True, errors="coerce"
    )).dt.total_seconds() / 86400.0
    _ages_days = _ages_days.dropna()
    _avg_age_years = float(_ages_days.mean() / 365.25) if len(_ages_days) else 0.0

    _kpi_html = f"""
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;
                margin:4px 0 14px 0">
      <div style="background:#1a1c24;border-radius:6px;padding:10px 14px;
                  border-left:3px solid #58A6FF">
        <div style="color:#888;font-size:11px;font-weight:600;
                    text-transform:uppercase;letter-spacing:0.5px">
          Total views (top 100)
        </div>
        <div style="color:#FAFAFA;font-size:22px;font-weight:700;
                    margin-top:2px">{fmt_num(_top_views)}</div>
      </div>
      <div style="background:#1a1c24;border-radius:6px;padding:10px 14px;
                  border-left:3px solid #00CC96">
        <div style="color:#888;font-size:11px;font-weight:600;
                    text-transform:uppercase;letter-spacing:0.5px">
          Share of lifetime
        </div>
        <div style="color:#FAFAFA;font-size:22px;font-weight:700;
                    margin-top:2px">{_pct_lifetime:.1f}%</div>
      </div>
      <div style="background:#1a1c24;border-radius:6px;padding:10px 14px;
                  border-left:3px solid #FFA15A">
        <div style="color:#888;font-size:11px;font-weight:600;
                    text-transform:uppercase;letter-spacing:0.5px">
          Avg views / video
        </div>
        <div style="color:#FAFAFA;font-size:22px;font-weight:700;
                    margin-top:2px">{fmt_num(_avg_views)}</div>
      </div>
      <div style="background:#1a1c24;border-radius:6px;padding:10px 14px;
                  border-left:3px solid #AB63FA">
        <div style="color:#888;font-size:11px;font-weight:600;
                    text-transform:uppercase;letter-spacing:0.5px">
          Cutoff (rank 100)
        </div>
        <div style="color:#FAFAFA;font-size:22px;font-weight:700;
                    margin-top:2px">{fmt_num(_cutoff)}</div>
      </div>
      <div style="background:#1a1c24;border-radius:6px;padding:10px 14px;
                  border-left:3px solid #EF553B">
        <div style="color:#888;font-size:11px;font-weight:600;
                    text-transform:uppercase;letter-spacing:0.5px">
          Avg age
        </div>
        <div style="color:#FAFAFA;font-size:22px;font-weight:700;
                    margin-top:2px">{_avg_age_years:.1f}y</div>
      </div>
    </div>
    """
    st.markdown(_kpi_html, unsafe_allow_html=True)
except Exception as _e:
    st.caption(f"(KPIs unavailable: {_e})")

filtered = df.sort_values("view_count", ascending=False).head(100).reset_index(drop=True)

from zoneinfo import ZoneInfo as _ZoneInfo
current_year = datetime.now(_ZoneInfo("Europe/Rome")).year

# ── Video Table ───────────────────────────────────────────────
st.subheader("Video List")
dual_colors = get_global_color_map_dual()
color_map_tbl = get_global_color_map()
_now = pd.Timestamp.now(tz="UTC")

# Name → flag lookup, plus a name → channel-dict map so we can render the
# standard channel_badge marker (flag for League channels, dual_dot for clubs)
# instead of the previous flag+dot combo that produced a doubled glyph on
# league rows.
_ch_flag = {}
_ch_by_name = {}
for _c in all_channels:
    _lg = COUNTRY_TO_LEAGUE.get((_c.get("country") or "").strip(), "")
    _ch_flag[_c["name"]] = LEAGUE_FLAG.get(_lg, "")
    _ch_by_name[_c["name"]] = _c

_rows_html = ""
for i, r in enumerate(filtered.itertuples(index=False), 1):
    ch = getattr(r, "channel_name", "") or ""
    title = (getattr(r, "title", "") or "").replace("<", "&lt;").replace(">", "&gt;")
    yt_id = getattr(r, "youtube_video_id", "") or ""
    title_cell = (
        f'<a href="https://www.youtube.com/watch?v={yt_id}" target="_blank" rel="noopener" '
        f'style="color:#FAFAFA;text-decoration:none;font-weight:700">{title}</a>' if yt_id else title
    )
    pub = pd.to_datetime(getattr(r, "published_at", None), utc=True) if getattr(r, "published_at", None) else None
    age = f"{((_now - pub).days / 365.25):.1f}y" if pub is not None else ""
    pub_s = pub.strftime("%Y-%m-%d") if pub is not None else ""
    dur = getattr(r, "duration_seconds", 0) or 0
    dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else "0:00"
    cat = getattr(r, "category", "") or ""
    fmt_raw = (getattr(r, "format", "") or "").lower()
    if fmt_raw not in ("long", "short", "live"):
        fmt_raw = "long" if dur >= 60 else "short"
    # Detect scheduled/premiere (future actual_start_time) for any format
    _is_sched = False
    _ast = getattr(r, "actual_start_time", None) or ""
    if _ast:
        try:
            _ast_dt = pd.to_datetime(_ast, utc=True)
            if _ast_dt > _now:
                _is_sched = True
        except Exception:
            pass
    elif fmt_raw == "live" and dur == 0:
        _is_sched = True
    if _is_sched:
        fmt_label = "Scheduled"
        fmt_color = "#AB63FA"
    else:
        fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[fmt_raw]
        fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}[fmt_raw]
    fmt_cell = f'<span style="color:{fmt_color}">{fmt_label}</span>'
    row_url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""
    row_attrs = f'onclick="window.open(\'{row_url}\',\'_blank\',\'noopener\')" style="cursor:pointer"' if row_url else ''
    # channel_badge: flag for League channels, dual_dot for clubs. Replaces
    # the older `{flag} {dot}` combo which on league rows rendered both a
    # flag AND a dot — the doubled-marker bug.
    # Size 14 to match Daily Recap / Latest / Other Social / Channels / Season
    _badge = channel_badge(_ch_by_name.get(ch, {"name": ch}), color_map_tbl, dual_colors, 14)
    _cat_color = CATEGORY_COLORS.get(cat, "#888")
    _cat_span = f' · <span style="color:{_cat_color}">{cat}</span>' if cat and cat != "Other" else ""
    _meta = f'{_badge} <span style="color:#AAA">{ch}</span> · {fmt_cell}{_cat_span}'
    _views = int(getattr(r, 'view_count', 0) or 0)
    _likes = int(getattr(r, 'like_count', 0) or 0)
    _comments = int(getattr(r, 'comment_count', 0) or 0)
    _age_days = round((_now - pub).total_seconds() / 86400, 2) if pub is not None else 0
    _rows_html += f"""<tr {row_attrs} data-views="{_views}" data-likes="{_likes}" data-comments="{_comments}" data-age="{_age_days}" data-dur="{dur}">
        <td style="padding:6px 12px;text-align:right;color:#888;vertical-align:top">{i}</td>
        <td style="padding:6px 12px;vertical-align:top">
          <div>{title_cell}</div>
          <div style="font-size:12px;margin-top:3px;white-space:nowrap">{_meta}</div>
        </td>
        <td style="padding:6px 12px;text-align:right">{fmt_num(_views)}</td>
        <td style="padding:6px 12px;text-align:right">{fmt_num(_likes)}</td>
        <td style="padding:6px 12px;text-align:right">{fmt_num(_comments)}</td>
        <td style="padding:6px 12px;white-space:nowrap">{age}</td>
        <td style="padding:6px 12px;white-space:nowrap">{dur_s}</td>
    </tr>"""

# Two-line rows (channel meta + title) actually run ~50px each, header ~45px.
# Old formula `80 + 34*n, cap 1200` undercounted per-row but capped too low,
# so when filters narrowed results we got both inner-scroll AND empty tail.
# New: 50px×n + 50 header, capped at viewport-friendly 1400.
_table_height = min(50 + 50 * len(filtered), 1400)
components.html(
    f"""
    <style>
      .vidlist {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                  font-family:"Source Sans Pro",sans-serif; }}
      .vidlist th {{ padding:8px 12px; border-bottom:2px solid #444; text-align:left;
                     background:#0E1117; position:sticky; top:0; z-index:1;
                     user-select:none; }}
      .vidlist td {{ border-bottom:1px solid #262730; }}
      .vidlist tr:hover td {{ background:#1a1c24; }}
      .vidlist th[data-col] {{ cursor:pointer; }}
      .vidlist th[data-col]:hover {{ color:#58A6FF; }}
      /* Match the inline text-arrow + .active pattern used everywhere else
         on the site (Channels, Season, Daily Recap, Federations, …). */
      .vidlist th.active {{ color:#58A6FF; }}
    </style>
    <div style="max-height:1350px;overflow:auto">
    <table class="vidlist" id="vlTable"><thead><tr>
      <th style="text-align:right">#</th>
      <th>Video</th>
      <th data-col="views" style="text-align:right" class="active">Views ▼</th>
      <th data-col="likes" style="text-align:right">Likes</th>
      <th data-col="comments" style="text-align:right">Comments</th>
      <th data-col="age">Age</th>
      <th data-col="dur">Duration</th>
    </tr></thead><tbody>{_rows_html}</tbody></table>
    </div>
    <script>
    (function() {{
      const table = document.getElementById('vlTable');
      const headers = table.querySelectorAll('th[data-col]');
      // Initial sort: rows arrive ordered by view_count DESC. Views ▼ marked.
      let currentCol = 'views', asc = false;
      headers.forEach(th => {{
        th.addEventListener('click', () => {{
          const col = th.dataset.col;
          if (currentCol === col) {{ asc = !asc; }}
          else {{ currentCol = col; asc = false; }}
          // Strip arrows + active class from everyone, then mark this one.
          headers.forEach(h => {{
            h.classList.remove('active');
            h.textContent = h.textContent.replace(/ [▲▼]/g, '');
          }});
          th.classList.add('active');
          th.textContent += asc ? ' ▲' : ' ▼';
          const tbody = table.querySelector('tbody');
          const rows = Array.from(tbody.querySelectorAll('tr'));
          rows.sort((a, b) => {{
            const va = parseFloat(a.dataset[col]) || 0;
            const vb = parseFloat(b.dataset[col]) || 0;
            return asc ? va - vb : vb - va;
          }});
          rows.forEach((r, idx) => {{
            r.querySelector('td').textContent = idx + 1;
            tbody.appendChild(r);
          }});
        }});
      }});
    }})();
    </script>
    {yt_popup_js()}
    """,
    height=_table_height,
    scrolling=True,
)
# ── Views by Rank chart ───────────────────────────────────────
st.subheader("Views by Rank")
chart_df = filtered[["view_count", "title", "channel_name"]].copy()
chart_df[color_field] = filtered[color_field] if color_field in filtered.columns else filtered["channel_name"]
chart_df["rank"] = range(1, len(chart_df) + 1)

fig = px.bar(
    chart_df, x="rank", y="view_count",
    color=color_field, color_discrete_map=color_map,
    hover_data=["title", "channel_name"],
    labels={"rank": "Rank", "view_count": "Views"},
)
fig.update_layout(
    xaxis=dict(tickvals=[1] + list(range(5, 96, 5)) + [100], title="Rank"),
    yaxis_title="Total Views",
    margin=dict(t=20, b=40),
    bargap=0.1,
)
st.plotly_chart(fig, use_container_width=True)

# ── Rank vs Year ──────────────────────────────────────────────
st.subheader("Rank vs Publication Year")
scatter_df = filtered[["title", "channel_name", "view_count", "published_at"]].copy()
scatter_df[color_field] = filtered[color_field] if color_field in filtered.columns else filtered["channel_name"]
scatter_df["rank"] = range(1, len(scatter_df) + 1)
scatter_df["year"] = pd.to_datetime(scatter_df["published_at"], utc=True).dt.year

fig_scatter = go.Figure()
baseline = current_year

for group_name in scatter_df[color_field].unique():
    grp = scatter_df[scatter_df[color_field] == group_name]
    bar_color = color_map.get(group_name, None) if color_map else None
    fig_scatter.add_trace(go.Bar(
        x=grp["rank"],
        y=grp["year"] - baseline,
        base=baseline,
        name=str(group_name),
        marker_color=bar_color,
        customdata=list(zip(grp["title"], grp["view_count"])),
        hovertemplate="Rank %{x}<br>Year: %{y}<br>%{customdata[0]}<br>Views: %{customdata[1]:,}<extra></extra>",
        width=0.8,
    ))

fig_scatter.update_layout(
    yaxis=dict(autorange="reversed", dtick=1, title="Year"),
    xaxis=dict(range=[0.5, 100.5], tickvals=[1] + list(range(5, 96, 5)) + [100], title="Rank"),
    margin=dict(t=20, b=40),
    bargap=0.1,
    barmode="overlay",
)
st.plotly_chart(fig_scatter, use_container_width=True)

# ── Videos by Year ────────────────────────────────────────────
st.subheader("Top 100 Videos by Publication Year")
year_df = filtered.copy()
year_df["year"] = pd.to_datetime(year_df["published_at"], utc=True).dt.year
year_counts = year_df.groupby("year").size().reset_index(name="count").sort_values("year")

fig_year = px.bar(year_counts, x="year", y="count",
                  labels={"year": "Year", "count": "Videos in Top 100"},
                  color_discrete_sequence=["#636EFA"])
fig_year.update_layout(xaxis=dict(dtick=1, title="Year"), yaxis_title="Videos in Top 100", margin=dict(t=20, b=40))
st.plotly_chart(fig_year, use_container_width=True)

# ── Theme Distribution ────────────────────────────────────────
theme_df = compute_theme_distribution(filtered)
if not theme_df.empty:
    from src.analytics import build_category_pie
    cat_counts = dict(zip(theme_df["category"], theme_df["count"]))
    st.plotly_chart(build_category_pie(cat_counts, "Theme Distribution", "videos"), use_container_width=True)

# ── Channel table (same as Clubs page) ───────────────────────
if not club:
    _loading = st.info("⏳ Building channel stats table…")
    now = datetime.now(timezone.utc)
    include_league = get_include_league()
    if league:
        # One league selected — use its channels
        base_channels = league_channels
        if include_league:
            table_channels = [ch for ch in base_channels if ch.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")]
        else:
            table_channels = [ch for ch in base_channels if ch.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub")]
    else:
        # All leagues — respect scope
        scope = get_all_leagues_scope()
        if scope == "Leagues only":
            table_channels = [ch for ch in all_channels if ch.get("entity_type") == "League"]
        elif scope == "All clubs":
            table_channels = [ch for ch in all_channels if ch.get("entity_type") not in ("League", "Player", "Federation", "OtherClub", "WomenClub")]
        else:
            table_channels = [ch for ch in all_channels if ch.get("entity_type") not in ("Player", "Federation", "OtherClub", "WomenClub")]

    if not table_channels:
        _loading.empty()
    if table_channels:
        ch_df = compute_channel_comparison(table_channels)
        ch_color_map = get_global_color_map()
        ch_dual_colors = get_global_color_map_dual()

        # Read precomputed top-100 stats directly from channel records (zero video queries)
        t100_rows = []
        t100_stats = {}
        for ch in table_channels:
            at_views = int(ch.get("top100_views") or 0)
            at_avg_age_days = float(ch.get("top100_avg_age_days") or 0)
            at_long_pct = int(ch.get("top100_long_pct") or 0)
            at1_views = int(ch.get("top1_views") or 0)
            at1_dur_s = int(ch.get("top1_dur_s") or 0)

            # Compute #1 video age from stored published_at
            at1_pub = ch.get("top1_published_at")
            if at1_pub:
                try:
                    _dt = datetime.fromisoformat(str(at1_pub).replace("Z", "+00:00"))
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    at1_age_days = (now - _dt).days
                except Exception:
                    at1_age_days = 0
            else:
                at1_age_days = 0

            at_avg_age_str = f"{at_avg_age_days / 365.25:.1f}y" if at_avg_age_days else "-"
            at_ls_str = f"{at_long_pct}/{100 - at_long_pct}" if at_views else "-"
            at1_age_str = f"{at1_age_days / 365.25:.1f}y" if at1_age_days else "-"
            at1_dur_str = f"{at1_dur_s // 60}:{at1_dur_s % 60:02d}" if at1_dur_s else "-"

            st_views = int(ch.get("season_top100_views") or 0)
            st_avg_age_days = float(ch.get("season_top100_avg_age_days") or 0)
            st_long_pct = int(ch.get("season_top100_long_pct") or 0)
            s1_views = int(ch.get("season_top1_views") or 0)
            s1_dur_s = int(ch.get("season_top1_dur_s") or 0)

            s1_pub = ch.get("season_top1_published_at")
            if s1_pub:
                try:
                    _dt = datetime.fromisoformat(str(s1_pub).replace("Z", "+00:00"))
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    s1_age_days = (now - _dt).days
                except Exception:
                    s1_age_days = 0
            else:
                s1_age_days = 0

            st_avg_age_str = f"{int(st_avg_age_days)}d" if st_avg_age_days else "-"
            st_ls_str = f"{st_long_pct}/{100 - st_long_pct}" if st_views else "-"
            s1_age_str = f"{int(s1_age_days)}d" if s1_age_days else "-"
            s1_dur_str = f"{s1_dur_s // 60}:{s1_dur_s % 60:02d}" if s1_dur_s else "-"

            t100_rows.append({"name": ch["name"],
                "at_views": at_views, "at_avg_age_days": at_avg_age_days, "at_avg_dur_s": 0,
                "at_long_pct": at_long_pct,
                "at1_views": at1_views, "at1_age_days": at1_age_days, "at1_dur_s": at1_dur_s,
                "st_views": st_views, "st_avg_age_days": st_avg_age_days, "st_avg_dur_s": 0,
                "st_long_pct": st_long_pct,
                "s1_views": s1_views, "s1_age_days": s1_age_days, "s1_dur_s": s1_dur_s})
            t100_stats[ch["name"]] = {
                "at_views": at_views, "at_avg_age": at_avg_age_str,
                "at_ls": at_ls_str,
                "at1_views": at1_views, "at1_age": at1_age_str, "at1_dur": at1_dur_str,
                "st_views": st_views, "st_avg_age": st_avg_age_str,
                "st_ls": st_ls_str,
                "s1_views": s1_views, "s1_age": s1_age_str, "s1_dur": s1_dur_str}

        if t100_rows:
            t_df = pd.DataFrame(t100_rows)
            ch_df = ch_df.merge(t_df, on="name", how="left")

        ch_df = ch_df.sort_values("at_views", ascending=False, na_position="last").reset_index(drop=True)

        rows_html = ""
        for _, row in ch_df.iterrows():
            dot = channel_badge(row.to_dict(), ch_color_map, ch_dual_colors, 14)
            handle = row.get("handle", "")
            _row_click = f'onclick="window.open(\'https://www.youtube.com/{handle}\',\'_blank\',\'noopener\')" style="cursor:pointer"' if handle else ''
            ss = t100_stats.get(row["name"], {})
            rows_html += f"""<tr {_row_click}>
                <td style="padding:6px 12px">{dot}</td>
                <td style="padding:6px 12px" data-val="{row['name']}">{row['name']}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('at_views', 0)}">{fmt_num(ss['at_views']) if ss.get('at_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at_avg_age_days', 0) or 0}">{ss.get('at_avg_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at_long_pct', 0) or 0}">{ss.get('at_ls', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('at1_views', 0)}">{fmt_num(ss['at1_views']) if ss.get('at1_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at1_age_days', 0) or 0}">{ss.get('at1_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('at1_dur_s', 0) or 0}">{ss.get('at1_dur', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('st_views', 0)}">{fmt_num(ss['st_views']) if ss.get('st_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('st_long_pct', 0) or 0}">{ss.get('st_ls', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{ss.get('s1_views', 0)}">{fmt_num(ss['s1_views']) if ss.get('s1_views') else '-'}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('s1_age_days', 0) or 0}">{ss.get('s1_age', '-')}</td>
                <td style="padding:6px 12px;text-align:right" data-val="{row.get('s1_dur_s', 0) or 0}">{ss.get('s1_dur', '-')}</td>
            </tr>"""

        # Tightened heuristic: single-line rows ~30px + two-row sticky
        # header ~60px (was 37×n + 100, overshooting by ~150px per page).
        _table_height = len(ch_df) * 32 + 70
        components.html(f"""
        <style>
            .st-table {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                         font-family:"Source Sans Pro",sans-serif; background:transparent; }}
            .st-table th {{ padding:6px 12px; user-select:none; }}
            .st-table th[data-col] {{ cursor:pointer; }}
            .st-table th[data-col]:hover {{ color:#636EFA; }}
            .st-table td {{ padding:6px 12px; border-bottom:1px solid #262730; }}
            .st-table tr:hover td {{ background:#1a1c24; }}
            .st-table a {{ color:inherit; text-decoration:none; }}
            .st-table .active {{ color:#636EFA; }}
        </style>
        <table class="st-table">
        <thead>
        <tr>
            <th colspan="2"></th>
            <th colspan="3" style="text-align:center;border-bottom:2px solid #636EFA;color:#636EFA">Top 100</th>
            <th colspan="3" style="text-align:center;border-bottom:2px solid #EF553B;color:#EF553B">#1 Video</th>
            <th colspan="2" style="text-align:center;border-bottom:2px solid #00CC96;color:#00CC96">Season Top 100</th>
            <th colspan="3" style="text-align:center;border-bottom:2px solid #FFA15A;color:#FFA15A">Season #1</th>
        </tr>
        <tr style="border-bottom:2px solid #444">
            <th style="width:30px"></th>
            <th data-col="1" data-type="str" style="text-align:left">Channel</th>
            <th data-col="2" data-type="num" style="text-align:right" class="active">Views ▼</th>
            <th data-col="3" data-type="num" style="text-align:right">Avg Age</th>
            <th data-col="4" data-type="num" style="text-align:right">Long/Short</th>
            <th data-col="5" data-type="num" style="text-align:right">Views</th>
            <th data-col="6" data-type="num" style="text-align:right">Age</th>
            <th data-col="7" data-type="num" style="text-align:right">Dur</th>
            <th data-col="8" data-type="num" style="text-align:right">Views</th>
            <th data-col="9" data-type="num" style="text-align:right">Long/Short</th>
            <th data-col="10" data-type="num" style="text-align:right">Views</th>
            <th data-col="11" data-type="num" style="text-align:right">Age</th>
            <th data-col="12" data-type="num" style="text-align:right">Dur</th>
        </tr>
        </thead>
        <tbody>{rows_html}</tbody>
        </table>
        <script>
        (function() {{
            const table = document.querySelector('.st-table');
            const tbody = table.querySelector('tbody');
            const headers = table.querySelectorAll('th[data-col]');
            let currentCol = 2;
            let currentAsc = false;

            function sortTable(colIdx, type) {{
                const rows = Array.from(tbody.rows);
                const isStr = type === 'str';
                if (colIdx === currentCol) {{
                    currentAsc = !currentAsc;
                }} else {{
                    currentCol = colIdx;
                    currentAsc = isStr;
                }}
                rows.sort((a, b) => {{
                    const va = a.cells[colIdx].dataset.val || '';
                    const vb = b.cells[colIdx].dataset.val || '';
                    let cmp;
                    if (isStr) {{
                        cmp = va.localeCompare(vb, undefined, {{sensitivity:'base'}});
                    }} else {{
                        cmp = (parseFloat(va) || 0) - (parseFloat(vb) || 0);
                    }}
                    return currentAsc ? cmp : -cmp;
                }});
                rows.forEach(r => tbody.appendChild(r));

                headers.forEach(h => {{
                    h.classList.remove('active');
                    const base = h.textContent.replace(/ [▲▼]/g, '');
                    h.textContent = base;
                }});
                const activeHdr = table.querySelector('th[data-col="' + colIdx + '"]');
                activeHdr.classList.add('active');
                activeHdr.textContent += currentAsc ? ' ▲' : ' ▼';
            }}

            headers.forEach(h => {{
                h.addEventListener('click', function() {{
                    sortTable(parseInt(this.dataset.col), this.dataset.type || 'num');
                }});
            }});
        }})();
        </script>
        {yt_popup_js()}
        """, height=_table_height, scrolling=False)
    _loading.empty()


