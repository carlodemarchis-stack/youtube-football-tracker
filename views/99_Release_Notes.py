"""Release Notes — public changelog of user-facing changes.

Reads RELEASES.json (repo root) via src.releases. Public page (no login
gate) so anyone can see what's shipped. Linked from the Home footer and
the sidebar version badge.
"""
import streamlit as st
from src import releases as _rel

st.title("📋 Release Notes")

_d = _rel.load()
_ver = _rel.current_version()
st.caption(f"Current version · **v{_ver}**  ·  YouTube Football Tracker")
st.markdown(
    "<span style='color:#9aa0ab;font-size:14px'>What's changed, newest "
    "first. Only user-facing updates are listed.</span>",
    unsafe_allow_html=True,
)
st.markdown("---")

_releases = _d.get("releases") or []
if not _releases:
    st.info("No release notes yet.")
else:
    for r in _releases:
        _v = r.get("version", "")
        _date = r.get("date", "")
        st.markdown(
            f"<div style='margin:2px 0 4px 0'>"
            f"<span style='font-size:18px;font-weight:700;color:#FAFAFA'>v{_v}</span>"
            f"<span style='color:#6b7280;font-size:13px;margin-left:10px'>{_date}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        for _n in r.get("notes", []):
            st.markdown(f"- {_n}")
        st.markdown(
            "<div style='border-bottom:1px solid #2a2c34;margin:10px 0 14px 0'></div>",
            unsafe_allow_html=True,
        )
