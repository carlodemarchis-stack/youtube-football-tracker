"""Shared 48-hour publishing timeline component.

Renders a horizontal strip with thumbnail cards positioned by exact publish
time over the last 48 hours. Long/live cards are 108×80 (16:9 thumb), shorts
are 70×130 (9:16 thumb cropped from the source 16:9 via object-fit:cover —
this trims the letterbox bars and leaves the central vertical content).

Cards click through to the in-page YouTube popup (yt_popup_js) — the popup
respects data-fmt and renders shorts in a 9:16 frame.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from src.analytics import yt_popup_js


def _fmt_of(v: dict) -> str:
    f = (v.get("format") or "").lower()
    if f in ("long", "short", "live"):
        return f
    return "long" if (v.get("duration_seconds") or 0) >= 60 else "short"


def render_48h_timeline(
    videos: list[dict],
    *,
    header: str = "⏱️ Last 48 hours — published timeline",
    caption: str | None = None,
    channel_resolver=None,
) -> bool:
    """Render the timeline. Returns True if anything was drawn.

    channel_resolver: optional callable(video) -> str for the tooltip
    prefix (e.g. club name on the multi-channel Latest page).
    """
    try:
        now48 = datetime.now(timezone.utc)
        from48 = now48 - timedelta(hours=48)

        recent = []
        for v in videos:
            ts = pd.to_datetime(v.get("published_at"), utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            if ts >= from48:
                recent.append((v, ts))
        if not recent:
            return False
        recent.sort(key=lambda t: t[1])

        LEFT_MARGIN = 5.0
        RIGHT_RESERVE = 10.0
        USABLE = 100.0 - LEFT_MARGIN - RIGHT_RESERVE  # 85
        LONG_W_PCT = 8.5
        SHORT_W_PCT = 5.5
        LANES = 4
        LANE_H = 140
        lanes_last_right = [-100.0] * LANES
        placements = []
        for v, pub in recent:
            raw_pct = (pub - from48).total_seconds() / (48 * 3600) * 100
            x_pct = LEFT_MARGIN + raw_pct * USABLE / 100
            f = _fmt_of(v)
            w_pct = SHORT_W_PCT if f == "short" else LONG_W_PCT
            lane = LANES - 1
            for i, lr in enumerate(lanes_last_right):
                if x_pct >= lr:
                    lane = i
                    break
            lanes_last_right[lane] = x_pct + w_pct
            placements.append((v, x_pct, lane, pub, f))

        if header:
            st.subheader(header)
        if caption is None:
            caption = (f"{len(recent)} video(s) in the last 48h. Cards "
                       f"positioned by exact publish time (CET ticks). "
                       f"Click any card to open in popup.")
        if caption:
            st.caption(caption)

        cards_html = ""
        for v, x_pct, lane, pub, f in placements:
            pub_cet = pub.tz_convert("Europe/Rome")
            yt_url = f"https://www.youtube.com/watch?v={v.get('youtube_video_id','')}"
            thumb = (v.get("thumbnail_url") or "").replace('"', "&quot;")
            title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            fmt_color = {"long": "#636EFA", "short": "#00CC96", "live": "#FFA15A"}[f]
            fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[f]
            pub_str = pub_cet.strftime("%a %H:%M")
            ch_prefix = ""
            if channel_resolver is not None:
                try:
                    cn = channel_resolver(v) or ""
                    if cn:
                        ch_prefix = (cn.replace("<", "&lt;").replace(">", "&gt;")
                                       .replace('"', "&quot;") + " · ")
                except Exception:
                    pass
            top_px = lane * LANE_H + 8 + 30
            shape_cls = "t48-short" if f == "short" else "t48-long"
            cards_html += (
                f'<a href="{yt_url}" target="_blank" rel="noopener" '
                f'data-fmt="{f}" '
                f'class="t48-card {shape_cls}" '
                f'style="left:{x_pct:.2f}%;top:{top_px}px;'
                f'border-top:3px solid {fmt_color}" '
                f'title="{ch_prefix}{title} · {fmt_label} · {pub_str}">'
                f'<img src="{thumb}" />'
                f'<div class="t48-title">{title}</div>'
                f'</a>'
            )

        now_cet = now48.astimezone(ZoneInfo("Europe/Rome")).replace(
            minute=0, second=0, microsecond=0
        )
        ticks_html = ""
        for h in range(0, 49, 6):
            x = LEFT_MARGIN + (48 - h) / 48 * USABLE
            if h == 0:
                lab = "now"
            else:
                t = now_cet - timedelta(hours=h)
                lab = t.strftime("%a %H:%M")
            ticks_html += (
                f'<div class="t48-tick" style="left:{x:.1f}%"></div>'
                f'<div class="t48-ticklabel t48-tick-top" style="left:{x:.1f}%">{lab}</div>'
                f'<div class="t48-ticklabel t48-tick-bot" style="left:{x:.1f}%">{lab}</div>'
            )

        total_height = 30 + LANES * LANE_H + 30
        components.html(f"""
        <style>
          .t48-wrap {{ position:relative; width:100%; height:{total_height}px;
                       background:#0E1117; border-radius:6px; overflow:hidden;
                       font-family:"Source Sans Pro",sans-serif; }}
          .t48-tick {{ position:absolute; top:30px; bottom:30px; width:1px;
                       background:rgba(255,255,255,0.06); }}
          .t48-ticklabel {{ position:absolute; transform:translateX(-50%);
                            font-size:10px; color:#888; }}
          .t48-tick-top {{ top:8px; }}
          .t48-tick-bot {{ bottom:8px; }}
          .t48-card {{ position:absolute;
                       background:#1a1c24; border-radius:4px; overflow:hidden;
                       color:#FAFAFA; text-decoration:none; display:block;
                       transition:transform 0.1s ease; cursor:pointer; }}
          .t48-card:hover {{ transform:translateY(-2px); z-index:10;
                             box-shadow:0 4px 12px rgba(0,0,0,0.4); }}
          .t48-long {{ width:108px; height:80px; }}
          .t48-long img {{ width:108px; height:45px; object-fit:cover; display:block; }}
          .t48-short {{ width:70px; height:130px; }}
          .t48-short img {{ width:70px; height:100px; object-fit:cover;
                            object-position:center; display:block; }}
          .t48-title {{ font-size:10px; line-height:1.15; padding:3px 5px 0 5px;
                        font-weight:600; color:#FAFAFA;
                        display:-webkit-box; -webkit-line-clamp:2;
                        -webkit-box-orient:vertical; overflow:hidden;
                        text-overflow:ellipsis; }}
        </style>
        <div class="t48-wrap">
          {ticks_html}
          {cards_html}
        </div>
        {yt_popup_js()}
        """, height=total_height + 20, scrolling=False)
        return True
    except Exception as e:
        st.caption(f"(48h timeline unavailable: {e})")
        return False


def render_48h_dots(
    videos: list[dict],
    *,
    header: str = "⏱️ Last 48 hours — published timeline",
    caption: str | None = None,
    channel_resolver=None,
    color_resolver=None,
) -> bool:
    """Compact dot version of the 48h timeline.

    One row per channel; each video is a dot positioned at its publish
    time and colored by `color_resolver(video) -> "#rrggbb"`. Dot shape
    encodes format (long=circle, short=diamond, live=square). Click a
    dot to open the video in the popup.
    """
    try:
        now48 = datetime.now(timezone.utc)
        from48 = now48 - timedelta(hours=48)

        recent = []
        for v in videos:
            ts = pd.to_datetime(v.get("published_at"), utc=True, errors="coerce")
            if pd.isna(ts):
                continue
            if ts >= from48:
                recent.append((v, ts))
        if not recent:
            return False

        # Group by channel; row order = most recent activity first.
        by_ch: dict[str, list[tuple[dict, object]]] = {}
        ch_label: dict[str, str] = {}
        ch_last: dict[str, object] = {}
        for v, ts in recent:
            cid = v.get("channel_id") or "?"
            by_ch.setdefault(cid, []).append((v, ts))
            if channel_resolver is not None:
                try:
                    ch_label[cid] = channel_resolver(v) or v.get("channel_name") or cid
                except Exception:
                    ch_label[cid] = v.get("channel_name") or cid
            else:
                ch_label[cid] = v.get("channel_name") or cid
            if cid not in ch_last or ts > ch_last[cid]:
                ch_last[cid] = ts
        rows = sorted(by_ch.keys(), key=lambda c: ch_last[c], reverse=True)

        LEFT_MARGIN = 12.0   # leaves room for the channel-name labels
        RIGHT_RESERVE = 4.0
        USABLE = 100.0 - LEFT_MARGIN - RIGHT_RESERVE
        ROW_H = 22
        TOP_PAD = 28        # axis labels at top
        BOT_PAD = 24        # axis labels at bottom

        if header:
            st.subheader(header)
        if caption is None:
            caption = (f"{len(recent)} video(s) across {len(rows)} club(s) "
                       f"in the last 48h. Click any dot to open the video.")
        if caption:
            st.caption(caption)

        # Build axis ticks (every 6h, wall-clock CET, full hour).
        now_cet = now48.astimezone(ZoneInfo("Europe/Rome")).replace(
            minute=0, second=0, microsecond=0
        )
        ticks_html = ""
        for h in range(0, 49, 6):
            x = LEFT_MARGIN + (48 - h) / 48 * USABLE
            lab = "now" if h == 0 else (now_cet - timedelta(hours=h)).strftime("%a %H:%M")
            ticks_html += (
                f'<div class="dt-tick" style="left:{x:.2f}%"></div>'
                f'<div class="dt-ticklabel dt-tick-top" style="left:{x:.2f}%">{lab}</div>'
                f'<div class="dt-ticklabel dt-tick-bot" style="left:{x:.2f}%">{lab}</div>'
            )

        # Build channel rows + dots.
        rows_html = ""
        for ridx, cid in enumerate(rows):
            top_px = TOP_PAD + ridx * ROW_H
            label = (ch_label.get(cid, "") or "").replace("<", "&lt;").replace(">", "&gt;")
            rows_html += (
                f'<div class="dt-rowline" style="top:{top_px + ROW_H // 2}px"></div>'
                f'<div class="dt-rowlabel" style="top:{top_px}px;line-height:{ROW_H}px">'
                f'{label}</div>'
            )
            for v, ts in by_ch[cid]:
                f = _fmt_of(v)
                color = "#636EFA"
                if color_resolver is not None:
                    try:
                        color = color_resolver(v) or color
                    except Exception:
                        pass
                raw_pct = (ts - from48).total_seconds() / (48 * 3600) * 100
                x_pct = LEFT_MARGIN + raw_pct * USABLE / 100
                yt_url = f"https://www.youtube.com/watch?v={v.get('youtube_video_id','')}"
                title = (v.get("title") or "").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
                pub_str = ts.tz_convert("Europe/Rome").strftime("%a %H:%M")
                fmt_label = {"long": "Long", "short": "Shorts", "live": "Live"}[f]
                shape_cls = {"long": "dt-long", "short": "dt-short", "live": "dt-live"}[f]
                rows_html += (
                    f'<a href="{yt_url}" target="_blank" rel="noopener" '
                    f'data-fmt="{f}" '
                    f'class="dt-dot {shape_cls}" '
                    f'style="left:{x_pct:.3f}%;top:{top_px + ROW_H // 2}px;'
                    f'background:{color}" '
                    f'title="{label} · {title} · {fmt_label} · {pub_str}"></a>'
                )

        total_height = TOP_PAD + len(rows) * ROW_H + BOT_PAD
        components.html(f"""
        <style>
          .dt-wrap {{ position:relative; width:100%; height:{total_height}px;
                     background:#0E1117; border-radius:6px; overflow:hidden;
                     font-family:"Source Sans Pro",sans-serif; }}
          .dt-tick {{ position:absolute; top:{TOP_PAD - 4}px;
                      bottom:{BOT_PAD - 4}px; width:1px;
                      background:rgba(255,255,255,0.06); }}
          .dt-ticklabel {{ position:absolute; transform:translateX(-50%);
                           font-size:10px; color:#888; }}
          .dt-tick-top {{ top:6px; }}
          .dt-tick-bot {{ bottom:4px; }}
          .dt-rowline {{ position:absolute; left:{LEFT_MARGIN}%;
                         right:{RIGHT_RESERVE}%; height:1px;
                         background:rgba(255,255,255,0.04); }}
          .dt-rowlabel {{ position:absolute; left:4px; width:{LEFT_MARGIN - 1}%;
                          font-size:11px; color:#bbb; overflow:hidden;
                          white-space:nowrap; text-overflow:ellipsis;
                          padding-right:6px; box-sizing:border-box; }}
          .dt-dot {{ position:absolute; transform:translate(-50%,-50%);
                     border:1px solid rgba(0,0,0,0.5); cursor:pointer;
                     transition:transform 0.1s ease; }}
          .dt-dot:hover {{ transform:translate(-50%,-50%) scale(1.6);
                           z-index:10; box-shadow:0 0 0 2px rgba(255,255,255,0.4); }}
          .dt-long  {{ width:11px; height:11px; border-radius:50%; }}
          .dt-short {{ width:10px; height:10px;
                       transform:translate(-50%,-50%) rotate(45deg); }}
          .dt-short:hover {{ transform:translate(-50%,-50%) rotate(45deg) scale(1.6); }}
          .dt-live  {{ width:11px; height:11px; }}
        </style>
        <div class="dt-wrap">
          {ticks_html}
          {rows_html}
        </div>
        {yt_popup_js()}
        """, height=total_height + 16, scrolling=False)
        return True
    except Exception as e:
        st.caption(f"(48h dot timeline unavailable: {e})")
        return False
