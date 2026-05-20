"""Intent-based `captured_date` policy for daily snapshot crons.

Semantics: ``captured_date`` = the CET (Europe/Rome) calendar day this
snapshot represents the end-of-day cumulative state for.

Replaces the naive ``datetime.now(timezone.utc).date()`` that
``snapshot_channel`` uses by default, which is fine when the cron fires
inside its target UTC day but mis-dates the row when the run is
delayed across the UTC-midnight boundary (e.g. the Railway outage on
2026-05-19 → 2026-05-20: the catch-up ran at 02:00 UTC May 20, the
naive default stamped ``2026-05-20``, the intent was ``2026-05-19``).

Policy (wall-clock = Europe/Rome, DST handled automatically):

    22:00 ≤ now < 24:00  →  captured_date = today CET
    00:00 ≤ now <  6:00  →  captured_date = yesterday CET
    06:00 ≤ now < 22:00  →  ABORT (refuse to stamp a date silently)

Accepted window combined = 22:00 → 06:00 CET (8h straddling midnight),
all mapping to the same logical day. Outside that window, the operator
must pass an explicit ``--force-date YYYY-MM-DD`` so misdated rows
can't ship by accident.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_CET = ZoneInfo("Europe/Rome")


class OutOfWindowError(RuntimeError):
    """Raised when the cron fires inside the rejected 06:00–22:00 CET
    window without an explicit force-date override."""


def intended_capture_date(force_date: str | None = None,
                          now_cet: datetime | None = None) -> str:
    """Return the ``captured_date`` (ISO ``YYYY-MM-DD``) this run
    should write to, per the policy above.

    Parameters
    ----------
    force_date
        Explicit override (ISO date). Used by the operator to push a
        run inside the rejected window — e.g. when manually catching
        up a missed cron during business hours. When passed, NO time
        check is done; we trust the caller.
    now_cet
        Override the wall clock (testing). Defaults to ``now`` in
        Europe/Rome.

    Raises
    ------
    OutOfWindowError
        If ``force_date`` is not given and ``now_cet`` falls in
        06:00–22:00 CET.
    """
    if force_date:
        # Light sanity — must parse as a date, no further checks.
        datetime.strptime(force_date, "%Y-%m-%d")
        return force_date
    if now_cet is None:
        now_cet = datetime.now(_CET)
    h = now_cet.hour
    if h >= 22:
        # Late evening (22:00–23:59 CET) — the day is wrapping up.
        return now_cet.date().isoformat()
    if h < 6:
        # Early morning (00:00–05:59 CET) — the previous CET day has
        # just ended; this snapshot is its end-of-day reading.
        return (now_cet - timedelta(days=1)).date().isoformat()
    raise OutOfWindowError(
        f"Daily snapshot fired at {now_cet.strftime('%H:%M %Z')} — outside "
        f"the safe window (22:00–06:00 CET). Pass --force-date YYYY-MM-DD "
        f"on the command line to override (use this only for manual "
        f"catch-ups when you know which day you intend to write)."
    )
