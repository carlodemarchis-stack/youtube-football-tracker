from __future__ import annotations

import os
import traceback

import streamlit as st
import pandas as pd
from dotenv import load_dotenv

import json as _json
import anthropic

from src.auth import require_admin, show_auth_sidebar
from src.youtube_api import YouTubeClient
from src.database import Database
from src.analytics import classify_videos, fmt_num, fmt_date
from src.filters import get_global_filter, get_channels_for_filter

load_dotenv()

require_admin()

st.title("Data")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")

if not YOUTUBE_API_KEY:
    st.error("Set YOUTUBE_API_KEY in your .env file.")
    st.stop()

db = Database(SUPABASE_URL, SUPABASE_KEY)
yt = YouTubeClient(YOUTUBE_API_KEY)

# ── All channels + state (needed for AI command + selectors) ──
all_channels = db.get_active_channels()

if not all_channels:
    st.warning("No channels in the database. Go to **Channel Management** to add some.")
    st.stop()


# ══════════════════════════════════════════════════════════════
# AI COMMAND BAR
# ══════════════════════════════════════════════════════════════

def _build_channel_context(channels):
    lines = []
    for ch in channels:
        fetched = fmt_date(ch.get("last_fetched"))
        ins = db.get_insights(ch["id"])
        ai = fmt_date(ins.get("generated_at", "") if ins else "")
        lines.append(f"- {ch['name']} | type:{ch.get('entity_type','Club')} | country:{ch.get('country','')} | data:{fetched} | ai:{ai}")
    return "\n".join(lines)


def _run_ai_command(prompt: str, channels: list[dict]) -> dict | None:
    try:
        api_key = st.secrets["app"]["anthropic_api_key"]
    except Exception:
        st.error("Set anthropic_api_key in .streamlit/secrets.toml under [app].")
        return None

    context = _build_channel_context(channels)

    # Include current dropdown selection for context
    current_sel = st.session_state.get("_w_channels", [])
    sel_note = f"\n\nCurrently selected in dropdown: {', '.join(current_sel) if current_sel else 'none'}"

    system = f"""You are an assistant for a YouTube channel data management tool.
The user will give you a natural language instruction about fetching data or generating AI analysis.

Available channels:
{context}
{sel_note}

Today's date: {pd.Timestamp.now().strftime('%Y-%m-%d')}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "action": "fetch" | "ai" | "both" | "select" | "info",
  "mode": "smart" | "full" | "quick_stats",
  "channels": ["channel name 1", "channel name 2", ...],
  "top_n": 100,
  "message": "brief explanation of what you're doing"
}}

Action types:
- "select": just fill the dropdown with matching channels, no confirmation needed. Use this when the user is filtering/selecting channels by a condition (e.g. "channels with no data", "clubs with stale AI", "all Serie A clubs").
- "fetch" / "ai" / "both": the user explicitly asks to run a fetch or AI operation. These require confirmation.
- "info": just answer a question, no channels to select.

Default to "select" unless the user explicitly says to run/fetch/generate/execute something.

Rules:
- "channels with no data" means data == "never"
- "stale AI" means data date > ai date, or ai == "never"
- "all channels" means every channel listed
- Default mode is "smart" unless specified
- Default top_n is 100 unless specified
- For "info" action, just explain the state in "message" and set channels to []
- Always use exact channel names from the list
- When the user refers to "those", "them", "these", or "the selected ones", they mean the channels currently selected in the dropdown
- When the user says "select 5 of those", pick 5 from the currently selected channels, not from all channels
- NEVER allow destructive actions: no deleting channels, no deleting data, no dropping tables, no removing insights, no resetting anything. If the user asks for a destructive action, respond with action "info" and explain that destructive operations must be done manually from Channel Management"""

    # Build conversation history
    if "_ai_cmd_history" not in st.session_state:
        st.session_state["_ai_cmd_history"] = []

    history = st.session_state["_ai_cmd_history"]
    messages = list(history) + [{"role": "user", "content": prompt}]

    import time
    client = anthropic.Anthropic(api_key=api_key)
    for attempt in range(3):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                system=system,
                messages=messages,
            )
            break
        except anthropic.APIStatusError as e:
            if getattr(e, "status_code", None) in (429, 529) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            st.error(f"Claude API error: {e}")
            return None
        except anthropic.APIError as e:
            st.error(f"API error: {e}")
            return None

    response_text = resp.content[0].text.strip()

    # Save to conversation history
    st.session_state["_ai_cmd_history"] = messages + [{"role": "assistant", "content": response_text}]

    try:
        text = response_text
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return _json.loads(text)
    except Exception:
        st.error(f"Could not parse AI response: {response_text}")
        return None


