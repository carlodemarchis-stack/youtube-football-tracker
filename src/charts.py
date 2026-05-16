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


def readable_hover(fig, *, x_date: bool = False, value_fmt: str = ",",
                    skip_existing: bool = False):
    """Give every trace a readable hovertemplate — thousands separators
    on numbers, a formatted date when the x-axis is temporal — WITHOUT
    touching data, colours, layout or chart type. Returns ``fig`` so it
    can be chained before ``st.plotly_chart``.

    Default-hover Plotly charts (and Plotly Express' auto template) show
    raw values like ``14924413`` and, for temporal axes Altair-style,
    can be unreadable. This standardises them:

    - **Pie** → ``label: value`` (comma-grouped).
    - **Bar / scatter** → optional series name, the x value (formatted
      date when ``x_date``), then the comma-grouped numeric value.
      Horizontal bars (``orientation='h'``) swap x/y correctly.

    ``skip_existing=True`` leaves any trace that already carries a
    hovertemplate untouched — note Plotly Express always sets one, so
    keep the default (``False``) when the intent is to replace px's
    auto template; pass ``True`` only for hand-tuned ``go`` figures you
    want to preserve.
    """
    x_expr = "%{x|%a %b %d, %Y}" if x_date else "%{x}"
    for tr in fig.data:
        if skip_existing and getattr(tr, "hovertemplate", None):
            continue
        cls = type(tr).__name__
        if cls == "Pie":
            tr.hovertemplate = (
                "%{label}: %{value:" + value_fmt + "}<extra></extra>"
            )
            continue
        name = getattr(tr, "name", None)
        prefix = f"<b>{name}</b><br>" if name else ""
        if cls == "Bar" and getattr(tr, "orientation", None) == "h":
            tr.hovertemplate = (
                f"{prefix}%{{y}}<br>%{{x:{value_fmt}}}<extra></extra>"
            )
        else:
            tr.hovertemplate = (
                f"{prefix}{x_expr}<br>%{{y:{value_fmt}}}<extra></extra>"
            )
    return fig
