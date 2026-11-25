#!/usr/bin/env python3
"""Stop-hook entry point for the W7 queue drainer.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W7. Runs at session end
(``Stop`` event), drains a Fibonacci-sized batch off the head of the
propagation queue, records outcomes to the ledger, and emits one telemetry
line per drain step.

Fails open: any exception is logged to ``logs/queue-drainer-errors.jsonl``
and the hook exits with code 0 so the session-end pipeline is not blocked.

Wiring: this hook delegates the actual canonical-file write work to
``insight_propagator.propagate``. If that import fails (e.g. during
bootstrap before W1-W5 of the prior plan ships), the hook treats every
queue entry as ``DEFERRED`` (re-queued for next session) so no data is
lost — only progress is delayed.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path


_LIB = Path.home() / ".claude" / "lib"
sys.path.insert(0, str(_LIB))


def _load_propagate_fn():
    """Import the real propagator if available, else a defer-everything stub."""
    try:
        from insight_propagator import propagate as real_propagate  # type: ignore
        from propagation_ledger import PropagationOutcome

        def _wrapper(attempt):
            try:
                outcome = real_propagate(attempt)
                if isinstance(outcome, PropagationOutcome):
                    return outcome
                return PropagationOutcome.SUCCESS if outcome else PropagationOutcome.DEFERRED
            except Exception:
                return PropagationOutcome.ERROR

        return _wrapper
    except ImportError:
        from propagation_ledger import PropagationOutcome
        return lambda _attempt: PropagationOutcome.DEFERRED


def _log_error(error: Exception) -> None:
    err_log = Path.home() / ".claude" / "logs" / "queue-drainer-errors.jsonl"
    err_log.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error": repr(error),
        "trace": traceback.format_exc(),
    }
    try:
        with err_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def main() -> int:
    """Stop-hook entry. Fails open on any error."""
    try:
        from queue_drainer import drain  # type: ignore

        propagate_fn = _load_propagate_fn()
        drain(propagate_fn)
    except Exception as e:  # noqa: BLE001
        _log_error(e)
    return 0


if __name__ == "__main__":
    try:
        sys.stdin.read()
    except Exception:
        pass
    sys.exit(main())
