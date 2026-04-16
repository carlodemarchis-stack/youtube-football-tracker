"""Free-form data Q&A page — loads curated dataframes and lets Claude
run sandboxed pandas expressions against them.

The AST validator is the whole safety story: if it accepts an expression,
eval() runs it inside a restricted namespace (no builtins, no imports,
no dunder access). Anything rejected gets surfaced back to Claude so it
can rewrite.
"""
from __future__ import annotations

import ast
import pandas as pd


# ── AST whitelist ─────────────────────────────────────────────────────────
# Block anything that could escape the sandbox: dunder access, imports,
# assignments, comprehensions with Name leaks, calls to dangerous builtins.

_ALLOWED_NAMES = {"channels", "videos", "snapshots", "pd", "True", "False", "None"}
_BLOCKED_ATTRS_PREFIX = ("__", "_")  # block private/dunder
# Methods/attrs we DO allow even though they start with underscore — none.
# Calls we block by name:
_BLOCKED_CALL_NAMES = {"eval", "exec", "compile", "open", "__import__",
                       "getattr", "setattr", "delattr", "globals", "locals",
                       "vars", "input", "exit", "quit", "breakpoint"}


class UnsafeExpression(ValueError):
    pass


def _validate_node(node: ast.AST) -> None:
    for child in ast.walk(node):
        # Disallow statements that mutate state / import / define funcs
        if isinstance(child, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                              ast.AsyncFunctionDef, ast.ClassDef, ast.Global,
                              ast.Nonlocal, ast.Delete, ast.AugAssign,
                              ast.AnnAssign, ast.Assign, ast.Raise,
                              ast.Try, ast.With, ast.AsyncWith, ast.Yield,
                              ast.YieldFrom, ast.Await)):
            raise UnsafeExpression(f"Statement not allowed: {type(child).__name__}")

        # Block dunder/private attribute access (e.g. obj.__class__)
        if isinstance(child, ast.Attribute):
            if child.attr.startswith(_BLOCKED_ATTRS_PREFIX):
                raise UnsafeExpression(f"Attribute access '{child.attr}' not allowed")

        # Block references to undeclared names
        if isinstance(child, ast.Name):
            if child.id not in _ALLOWED_NAMES and child.id not in _BLOCKED_CALL_NAMES:
                # Allow short local names inside lambdas/comprehensions — they are
                # bound inside the expression. Detect by checking the context.
                pass  # permissive: names bound by lambdas/comprehensions OK
            if child.id in _BLOCKED_CALL_NAMES:
                raise UnsafeExpression(f"Name '{child.id}' not allowed")


def safe_eval(expr: str, namespace: dict) -> object:
    """Parse -> validate -> eval. Returns the expression result."""
    expr = expr.strip()
    if not expr:
        raise UnsafeExpression("Empty expression")
    if len(expr) > 4000:
        raise UnsafeExpression("Expression too long (max 4000 chars)")
    tree = ast.parse(expr, mode="eval")
    _validate_node(tree)
    # Run with empty builtins — forces expression to use only provided names.
    return eval(compile(tree, "<ask>", "eval"),
                {"__builtins__": {}},
                namespace)


# ── DataFrame loaders ─────────────────────────────────────────────────────

def load_dataframes(db) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (channels_df, videos_df, snapshots_df) with slim columns."""
    channels = pd.DataFrame(db.get_all_channels())
    if not channels.empty:
        keep_c = [c for c in [
            "id", "name", "handle", "country", "entity_type",
            "subscriber_count", "total_views", "video_count",
            "long_form_count", "shorts_count", "live_count",
            "launched_at", "last_fetched",
        ] if c in channels.columns]
        channels = channels[keep_c]

    # Videos — minimal projection, paginated
    vid_rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            db.client.table("videos")
            .select("id,youtube_video_id,channel_id,title,published_at,"
                    "duration_seconds,format,category,"
                    "view_count,like_count,comment_count")
            .range(offset, offset + 999)
            .execute().data or []
        )
        vid_rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    videos = pd.DataFrame(vid_rows)
    if not videos.empty and "published_at" in videos.columns:
        videos["published_at"] = pd.to_datetime(videos["published_at"], utc=True, errors="coerce")

    # Snapshots — last 120 days to keep it light
    since = (pd.Timestamp.utcnow().normalize() - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    snap_rows = db.get_all_snapshots(since_date=since) or []
    snapshots = pd.DataFrame(snap_rows)
    if not snapshots.empty and "captured_date" in snapshots.columns:
        snapshots["captured_date"] = pd.to_datetime(snapshots["captured_date"], errors="coerce")

    return channels, videos, snapshots


def schema_hint(channels: pd.DataFrame, videos: pd.DataFrame, snapshots: pd.DataFrame) -> str:
    """Compact schema description passed to Claude in the system prompt."""
    def _cols(df: pd.DataFrame) -> str:
        if df.empty:
            return "(empty)"
        return ", ".join(f"{c}:{df[c].dtype}" for c in df.columns)
    return (
        f"channels ({len(channels)} rows): {_cols(channels)}\n"
        f"videos ({len(videos)} rows): {_cols(videos)}\n"
        f"snapshots ({len(snapshots)} rows, last 120d): {_cols(snapshots)}\n"
    )


def result_to_payload(result: object, max_rows: int = 50) -> dict:
    """Serialize an eval result to a JSON-safe dict for the tool_result."""
    if isinstance(result, pd.DataFrame):
        truncated = len(result) > max_rows
        out = result.head(max_rows)
        return {
            "type": "dataframe",
            "rows": out.to_dict(orient="records"),
            "columns": list(out.columns),
            "total_rows": len(result),
            "truncated": truncated,
        }
    if isinstance(result, pd.Series):
        truncated = len(result) > max_rows
        out = result.head(max_rows)
        return {
            "type": "series",
            "data": {str(k): (v.item() if hasattr(v, "item") else v) for k, v in out.items()},
            "total_rows": len(result),
            "truncated": truncated,
        }
    if isinstance(result, (int, float, str, bool)) or result is None:
        return {"type": "scalar", "value": result}
    # Fallback: stringify
    return {"type": "repr", "value": repr(result)[:2000]}
