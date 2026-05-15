"""Shared chart-presentation helpers.

Donut/pie charts in a "KPI bar → row of donuts" layout were each
setting a plotly in-chart title (anchored to the chart container, so
it drifted off-centre relative to the donut) plus a tall t=40 top
margin that left a big empty band under the KPI cards.

The fix, applied consistently everywhere: no in-chart title — render
it as a centred caption above the chart via `chart_title()` — and use
the tight donut layout constants below. One definition so every
surface stays visually identical.
"""
from __future__ import annotations

import streamlit as st

from src import theme as _T

# Standard compact donut layout (no title band, snug margins).
DONUT_HEIGHT = 260
DONUT_MARGIN = dict(t=10, b=10, l=10, r=10)


def chart_title(text: str) -> None:
    """Centred caption rendered directly above a chart, sized to sit
    snug against it (negative bottom margin pulls the chart up)."""
    st.markdown(
        f"<div style='text-align:center;font-weight:600;font-size:15px;"
        f"color:{_T.TEXT};margin:0 0 -6px 0'>{text}</div>",
        unsafe_allow_html=True,
    )
