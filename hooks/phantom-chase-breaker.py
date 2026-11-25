#!/usr/bin/env python3
"""PreToolUse advisory breaker: HALT a phantom chase after too many untested iterations.

Fires on Agent/Task dispatches. When a dispatch prompt asserts a failure
hypothesis (via lib/premise_reproduce_gate.py), this hook records one unit of
investigation effort against that hypothesis's slug
(lib/phantom_chase_breaker.py). A prompt carrying controlled-test evidence
RESETS the slug's counter; once the untested run-length EXCEEDS the threshold
(default 5, env GRIP_PHANTOM_CHASE_THRESHOLD), the hook injects an advisory
WARN to HALT and run the cheap ground-truth test. It ALWAYS allows — advisory
only — and fails open on any internal error (a broken safety hook must never
block work).

Counter:  lib/phantom_chase_breaker.py (state/confab/phantom-chase.json;
          override via GRIP_PHANTOM_CHASE_STATE)
Detector: lib/premise_reproduce_gate.py (slug + controlled-test evidence)
Tests:    tests/test_phantom_chase_breaker.py
Audit:    logs/phantom-chase-breaker.jsonl (one line per recorded iteration)
Origin:   2026-07-14 RTK incident (hypothesis H798) — a false "RTK is mangling
          reads" hypothesis absorbed two sessions because no iteration ever
          ran the 30-second controlled test.

Activation is the operator-gated step (wire into settings.json PreToolUse for
Agent/Task). Until wired, this file is inert — zero blast radius from merging.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CLAUDE = Path(os.environ.get("CLAUDE_DIR") or (Path.home() / ".claude"))
if str(_CLAUDE) not in sys.path:
    sys.path.insert(0, str(_CLAUDE))

try:
    from lib.phantom_chase_breaker import DEFAULT_THRESHOLD, record_iteration
    from lib.premise_reproduce_gate import classify_dispatch_prompt
except Exception:  # noqa: BLE001 — fail-open: a broken import must never block a dispatch
    sys.exit(0)

_DISPATCH_TOOLS = {"Agent", "Task"}
_AUDIT = _CLAUDE / "logs" / "phantom-chase-breaker.jsonl"

_WARN_TEMPLATE = (
    "[PHANTOM-CHASE] {iterations} investigation iterations on '{slug}' with no "
    "controlled test since — HALT and run the cheap <30s ground-truth test "
    "before continuing (verify the premise, not the conclusion)."
)


def _threshold() -> int:
    """Warn threshold: GRIP_PHANTOM_CHASE_THRESHOLD when a valid int, else the default."""
    try:
        return int(os.environ.get("GRIP_PHANTOM_CHASE_THRESHOLD", ""))
    except ValueError:
        return DEFAULT_THRESHOLD


def _state_path() -> Path | None:
    """State override: GRIP_PHANTOM_CHASE_STATE when set, else the lib default (None)."""
    override = os.environ.get("GRIP_PHANTOM_CHASE_STATE", "").strip()
    return Path(override) if override else None


def _audit(record: dict) -> None:
    """Append one JSONL audit record; audit loss never blocks the dispatch."""
    try:
        _AUDIT.parent.mkdir(parents=True, exist_ok=True)
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with _AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        print(f"[phantom-chase-breaker] audit write failed ({exc})", file=sys.stderr)


def _emit_warn(status: dict) -> None:
    """Inject the HALT advisory as PreToolUse additional context (never a deny)."""
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": _WARN_TEMPLATE.format(
            iterations=status["iterations"], slug=status["slug"]),
    }}))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — fail-open on malformed stdin
        return 0
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool not in _DISPATCH_TOOLS:
        return 0
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    prompt = tool_input.get("prompt") or ""
    if not prompt:
        return 0

    classified = classify_dispatch_prompt(prompt)
    if not classified["premise_detected"]:
        return 0  # no failure hypothesis in play — nothing to count
    slug_source = classified["premises"][0]["subject"]
    status = record_iteration(
        slug_source,
        had_controlled_test=classified["evidence_present"],
        state_path=_state_path(),
        threshold=_threshold(),
    )
    _audit({"tool": tool, "slug": status["slug"], "iterations": status["iterations"],
            "tested": status["tested"], "verdict": status["verdict"]})
    if status["verdict"] == "warn":
        _emit_warn(status)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — fail-open: never block work on a hook defect
        sys.exit(0)
