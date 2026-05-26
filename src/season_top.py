"""Shared 'Top Season Videos' table renderer.

Used by views/3b_Season_Top_Videos.py and (historically) was inlined in
views/3_Season.py at all 3 zoom levels.

Each call hits db.get_top_season_videos(channel_ids, since, limit,
order_by) once and renders the standard table layout — channel marker
on the meta line, format/duration/date/category badges, click-to-open.
"""
from __future__ import annotations

import streamlit as st
from src import components_compat as components
from src.analytics import (fmt_num, fmt_pub_date, yt_popup_js,
                            CATEGORY_COLORS, video_table_height)
from src.dot import channel_badge
from src import theme as _T
from src.filters import get_global_color_map, get_global_color_map_dual


def fetch_top_season_videos(
    db,
    channel_ids: list[str],
    since: str,
    *,
    limit: int = 20,
    order_by: str = "view_count",
    excluded_channel_ids: list[str] | None = None,
) -> list[dict]:
    """Same query the renderer used to do internally — exposed so the
    KPI bar can compute aggregates without re-fetching.

    Z1 hint: when `channel_ids` covers ~all of the videos table (i.e.,
    "everything except a few isolated entity types"), pass the small
    exclusion list as `excluded_channel_ids` instead. We then issue a
    global query with no `IN` filter and post-filter Python-side —
    avoids the 100-element `IN` clause that has been timing out under
    anon connections from Railway → Supabase. Over-fetches by 4× to
    leave room for excluded rows in the head of the result.
    """
    if excluded_channel_ids:
        # Global query, server-side over-fetch, client-side exclude.
        ex = set(excluded_channel_ids)
        rows = db.get_top_season_videos(
            channel_ids=None, since=since,
            limit=max(limit * 4, 50), order_by=order_by,
        ) or []
        return [r for r in rows if r.get("channel_id") not in ex][:limit]

    return db.get_top_season_videos(
        channel_ids=channel_ids, since=since,
        limit=limit, order_by=order_by,
    ) or []


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
    """Render one Top-N season-videos table. Returns the row count.
    Convenience wrapper — fetches via fetch_top_season_videos and hands
    off to render_top_season_videos_table."""
    vids = fetch_top_season_videos(db, channel_ids, since,
                                   limit=limit, order_by=order_by)
    if not vids:
        return 0
    return render_top_season_videos_table(vids, channels_by_id, header=header)


