#!/usr/bin/env python3
"""PRECOG orchestration hook — UserPromptSubmit event.

Fires ``lib.precog.orchestrator.orchestrate(prompt)`` on every user prompt
when PRECOG is enabled (``PRECOG_ENABLED=1`` env OR an open flag window
per ``lib.precog.state.is_precog_enabled``). The orchestrator writes
``state/precog/current.idr.json``; ``lib.hal.router._route_quality_cost``
already reads that pointer (shipped via PR #3192) and dispatches per the
IDR's ``provider_addresses`` + ``effort_tier`` instead of the word-count
heuristic that V>> wanted replaced.

Closes the production loop the W0-W3 pilot stopped one step short of:
without this hook the orchestrator never fires in production and the
router override has nothing to consume — the W4 wiring is a complete
no-op until SOMETHING writes the IDR pointer. THIS hook is that
"something".

Activation contract
-------------------
Default OFF (byte-identical to pre-hook). Two activation paths:

1. ``PRECOG_ENABLED=1`` env var (per shell).
2. Open flag window: ``bin/precog-window open --hours 4 --reason "dogfood"``.

Both routes are checked by the same ``lib.precog.state.is_precog_enabled``
shim, so turning EITHER on fires the hook AND the router consumption.

Performance budget
------------------
< 1s p95. Pure-Python orchestrator + sha256 + JSON write — no LLM call.
Over-budget invocations log a ``over_budget=true`` flag to the JSONL log
(``logs/precog-orchestrate-hook.jsonl``); a sustained over-budget signal
falsifies the H-PRECOG token-budget hypothesis.

Best-effort contract
--------------------
Any error/timeout/missing-substrate path returns exit 0 with no IDR
written. The router falls through to the heuristic exactly as it would
without this hook. The hook NEVER blocks the user prompt; it NEVER
returns a non-zero decision; it NEVER mutates the prompt itself.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parent.parent
if str(CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR))

LATENCY_BUDGET_S = 1.0
HOOK_LOG = CLAUDE_DIR / "logs" / "precog-orchestrate-hook.jsonl"
MIN_PROMPT_CHARS = 10  # too short → nothing meaningful to orchestrate


def _log(payload: dict) -> None:
    """Append a JSONL telemetry line. Silent on write failure."""
    try:
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with HOOK_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception:  # noqa: BLE001 — telemetry write must not block hook
        pass


def _parse_stdin() -> dict:
    """Read Claude Code's hook-input JSON from stdin. Empty dict on parse error."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _is_enabled() -> bool:
    """Activation gate — single source of truth in lib.precog.state."""
    try:
        from lib.precog.state import is_precog_enabled  # noqa: PLC0415
        return bool(is_precog_enabled())
    except Exception:  # noqa: BLE001 — substrate missing → no-op
        return False


def _orchestrate_safely(prompt: str, t0: float) -> None:
    """Call the orchestrator; log outcome. Never raises."""
    try:
        from lib.precog.orchestrator import orchestrate  # noqa: PLC0415
        idr = orchestrate(prompt)
        elapsed_ms = int((time.time() - t0) * 1000)
        _log({
            "ts": time.time(),
            "ok": True,
            "elapsed_ms": elapsed_ms,
            "tier": idr.decision.get("effort_tier"),
            "fingerprint": idr.fingerprint[:16],
            "over_budget": elapsed_ms > int(LATENCY_BUDGET_S * 1000),
        })
    except Exception as exc:  # noqa: BLE001 — best-effort; hook never blocks
        _log({
            "ts": time.time(),
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        })


def main() -> int:
    """UserPromptSubmit entry point. Always exits 0 (best-effort)."""
    t0 = time.time()
    if not _is_enabled():
        return 0
    hook_input = _parse_stdin()
    prompt = (hook_input.get("prompt") or "").strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        return 0
    _orchestrate_safely(prompt, t0)
    return 0


if __name__ == "__main__":
    # Dual-sign custody guard (GRIP#5628). This entrypoint has a
    # `#!/usr/bin/env python3` shebang and reaches a signed-record write, so under
    # an interpreter without `cryptography>=48` it would silently emit forgeable
    # `demo-shared-key` records. Re-exec under a capable interpreter FIRST, before
    # main() reaches any signing path.
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "lib"))
    from pq_reexec import reexec_if_incapable as _reexec
    _reexec(_Path(__file__))

    sys.exit(main())