st.markdown("**AI Command**")
ai_cmd = st.text_input(
    "Ask me what to do...",
    placeholder="e.g. fetch all channels with no data, run AI for stale channels, what needs updating?",
    label_visibility="collapsed",
    key="_ai_cmd_input",
)

if ai_cmd and ai_cmd != st.session_state.get("_ai_cmd_last_input"):
    st.session_state["_ai_cmd_last_input"] = ai_cmd
    with st.spinner("Thinking..."):
        result = _run_ai_command(ai_cmd, all_channels)
    if result:
        action = result.get("action", "info")
        if action == "select":
            # Just fill the dropdown, no confirmation
            st.session_state["_ai_cmd_channels"] = result.get("channels", [])
            if result.get("mode"):
                st.session_state["_ai_cmd_mode"] = result["mode"]
            if result.get("top_n") and result["top_n"] != 100:
                st.session_state["_ai_cmd_top_n"] = result["top_n"]
            st.toast(result.get("message", "Channels selected."))
            st.rerun()
        elif action == "info" or not result.get("channels"):
            st.info(result.get("message", ""))
        else:
            st.session_state["_ai_cmd_pending"] = result
            st.rerun()

# Show pending AI command for confirmation (only for fetch/ai/both)
pending = st.session_state.get("_ai_cmd_pending")
if pending:
    mode_labels = {"smart": "Smart", "full": "Full", "quick_stats": "Quick Stats"}
    action_labels = {"fetch": "Data Fetch", "ai": "AI Analysis", "both": "Data Fetch + AI Analysis"}
    msg = pending.get("message", "")
    action_label = action_labels.get(pending.get("action", "fetch"), pending.get("action"))
    mode_label = mode_labels.get(pending.get("mode", "smart"), pending.get("mode"))
    ch_list = ", ".join(pending.get("channels", []))

    st.info(f"{msg}\n\n**Action:** {action_label} · **Mode:** {mode_label} · **Channels:** {ch_list}")

    col_yes, col_no, _ = st.columns([1, 1, 6])
    if col_yes.button("Apply", type="primary"):
        st.session_state["_ai_cmd_channels"] = pending["channels"]
        st.session_state["_ai_cmd_mode"] = pending.get("mode", "smart")
        st.session_state["_ai_cmd_top_n"] = pending.get("top_n", 100)
        st.session_state.pop("_ai_cmd_pending", None)
        st.rerun()
    if col_no.button("Dismiss"):
        st.session_state.pop("_ai_cmd_pending", None)
        st.session_state.pop("_ai_cmd_last_input", None)
        st.rerun()

st.divider()


# ══════════════════════════════════════════════════════════════
# CHANNEL SELECTION
# ══════════════════════════════════════════════════════════════

# Last fetch info
history = db.get_fetch_history(limit=1)
if history:
    last = history[0]
    st.caption(f"Last refresh: {fmt_date(last['fetched_at'])} · {last['status']} · {last['channels_updated']} channels · {last['videos_fetched']} videos")

