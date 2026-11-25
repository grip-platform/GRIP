"""Dispatch telemetry + suppression shadow ledger (C4).

One append-only JSONL stream (data/dispatch-telemetry.jsonl) with two row
kinds, feeding the weekly /evo aggregate:

  kind="dispatch"   : one row per dispatch, deliverable defined by OUTCOME
  kind="suppressed" : one row per capability WARN — the shadow ledger

Goodhart guard (scientific-method.md, PARAMOUNT): `deliverable` is an outcome
(PR merged / file shipped / question answered), NEVER a call or agent count.
An unresolved dispatch is `deliverable=None` and counts as pure cost — the
honest accounting.

The shadow metric `override_success_rate` (H490 / H-DISP-4) is the futurist
council's catch: with an outcome-only efficiency ratio, the only lever to
"improve" it is to attempt fewer expensive dispatches, and every plan
falsifier is structurally blind to deliverables never attempted. Tracking
whether WARNed-but-proceeded dispatches actually produced deliverables makes
under-dispatch ossification visible BEFORE the capability is gone, not after.

Path is injectable via GRIP_DISPATCH_TELEMETRY_PATH (dependency injection —
testable without mocking internals, per Rule 14).
"""

from __future__ import annotations

import json
import os
import time

_DEFAULT_PATH = os.path.join(
    os.environ.get("HOME", "/tmp"), ".claude", "data", "dispatch-telemetry.jsonl"
)


def _path() -> str:
    return os.environ.get("GRIP_DISPATCH_TELEMETRY_PATH", _DEFAULT_PATH)


def _append(row: dict) -> None:
    """Append one JSON line. Fail-open: telemetry must never break dispatch.

    A single json.dumps line is < PIPE_BUF, so an O_APPEND write is atomic
    on POSIX even under concurrent writers — no lock needed for append-only.
    """
    try:
        path = _path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}
        with open(path, "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        pass


def record_dispatch(
    tier: int | None,
    shape: str,
    est_input_tokens: int,
    deliverable: str | None = None,
    deliverable_kind: str | None = None,
) -> None:
    """Record one dispatch. `deliverable` is an outcome string or None."""
    _append(
        {
            "kind": "dispatch",
            "tier": tier,
            "shape": shape,
            "est_input_tokens": est_input_tokens,
            "deliverable": deliverable,
            "deliverable_kind": deliverable_kind,
        }
    )


def record_suppression(
    prompt_shape: str,
    est_useful_work: int,
    discouraged_parallel_count: int,
    proceeded_anyway: bool = False,
    produced_deliverable: bool = False,
) -> None:
    """Record one capability WARN — the shadow-ledger row (H490)."""
    _append(
        {
            "kind": "suppressed",
            "prompt_shape": prompt_shape,
            "est_useful_work": est_useful_work,
            "discouraged_parallel_count": discouraged_parallel_count,
            "proceeded_anyway": proceeded_anyway,
            "produced_deliverable": produced_deliverable,
        }
    )


def _read_rows() -> list[dict]:
    """Read every well-formed JSONL row; skip malformed lines (fail-open)."""
    try:
        with open(_path()) as fh:
            rows = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return rows
    except OSError:
        return []


def aggregate(window_days: int = 7) -> dict:
    """The two numbers the /evo feed reports side by side.

    tokens_per_deliverable — efficiency (pressure to dispatch LESS).
    override_success_rate  — the SHADOW metric: of WARNed dispatches that
        proceeded anyway, the fraction that produced a deliverable. Rising
        ⇒ the gate is suppressing value not waste (the bad timeline, H490).

    Goodhart-safe by construction: a deliverable is counted only when its
    outcome field is non-null; null deliverables contribute tokens (cost)
    but never inflate the denominator.
    """
    rows = _read_rows()
    dispatches = [r for r in rows if r.get("kind") == "dispatch"]
    suppressed = [r for r in rows if r.get("kind") == "suppressed"]

    total_tokens = sum(int(r.get("est_input_tokens") or 0) for r in dispatches)
    delivered = sum(1 for r in dispatches if r.get("deliverable") is not None)
    overridden = [r for r in suppressed if r.get("proceeded_anyway")]
    override_hits = sum(1 for r in overridden if r.get("produced_deliverable"))

    return {
        "window_days": window_days,
        "n_dispatch": len(dispatches),
        "n_suppressed": len(suppressed),
        "tokens_per_deliverable": total_tokens / delivered if delivered else None,
        "override_success_rate": (
            override_hits / len(overridden) if overridden else None
        ),
    }
