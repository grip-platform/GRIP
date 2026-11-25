"""Deadline utilities for broly's Task-lifecycle /evo dispatch (Layer C).

Pure functions. No subprocess, no I/O, no external state. Each function
is independently testable and Goodhart-resistant.

Layer C complements Layer A (markdown subprocess-deadline contract in
agents/broly.md) and Layer B (lib/broly_deadline.py + watchdog cap).
Where Layer A enforces deadlines on Bash subprocesses via Python's
subprocess.run(timeout=N), Layer C replaces Bash dispatch for /evo
specifically with the Task tool family (TaskCreate / TaskGet / TaskStop).
The discriminator between the two timeout error formats is the
``layer`` field plus the ``task_id`` vs ``cmd`` payload key.
"""
from __future__ import annotations

import json
import time
from typing import Any


def format_timeout_result(
    task_id: str, deadline_s: float, elapsed_s: float
) -> dict[str, Any]:
    """Format the structured timeout result emitted on deadline exceedance.

    Layer C discriminator: includes ``task_id`` and ``layer: "C"`` so it
    cannot be confused with Layer A's ``{cmd, deadline_s, elapsed_s}``
    format. The five canonical keys are always present and stable across
    callers; tests in ``tests/test_broly_task_deadline.py`` enforce the
    schema.
    """
    return {
        "error": "deadline_exceeded",
        "task_id": task_id,
        "deadline_s": deadline_s,
        "elapsed_s": elapsed_s,
        "layer": "C",
    }


def deadline_exceeded(start_monotonic: float, deadline_s: float) -> bool:
    """True iff (now - start) exceeds the deadline.

    Pure predicate over ``time.monotonic()``. Monotonic is required because
    the polling loop must be robust to wall-clock adjustments mid-run.
    """
    return (time.monotonic() - start_monotonic) > deadline_s


def emit_timeout_json(
    task_id: str, deadline_s: float, elapsed_s: float
) -> str:
    """JSON-encode the timeout result for stderr emission.

    Stable serialisation: keys in insertion order matching
    ``format_timeout_result``. Caller is expected to write the returned
    string to stderr followed by a newline, then exit 124 to match
    Layer A's GNU-timeout convention.
    """
    return json.dumps(format_timeout_result(task_id, deadline_s, elapsed_s))
