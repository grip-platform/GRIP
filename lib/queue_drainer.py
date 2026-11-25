"""W7 — propagation queue drainer via DFI-FIB kernel.

Per plans/auto-insight-rsi-sprint-2026-05-05.md W7. Wraps
``propagation_ledger.queue()`` in ``InnerspeakerGo`` so that each call
drains a Fibonacci(depth) batch off the head of the pending queue.

Each batch entry is passed to an injected ``propagate_fn(attempt) ->
PropagationOutcome`` callable; the result is recorded back to the ledger.
Entries that consistently fail to make progress get classified as
``UNPROPAGATABLE`` and added to the kernel's persistent fixed-point
registry — they stop being retried in subsequent sessions.

State::

    PendingQueue(
        entries: tuple[PropagationAttempt, ...],   # ordered, head first
        unpropagatable: frozenset[str],            # fingerprints never to retry
    )

Threshold: ``{"queue_depth": ("le", 50)}`` — stop when fewer than 50 entries
remain; the kernel exits early on threshold pass OR fixed point OR depth cap.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from improve_kernel import ImproveResult, InnerspeakerGo, fingerprint
from propagation_ledger import (
    LEDGER_PATH,
    PropagationAttempt,
    PropagationOutcome,
    queue as ledger_queue,
    record_outcome,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingQueue:
    """Drainer state — ordered head-first entries + unpropagatable set."""
    entries: tuple[PropagationAttempt, ...] = ()
    unpropagatable: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict:
        """Stable representation for fingerprinting."""
        return {
            "entries": [(a.fingerprint, a.target, a.outcome.value) for a in self.entries],
            "unpropagatable": sorted(self.unpropagatable),
        }


# ---------------------------------------------------------------------------
# Fibonacci
# ---------------------------------------------------------------------------


def fibonacci(n: int) -> int:
    """Fibonacci number for batch sizing. fib(1)=1, fib(2)=1, fib(3)=2, ..."""
    if n <= 0:
        return 0
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


# ---------------------------------------------------------------------------
# Drainer
# ---------------------------------------------------------------------------


PropagateFn = Callable[[PropagationAttempt], PropagationOutcome]

DEFAULT_THRESHOLD = {"queue_depth": ("le", 50.0)}
DEFAULT_TELEMETRY = Path.home() / ".claude" / "logs" / "queue-drainer.jsonl"


@dataclass
class _BatchResult:
    """Per-batch counters and updates passed back to drain_step."""
    remaining: list
    new_unpropagatable: set
    propagated: int = 0
    deferred: int = 0
    terminal: int = 0


def _process_batch(
    batch: tuple,
    remaining: list,
    new_unpropagatable: set,
    propagate_fn: PropagateFn,
    ledger_path: Path | None,
) -> _BatchResult:
    """Run propagate_fn against each batch entry, record outcome, classify."""
    out = _BatchResult(remaining=remaining, new_unpropagatable=new_unpropagatable)
    for attempt in batch:
        try:
            outcome = propagate_fn(attempt)
        except Exception:
            outcome = PropagationOutcome.ERROR
        recorded = record_outcome(_replace_outcome(attempt, outcome), path=ledger_path)
        if recorded.outcome == PropagationOutcome.SUCCESS:
            out.propagated += 1
        elif recorded.outcome == PropagationOutcome.UNPROPAGATABLE:
            out.new_unpropagatable.add(attempt.fingerprint)
            out.terminal += 1
        else:
            out.deferred += 1
            out.remaining.append(_replace_outcome(attempt, PropagationOutcome.DEFERRED))
    return out


def _compute_metrics(
    state: PendingQueue, new_state: PendingQueue, batch: _BatchResult
) -> dict[str, float]:
    """Build the metrics dict surfaced to the kernel + telemetry."""
    total_seen = len(state.entries) + len(state.unpropagatable)
    return {
        "queue_depth": float(len(new_state.entries)),
        "unpropagatable_ratio": len(batch.new_unpropagatable) / max(1, total_seen),
        "propagated_this_step": float(batch.propagated),
        "deferred_this_step": float(batch.deferred),
        "terminal_this_step": float(batch.terminal),
    }


def _make_converge(
    propagate_fn: PropagateFn,
    *,
    ledger_path: Path | None = None,
    telemetry_path: Path | None = None,
):
    """Closure: bind propagate_fn so the kernel can call drain_step(state, depth)."""
    log_path = telemetry_path or DEFAULT_TELEMETRY

    def drain_step(state: PendingQueue, depth: int) -> ImproveResult[PendingQueue]:
        depth_in = max(1, depth)
        batch_size = fibonacci(depth_in)
        batch = state.entries[:batch_size]
        result = _process_batch(
            batch=batch,
            remaining=list(state.entries[batch_size:]),
            new_unpropagatable=set(state.unpropagatable),
            propagate_fn=propagate_fn,
            ledger_path=ledger_path,
        )
        new_state = PendingQueue(
            entries=tuple(result.remaining),
            unpropagatable=frozenset(result.new_unpropagatable),
        )
        metrics = _compute_metrics(state, new_state, result)
        _emit_drain_telemetry(
            log_path, depth_in, len(state.entries), len(new_state.entries), metrics
        )
        return ImproveResult(
            state=new_state, metrics=metrics, depth=depth_in,
            fingerprint=fingerprint(new_state),
        )

    return drain_step


def _replace_outcome(
    attempt: PropagationAttempt, outcome: PropagationOutcome
) -> PropagationAttempt:
    """Return a copy of attempt with the outcome replaced and timestamp updated."""
    from dataclasses import replace
    return replace(attempt, outcome=outcome, timestamp=time.time())


def _emit_drain_telemetry(
    path: Path, depth: int, qin: int, qout: int, metrics: dict
) -> None:
    """One-line JSONL record per drain step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "depth": depth,
        "queue_in": qin,
        "queue_out": qout,
        **metrics,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def make_drainer(
    propagate_fn: PropagateFn,
    *,
    threshold: dict[str, tuple[str, float]] | None = None,
    max_depth: int = 13,
    ledger_path: Path | None = None,
    telemetry_path: Path | None = None,
    fixed_points_path: Path | None = None,
) -> InnerspeakerGo[PendingQueue]:
    """Build a configured drainer kernel."""
    return InnerspeakerGo(
        converge=_make_converge(
            propagate_fn,
            ledger_path=ledger_path,
            telemetry_path=telemetry_path,
        ),
        threshold=threshold or DEFAULT_THRESHOLD,
        max_depth=max_depth,
        fixed_points_path=fixed_points_path,
        telemetry_path=telemetry_path,
    )


def load_pending_state(*, ledger_path: Path | None = None) -> PendingQueue:
    """Read the current pending queue from the ledger."""
    pending = ledger_queue(path=ledger_path)
    return PendingQueue(entries=tuple(pending), unpropagatable=frozenset())


def drain(
    propagate_fn: PropagateFn,
    *,
    threshold: dict[str, tuple[str, float]] | None = None,
    max_depth: int = 13,
    ledger_path: Path | None = None,
    telemetry_path: Path | None = None,
    fixed_points_path: Path | None = None,
) -> ImproveResult[PendingQueue]:
    """Top-level drain entry — load state, run kernel, return result."""
    drainer = make_drainer(
        propagate_fn,
        threshold=threshold,
        max_depth=max_depth,
        ledger_path=ledger_path,
        telemetry_path=telemetry_path,
        fixed_points_path=fixed_points_path,
    )
    initial_state = load_pending_state(ledger_path=ledger_path)
    return drainer(initial_state)