# ── Daily Snapshot Health ──────────────────────────────────────
with st.expander("📅 Daily Snapshot Health", expanded=False):
    _all_hist = db.get_fetch_history(limit=100)
    _snap_rows = [h for h in _all_hist if (h.get("status") or "").startswith("daily_snapshot")]
    if not _snap_rows:
        st.info("No daily-snapshot runs yet. The GitHub Action fires at 03:15 UTC daily.")
    else:
        from datetime import datetime as _dt, timezone as _tz
        last_ok = next((h for h in _snap_rows if h.get("status") == "daily_snapshot"), None)
        if last_ok:
            try:
                _hrs = (_dt.now(_tz.utc) - _dt.fromisoformat(last_ok["fetched_at"].replace("Z", "+00:00"))).total_seconds() / 3600
            except Exception:
                _hrs = None
            if _hrs is not None and _hrs > 36:
                st.error(f"⚠️ Last successful snapshot was {_hrs:.0f} hours ago — check the GitHub Action.")
            else:
                st.success(f"✅ Last successful snapshot: {fmt_date(last_ok['fetched_at'])}")
        else:
            st.warning("No successful snapshot runs yet — only partial/error runs below.")

        _rows_html = ""
        for h in _snap_rows[:20]:
            status = h.get("status", "")
            pill_color = "#00CC96" if status == "daily_snapshot" else ("#FFA15A" if "partial" in status else "#EF553B")
            msg = (h.get("error_message") or "").replace("<", "&lt;").replace(">", "&gt;")[:200]
            _rows_html += f"""<tr>
                <td style="padding:6px 12px;color:#888">{fmt_date(h.get('fetched_at', ''))}</td>
                <td style="padding:6px 12px"><span style="background:{pill_color};color:#000;padding:2px 8px;border-radius:4px;font-size:12px">{status}</span></td>
                <td style="padding:6px 12px;text-align:right">{h.get('channels_updated', 0)}</td>
                <td style="padding:6px 12px;color:#888;font-size:12px">{msg}</td>
            </tr>"""
        import streamlit.components.v1 as _components
        _components.html(f"""
        <style>
          .sh {{ width:100%; border-collapse:collapse; font-size:14px; color:#FAFAFA;
                 font-family:"Source Sans Pro",sans-serif; }}
          .sh th {{ padding:6px 12px; border-bottom:2px solid #444; text-align:left; }}
          .sh td {{ border-bottom:1px solid #262730; }}
        </style>
        <table class="sh"><thead><tr>
          <th>When (UTC)</th><th>Status</th>
          <th style="text-align:right">Channels</th>
          <th>Message</th>
        </tr></thead><tbody>{_rows_html}</tbody></table>
        """, height=min(len(_snap_rows[:20]) * 37 + 80, 800), scrolling=True)

st.subheader("Select channels")

# Filter channels based on global header filter
league, club = get_global_filter()
if league is not None:
    filtered_channels = get_channels_for_filter(all_channels, league)
else:
    filtered_channels = all_channels

channel_names = [c["name"] for c in filtered_channels]

# Apply AI command pre-selections by setting widget keys directly
if "_ai_cmd_channels" in st.session_state:
    st.session_state["_w_channels"] = [n for n in st.session_state.pop("_ai_cmd_channels") if n in channel_names]
if "_ai_cmd_mode" in st.session_state:
    mode_key = st.session_state.pop("_ai_cmd_mode")
    MODE_OPTIONS = ["Smart (new videos + update top stats)", "Full (re-fetch all videos)", "Quick Stats (channel stats only)", "Season (all videos from Aug 2025)"]
    st.session_state["_w_mode"] = [MODE_OPTIONS[{"smart": 0, "full": 1, "quick_stats": 2, "season": 3}.get(mode_key, 0)]]
if "_ai_cmd_top_n" in st.session_state:
    st.session_state["_w_top_n"] = st.session_state.pop("_ai_cmd_top_n")

MODE_OPTIONS = ["Smart (new videos + update top stats)", "Full (re-fetch all videos)", "Quick Stats (channel stats only)", "Season (all videos from Aug 2025)"]

selected = st.multiselect("Channels", channel_names, key="_w_channels")

top_n = st.slider("Top N most popular videos to keep per channel", 50, 500, 100, step=50, key="_w_top_n")

BATCH_SAVE_SIZE = 500

# ══════════════════════════════════════════════════════════════
# SESSION STATE
# ══════════════════════════════════════════════════════════════

STOP_KEY = "_refresh_stop"
AI_STOP_KEY = "_ai_stop"
for key in [STOP_KEY, AI_STOP_KEY]:
    if key not in st.session_state:
        st.session_state[key] = False

# ── Detect interrupted runs (page reload during processing) ──
if st.session_state.get("_refresh_running"):
    completed = st.session_state.get("_run_completed", [])
    remaining = st.session_state.get("_run_remaining", [])
    st.session_state["_refresh_running"] = False
    st.session_state[STOP_KEY] = False
    if completed:
        db.log_fetch(len(completed), 0, "interrupted", f"Page reloaded — completed: {', '.join(completed)}")
    if remaining:
        st.warning(f"Previous run was interrupted by page reload. Completed: {', '.join(completed) or 'none'}. Remaining channels pre-selected.")
        st.session_state["_w_channels"] = remaining
    st.session_state.pop("_run_completed", None)
    st.session_state.pop("_run_remaining", None)

