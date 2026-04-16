"""Ask Data — free-form chat over channels / videos / snapshots.

Claude gets a schema hint and a single tool `run_pandas(expr)`. The expr
is AST-validated then eval'd against cached DataFrames. Loops until the
model stops calling the tool.
"""
from __future__ import annotations

import json
import os

import streamlit as st
import pandas as pd
from dotenv import load_dotenv
import anthropic

from src.database import Database
from src.auth import require_premium
from src.ask_data import (
    load_dataframes, schema_hint, safe_eval, result_to_payload, UnsafeExpression,
)

load_dotenv()

st.title("Ask the Data")
require_premium()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
db = Database(SUPABASE_URL, SUPABASE_KEY)


# ── Load dataframes (cached 5 min) ──────────────────────────────────────
@st.cache_data(ttl=300, show_spinner="Loading channels, videos & snapshots…")
def _load():
    channels, videos, snapshots = load_dataframes(db)
    return channels, videos, snapshots, schema_hint(channels, videos, snapshots)

channels, videos, snapshots, schema = _load()

st.caption(
    f"Loaded **{len(channels)}** channels · **{len(videos):,}** videos · "
    f"**{len(snapshots):,}** snapshots (last 120d)"
)
with st.expander("Schema"):
    st.code(schema, language="text")

show_details = st.toggle("Show AI reasoning & queries", value=False,
                         help="Show the pandas expressions Claude ran and their raw results.")


# ── Anthropic key ────────────────────────────────────────────────────────
try:
    API_KEY = st.secrets["app"]["anthropic_api_key"]
except Exception:
    API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if not API_KEY:
    st.error("Set anthropic_api_key in .streamlit/secrets.toml [app] or ANTHROPIC_API_KEY env.")
    st.stop()


SYSTEM = f"""You answer questions about a YouTube-analytics database by writing
pandas one-liners that are evaluated against three DataFrames.

Available DataFrames:
{schema}

Rules:
- Use the tool `run_pandas(expr)` to evaluate any pandas expression.
- Expressions must be a SINGLE expression (no statements, no assignments).
- Available names: channels, videos, snapshots, pd.
- No imports. No attribute access starting with underscore.
- Prefer returning a DataFrame or Series so the user sees tabular data.
- When asked about a club/league, filter by `channels.name` or map by
  `channels.country` (IT=Serie A, GB=Premier League, ES=La Liga,
  DE=Bundesliga, FR=Ligue 1, US=MLS).
- `videos.channel_id` joins to `channels.id`. `snapshots.channel_id` too.
- Format values are 'long', 'short', 'live'. Category values are themes
  like 'Highlights', 'Match Recap', 'Goals & Skills', etc.
- After tool_result, explain the answer briefly in plain English.
- If an expression is rejected, rewrite it without the forbidden parts.
"""

TOOLS = [{
    "name": "run_pandas",
    "description": "Evaluate a single pandas expression against channels, videos, snapshots. Returns up to 50 rows.",
    "input_schema": {
        "type": "object",
        "properties": {
            "expr": {"type": "string", "description": "A pandas one-liner expression."},
        },
        "required": ["expr"],
    },
}]


# ── Chat state ───────────────────────────────────────────────────────────
if "_ask_history" not in st.session_state:
    st.session_state["_ask_history"] = []  # list of user/assistant messages

# Render history
for msg in st.session_state["_ask_history"]:
    if msg["role"] == "user":
        with st.chat_message("user"):
            st.markdown(msg["content"])
    else:
        with st.chat_message("assistant"):
            st.markdown(msg.get("text", ""))
            for tool in (msg.get("tools", []) if show_details else []):
                with st.expander(f"🔧 run_pandas — {tool['expr'][:80]}"):
                    st.code(tool["expr"], language="python")
                    res = tool["result"]
                    if res.get("type") == "dataframe":
                        st.dataframe(pd.DataFrame(res["rows"]))
                        if res.get("truncated"):
                            st.caption(f"…{res['total_rows']} total rows (showing 50)")
                    elif res.get("type") == "series":
                        st.json(res["data"])
                    else:
                        st.write(res.get("value", res))


