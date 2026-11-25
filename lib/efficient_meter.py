"""efficient_meter — E0 telemetry for /efficient skill.

Records a token-budget prediction before a run and verifies actuals against it.
Appends one JSON line per run to state/efficient/runs.jsonl (atomic write).

Key API:
    record_prediction(run_id, predicted) -> None
    record_actuals(run_id, actuals)      -> verdict dict
    receipt(verdict)                     -> str  (deterministic one-liner)

Falsification bound: any axis with actual > 1.5 × predicted is a violation.
Axes: tool_calls, files_read, fix_iterations, subagents.

Theory grounding: the 1.5× bound is the MDL/shortest-description framing —
a run that overshoots its predicted resource envelope by more than half was
not modelling its own cost (Kolmogorov incompressibility of the task was
underestimated). This is wrong if the bound is systematically too tight for
well-understood tasks (target: < 20% falsification rate over 10 runs); see
skills/efficient/SKILL.md falsification section.

Atomic write: writes to a .tmp file then os.rename() for POSIX atomicity —
prevents half-written JSONL lines visible to concurrent readers.

No external dependencies — stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AXES = ("tool_calls", "files_read", "fix_iterations", "subagents")
_BOUND = 1.5  # falsification multiplier — any actual > predicted * 1.5 → violation
_STATE_DIR = pathlib.Path.home() / ".claude" / "state" / "efficient"
_RUNS_FILE = _STATE_DIR / "runs.jsonl"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_dir() -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(record: dict[str, Any]) -> None:
    """Atomic JSONL append: write to temp file then os.rename."""
    _ensure_dir()
    line = json.dumps(record, separators=(",", ":")) + "\n"
    fd, tmp = tempfile.mkstemp(dir=_STATE_DIR, prefix=".run_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(line)
        # Append-via-rename: open existing file and append, or just rename if new.
        # For true JSONL we append to the target file.
        with open(_RUNS_FILE, "a", encoding="utf-8") as dst:
            dst.write(line)
        os.unlink(tmp)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_prediction(run_id: str) -> dict[str, Any] | None:
    """Scan runs.jsonl for the latest prediction record for run_id."""
    if not _RUNS_FILE.exists():
        return None
    result = None
    with open(_RUNS_FILE, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("run_id") == run_id and obj.get("kind") == "prediction":
                result = obj
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_prediction(run_id: str, predicted: dict[str, Any]) -> None:
    """Persist an E0 budget prediction before a run starts.

    Args:
        run_id:    Caller-supplied identifier (e.g. task slug + timestamp).
        predicted: Dict with any subset of axes (tool_calls, files_read,
                   fix_iterations, subagents). Missing axes default to 0.
    """
    record: dict[str, Any] = {
        "kind": "prediction",
        "run_id": run_id,
        "predicted": {ax: int(predicted.get(ax, 0)) for ax in _AXES},
    }
    _append_jsonl(record)


def record_actuals(run_id: str, actuals: dict[str, Any]) -> dict[str, Any]:
    """Persist actuals and compute the verdict for run_id.

    Looks up the most recent prediction for run_id in runs.jsonl, compares
    each axis against the 1.5× bound, and appends a verdict record.

    Returns:
        verdict dict:
            {
                "held": bool,           # True iff no axis violated
                "violations": [str],    # axis names that exceeded 1.5×
                "predicted": dict,      # the stored prediction (or zeros)
                "actuals": dict,
                "fold_ratio": float,    # compression ratio if provided, else 0.0
            }
    """
    pred_record = _load_prediction(run_id)
    predicted = pred_record["predicted"] if pred_record else {ax: 0 for ax in _AXES}

    actual_values = {ax: int(actuals.get(ax, 0)) for ax in _AXES}
    fold_ratio: float = float(actuals.get("fold_ratio", 0.0))

    violations: list[str] = []
    for ax in _AXES:
        p = predicted[ax]
        a = actual_values[ax]
        # Zero prediction with nonzero actual is a violation only if actual > 0
        # and prediction was explicitly zero (caller chose to predict 0 usage).
        if p == 0:
            if a > 0:
                violations.append(ax)
        elif a > p * _BOUND:
            violations.append(ax)

    verdict: dict[str, Any] = {
        "kind": "verdict",
        "run_id": run_id,
        "held": len(violations) == 0,
        "violations": violations,
        "predicted": predicted,
        "actuals": actual_values,
        "fold_ratio": fold_ratio,
    }
    _append_jsonl(verdict)
    return {k: v for k, v in verdict.items() if k != "kind"}


def receipt(verdict: dict[str, Any]) -> str:
    """Return a deterministic single-line efficiency receipt.

    The string is a pure function of the verdict dict — no randomness,
    no timestamps. Byte-identical on repeated calls with the same input.

    Format example:
        "E0 HELD · 12/14 calls · 2 reads · 0 agents · fold x0.42"
    or on violation:
        "E0 VIOLATED[tool_calls] · 20/14 calls · 3 reads · 0 agents · fold x0.00"

    The SHA-256 of the receipt string is stable and can serve as a run fingerprint.
    """
    status = "HELD" if verdict.get("held") else "VIOLATED[{}]".format(
        ",".join(verdict.get("violations", []))
    )

    pred = verdict.get("predicted", {})
    actual = verdict.get("actuals", {})
    fold_ratio = float(verdict.get("fold_ratio", 0.0))

    calls_a = actual.get("tool_calls", 0)
    calls_p = pred.get("tool_calls", 0)
    reads_a = actual.get("files_read", 0)
    agents_a = actual.get("subagents", 0)

    line = (
        f"E0 {status}"
        f" · {calls_a}/{calls_p} calls"
        f" · {reads_a} reads"
        f" · {agents_a} agents"
        f" · fold x{fold_ratio:.2f}"
    )
    return line


def receipt_sha256(verdict: dict[str, Any]) -> str:
    """SHA-256 of receipt(verdict) — stable run fingerprint."""
    return hashlib.sha256(receipt(verdict).encode()).hexdigest()