if st.session_state.get("_ai_running"):
    completed = st.session_state.get("_ai_completed", [])
    remaining = st.session_state.get("_ai_remaining", [])
    st.session_state["_ai_running"] = False
    st.session_state[AI_STOP_KEY] = False
    if completed:
        db.log_fetch(len(completed), 0, "interrupted", f"AI interrupted — completed: {', '.join(completed)}")
    if remaining:
        st.warning(f"AI generation was interrupted by page reload. Completed: {', '.join(completed) or 'none'}. Remaining channels pre-selected.")
        st.session_state["_w_channels"] = remaining
    st.session_state.pop("_ai_completed", None)
    st.session_state.pop("_ai_remaining", None)


# ══════════════════════════════════════════════════════════════
# DATA FETCH
# ══════════════════════════════════════════════════════════════

def process_channel(ch, is_full, top_n, progress, chan_idx, chan_total, stop_flag_key):
    """Fetch and save videos for one channel using UULP+UUPS playlists.
    Returns (videos_saved, was_cancelled)."""
    prefix = f"[{chan_idx}/{chan_total}] {ch['name']}"
    channel_id = ch["youtube_channel_id"]

    progress.progress(chan_idx / chan_total, text=f"{prefix}: getting channel stats...")
    stats = yt.get_channel_stats(channel_id)
    if not stats:
        return 0, False

    channel_row = db.upsert_channel({**ch, **stats})
    channel_db_id = channel_row["id"]
    db.snapshot_channel(channel_db_id, stats)

    if st.session_state.get(stop_flag_key):
        return 0, True

    progress.progress(chan_idx / chan_total, text=f"{prefix}: fetching popular video IDs...")
    pop = yt.get_popular_video_ids(channel_id)
    long_ids = pop["long"]
    short_ids = pop["shorts"]

    if st.session_state.get(stop_flag_key):
        return 0, True

    # Fetch details for both sets
    all_videos = []
    all_ids = long_ids + short_ids
    long_id_set = set(long_ids)
    total = len(all_ids)
    progress.progress(chan_idx / chan_total, text=f"{prefix}: fetching details for {len(long_ids)} long + {len(short_ids)} shorts...")

    def _split_top(videos, n):
        """Keep top N long + top N shorts, each sorted by views."""
        longs = sorted([v for v in videos if v.get("format") == "long"], key=lambda v: v["view_count"], reverse=True)[:n]
        shorts = sorted([v for v in videos if v.get("format") == "short"], key=lambda v: v["view_count"], reverse=True)[:n]
        return longs + shorts

    for batch_start in range(0, total, 50):
        if st.session_state.get(stop_flag_key):
            if all_videos:
                db.upsert_videos(classify_videos(_split_top(all_videos, top_n)), channel_db_id)
            return len(all_videos), True

        batch = all_ids[batch_start : batch_start + 50]
        batch_videos = yt.get_video_details(batch)
        # Tag each video with its format
        for v in batch_videos:
            v["format"] = "long" if v["youtube_video_id"] in long_id_set else "short"
        all_videos.extend(batch_videos)

        done = min(batch_start + 50, total)
        pct = (chan_idx - 1 + done / max(total, 1)) / chan_total
        progress.progress(min(pct, 0.99), text=f"{prefix}: {fmt_num(done)}/{fmt_num(total)} videos fetched...")

    to_save = classify_videos(_split_top(all_videos, top_n))
    db.upsert_videos(to_save, channel_db_id)
    return len(to_save), False


st.subheader("Data Refresh")

col_btn, col_mode = st.columns([1, 3])
with col_mode:
    refresh_modes = st.multiselect("Mode", MODE_OPTIONS, default=[MODE_OPTIONS[0]], key="_w_mode", label_visibility="collapsed", help="Pick one or more — they run in sequence for the selected channels.")
with col_btn:
    start = st.button("Fetch / Update Data", type="primary", disabled=not selected or not refresh_modes)

# Stop button placeholder — shown only during processing
stop_fetch_ph = st.empty()