# ── Input ────────────────────────────────────────────────────────────────
prompt = st.chat_input("Ask about channels, videos, views, subs…")
if prompt:
    st.session_state["_ask_history"].append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Build API-format messages (user/assistant only, with tool_use blocks
    # recreated where needed). Easier: just replay user+final-text as chat.
    api_messages = []
    for m in st.session_state["_ask_history"]:
        if m["role"] == "user":
            api_messages.append({"role": "user", "content": m["content"]})
        else:
            # Reconstruct content blocks if there were tool calls
            blocks = []
            if m.get("text"):
                blocks.append({"type": "text", "text": m["text"]})
            if blocks:
                api_messages.append({"role": "assistant", "content": blocks})

    client = anthropic.Anthropic(api_key=API_KEY)
    ns = {"channels": channels, "videos": videos, "snapshots": snapshots, "pd": pd}

    with st.chat_message("assistant"):
        text_acc = ""
        tools_acc: list[dict] = []
        status = st.status("Thinking…", expanded=False)
        MAX_TURNS = 8
        try:
            for turn in range(MAX_TURNS):
                resp = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=2000,
                    system=SYSTEM,
                    tools=TOOLS,
                    messages=api_messages,
                )
                # Collect text + tool_use blocks
                assistant_blocks = []
                tool_uses = []
                for block in resp.content:
                    if block.type == "text":
                        assistant_blocks.append({"type": "text", "text": block.text})
                        text_acc += block.text
                    elif block.type == "tool_use":
                        assistant_blocks.append({
                            "type": "tool_use", "id": block.id,
                            "name": block.name, "input": block.input,
                        })
                        tool_uses.append(block)
                api_messages.append({"role": "assistant", "content": assistant_blocks})

                if resp.stop_reason != "tool_use" or not tool_uses:
                    break

                # Execute each tool_use, append tool_result
                tool_results = []
                for tu in tool_uses:
                    expr = tu.input.get("expr", "")
                    status.update(label=f"Running: {expr[:70]}", state="running")
                    try:
                        result = safe_eval(expr, ns)
                        payload = result_to_payload(result)
                        tools_acc.append({"expr": expr, "result": payload})
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id,
                            "content": json.dumps(payload, default=str)[:20000],
                        })
                    except UnsafeExpression as e:
                        msg = f"Expression rejected by sandbox: {e}"
                        tools_acc.append({"expr": expr, "result": {"type": "error", "value": msg}})
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id,
                            "content": msg, "is_error": True,
                        })
                    except Exception as e:
                        msg = f"{type(e).__name__}: {e}"
                        tools_acc.append({"expr": expr, "result": {"type": "error", "value": msg}})
                        tool_results.append({
                            "type": "tool_result", "tool_use_id": tu.id,
                            "content": msg, "is_error": True,
                        })
                api_messages.append({"role": "user", "content": tool_results})
            status.update(label="Done", state="complete")
        except anthropic.APIStatusError as e:
            st.error(f"API error: {e}")
            status.update(label="Error", state="error")

        # Render this turn
        st.markdown(text_acc or "_(no text response)_")
        for tool in (tools_acc if show_details else []):
            with st.expander(f"🔧 run_pandas — {tool['expr'][:80]}"):
                st.code(tool["expr"], language="python")
                res = tool["result"]
                if res.get("type") == "dataframe":
                    st.dataframe(pd.DataFrame(res["rows"]))
                    if res.get("truncated"):
                        st.caption(f"…{res['total_rows']} total rows (showing 50)")
                elif res.get("type") == "series":
                    st.json(res["data"])
                elif res.get("type") == "error":
                    st.error(res["value"])
                else:
                    st.write(res.get("value", res))

        st.session_state["_ask_history"].append({
            "role": "assistant", "text": text_acc, "tools": tools_acc,
        })

if st.session_state["_ask_history"] and st.button("Clear conversation"):
    st.session_state["_ask_history"] = []
    st.rerun()