def render_top_season_videos_table(
    vids: list[dict],
    channels_by_id: dict,
    *,
    header: str,
    subtitle: str | None = None,
    order_by: str = "views",
    max_height: int | None = None,
    extra_metric_col: dict | None = None,
) -> int:
    """Renderer-only path. Takes a pre-fetched video list so callers
    that also need the same data for KPIs aren't paying for the query
    twice.

    order_by: which metric the list is ranked by ("views" | "likes" |
    "comments"). That column is highlighted (accent) so it's obvious
    what defines the order. Column widths are fixed so every ranked
    table on the page lines up identically.

    max_height: cap the iframe height in pixels and enable inner
    scrolling. Useful for long lists (100 rows) where letting the
    table take its natural height makes the page unmanageable.
    """
    if not vids:
        return 0
    st.subheader(header)
    if subtitle:
        st.caption(subtitle)

    def _fmt_of_local(v):
        f = (v.get("format") or "").lower()
        if f in ("long", "short", "live"):
            return f
        return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"

    # Format colors per CONVENTIONS §1 (Long=ACCENT, Shorts=POS, Live=WARN).
    _COLOR = {"long": _T.ACCENT, "short": _T.POS, "live": _T.WARN}
    _LABEL = {"long": "Long", "short": "Shorts", "live": "Live"}

    color_map = get_global_color_map() or {}
    dual_map = get_global_color_map_dual() or {}

    def _dur_local(secs):
        s = int(secs or 0)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # Which metric column defines the ranking → gets the accent
    # highlight (same "active column = accent" idiom as the other
    # tables). Empty class on the others.
    _ob = (order_by or "views").lower()
    if _ob not in ("views", "likes", "comments", "extra"):
        _ob = "views"
    _ordc = {"views": "", "likes": "", "comments": "", "extra": ""}
    _ordc[_ob] = "ord"

    # extra_metric_col, when provided, inserts an additional metric
    # column LEFT of "Views" (label/field per call). Common use: the
    # 30-Day Trends page wants Δ views per video on top of the standard
    # cumulative views/likes/comments. Set order_by="extra" to make
    # the inserted column the highlighted (ranking) column.
    # Normalise extra_metric_col to a list of dicts (back-compat: a
    # single dict still works). The first entry is the "ranking" column
    # — it gets the accent highlight when order_by="extra". Subsequent
    # entries render as plain right-aligned metric cells.
    _xcs_in = (extra_metric_col if isinstance(extra_metric_col, list)
               else ([extra_metric_col] if extra_metric_col else []))
    _xcs: list[dict] = []
    for _i, _c in enumerate(_xcs_in):
        if not _c or not _c.get("field"):
            continue
        _xcs.append({
            "field": _c["field"],
            "label": _c.get("label") or ("Δ Views" if _i == 0 else _c["field"]),
            # Optional custom formatter for the extra cell — default keeps
            # the historic "+N" + fmt_num (K-abbreviated) behaviour the
            # 30-Day Trends page relies on. Pass format=lambda x: ...
            # to override.
            "format": _c.get("format"),
        })

    rows = ""
    for i, v in enumerate(vids, 1):
        ch = channels_by_id.get(v.get("channel_id")) or v.get("channels") or {}
        ch_name = ch.get("name", "?")
        ch_badge = channel_badge(ch, color_map, dual_map, 14)
        yt_url = f"https://www.youtube.com/watch?v={v['youtube_video_id']}"
        thumb = v.get("thumbnail_url") or ""
        _f = _fmt_of_local(v)
        fmt_label = _LABEL[_f]
        fmt_color = _COLOR[_f]
        dur_s = _dur_local(v.get("duration_seconds", 0))
        # Canonical pub-date format (see src.analytics.fmt_pub_date).
        pub = fmt_pub_date(v.get("published_at"))
        title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;")
        cat = (v.get("category") or "").replace("<", "&lt;")
        _cat_color = CATEGORY_COLORS.get(cat, _T.MUTED)
        _cat_span = (f' · <span style="color:{_cat_color}">{cat}</span>'
                     if cat and cat != "Other" else "")
        # 3rd row — attributes stay inline (format · duration · date · cat).
        _context = (
            f'<span style="color:{fmt_color}">{fmt_label}</span>'
            f' · {dur_s}'
            f' · <span style="color:{_T.MUTED}">{pub}</span>'
            f'{_cat_span}'
        )
        _extra_cell = ""
        for _xi, _xc in enumerate(_xcs):
            _xv = int(v.get(_xc["field"]) or 0)
            _xstr = (_xc["format"](_xv) if _xc.get("format")
                     else (("+" if _xv > 0 else "") + fmt_num(_xv)))
            _klass = _ordc["extra"] if _xi == 0 else ""
            _extra_cell += (f'<td class="{_klass}" '
                            f'style="padding:6px 12px;text-align:right">{_xstr}</td>')
        rows += f"""<tr onclick="window.open('{yt_url}','_blank','noopener')" style="cursor:pointer">
            <td style="padding:6px 12px;text-align:right;color:{_T.MUTED};vertical-align:top">{i}</td>
            <td style="padding:6px 12px;vertical-align:top"><img src="{thumb}" style="width:110px;height:62px;object-fit:cover;border-radius:4px;display:block"></td>
            <td style="padding:6px 12px;vertical-align:top">
              <div class="v-info">
                <div class="v-channel">{ch_badge} <span style="color:{_T.MUTED_2}">{ch_name}</span></div>
                <a href="{yt_url}" target="_blank" rel="noopener" class="v-title">{title}</a>
                <div class="v-meta">{_context}</div>
              </div>
            </td>
            {_extra_cell}
            <td class="{_ordc['views']}" style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('view_count') or 0))}</td>
            <td class="{_ordc['likes']}" style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('like_count') or 0))}</td>
            <td class="{_ordc['comments']}" style="padding:6px 12px;text-align:right">{fmt_num(int(v.get('comment_count') or 0))}</td>
        </tr>"""

    # Fixed column widths so all ranked tables on the page line up
    # identically regardless of content (table-layout:fixed reads
    # these). Video column has no width → takes the remaining space.
    _COLGROUP = (
        "<colgroup>"
        '<col style="width:44px">'
        '<col style="width:134px">'
        "<col>"
        + ('<col style="width:120px">' * len(_xcs))
        + '<col style="width:96px">'
        '<col style="width:96px">'
        '<col style="width:110px">'
        "</colgroup>"
    )
    # Small ▼ on the ranking column (descending). It sits inside the
    # th.ord, so it inherits the accent color automatically.
    def _hd(label: str, key: str) -> str:
        arrow = " ▼" if key == _ob else ""
        return (f'<th class="{_ordc[key]}" style="text-align:right">'
                f'{label}{arrow}</th>')

    _THEAD = (
        "<thead><tr>"
        '<th style="text-align:right">#</th>'
        "<th></th>"
        "<th>Video</th>"
        + "".join(
            _hd(_xc["label"], "extra") if _xi == 0
            else f'<th style="text-align:right">{_xc["label"]}</th>'
            for _xi, _xc in enumerate(_xcs)
        )
        + _hd("Views", "views")
        + _hd("Likes", "likes")
        + _hd("Comments", "comments")
        + "</tr></thead>"
    )
    natural = video_table_height(len(vids))
    if max_height is not None and natural > max_height:
        # Scrollable container; sticky header keeps context on scroll.
        body_html = (
            f'<div style="max-height:{max_height - 16}px;overflow-y:auto">'
            '<table class="top-vids">'
            + _COLGROUP + _THEAD +
            f'<tbody>{rows}</tbody></table></div>'
        )
        height_used = max_height
        scrolling = False
    else:
        body_html = (
            '<table class="top-vids">'
            + _COLGROUP + _THEAD +
            f'<tbody>{rows}</tbody></table>'
        )
        height_used = natural
        scrolling = False

    components.html(f"""
    <style>
        .top-vids {{ width:100%; table-layout:fixed; border-collapse:collapse;
                     font-size:14px; color:{_T.TEXT};
                     font-family:"Source Sans Pro",sans-serif; }}
        .top-vids th {{ padding:8px 12px; border-bottom:2px solid {_T.BORDER_STRONG};
                        text-align:left; background:{_T.BG}; position:sticky;
                        top:0; z-index:1; }}
        .top-vids td {{ border-bottom:1px solid {_T.BORDER}; }}
        .top-vids tr:hover td {{ background:{_T.SURFACE}; }}
        /* Ranking column highlight (accent = "this defines the order") */
        .top-vids th.ord, .top-vids td.ord {{ color:{_T.ACCENT};
                                              font-weight:700; }}
        .top-vids .v-info {{ min-width:0; display:flex; flex-direction:column;
                             justify-content:space-between; height:62px; }}
        .top-vids .v-channel {{ font-size:12px; display:flex; align-items:center;
                                gap:6px; white-space:nowrap; overflow:hidden;
                                text-overflow:ellipsis; }}
        .top-vids .v-title {{ color:{_T.TEXT}; text-decoration:none; font-size:13px;
                              line-height:1.25; font-weight:700; display:-webkit-box;
                              -webkit-line-clamp:1; -webkit-box-orient:vertical;
                              overflow:hidden; text-overflow:ellipsis; }}
        .top-vids .v-meta {{ font-size:12px; white-space:nowrap; overflow:hidden;
                             text-overflow:ellipsis; }}
    </style>
    {body_html}
    {yt_popup_js()}
    """, height=height_used, scrolling=scrolling)
    return len(vids)