if start and selected and refresh_modes:
    st.session_state[STOP_KEY] = False
    st.session_state["_refresh_running"] = True

    selected_channels = [c for c in filtered_channels if c["name"] in selected]
    all_names = [ch["name"] for ch in selected_channels]
    completed_names = []
    # Track for interrupt detection
    st.session_state["_run_completed"] = completed_names
    st.session_state["_run_remaining"] = list(all_names)

    chan_total = len(selected_channels)
    stop_fetch_ph.button("Stop Data Refresh", type="secondary", key="_stop_fetch_btn", on_click=lambda: st.session_state.update({STOP_KEY: True}))

    # Run selected modes in the order they appear in MODE_OPTIONS
    ordered_modes = [m for m in MODE_OPTIONS if m in refresh_modes]

    for mode_idx, refresh_mode in enumerate(ordered_modes, 1):
        if st.session_state.get(STOP_KEY):
            break
        st.markdown(f"### [{mode_idx}/{len(ordered_modes)}] {refresh_mode}")
        total_videos = 0
        channels_ok = 0
        cancelled = False
        is_quick_stats = refresh_mode.startswith("Quick")
        is_full = refresh_mode.startswith("Full")
        is_season = refresh_mode.startswith("Season")

        # Reset completed/remaining for each mode pass
        completed_names = []
        st.session_state["_run_completed"] = completed_names
        st.session_state["_run_remaining"] = list(all_names)

        progress = st.progress(0, text="Starting...")
        results_area = st.container()

        try:
            if is_quick_stats:
                for i, ch in enumerate(selected_channels, 1):
                    if st.session_state.get(STOP_KEY):
                        cancelled = True
                        break
                    progress.progress((i - 1) / chan_total, text=f"[{i}/{chan_total}] {ch['name']}: updating stats...")
                    stats = yt.get_channel_stats(ch["youtube_channel_id"])
                    if stats:
                        row = db.upsert_channel({**ch, **stats})
                        if row.get("id"):
                            db.snapshot_channel(row["id"], stats)
                        channels_ok += 1
                        completed_names.append(ch["name"])
                        st.session_state["_run_remaining"] = [n for n in all_names if n not in completed_names]
                        results_area.caption(f"  {ch['name']} — stats updated")

                if cancelled:
                    progress.progress(1.0, text="Cancelled.")
                    db.log_fetch(channels_ok, 0, "cancelled", f"Quick Stats — stopped after: {', '.join(completed_names)}" if completed_names else "Quick Stats — stopped before any channel completed")
                    st.warning(f"Cancelled. Updated stats for {channels_ok}/{chan_total} channels.")
                else:
                    progress.progress(1.0, text=f"Done — {channels_ok} channels updated.")
                    db.log_fetch(channels_ok, 0, "stats_only", f"Quick Stats — {', '.join(completed_names)}")
                    st.success(f"Updated stats for {channels_ok} channels.")
            elif is_season:
                SEASON_SINCE = "2025-08-01"
                for i, ch in enumerate(selected_channels, 1):
                    if st.session_state.get(STOP_KEY):
                        cancelled = True
                        break

                    prefix = f"[{i}/{chan_total}] {ch['name']}"
                    channel_id = ch["youtube_channel_id"]

                    progress.progress((i - 1) / chan_total, text=f"{prefix}: getting channel stats...")
                    stats = yt.get_channel_stats(channel_id)
                    if not stats:
                        results_area.caption(f"  {ch['name']} — skipped (no stats)")
                        continue

                    channel_row = db.upsert_channel({**ch, **stats})
                    channel_db_id = channel_row["id"]
                    db.snapshot_channel(channel_db_id, stats)

                    progress.progress((i - 1) / chan_total, text=f"{prefix}: fetching season video IDs (long + shorts)...")
                    season = yt.get_video_ids_since_by_format(channel_id, SEASON_SINCE)
                    long_ids = season["long"]
                    short_ids = season["shorts"]
                    live_ids = season.get("live", [])

                    if st.session_state.get(STOP_KEY):
                        cancelled = True
                        break

                    # De-dup (some live videos may also appear in long playlist)
                    seen = set()
                    all_season_ids = []
                    for vid in long_ids + short_ids + live_ids:
                        if vid not in seen:
                            seen.add(vid)
                            all_season_ids.append(vid)
                    if not all_season_ids:
                        results_area.caption(f"  {ch['name']} — no videos since {SEASON_SINCE}")
                        channels_ok += 1
                        completed_names.append(ch["name"])
                        st.session_state["_run_remaining"] = [n for n in all_names if n not in completed_names]
                        continue

                    long_id_set = set(long_ids)
                    short_id_set = set(short_ids)
                    live_id_set = set(live_ids)
                    progress.progress((i - 1) / chan_total, text=f"{prefix}: fetching details for {len(long_ids)} long + {len(short_ids)} shorts + {len(live_ids)} live...")
                    season_videos = []
                    for batch_start in range(0, len(all_season_ids), 50):
                        if st.session_state.get(STOP_KEY):
                            cancelled = True
                            break
                        batch = all_season_ids[batch_start : batch_start + 50]
                        batch_videos = yt.get_video_details(batch)
                        for v in batch_videos:
                            vid = v["youtube_video_id"]
                            # Live takes precedence over long (live videos may appear in both playlists)
                            if vid in live_id_set:
                                v["format"] = "live"
                            elif vid in short_id_set:
                                v["format"] = "short"
                            else:
                                v["format"] = "long"
                        season_videos.extend(batch_videos)
                        done = min(batch_start + 50, len(all_season_ids))
                        pct = (i - 1 + done / max(len(all_season_ids), 1)) / chan_total
                        progress.progress(min(pct, 0.99), text=f"{prefix}: {done}/{len(all_season_ids)} videos fetched...")

                    if cancelled:
                        break

                    # Save ALL season videos (classified, with format tag)
                    classified = classify_videos(season_videos)
                    db.upsert_videos(classified, channel_db_id)
                    total_videos += len(classified)
                    channels_ok += 1
                    completed_names.append(ch["name"])
                    st.session_state["_run_remaining"] = [n for n in all_names if n not in completed_names]
                    results_area.caption(f"  {ch['name']} — {len(long_ids)} long + {len(short_ids)} shorts = {fmt_num(len(classified))} season videos saved")

                if cancelled:
                    progress.progress(1.0, text="Cancelled.")
                    db.log_fetch(channels_ok, total_videos, "cancelled", f"Season Fetch — stopped after: {', '.join(completed_names)}" if completed_names else "Season Fetch — stopped before any channel completed")
                    st.warning(f"Cancelled. Saved {fmt_num(total_videos)} season videos across {channels_ok}/{chan_total} channels.")
                else:
                    progress.progress(1.0, text=f"Done — {channels_ok} channels, {fmt_num(total_videos)} season videos.")
                    db.log_fetch(channels_ok, total_videos, "success", f"Season Fetch — {', '.join(completed_names)}")
                    st.success(f"Season fetch complete: {channels_ok} channels, {fmt_num(total_videos)} videos since {SEASON_SINCE}.")
            else:
                mode_label = "Full Fetch" if is_full else "Smart Update"
                for i, ch in enumerate(selected_channels, 1):
                    if st.session_state.get(STOP_KEY):
                        cancelled = True
                        break

                    saved, was_cancelled = process_channel(ch, is_full, top_n, progress, i, chan_total, STOP_KEY)
                    total_videos += saved
                    if saved > 0:
                        channels_ok += 1
                        completed_names.append(ch["name"])
                        st.session_state["_run_remaining"] = [n for n in all_names if n not in completed_names]
                        results_area.caption(f"  {ch['name']} — {fmt_num(saved)} videos saved")
                    if was_cancelled:
                        cancelled = True
                        break

                if cancelled:
                    progress.progress(1.0, text="Cancelled.")
                    db.log_fetch(channels_ok, total_videos, "cancelled", f"{mode_label} — stopped after: {', '.join(completed_names)}" if completed_names else f"{mode_label} — stopped before any channel completed")
                    st.warning(f"Cancelled. Saved {fmt_num(total_videos)} videos across {channels_ok}/{chan_total} channels.")
                else:
                    progress.progress(1.0, text=f"Done — {channels_ok} channels, {fmt_num(total_videos)} videos.")
                    db.log_fetch(channels_ok, total_videos, "success", f"{mode_label} — {', '.join(completed_names)}")
                    st.success(f"Refreshed {channels_ok} channels, {fmt_num(total_videos)} videos total.")

        except Exception as e:
            db.log_fetch(channels_ok, total_videos, "error", str(e))
            st.error(f"Error during fetch: {e}")
            st.code(traceback.format_exc())

    st.session_state["_refresh_running"] = False
    st.session_state[STOP_KEY] = False
    st.session_state.pop("_run_completed", None)
    st.session_state.pop("_run_remaining", None)
    stop_fetch_ph.empty()


