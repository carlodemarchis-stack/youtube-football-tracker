"""Shared publishing-heatmap figure (day-of-week × hour, CET).

The Season page (Z1/Z2/Z3) has its own inline renders; this module is the
reusable figure builder behind the league / channel heatmap grids so they
look identical. Colour scale lifted verbatim from the Season page.
"""
from __future__ import annotations

import plotly.graph_objects as go

from src.analytics import fmt_num

_HEATMAP_SCALE = [
    [0.0, "#0E1117"], [0.001, "#2a1c1c"], [0.30, "#7a2c2c"],
    [0.55, "#FF6B35"], [0.80, "#FF1744"], [1.0, "#FFEB3B"],
]
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def peak_label(counts) -> str:
    """'Peak slot: **Mon 00:00 CET** with N posts.' (or '' if empty)."""
    mx = pdow = phr = 0
    for r in range(7):
        for c in range(24):
            v = int(counts[r][c] or 0)
            if v > mx:
                mx, pdow, phr = v, r, c
    if mx <= 0:
        return ""
    return f"peak {_DOW[pdow]} {phr:02d}:00 CET ({mx} posts)"


def heatmap_figure(counts, avg_views, height: int = 280) -> go.Figure:
    """7×24 publishing-rhythm heatmap. counts / avg_views are 7-row (Mon→Sun)
    × 24-col (00→23, CET) 2D lists."""
    hours = [f"{h:02d}" for h in range(24)]
    avg_disp = [[fmt_num(int(round(avg_views[r][c] or 0))) for c in range(24)]
                for r in range(7)]
    z = [[int(counts[r][c] or 0) for c in range(24)] for r in range(7)]
    fig = go.Figure(go.Heatmap(
        z=z, x=hours, y=_DOW, customdata=avg_disp,
        colorscale=_HEATMAP_SCALE,
        colorbar=dict(title="Posts", thickness=10, x=1.02, len=0.9),
        hovertemplate=("<b>%{y} %{x}:00</b><br>%{z} post(s)<br>"
                       "Avg views/post: %{customdata}<extra></extra>"),
        xgap=1, ygap=1,
    ))
    fig.update_layout(
        height=height,
        xaxis=dict(title="Hour (CET)", tickfont=dict(size=10), side="bottom",
                   tickmode="array",
                   tickvals=[f"{h:02d}" for h in range(0, 24, 3)],
                   ticktext=[f"{h:02d}" for h in range(0, 24, 3)]),
        yaxis=dict(title="", autorange="reversed", tickfont=dict(size=11)),
        margin=dict(t=10, b=40, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"),
    )
    return fig
