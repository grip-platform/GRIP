"""Shared value types for the radar sources."""
from __future__ import annotations

from dataclasses import dataclass, field

IMMEDIATE = "immediate"   # pushed on the tick that finds it
ROLLUP = "rollup"         # folded into the 08:00 SAST line, only on days with movement


@dataclass
class Result:
    """What one source hands back from one ``collect()``.

    ``slice`` is the source's own state after the run and is always persisted,
    even on a failure, so a failure counter survives. ``skipped`` is a fatal
    reason (network down, gh not logged in, gog refused): the source did not run,
    so the tick leaves ``last_run`` alone and the schedule retries next tick.
    Partial skips (one traffic endpoint answering 403) go in ``detail`` and the
    log, never in ``skipped``.
    """

    slice: dict
    changes: list[dict] = field(default_factory=list)
    skipped: str | None = None
    detail: dict = field(default_factory=dict)


def change(kind: str, source: str, delivery: str = IMMEDIATE, **fields) -> dict:
    """One detected change. ``kind`` picks the alert line in ``notify.FORMATTERS``."""
    return {"kind": kind, "source": source, "delivery": delivery, **fields}