# ══════════════════════════════════════════════════════════════
# AI INSIGHTS
# ══════════════════════════════════════════════════════════════

st.subheader("AI Insights")

ai_start = st.button("Generate AI Insights", type="primary", disabled=not selected)

# Stop button placeholder — shown only during processing
stop_ai_ph = st.empty()

if ai_start and selected:
    st.session_state[AI_STOP_KEY] = False
    st.session_state["_ai_running"] = True

    try:
        api_key = st.secrets["app"]["anthropic_api_key"]
    except Exception:
        st.error("Set anthropic_api_key in .streamlit/secrets.toml under [app].")
        st.session_state["_ai_running"] = False
        st.stop()

    from src.ai_analysis import analyze_channel_videos, MODEL as AI_MODEL
    ai_targets = [c for c in filtered_channels if c["name"] in selected]
    all_ai_names = [ch["name"] for ch in ai_targets]
    ai_ok = 0
    ai_names = []
    ai_total = len(ai_targets)

    # Track for interrupt detection
    st.session_state["_ai_completed"] = ai_names
    st.session_state["_ai_remaining"] = list(all_ai_names)

    stop_ai_ph.button("Stop AI Generation", type="secondary", key="_stop_ai_btn", on_click=lambda: st.session_state.update({AI_STOP_KEY: True}))

    progress = st.progress(0, text="Starting AI analysis...")
    results_area = st.container()

    for i, ch in enumerate(ai_targets, 1):
        if st.session_state.get(AI_STOP_KEY):
            progress.progress(1.0, text="Cancelled.")
            st.warning(f"AI generation stopped after {ai_ok}/{ai_total} channels." + (f" Completed: {', '.join(ai_names)}" if ai_names else ""))
            break

        progress.progress((i - 1) / ai_total, text=f"[{i}/{ai_total}] {ch['name']}: loading videos...")
        videos_for_ai = db.get_videos_by_channel(ch["id"])[:100]
        if not videos_for_ai:
            results_area.caption(f"  {ch['name']} — skipped (no videos)")
            continue

        progress.progress((i - 1) / ai_total, text=f"[{i}/{ai_total}] {ch['name']}: analyzing with Claude...")
        try:
            insights = analyze_channel_videos(api_key, videos_for_ai, ch["name"])
            db.save_insights(ch["id"], insights, model=AI_MODEL)
            ai_ok += 1
            ai_names.append(ch["name"])
            st.session_state["_ai_remaining"] = [n for n in all_ai_names if n not in ai_names]
            results_area.caption(f"  {ch['name']} — insights generated")
        except Exception as e:
            results_area.caption(f"  {ch['name']} — failed: {e}")

    if ai_ok > 0 and not st.session_state.get(AI_STOP_KEY):
        progress.progress(1.0, text=f"Done — {ai_ok} channels analyzed.")
        db.log_fetch(ai_ok, 0, "ai_insights", f"AI Analysis — {', '.join(ai_names)}")
        st.success(f"Generated insights for {ai_ok} channels.")
    elif ai_ok > 0:
        db.log_fetch(ai_ok, 0, "ai_insights", f"AI Analysis (partial) — {', '.join(ai_names)}")

    st.session_state["_ai_running"] = False
    st.session_state[AI_STOP_KEY] = False
    st.session_state.pop("_ai_completed", None)
    st.session_state.pop("_ai_remaining", None)
    stop_ai_ph.empty()


# ══════════════════════════════════════════════════════════════
# FETCH HISTORY
# ══════════════════════════════════════════════════════════════

with st.expander("Fetch History"):
    history = db.get_fetch_history(limit=20)
    if history:
        hist_df = pd.DataFrame(history)
        status_map = {"success": "OK", "cancelled": "Cancelled", "error": "Error", "stats_only": "OK", "ai_insights": "OK"}
        hist_df["type"] = hist_df["status"].map(status_map).fillna(hist_df["status"])
        hist_df["fetched_at"] = hist_df["fetched_at"].apply(fmt_date)
        st.dataframe(
            hist_df[["fetched_at", "type", "channels_updated", "videos_fetched", "error_message"]].rename(columns={
                "fetched_at": "Time",
                "type": "Status",
                "channels_updated": "Channels",
                "videos_fetched": "Videos",
                "error_message": "Details",
            }),
            use_container_width=True,
        )
    else:
        st.caption("No fetch history yet.")
