"""Shared 'Top Season Videos' table renderer.

Used by views/3b_Season_Top_Videos.py and (historically) was inlined in
views/3_Season_2526.py at all 3 zoom levels.

Each call hits db.get_top_season_videos(channel_ids, since, limit,
order_by) once and renders the standard table layout — channel marker
on the meta line, format/duration/date/category badges, click-to-open.
"""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from src.analytics import fmt_num, yt_popup_js, CATEGORY_COLORS, video_table_height
from src.dot import channel_badge
from src.filters import get_global_color_map, get_global_color_map_dual


def render_top_season_videos(
    db,
    channel_ids: list[str],
    channels_by_id: dict,
    since: str,
    *,
    limit: int = 20,
    header: str = "Top Season Videos",
    order_by: str = "view_count",
) -> int:
    """Render one Top-N season-videos table. Returns the row count."""
    vids = db.get_top_season_videos(
        channel_ids=channel_ids, since=since,
        limit=limit, order_by=order_by,
    )
    if not vids:
        return 0
    st.subheader(header)

    def _fmt_of_local(v):
        f = (v.get("format") or "").lower()
        if f in ("long", "short", "live"):
            return f
        return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"

    _COLOR = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}
    _LABEL = {"long": "Long", "short": "Shorts", "live": "Live"}

    color_map = get_global_color_map() or {}
    dual_map = get_global_color_map_dual() or {}

    def _dur_local(secs):
        s = int(secs or 0)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    rows = ""
    for i, v in enumerate(vids, 1):
        ch = channels_by_id.get(v.get("channel_id")) or v.get("channels") or {}
        ch_name = ch.get("name", "?")
        ch_dot = channel_badge(ch, color_map, dual_map, 12)
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        _f = _fmt_of_local(v)
        fmt_label = _LABEL[_f]
        fmt_color = _COLOR[_f]
        pub = (v.get("published_at") or "")[:10]
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        _cat = (v.get("category") or "").replace("<", "&lt;")
        _cat_color = CATEGORY_COLORS.get(_cat, "#888")
        _cat_span = (f' · <span style="color:{_cat_color}">{_cat}</span>'
                     if _cat and _cat != "Other" else "")
        _meta = (
            f'<span style="display:inline-flex;align-items:center;gap:6px">'
            f'{ch_dot}<span style="color:#FAFAFA">{ch_name}</span></span>'
            f' · <span style="color:{fmt_color}">{fmt_label}</span>'
            f' · {_dur_local(v.get("duration_seconds", 0))}'
            f' · <span style="color:#888">{pub}</span>'
            f'{_cat_span}'
        )
        rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:6px 12px;vertical-align:top"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px;display:block"></td>
            <td style="padding:6px 12px;vertical-align:top">
              <div style="display:flex;flex-direction:column;justify-content:space-between;height:62px">
                <a href="{yt_url}" target="_blank" style="color:#FAFAFA;text-decoration:none;font-weight:700"><br>{title}</a>
                <div style="font-size:12px">{_meta}</div>
              </div>
            </td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('view_count') or 0))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('like_count') or 0))}</td>
            <td style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('comment_count') or 0))}</td>
        </tr>"""
    components.html(f"""
    <style>
        .top-vids {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                     font-family:"Source Sans Pro",sans-serif; }}
        .top-vids th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
        .top-vids td {{ border-bottom:1px solid #262730; }}
        .top-vids tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="top-vids">
    <thead><tr>
        <th style="text-align:right">#</th>
        <th></th>
        <th>Video</th>
        <th style="text-align:right">Views</th>
        <th style="text-align:right">Likes</th>
        <th style="text-align:right">Comments</th>
    </tr></thead>
    <tbody>{rows}</tbody>
    </table>
    {yt_popup_js()}
    """, height=video_table_height(len(vids)), scrolling=False)
    return len(vids)
