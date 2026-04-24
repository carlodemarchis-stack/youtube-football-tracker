"""Players page — top football players on YouTube (personal channels).

Deliberately isolated from the rest of the app:
- No inclusion in global League/Club filter
- No integration with Channels / Top Videos / Compare / Season / AI Analysis / Daily Recap
- No per-player detail page — clicking a row opens the YouTube channel
- Powered by its own dedicated daily cron (scripts/daily_players.py)

Kill-switch: delete this file, the nav entry in app.py, the cron script,
its Railway service, and the Player rows in `channels`. Zero impact elsewhere.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.express as px
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.auth import require_login
from src.filters import (
    get_global_channels, get_global_color_map, get_global_color_map_dual,
    render_page_subtitle,
)
from src.channels import COUNTRY_TO_LEAGUE, LEAGUE_FLAG

load_dotenv()
require_login()

st.title("Players")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()
players = [c for c in all_channels if c.get("entity_type") == "Player"]

_players_updated = db.get_last_fetch_time("daily_players")
render_page_subtitle(
    "Top football players on YouTube — personal channels",
    updated_raw=_players_updated,
)

if not players:
    st.info(
        "No players tracked yet. Seed the 10 player rows in `channels` "
        "(see `db/players_seed.sql`) and run `scripts/daily_players.py` "
        "to backfill stats."
    )
    st.stop()

# ── Sort by subscribers desc ─────────────────────────────────
players.sort(key=lambda c: int(c.get("subscriber_count") or 0), reverse=True)

# ── Narrative headline stat ──────────────────────────────────
_top = players[0]
_top_subs = int(_top.get("subscriber_count") or 0)
_all_clubs = [c for c in all_channels if c.get("entity_type") not in ("League", "Player")]
# Find a league whose aggregate club subs < top player's subs (for the headline)
_league_totals: dict[str, int] = {}
for c in _all_clubs:
    lg = COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper(), "")
    if not lg:
        continue
    _league_totals[lg] = _league_totals.get(lg, 0) + int(c.get("subscriber_count") or 0)

_beaten_leagues = [lg for lg, tot in _league_totals.items() if tot < _top_subs]
if _beaten_leagues and _top.get("name"):
    _beats = ", ".join(f"**{lg}**" for lg in sorted(_beaten_leagues,
                        key=lambda lg: _league_totals[lg], reverse=True)[:3])
    st.info(
        f"**{_top['name']}** alone has **{fmt_num(_top_subs)} subs** — more than "
        f"every club combined in {_beats}."
    )

# ── KPIs ─────────────────────────────────────────────────────
_tot_subs = sum(int(c.get("subscriber_count") or 0) for c in players)
_tot_views = sum(int(c.get("total_views") or 0) for c in players)
_tot_videos = sum(int(c.get("video_count") or 0) for c in players)

k1, k2, k3, k4 = st.columns(4)
k1.metric("Players tracked", len(players))
k2.metric("Total subscribers", fmt_num(_tot_subs))
k3.metric("Total views", fmt_num(_tot_views))
k4.metric("Total videos", fmt_num(_tot_videos))

# ── Helpers ──────────────────────────────────────────────────
now = datetime.now(timezone.utc)
color_map = get_global_color_map() or {}
dual = get_global_color_map_dual() or {}

def _age_from_dob(dob_iso: str | None) -> int | None:
    if not dob_iso:
        return None
    try:
        d = datetime.fromisoformat(str(dob_iso)[:10] + "T00:00:00+00:00")
        years = (now - d).days / 365.25
        return int(years) if years > 0 else None
    except Exception:
        return None


def _subs_per_year(subs: int, launched_iso: str | None) -> int:
    if not launched_iso or not subs:
        return 0
    try:
        d = datetime.fromisoformat(str(launched_iso).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        years = (now - d).days / 365.25
        if years <= 0.1:
            return 0
        return int(int(subs) / years)
    except Exception:
        return 0


# ── Last-upload lookup (needed for leaderboard "Active" column) ──
_last_by_cid: dict[str, str] = {}
try:
    _last_resp = (
        db.client.table("videos")
        .select("channel_id,published_at")
        .in_("channel_id", [p["id"] for p in players])
        .order("published_at", desc=True)
        .execute()
        .data or []
    )
    for r in _last_resp:
        cid = r.get("channel_id")
        if cid and cid not in _last_by_cid:
            _last_by_cid[cid] = (r.get("published_at") or "")[:10]
except Exception:
    pass

# Fallback via live YouTube API for fully-dormant players (none in DB)
from src.youtube_api import YouTubeClient as _YT
_yt = _YT(os.getenv("YOUTUBE_API_KEY", ""))
for p in players:
    if p["id"] in _last_by_cid:
        continue
    yt_cid = p.get("youtube_channel_id")
    if not yt_cid or not yt_cid.startswith("UC"):
        continue
    try:
        uploads = "UU" + yt_cid[2:]
        recent = _yt.get_recent_video_entries(uploads, max_results=1)
        if recent:
            _last_by_cid[p["id"]] = (recent[0].get("published") or "")[:10]
    except Exception:
        pass


def _days_since(iso: str) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso + "T00:00:00+00:00")
        return (now - d).days
    except Exception:
        return None


def _status(days: int | None) -> tuple[str, str]:
    if days is None:
        return "—", "#666"
    if days <= 14:
        return "🟢 Active", "#00CC96"
    if days <= 30:
        return "🟡 Slowing", "#FFA15A"
    if days <= 90:
        return "🟠 Quiet", "#FF6B6B"
    return "🔴 Dormant", "#AA2222"


# Retired / still-playing career status — hardcoded by substring match
# against channel name. Edit as careers change.
_RETIRED_NAMES = (
    "Rio Ferdinand", "Ben Foster", "Iker Casillas",
    "Peter Crouch", "Sergio Agüero", "Sergio Aguero",
)


def _is_retired(name: str) -> bool:
    n = (name or "").lower()
    return any(r.lower() in n for r in _RETIRED_NAMES)


def _career_dot(name: str) -> tuple[str, int]:
    """Return (dot, sort_key). 0 = active (green), 1 = retired (red)."""
    if _is_retired(name):
        return "🔴", 1
    return "🟢", 0


def _status_dot(days: int | None) -> str:
    """Just the coloured dot — no text. Used in the leaderboard."""
    if days is None:
        return "—"
    if days <= 14:
        return "🟢"
    if days <= 30:
        return "🟡"
    if days <= 90:
        return "🟠"
    return "🔴"


# ── Leaderboard table ────────────────────────────────────────
st.markdown("---")
st.subheader("Leaderboard")

rows_html = ""
for i, p in enumerate(players, 1):
    name = p.get("name", "?")
    c1, c2 = dual.get(name, (color_map.get(name, "#636EFA"), "#FFFFFF"))
    dot = (f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;'
           f'background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative">'
           f'<span style="display:block;width:7px;height:7px;border-radius:50%;'
           f'background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>')
    _last_iso = _last_by_cid.get(p["id"], "")
    _days = _days_since(_last_iso)
    _status_label, _ = _status(_days)
    _status_dot_s = _status_dot(_days)
    _status_sort = _days if _days is not None else 99999
    _career_d, _career_sort = _career_dot(name)
    _career_label = "Retired" if _career_sort else "Currently playing"
    _age = _age_from_dob(p.get("dob"))
    _age_disp = f"{_age}" if _age is not None else "—"
    _age_sort = _age if _age is not None else 0
    launched = (p.get("launched_at") or "")[:4] or "-"
    launched_val = (p.get("launched_at") or "9999")[:4]
    subs = int(p.get("subscriber_count") or 0)
    views = int(p.get("total_views") or 0)
    videos = int(p.get("video_count") or 0)
    vps = views // max(subs, 1)
    vpv = views // max(videos, 1)
    spy = _subs_per_year(subs, p.get("launched_at"))
    _ln = int(p.get("season_long_videos") or 0)
    _sn = int(p.get("season_short_videos") or 0)
    _lv = int(p.get("season_live_videos") or 0)
    _slsv = _ln + _sn + _lv
    handle = p.get("handle", "") or ""
    yt_url = f"https://www.youtube.com/{handle}" if handle else ""
    row_click = (f'onclick="window.open(\'{yt_url}\',\'_blank\',\'noopener\')" '
                 f'style="cursor:pointer"') if yt_url else ""
    rows_html += f"""<tr {row_click}>
        <td style="padding:6px 12px;text-align:right;color:#888" data-val="{i}">{i}</td>
        <td style="padding:6px 12px">{dot}</td>
        <td style="padding:6px 12px" data-val="{name}">{name}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{_age_sort}">{_age_disp}</td>
        <td style="padding:6px 12px;text-align:center" data-val="{_career_sort}" title="{_career_label}">{_career_d}</td>
        <td style="padding:6px 12px;text-align:center" data-val="{launched_val}">{launched}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{subs}">{fmt_num(subs)}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{spy}">{fmt_num(spy)}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{views}">{fmt_num(views)}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{vps}">{fmt_num(vps)}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{videos}">{fmt_num(videos)}</td>
        <td style="padding:6px 12px;text-align:center;white-space:nowrap;color:#aaa" data-val="{_slsv}">{_ln} / {_sn} / {_lv}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{vpv}">{fmt_num(vpv)}</td>
        <td style="padding:6px 12px;text-align:center" data-val="{_status_sort}" title="{_status_label} · last upload {(_days if _days is not None else '—')}d ago">{_status_dot_s}</td>
    </tr>"""

# Dynamic height — grows with the roster so every row is visible (no inner scroll).
_tbl_h = len(players) * 40 + 90
components.html(f"""
<style>
  .pl {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .pl th {{ padding:6px 12px; user-select:none; border-bottom:2px solid #444; }}
  .pl th[data-col] {{ cursor:pointer; }}
  .pl th[data-col]:hover {{ color:#636EFA; }}
  .pl td {{ padding:6px 12px; border-bottom:1px solid #262730; vertical-align:middle; }}
  .pl tr:hover td {{ background:#1a1c24; }}
  .pl .active {{ color:#636EFA; }}
</style>
<table class="pl">
<thead><tr>
  <th data-col="0" data-type="num" style="text-align:right">#</th>
  <th></th>
  <th data-col="2" data-type="str" style="text-align:left">Player</th>
  <th data-col="3" data-type="num" style="text-align:right">Age</th>
  <th data-col="4" data-type="num" style="text-align:center">Career</th>
  <th data-col="5" data-type="num" style="text-align:center">Since</th>
  <th data-col="6" data-type="num" style="text-align:right" class="active">Subs ▼</th>
  <th data-col="7" data-type="num" style="text-align:right">Subs/Year</th>
  <th data-col="8" data-type="num" style="text-align:right">Total Views</th>
  <th data-col="9" data-type="num" style="text-align:right">Views/Sub</th>
  <th data-col="10" data-type="num" style="text-align:right">Videos</th>
  <th data-col="11" data-type="num" style="text-align:center" title="Season: Long / Shorts / Live">L/S/Lv</th>
  <th data-col="12" data-type="num" style="text-align:right">Views/Video</th>
  <th data-col="13" data-type="num" style="text-align:center">Updates</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
<script>
(function() {{
  const table = document.querySelector('.pl');
  const tbody = table.querySelector('tbody');
  const headers = table.querySelectorAll('th[data-col]');
  let currentCol = 6, currentAsc = false;
  function sort(colIdx, type) {{
    const rows = Array.from(tbody.rows);
    const isStr = type === 'str';
    if (colIdx === currentCol) currentAsc = !currentAsc;
    else {{ currentCol = colIdx; currentAsc = isStr; }}
    rows.sort((a, b) => {{
      const va = a.cells[colIdx].dataset.val || '';
      const vb = b.cells[colIdx].dataset.val || '';
      let cmp = isStr ? va.localeCompare(vb) : ((parseFloat(va)||0) - (parseFloat(vb)||0));
      return currentAsc ? cmp : -cmp;
    }});
    rows.forEach(r => tbody.appendChild(r));
    headers.forEach(h => {{
      h.classList.remove('active');
      h.textContent = h.textContent.replace(/ [▲▼]/g, '');
    }});
    const a = table.querySelector('th[data-col="' + colIdx + '"]');
    a.classList.add('active');
    a.textContent += currentAsc ? ' ▲' : ' ▼';
  }}
  headers.forEach(h => h.addEventListener('click',
    () => sort(parseInt(h.dataset.col), h.dataset.type || 'num')));
}})();
</script>
""", height=_tbl_h, scrolling=False)

st.caption(
    "**Updates** — days since the player's latest YouTube upload: "
    "🟢 ≤14d  ·  🟡 ≤30d  ·  🟠 ≤90d  ·  🔴 >90d.  "
    "**Career** — 🟢 currently playing  ·  🔴 retired."
)


# ── Charts ───────────────────────────────────────────────────
st.markdown("---")
st.subheader("How they compare")

_df = pd.DataFrame([{
    "Player": p.get("name"),
    "Subs": int(p.get("subscriber_count") or 0),
    "Subs/Year": _subs_per_year(int(p.get("subscriber_count") or 0), p.get("launched_at")),
} for p in players])

log_scale = st.checkbox(
    "Log scale on subscribers (recommended — one player dwarfs the rest)",
    value=True,
)

col1, col2 = st.columns(2)
with col1:
    fig = px.bar(
        _df.sort_values("Subs", ascending=False),
        x="Player", y="Subs",
        color="Player", color_discrete_map=color_map,
        title="Total subscribers",
        log_y=log_scale,
    )
    fig.update_layout(showlegend=False, xaxis_title="", margin=dict(t=40, b=40))
    st.plotly_chart(fig, use_container_width=True)
with col2:
    fig = px.bar(
        _df.sort_values("Subs/Year", ascending=False),
        x="Player", y="Subs/Year",
        color="Player", color_discrete_map=color_map,
        title="Subscribers / year (normalised for channel age)",
    )
    fig.update_layout(showlegend=False, xaxis_title="", margin=dict(t=40, b=40))
    st.plotly_chart(fig, use_container_width=True)

# ── Posting activity ─────────────────────────────────────────
# Fetch last upload date per player (single query, fast)
st.markdown("---")
st.subheader("Posting activity")
st.caption(
    "Players run their channels on their own rhythm — unlike clubs, many go dormant "
    "for months or years. This shows who's actually still posting."
)

_activity_rows: list[dict] = []
for p in players:
    last_iso = _last_by_cid.get(p["id"], "")
    days = _days_since(last_iso)
    status_label, _ = _status(days)
    _sv = int(p.get("season_views") or 0)
    _ln = int(p.get("season_long_videos") or 0)
    _sn = int(p.get("season_short_videos") or 0)
    _lv = int(p.get("season_live_videos") or 0)
    season_vids = _ln + _sn + _lv
    _activity_rows.append({
        "Player": p.get("name", "?"),
        "Last upload": last_iso or "—",
        "Days ago": days if days is not None else 99999,
        "Status": status_label,
        "Season videos": season_vids,
        "L/S/Lv": f"{_ln} / {_sn} / {_lv}",
        "Season views": _sv,
        "_long": _ln,
        "_short": _sn,
        "_live": _lv,
    })

_activity_df = pd.DataFrame(_activity_rows).sort_values("Days ago", ascending=True)

# Build a styled HTML table matching the leaderboard's look.
_act_rows_html = ""
for idx, (_, r) in enumerate(_activity_df.iterrows(), 1):
    pname = r["Player"]
    pdata = next((pp for pp in players if pp.get("name") == pname), {})
    c1, c2 = dual.get(pname, (color_map.get(pname, "#636EFA"), "#FFFFFF"))
    pdot = (f'<span style="display:inline-block;width:14px;height:14px;border-radius:50%;'
            f'background:{c1};border:1px solid rgba(255,255,255,0.3);position:relative">'
            f'<span style="display:block;width:7px;height:7px;border-radius:50%;'
            f'background:{c2};position:absolute;top:2.5px;left:2.5px"></span></span>')
    handle = pdata.get("handle", "") or ""
    yt_url = f"https://www.youtube.com/{handle}" if handle else ""
    rclick = (f'onclick="window.open(\'{yt_url}\',\'_blank\',\'noopener\')" '
              f'style="cursor:pointer"') if yt_url else ""
    d = r["Days ago"]
    d_disp = f"{d}d ago" if d < 99999 else "—"
    _act_rows_html += f"""<tr {rclick}>
        <td style="padding:6px 12px;text-align:right;color:#888" data-val="{idx}">{idx}</td>
        <td style="padding:6px 12px">{pdot}</td>
        <td style="padding:6px 12px" data-val="{pname}">{pname}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{d}" title="{r['Last upload']}">{d_disp}</td>
        <td style="padding:6px 12px;text-align:left;white-space:nowrap" data-val="{d}">{r['Status']}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{r['Season videos']}">{fmt_num(r['Season videos'])}</td>
        <td style="padding:6px 12px;text-align:center;white-space:nowrap;color:#aaa" data-val="{r['Season videos']}">{r['L/S/Lv']}</td>
        <td style="padding:6px 12px;text-align:right" data-val="{r['Season views']}">{fmt_num(r['Season views'])}</td>
    </tr>"""

_act_h = len(_activity_df) * 40 + 90
components.html(f"""
<style>
  .pl2 {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
          font-family:"Source Sans Pro",sans-serif; }}
  .pl2 th {{ padding:6px 12px; user-select:none; border-bottom:2px solid #444; }}
  .pl2 th[data-col] {{ cursor:pointer; }}
  .pl2 th[data-col]:hover {{ color:#636EFA; }}
  .pl2 td {{ padding:6px 12px; border-bottom:1px solid #262730; vertical-align:middle; }}
  .pl2 tr:hover td {{ background:#1a1c24; }}
  .pl2 .active {{ color:#636EFA; }}
</style>
<table class="pl2">
<thead><tr>
  <th data-col="0" data-type="num" style="text-align:right">#</th>
  <th></th>
  <th data-col="2" data-type="str" style="text-align:left">Player</th>
  <th data-col="3" data-type="num" style="text-align:right" class="active">Last upload ▲</th>
  <th data-col="4" data-type="num" style="text-align:left">Status</th>
  <th data-col="5" data-type="num" style="text-align:right">Season videos</th>
  <th data-col="6" data-type="num" style="text-align:center" title="Long / Shorts / Live">L/S/Lv</th>
  <th data-col="7" data-type="num" style="text-align:right">Season views</th>
</tr></thead>
<tbody>{_act_rows_html}</tbody>
</table>
<script>
(function() {{
  const table = document.querySelector('.pl2');
  const tbody = table.querySelector('tbody');
  const headers = table.querySelectorAll('th[data-col]');
  let currentCol = 3, currentAsc = true;
  function sort(colIdx, type) {{
    const rows = Array.from(tbody.rows);
    const isStr = type === 'str';
    if (colIdx === currentCol) currentAsc = !currentAsc;
    else {{ currentCol = colIdx; currentAsc = isStr; }}
    rows.sort((a, b) => {{
      const va = a.cells[colIdx].dataset.val || '';
      const vb = b.cells[colIdx].dataset.val || '';
      let cmp = isStr ? va.localeCompare(vb) : ((parseFloat(va)||0) - (parseFloat(vb)||0));
      return currentAsc ? cmp : -cmp;
    }});
    rows.forEach(r => tbody.appendChild(r));
    headers.forEach(h => {{
      h.classList.remove('active');
      h.textContent = h.textContent.replace(/ [▲▼]/g, '');
    }});
    const a = table.querySelector('th[data-col="' + colIdx + '"]');
    a.classList.add('active');
    a.textContent += currentAsc ? ' ▲' : ' ▼';
  }}
  headers.forEach(h => h.addEventListener('click',
    () => sort(parseInt(h.dataset.col), h.dataset.type || 'num')));
}})();
</script>
""", height=_act_h, scrolling=False)

# Stacked bar chart — season video mix by format
_mix = _activity_df[["Player", "_long", "_short", "_live"]].rename(
    columns={"_long": "Long", "_short": "Shorts", "_live": "Live"}
)
_mix_long = _mix.melt(id_vars="Player", var_name="Format", value_name="Videos")
_mix_long = _mix_long[_mix_long["Videos"] > 0]
if not _mix_long.empty:
    # Preserve the table's sort order (most recently active first) on the x-axis
    _order = _activity_df["Player"].tolist()
    fig = px.bar(
        _mix_long, x="Player", y="Videos", color="Format",
        title="Season video mix by format",
        color_discrete_map={"Long": "#636EFA", "Shorts": "#FFA15A", "Live": "#EF553B"},
        category_orders={"Player": _order, "Format": ["Long", "Shorts", "Live"]},
    )
    fig.update_layout(
        barmode="stack", xaxis_title="", yaxis_title="Season videos",
        margin=dict(t=40, b=40), legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)

st.caption(
    "Status: 🟢 Active (≤14d)  ·  🟡 Slowing (≤30d)  ·  🟠 Quiet (≤90d)  ·  🔴 Dormant (>90d). "
    "Season window: published since the current-season start date (2025-08-01 for European football)."
)
