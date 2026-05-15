"""Shared sortable HTML table for the WC2026 surfaces.

Streamlit views can't import each other (they're only referenced by
path in st.Page), so the table the WC2026 *All Channels* page uses
lived inline in that view. The WC2026 *Trends* page needed the same
look + sort behaviour, so the component is extracted here and BOTH
views render through it — one implementation, one place to change.

Columns are described by `(label, type, align[, sortable])`:
  - type:  "num" | "str"  (controls sort comparator)
  - align: "left" | "right"
  - sortable: optional bool, default True. A non-sortable column
    renders a plain header (no click, no arrow) — used e.g. for the
    Trends "Long / Short / Live" composite column.

The sort JS keys off each header's `data-col` (its true cell index),
so non-sortable columns can sit anywhere between sortable ones without
breaking the column→cell mapping.
"""
from __future__ import annotations

from src import theme as _T


def td(val_sort, content, *, align: str = "right") -> str:
    """One <td>. `data-val` carries the raw sort key (number or
    lowercased string) so the JS sorts on real values, not the
    formatted display text."""
    v = "" if val_sort is None else str(val_sort)
    return f"<td style='text-align:{align}' data-val=\"{v}\">{content}</td>"


_TABLE_CSS = (
    "<style>"
    f"body{{margin:0;background:{_T.BG};color:{_T.TEXT};"
    "font-family:'Source Sans Pro',sans-serif}"
    ".wc-wrap{overflow-x:auto;width:100%}"
    ".wc-tbl{width:100%;border-collapse:collapse;border:0;font-size:14px;"
    f"color:{_T.TEXT};background:transparent}}"
    ".wc-tbl th,.wc-tbl td{border-left:0;border-right:0;border-top:0;"
    "padding:6px 12px;white-space:nowrap}"
    f".wc-tbl th{{user-select:none;font-weight:600;color:{_T.TEXT};border-bottom:0}}"
    f".wc-tbl th[data-col]{{cursor:pointer}}"
    f".wc-tbl th[data-col]:hover{{color:{_T.ACCENT}}}"
    f".wc-tbl th.active{{color:{_T.ACCENT}}}"
    f".wc-tbl td{{border-bottom:1px solid {_T.BORDER}}}"
    f".wc-tbl tr:hover td{{background:{_T.SURFACE}}}"
    f".wc-tbl a{{color:{_T.TEXT};text-decoration:none}}"
    ".wc-tbl a:hover{text-decoration:underline}"
    "</style>"
)


def _unpack(col):
    """(label, type, align[, sortable]) → 4-tuple with sortable default True."""
    lbl, tp, al = col[0], col[1], col[2]
    sortable = col[3] if len(col) > 3 else True
    return lbl, tp, al, sortable


def render_sortable_table(
    cols: list[tuple],
    rows_html: list[str],
    table_id: str,
    *,
    default_col: int = 0,
    default_asc: bool = False,
) -> str:
    """Return the full CSS + <table> + sort-JS string for components.html.

    `default_col` is a *cell index* (must point at a sortable column).
    The first paint is sorted client-side on that column.
    """
    th_parts = []
    for i, col in enumerate(cols):
        lbl, tp, al, sortable = _unpack(col)
        if sortable:
            th_parts.append(
                f"<th data-col='{i}' data-type='{tp}' "
                f"style='text-align:{al}'>{lbl}</th>"
            )
        else:
            th_parts.append(
                f"<th style='text-align:{al}'>{lbl}</th>"
            )
    th_html = "".join(th_parts)
    return (
        _TABLE_CSS
        + f"<div class='wc-wrap'><table class='wc-tbl' id='{table_id}'>"
        f"<thead><tr style='border-bottom:2px solid {_T.BORDER_STRONG}'>"
        + th_html +
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table></div>"
        "<script>(function(){"
        f"const t=document.getElementById('{table_id}');"
        "const tb=t.querySelector('tbody');"
        "const hs=Array.from(t.querySelectorAll('th[data-col]'));"
        f"let cur={int(default_col)},asc={'true' if default_asc else 'false'};"
        "function thFor(ci){return hs.find(h=>+h.dataset.col===ci);}"
        "function refresh(){"
            "const h=thFor(cur);if(!h)return;"
            "const isStr=h.dataset.type==='str';"
            "const rows=Array.from(tb.rows);"
            "rows.sort((a,b)=>{"
                "const va=a.cells[cur].dataset.val||'';"
                "const vb=b.cells[cur].dataset.val||'';"
                "let c=isStr?va.localeCompare(vb,undefined,{sensitivity:'base'})"
                         ":(parseFloat(va)||0)-(parseFloat(vb)||0);"
                "return asc?c:-c;"
            "});"
            "rows.forEach(r=>tb.appendChild(r));"
            "hs.forEach(x=>{x.classList.remove('active');"
            "x.textContent=x.textContent.replace(/ [▲▼]/g,'');});"
            "h.classList.add('active');"
            "h.textContent+=asc?' ▲':' ▼';"
        "}"
        "hs.forEach(h=>{h.addEventListener('click',()=>{"
            "const ci=+h.dataset.col;"
            "if(ci===cur)asc=!asc;else{cur=ci;asc=h.dataset.type==='str';}"
            "refresh();"
        "});});"
        "refresh();"
        "})();</script>"
    )
