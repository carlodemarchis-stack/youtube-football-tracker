"""Player detail — one player's full YouTube history.

Opened from the Updates dot on the Players page (/player?id=<UC… id>).
Data: channel stats from `channels` + the nightly 'player_history'
dashboard_cache row (src.player_history): every video since the
channel's first upload with its views, format and title — plotted one
dot per video by publish day, plus the top 20 by views.
"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src.analytics import fmt_num, kpi_row
from src.auth import require_login
from src.cached_db import get_all_channels as _cached_channels
from src.cached_db import get_player_history as _cached_player_history
from src.database import Database

load_dotenv()
require_login()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
players = sorted(
    [c for c in _cached_channels(db) if c.get("entity_type") == "Player"],
    key=lambda c: -int(c.get("subscriber_count") or 0),
)
if not players:
    st.info("No players tracked yet.")
    st.stop()

_FC = {"long": "#5B8DEF", "shorts": "#2FCB74", "live": "#E7A93B"}
_FL = {"long": "Long", "shorts": "Shorts", "live": "Live"}

# ── Player picker, synced with ?id= ──────────────────────────
_by_id = {c.get("youtube_channel_id") or "": c for c in players}
_ids = list(_by_id)
_qp = st.query_params.get("id", "")
# Seed the picker from ?id= on first load, or when the URL was changed
# from outside (e.g. a new link); afterwards the widget owns the choice.
if _qp in _by_id and _qp != st.session_state.get("_player_qp_seen"):
    st.session_state["player_pick"] = _qp
st.session_state["_player_qp_seen"] = _qp
if st.session_state.get("player_pick") not in _by_id:
    st.session_state["player_pick"] = _ids[0]


def _on_pick() -> None:
    st.query_params["id"] = st.session_state["player_pick"]
    st.session_state["_player_qp_seen"] = st.session_state["player_pick"]


st.selectbox("Player", _ids, key="player_pick", on_change=_on_pick,
             format_func=lambda i: _by_id[i].get("name", "?"),
             label_visibility="collapsed")
p = _by_id[st.session_state["player_pick"]]

st.title(p.get("name", "Player"))
handle = p.get("handle") or ""
if handle:
    st.caption(f"[youtube.com/{handle}](https://www.youtube.com/{handle})")

h = _cached_player_history(db, p["id"])
vids = pd.DataFrame(h.get("videos") or [],
                    columns=["id", "day", "views", "fmt", "title"])

st.markdown(kpi_row([
    ("Subscribers", fmt_num(int(p.get("subscriber_count") or 0))),
    ("Total views", fmt_num(int(p.get("total_views") or 0))),
    ("Videos",      fmt_num(int(p.get("video_count") or 0))),
    ("First upload", h.get("first") or "—"),
]), unsafe_allow_html=True)

if vids.empty:
    st.info("Upload history isn't computed for this player yet — "
            "it's refreshed by the nightly Daily Players job.")
    st.stop()

# ── Every video by publish day, height = views ───────────────
st.subheader("Every video, by day")
_log = st.toggle("Log scale", value=True,
                 help="On a log scale a 1M and a 100M video both stay readable.")
st.caption(f"{len(vids):,} videos since {h.get('first')} — one dot per video, "
           "height = views. Use the range buttons or drag the slider to zoom; "
           "hover a dot for its title.")
vids["day"] = pd.to_datetime(vids["day"])
fig = go.Figure()
for k in ("long", "shorts", "live"):
    # 0-view rows are upcoming premieres/streams; a log axis can't show them.
    d = vids[(vids["fmt"] == k) & (vids["views"] > 0)]
    if d.empty:
        continue
    fig.add_scatter(
        x=d["day"], y=d["views"], mode="markers", name=_FL[k],
        marker=dict(color=_FC[k], size=7, opacity=0.8, line=dict(width=0)),
        customdata=d[["title"]].values,
        hovertemplate="%{customdata[0]}<br>%{x|%d %b %Y} · %{y:,} views"
                      "<extra>" + _FL[k] + "</extra>",
    )
fig.update_layout(
    height=480, margin=dict(t=30, b=10, l=10, r=10),
    legend=dict(orientation="h", y=1.08, x=0, traceorder="normal"),
    yaxis=dict(title="Views", type="log" if _log else "linear"),
    xaxis=dict(
        type="date",
        rangeslider=dict(visible=True, thickness=0.08),
        rangeselector=dict(buttons=[
            dict(count=3, label="3m", step="month", stepmode="backward"),
            dict(count=1, label="1y", step="year", stepmode="backward"),
            dict(count=3, label="3y", step="year", stepmode="backward"),
            dict(step="all", label="All"),
        ]),
    ),
)
st.plotly_chart(fig, width="stretch")

# ── Top videos by views ──────────────────────────────────────
top = vids.sort_values("views", ascending=False).head(20)
st.subheader(f"Top {len(top)} videos by views")
_t = top.iloc[::-1]  # largest at the top of a horizontal bar
fig2 = go.Figure(go.Bar(
    x=_t["views"], y=[(s[:70] + "…") if len(s) > 70 else s for s in _t["title"]],
    orientation="h",
    marker_color=[_FC.get(f, _FC["long"]) for f in _t["fmt"]],
    customdata=list(zip([_FL.get(f, "Long") for f in _t["fmt"]],
                        _t["day"].dt.strftime("%Y-%m-%d"))),
    hovertemplate="%{y}<br>%{x:,} views · %{customdata[0]} · %{customdata[1]}<extra></extra>",
    text=[fmt_num(int(v)) for v in _t["views"]], textposition="outside", cliponaxis=False,
))
fig2.update_layout(height=max(320, 28 * len(_t) + 60),
                   margin=dict(t=10, b=10, l=10, r=60),
                   xaxis=dict(showticklabels=False, showgrid=False,
                              range=[0, int(_t["views"].max()) * 1.12]),
                   yaxis=dict(automargin=True))
st.plotly_chart(fig2, width="stretch")

st.dataframe(
    pd.DataFrame({
        "Video": "https://www.youtube.com/watch?v=" + top["id"],
        "Title": top["title"], "Views": top["views"],
        "Format": [_FL.get(f, "Long") for f in top["fmt"]],
        "Published": top["day"].dt.strftime("%Y-%m-%d"),
    }),
    hide_index=True, width="stretch",
    column_config={
        "Video": st.column_config.LinkColumn("Video", display_text="▶ Watch"),
        "Views": st.column_config.NumberColumn(format="localized"),
    },
)
