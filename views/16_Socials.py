"""Socials — clubs' presence across other social platforms.

Respects the global league/club filter. One row per club, with a strip of
clickable platform badges built from the channels.socials JSONB column
(populated by scripts/backfill_socials.py from Wikidata).
"""
from __future__ import annotations

import os

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

from src.database import Database
from src.analytics import fmt_num
from src.auth import require_login
from src.filters import (
    get_global_filter, get_global_channels, get_channels_for_filter,
    get_global_color_map, get_global_color_map_dual,
    render_page_subtitle,
)
from src.dot import dual_dot, channel_badge

load_dotenv()
require_login()

st.title("Other Social")
render_page_subtitle(
    "Where each club is active beyond YouTube — "
    "Instagram, X, Facebook, TikTok and more, plus follower counts. "
    "Click any badge to open the platform in a new tab."
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Set SUPABASE_URL and SUPABASE_KEY.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
all_channels = get_global_channels() or db.get_all_channels()

# ── Apply global filter ──────────────────────────────────────
# Top-5 European leagues — used to include the 5 league channels themselves
# alongside their clubs (Serie A, PL, La Liga, Bundesliga, Ligue 1).
from src.channels import COUNTRY_TO_LEAGUE
_TOP5 = {"Serie A", "Premier League", "La Liga", "Bundesliga", "Ligue 1"}

def _is_top5_league_channel(c: dict) -> bool:
    if c.get("entity_type") != "League":
        return False
    return COUNTRY_TO_LEAGUE.get((c.get("country") or "").upper()) in _TOP5

g_league, g_club = get_global_filter()
if g_club:
    rows = [g_club]
elif g_league:
    rows = [c for c in get_channels_for_filter(all_channels, g_league)
            if c.get("entity_type") == "Club" or _is_top5_league_channel(c)]
else:
    rows = [c for c in all_channels
            if c.get("entity_type") == "Club" or _is_top5_league_channel(c)]

# Sort by subscribers desc
rows.sort(key=lambda c: int(c.get("subscriber_count") or 0), reverse=True)

if not rows:
    st.info("No clubs match the current filter.")
    st.stop()

color_map = get_global_color_map() or {}
dual = get_global_color_map_dual() or {}

# ── Platform metadata ────────────────────────────────────────
# (key, label, brand color, text color) — matches keys written by
# scripts/backfill_socials.py.
PLATFORMS = [
    ("website",   "🌐", "#444444", "#FFFFFF"),
    ("instagram", "IG", "#E1306C", "#FFFFFF"),
    ("x",         "X",  "#000000", "#FFFFFF"),
    ("facebook",  "FB", "#1877F2", "#FFFFFF"),
    ("tiktok",    "TT", "#010101", "#FFFFFF"),
    # NB: YouTube is intentionally not rendered as a badge — the club name
    # is already a clickable link to the YT channel.
    ("linkedin",  "LI", "#0A66C2", "#FFFFFF"),
    ("threads",   "Th", "#000000", "#FFFFFF"),
    ("telegram",  "Tg", "#26A5E4", "#FFFFFF"),
    ("whatsapp",  "WA", "#25D366", "#FFFFFF"),
    ("snapchat",  "SC", "#FFFC00", "#000000"),
    ("twitch",    "Tw", "#9146FF", "#FFFFFF"),
    ("vk",        "VK", "#0077FF", "#FFFFFF"),
    ("weibo",     "Wb", "#E6162D", "#FFFFFF"),
    ("mastodon",  "Ms", "#6364FF", "#FFFFFF"),
    ("music",     "🎵", "#1DB954", "#FFFFFF"),
]


def _badges(socials: dict) -> str:
    """Render one badge per platform. When a platform has regional accounts
    (e.g. x_regional list), append a small superscript with the count and a
    tooltip listing every handle.
    """
    if not socials:
        return '<span style="color:#666">—</span>'
    out = ""
    for key, label, bg, fg in PLATFORMS:
        url = socials.get(key)
        if not url:
            continue
        u = (url or "").replace('"', "&quot;")
        regional = socials.get(f"{key}_regional") or []
        if not isinstance(regional, list):
            regional = []
        n_accounts = 1 + len(regional)

        # Build tooltip: each handle on its own line. &#10; → newline in
        # most browsers' native tooltips.
        def _handle(u_str):
            u_str = (u_str or "").rstrip("/")
            return u_str.split("/")[-1].lstrip("@") if u_str else ""
        if n_accounts > 1:
            lines = [f"{key}: {n_accounts} accounts",
                     f"@{_handle(url)} (main)"]
            for r_url in regional:
                lines.append(f"@{_handle(r_url)}")
            title = "&#10;".join(lines).replace('"', "&quot;")
        else:
            title = f"{key}: {u}"

        suffix = (f'<sup style="font-size:8px;margin-left:2px;opacity:0.85">'
                  f'+{len(regional)}</sup>') if n_accounts > 1 else ""
        out += (
            f'<a href="{u}" target="_blank" rel="noopener" '
            f'title="{title}" '
            f'style="display:inline-flex;align-items:center;justify-content:center;'
            f'min-width:26px;height:22px;padding:0 7px;margin-right:4px;'
            f'border-radius:4px;background:{bg};color:{fg};font-size:11px;'
            f'font-weight:600;text-decoration:none;font-family:monospace">'
            f'{label}{suffix}</a>'
        )
    return out or '<span style="color:#666">—</span>'


# ── Build table ──────────────────────────────────────────────
rows_html = ""
total_with_socials = 0
total_links = 0
for i, c in enumerate(rows, 1):
    name = c.get("name", "?")
    handle = c.get("handle", "") or ""
    yt_url = f"https://www.youtube.com/{handle}" if handle else ""
    dot = channel_badge(c, color_map, dual, 14)
    socials = c.get("socials") or {}
    if socials:
        total_with_socials += 1
        total_links += sum(1 for k, *_ in PLATFORMS if socials.get(k))
    name_html = (
        f'<a href="{yt_url}" target="_blank" rel="noopener" '
        f'style="color:#FAFAFA;text-decoration:none;border-bottom:1px dotted #555">'
        f'<b>{name}</b></a>'
    ) if yt_url else f"<b>{name}</b>"
    rows_html += f"""<tr>
        <td style="padding:8px 12px;text-align:right;color:#888">{i}</td>
        <td style="padding:8px 12px">{dot}</td>
        <td style="padding:8px 12px;white-space:nowrap">{name_html}</td>
        <td style="padding:8px 12px">{_badges(socials)}</td>
    </tr>"""

st.caption(
    f"**{total_with_socials}/{len(rows)}** clubs with at least one platform link · "
    f"{total_links} total links. Click any badge to open that account in a new tab; "
    "click a club name to open their YouTube channel."
)

components.html(f"""
<style>
  .so {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .so th {{ padding:8px 12px; user-select:none; border-bottom:2px solid #444;
            text-align:left; color:#aaa; font-weight:600; }}
  .so td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
  .so tr:hover td {{ background:#1a1c24; }}
</style>
<div style="overflow-x:auto;width:100%">
<table class="so">
<thead><tr>
  <th style="text-align:right">#</th>
  <th></th>
  <th>Club</th>
  <th>Other platforms</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>
""", height=len(rows) * 46 + 70, scrolling=False)

# ── Single-club: full account list ──────────────────────────────────
# When viewing one club, surface every individual account (main +
# regionals across every platform) with handle, URL and current
# follower count where we have it. The badge strip above is compact;
# this section is the exhaustive view.
if g_club:
    _ch_id = g_club["id"]
    _snaps_one = db.get_latest_follower_snapshots_bulk([_ch_id]).get(_ch_id, {})
    _socials_one = g_club.get("socials") or {}

    # Build (platform_label, [(handle, url, count_or_None, lang_tag)])
    _PLATFORM_LABELS = {k: lbl for k, lbl, *_ in PLATFORMS}

    def _h(u: str) -> str:
        u = (u or "").rstrip("/")
        return u.split("/")[-1].lstrip("@") if u else ""

    def _count_for(snap_key: str) -> int | None:
        row = _snaps_one.get(snap_key)
        if not row:
            return None
        v = row.get("follower_count")
        return int(v) if v else None

    # YouTube first (always present)
    _list_html = ""
    yt_handle = g_club.get("handle") or ""
    yt_url = f"https://www.youtube.com/{yt_handle}" if yt_handle else ""
    yt_subs = int(g_club.get("subscriber_count") or 0)
    if yt_url:
        _list_html += (
            '<div style="margin:0 0 14px 0">'
            '<div style="font-weight:600;color:#aaa;font-size:12px;'
            'margin-bottom:4px;letter-spacing:0.04em">YOUTUBE</div>'
            f'<div style="padding:6px 0">'
            f'<a href="{yt_url}" target="_blank" rel="noopener" '
            f'style="color:#FAFAFA;text-decoration:none">'
            f'<span style="color:#FF0000;font-weight:700;margin-right:8px">YT</span>'
            f'<b>{yt_handle or "—"}</b></a>'
            f'<span style="float:right;color:#aaa">{fmt_num(yt_subs)}</span>'
            '</div></div>'
        )

    for key, label, bg, fg in PLATFORMS:
        main_url = _socials_one.get(key)
        regional = _socials_one.get(f"{key}_regional") or []
        if not main_url and not regional:
            continue

        # Helper: extract handle from a snapshot's source field, but ONLY
        # when source looks like a URL. Old scrapes stored "chrome" as a
        # method tag, not a URL — those rows have no handle to extract.
        def _src_handle_url(sr):
            s = (sr.get("source") or "").strip().rstrip("/")
            if not s or "/" not in s:
                return ""  # e.g. "chrome", "manual" — not a URL
            return s.split("/")[-1].lstrip("@").lower()

        # Index regional snapshots by handle (from URL-shaped source)
        # AND by lang tag (from platform key) so we can match either way.
        snap_by_handle = {}
        snap_by_lang = {}
        for snap_key, snap_row in _snaps_one.items():
            if not snap_key.startswith(f"{key}_"):
                continue
            lang = snap_key[len(key)+1:]
            snap_by_lang[lang] = (snap_key, snap_row)
            h = _src_handle_url(snap_row)
            if h:
                snap_by_handle[h] = (snap_key, snap_row)

        accounts = []
        seen = set()
        if main_url:
            h = _h(main_url)
            cnt = _count_for(key)
            accounts.append((h, main_url, cnt, "main"))
            seen.add(h.lower())
        for r_url in regional:
            handle = _h(r_url)
            if handle.lower() in seen:
                continue
            seen.add(handle.lower())
            match = snap_by_handle.get(handle.lower())
            if match:
                sk, sr = match
                cnt = int(sr["follower_count"]) if sr.get("follower_count") else None
                lang = sk[len(key)+1:]
            else:
                cnt, lang = None, "regional"
            accounts.append((handle, r_url, cnt, lang))

        items = ""
        for handle, url, cnt, lang in accounts:
            tag = ("main" if lang == "main" else lang.upper())
            cnt_html = (f'<span style="float:right;color:#aaa">'
                        f'{fmt_num(cnt)}</span>') if cnt else (
                       '<span style="float:right;color:#666">—</span>')
            items += (
                f'<div style="padding:6px 0;border-bottom:1px solid #20222a">'
                f'<a href="{url}" target="_blank" rel="noopener" '
                f'style="color:#FAFAFA;text-decoration:none">'
                f'<span style="display:inline-block;min-width:26px;'
                f'text-align:center;padding:1px 6px;border-radius:3px;'
                f'background:{bg};color:{fg};font-size:10px;font-weight:700;'
                f'margin-right:8px">{label}</span>'
                f'<b>@{handle}</b>'
                f'<span style="color:#666;font-size:11px;margin-left:8px">'
                f'{tag}</span></a>{cnt_html}</div>'
            )
        _list_html += (
            f'<div style="margin:0 0 14px 0">'
            f'<div style="font-weight:600;color:#aaa;font-size:12px;'
            f'margin-bottom:4px;letter-spacing:0.04em">'
            f'{key.upper()}'
            f'{(" · " + str(len(accounts)) + " accounts") if len(accounts) > 1 else ""}'
            f'</div>{items}</div>'
        )

    st.markdown("##### All accounts")
    st.caption("Every individual handle on file for this club, "
               "across every platform. Click any to open.")
    if _list_html:
        st.markdown(
            f'<div style="font-family:\'Source Sans Pro\',sans-serif;'
            f'color:#FAFAFA;font-size:14px">{_list_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("No accounts on file for this club yet.")

# ── Followers leaderboard ──────────────────────────────────────────
st.markdown("---")
st.subheader("Followers across platforms")
st.caption(
    "Latest follower count per platform — YouTube updates daily, the rest "
    "are point-in-time snapshots. Click any column header to re-sort. "
    "**?** = account exists, no follower count yet. **—** = club isn't on this platform."
)

# Platforms to include as columns (skip non-follower-bearing ones)
_FCOLS = [
    ("youtube",   "YT", "#FF0000", "#FFFFFF"),
    ("instagram", "IG", "#E1306C", "#FFFFFF"),
    ("x",         "X",  "#000000", "#FFFFFF"),
    ("facebook",  "FB", "#1877F2", "#FFFFFF"),
    ("tiktok",    "TT", "#010101", "#FFFFFF"),
    ("linkedin",  "LI", "#0A66C2", "#FFFFFF"),
    ("threads",   "Th", "#000000", "#FFFFFF"),
    ("telegram",  "Tg", "#26A5E4", "#FFFFFF"),
    ("whatsapp",  "WA", "#25D366", "#FFFFFF"),
    ("snapchat",  "SC", "#FFFC00", "#000000"),
    ("twitch",    "Tw", "#9146FF", "#FFFFFF"),
    ("weibo",     "Wb", "#E6162D", "#FFFFFF"),
    ("vk",        "VK", "#0077FF", "#FFFFFF"),
]

_ch_ids = [c["id"] for c in rows]
_followers_map = db.get_latest_follower_snapshots_bulk(_ch_ids) if _ch_ids else {}

_lb_rows_html = ""
for i, c in enumerate(rows, 1):
    name = c.get("name", "?")
    handle = c.get("handle", "") or ""
    yt_url = f"https://www.youtube.com/{handle}" if handle else ""
    dot = channel_badge(c, color_map, dual, 14)
    name_html = (
        f'<a href="{yt_url}" target="_blank" rel="noopener" '
        f'style="color:#FAFAFA;text-decoration:none;border-bottom:1px dotted #555">'
        f'<b>{name}</b></a>'
    ) if yt_url else f"<b>{name}</b>"

    # Build the per-platform cells + accumulate total
    # Three states:
    #   has count           → number (aggregate of main + regional accounts)
    #   URL on file, no count → "?" (we know they're there, no measurement yet)
    #   not on platform      → "—"
    # Family aggregation: any snapshot row with platform key '<base>' OR
    # '<base>_<lang>' counts toward the same column. The cell shows the
    # aggregate; the title attribute lists every account (handle + count)
    # so hovering surfaces the breakdown without breaking column alignment.
    plat_cells = ""
    yt_subs = int(c.get("subscriber_count") or 0)
    snaps = _followers_map.get(c["id"], {})
    socials = c.get("socials") or {}
    total = 0

    def _handle_from_url(u: str) -> str:
        u = (u or "").rstrip("/")
        if not u:
            return ""
        # Trim /add, /company/, etc. — keep the last meaningful path segment
        seg = u.split("/")[-1]
        return seg.lstrip("@")

    for key, _label, _bg, _fg in _FCOLS:
        if key == "youtube":
            n = yt_subs
            n_acc = 1
            has_url = True  # YouTube is the channel itself
            breakdown = []
        else:
            # Sum main + regional siblings under this platform key
            n = 0
            n_acc = 0
            breakdown = []
            for snap_key, snap_row in snaps.items():
                if snap_key == key or snap_key.startswith(f"{key}_"):
                    cnt = int(snap_row.get("follower_count") or 0)
                    if cnt > 0:
                        n += cnt
                        n_acc += 1
                        url = snap_row.get("source_url") or ""
                        h = _handle_from_url(url)
                        lang = "main" if snap_key == key else snap_key[len(key)+1:]
                        breakdown.append((cnt, h, lang))
            # has_url accounts for both main URL and regional URL list
            has_url = (key in socials
                       or bool(socials.get(f"{key}_regional")))
        total += n
        if n > 0:
            # Build a multi-line tooltip listing every account when n_acc > 1.
            # &#10; renders as a newline in most browsers' native tooltips.
            tip = ""
            if n_acc > 1:
                breakdown.sort(key=lambda r: -r[0])
                lines = [f"{n_acc} accounts ({fmt_num(n)} total):"]
                for cnt, h, lang in breakdown:
                    if lang == "main":
                        lines.append(f"@{h} (main): {fmt_num(cnt)}")
                    else:
                        lines.append(f"@{h} ({lang}): {fmt_num(cnt)}")
                title_text = "&#10;".join(lines).replace('"', '&quot;')
                tip = f' title="{title_text}"'
            # Account-count marker placed BEFORE the number (left side) and
            # right-aligned overall, so right edges of numbers stay flush
            # across the column whether the cell has a marker or not.
            mark = ""
            if n_acc > 1:
                mark = (f'<span style="font-size:9px;color:#666;'
                        f'opacity:0.85;margin-right:4px">+{n_acc - 1}</span>')
            plat_cells += (f'<td style="padding:6px 12px;text-align:right;'
                           f'white-space:nowrap"{tip} data-val="{n}">'
                           f'{mark}{fmt_num(n)}</td>')
        elif has_url:
            plat_cells += ('<td style="padding:6px 12px;text-align:right;color:#aaa" '
                           'data-val="-1" title="Account exists, no count yet">?</td>')
        else:
            plat_cells += ('<td style="padding:6px 12px;text-align:right;color:#444" '
                           'data-val="-2" title="Not on this platform">—</td>')

    total_cell = (f'<td style="padding:6px 12px;text-align:right;font-weight:600" '
                  f'data-val="{total}">{fmt_num(total)}</td>')

    _lb_rows_html += f"""<tr>
        <td style="padding:6px 12px;text-align:right;color:#888" data-val="{i}">{i}</td>
        <td style="padding:6px 12px">{dot}</td>
        <td style="padding:6px 12px;white-space:nowrap" data-val="{name}">{name_html}</td>
        {total_cell}
        {plat_cells}
    </tr>"""

# Header row — col 0=# / col 2=Club / col 3=Total / cols 4+ = platforms
_thead_extra = ""
for idx, (key, label, bg, fg) in enumerate(_FCOLS, start=4):
    title = key.capitalize()
    _thead_extra += (
        f'<th data-col="{idx}" data-type="num" '
        f'style="text-align:right;cursor:pointer" '
        f'title="{title}">'
        f'<span style="display:inline-block;min-width:22px;height:18px;'
        f'padding:0 5px;border-radius:3px;background:{bg};color:{fg};'
        f'font-size:10px;font-weight:600;line-height:18px">{label}</span></th>'
    )

_lb_h = max(len(rows), 1) * 38 + 80
components.html(f"""
<style>
  .fl {{ width:100%; border-collapse:collapse; font-size:13px; color:#FAFAFA;
         font-family:"Source Sans Pro",sans-serif; }}
  .fl th {{ padding:6px 12px; user-select:none; border-bottom:2px solid #444;
            text-align:right; color:#aaa; font-weight:600; }}
  .fl th[data-col] {{ cursor:pointer; }}
  .fl th[data-col]:hover {{ color:#636EFA; }}
  .fl td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
  .fl tr:hover td {{ background:#1a1c24; }}
  .fl .active {{ color:#636EFA; }}
</style>
<div style="overflow-x:auto;width:100%">
<table class="fl">
<thead><tr>
  <th data-col="0" data-type="num" style="text-align:right">#</th>
  <th></th>
  <th data-col="2" data-type="str" style="text-align:left">Club</th>
  <th data-col="3" data-type="num" style="text-align:right;color:#FAFAFA">Total</th>
  {_thead_extra}
</tr></thead>
<tbody>{_lb_rows_html}</tbody>
</table>
</div>
<script>
(function() {{
  const table = document.querySelector('.fl');
  const tbody = table.querySelector('tbody');
  const headers = table.querySelectorAll('th[data-col]');
  let currentCol = -1, currentAsc = false;  // overridden by initial sort() below
  function sort(colIdx, type) {{
    const trs = Array.from(tbody.rows);
    const isStr = type === 'str';
    if (colIdx === currentCol) currentAsc = !currentAsc;
    else {{ currentCol = colIdx; currentAsc = isStr; }}
    trs.sort((a, b) => {{
      const va = a.cells[colIdx].dataset.val || '';
      const vb = b.cells[colIdx].dataset.val || '';
      let cmp = isStr ? va.localeCompare(vb) : ((parseFloat(va)||0) - (parseFloat(vb)||0));
      return currentAsc ? cmp : -cmp;
    }});
    trs.forEach(r => tbody.appendChild(r));
    headers.forEach(h => h.classList.remove('active'));
    table.querySelector('th[data-col="' + colIdx + '"]').classList.add('active');
  }}
  headers.forEach(h => h.addEventListener('click',
    () => sort(parseInt(h.dataset.col), h.dataset.type || 'num')));
  // Initial sort: YouTube desc
  sort(3, 'num');
  function _fit() {{
    const h = document.documentElement.scrollHeight + 8;
    window.parent.postMessage({{type:'streamlit:setFrameHeight', height: h}}, '*');
  }}
  new ResizeObserver(_fit).observe(document.body);
  _fit();
}})();
</script>
""", height=_lb_h, scrolling=False)

st.caption(
    "Counts are best-effort: some platforms abbreviate (\"1.2M\"), some hide "
    "the number from logged-out viewers, and a few clubs run multiple "
    "regional accounts. Treat these as a snapshot, not a precise count."
)


# ── Top X regional families ─────────────────────────────────────────
# Surfaces clubs running multiple X accounts (main + language-specific
# regionals). Built from follower_snapshots rows with platform 'x' or
# 'x_<lang>'. Only clubs with ≥2 X accounts appear.
st.markdown("---")
st.subheader("🌍 Top X regional families")
st.caption(
    "Clubs running a main X account plus regional language siblings. "
    "Most of European football's multi-region energy lives on X — "
    "Instagram and TikTok are single-account-per-club by convention."
)

_x_families = []
for c in rows:
    snaps = _followers_map.get(c["id"], {})
    accounts = []
    for snap_key, snap_row in snaps.items():
        if snap_key == "x" or snap_key.startswith("x_"):
            cnt = int(snap_row.get("follower_count") or 0)
            if cnt > 0:
                lang = "main" if snap_key == "x" else snap_key[2:]
                url = snap_row.get("source_url") or ""
                handle = (url.rstrip("/").split("/")[-1] if url else "").lstrip("@")
                accounts.append({"lang": lang, "handle": handle, "count": cnt, "url": url})
    if len(accounts) >= 2:
        accounts.sort(key=lambda a: -a["count"])
        family_total = sum(a["count"] for a in accounts)
        main_count = next((a["count"] for a in accounts if a["lang"] == "main"), 0)
        regional_sum = family_total - main_count
        _x_families.append({
            "ch": c, "accounts": accounts,
            "family": family_total, "main": main_count,
            "regional_sum": regional_sum,
            "n_acc": len(accounts),
        })

_x_families.sort(key=lambda f: -f["family"])

if not _x_families:
    st.info("No clubs in the current filter have multiple X accounts.")
else:
    _fam_rows = ""
    for i, f in enumerate(_x_families, 1):
        c = f["ch"]
        cdot = channel_badge(c, color_map, dual, 14)
        cname = c.get("name", "?")
        # Build the breakdown chips: one small pill per account, brand-X-styled
        chips = ""
        for a in f["accounts"]:
            lang_label = "main" if a["lang"] == "main" else a["lang"].upper()
            chips += (
                f'<a href="{a["url"]}" target="_blank" rel="noopener" '
                f'title="@{a["handle"]} ({a["lang"]}) — {fmt_num(a["count"])}" '
                f'style="display:inline-flex;align-items:center;gap:4px;'
                f'padding:3px 8px;margin:2px 4px 2px 0;border-radius:12px;'
                f'background:#1a1c24;border:1px solid #2c2f38;'
                f'color:#FAFAFA;text-decoration:none;font-size:11px;'
                f'white-space:nowrap">'
                f'<span style="font-weight:700;color:#aaa">{lang_label}</span>'
                f'<span>{fmt_num(a["count"])}</span></a>'
            )
        _fam_rows += f"""<tr>
            <td style="padding:8px 12px;text-align:right;color:#888">{i}</td>
            <td style="padding:8px 12px">{cdot}</td>
            <td style="padding:8px 12px;white-space:nowrap;font-weight:600">{cname}</td>
            <td style="padding:8px 12px;text-align:right;font-weight:600;color:#FAFAFA">{fmt_num(f['family'])}</td>
            <td style="padding:8px 12px;text-align:right;color:#888">{fmt_num(f['main'])}</td>
            <td style="padding:8px 12px;text-align:right;color:#00CC96">+{fmt_num(f['regional_sum'])}</td>
            <td style="padding:8px 12px;text-align:right;color:#888">{f['n_acc']}</td>
            <td style="padding:8px 12px">{chips}</td>
        </tr>"""

    components.html(f"""
    <style>
      .xf {{ width:100%; border-collapse:collapse; font-size:13px;
             color:#FAFAFA; font-family:"Source Sans Pro",sans-serif; }}
      .xf th {{ padding:8px 12px; border-bottom:2px solid #444;
                color:#aaa; font-weight:600; text-align:right; }}
      .xf th.l {{ text-align:left; }}
      .xf td {{ border-bottom:1px solid #262730; vertical-align:middle; }}
      .xf tr:hover td {{ background:#1a1c24; }}
    </style>
    <table class="xf">
      <thead><tr>
        <th>#</th><th></th>
        <th class="l">Club / League</th>
        <th>Family total</th>
        <th>Main</th>
        <th>+Regional</th>
        <th>Accounts</th>
        <th class="l">Breakdown</th>
      </tr></thead>
      <tbody>{_fam_rows}</tbody>
    </table>
    """, height=len(_x_families) * 56 + 90, scrolling=True)
