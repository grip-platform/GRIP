#!/usr/bin/env python3
"""Congruent Continuity PRECOG watcher hook — WARN-ONLY, PreToolUse-shaped.

Contract (mirrors ``hooks/no-stubs-gate.py:100-110``): read one JSON payload
``{"tool_name": ..., "tool_input": {...}}`` from stdin, consult
``lib.continuity_watch.check_action``, and on ``level == "warn"`` emit ONE
WARN line to stderr. The hook ALWAYS allows: exit code is 0 on every path and
nothing is ever written to stdout (no ``permissionDecision`` — allow by
omission). WARN-only is the locked build-order decision D3
(``docs/congruent-continuity-genesis.md:81``): an unmeasured brake teaches the
operator to ignore it, so no deny tier exists here at all.

Fail-open by construction: any internal error (garbage stdin, broken import,
unreadable chain) is appended to ``logs/continuity-watch.jsonl`` and the tool
call is allowed. The same log collects every WARN emitted — that audit trail
IS the checker-precision corpus D3 requires before any brake is armed.

INERT until bound: nothing wires this file into ``settings.json`` or
``config/hooks.registry.json`` — activation is the V>>-gated step (same
posture as ``hooks/no-stubs-gate.py:12-14``). Zero blast radius from merging.

Env overrides (each read below — declared-input-must-reach-the-actor):
- ``GRIP_CONTINUITY_CHAIN``      chain file to check (tests; default = live
  session chain via ``lib/continuity_chain.py:81-83``)
- ``GRIP_CONTINUITY_WATCH_LOG``  audit/error log path (default:
  ``<repo>/logs/continuity-watch.jsonl`` beside this hook's repo root)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_LOG = Path(
    os.environ.get("GRIP_CONTINUITY_WATCH_LOG")
    or (_REPO_ROOT / "logs" / "continuity-watch.jsonl")
)
_MARKER = "[continuity-watch]"


def _log(event: dict) -> None:
    """Best-effort JSONL append; a failing audit must never block the tool call."""
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **event}
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool_name = str(payload.get("tool_name", ""))
        tool_input = payload.get("tool_input", {}) or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        chain_env = os.environ.get("GRIP_CONTINUITY_CHAIN")
        from lib.continuity_watch import check_action

        result = check_action(
            tool_name, tool_input, chain_path=Path(chain_env) if chain_env else None
        )
        if result.get("level") == "warn":
            reasons = [str(r) for r in result.get("reasons", [])]
            print(
                f"{_MARKER} WARN (advisory only, action allowed): "
                + " | ".join(reasons),
                file=sys.stderr,
            )
            _log({"event": "warn", "tool_name": tool_name, "reasons": reasons})
    except Exception as exc:  # noqa: BLE001 — fail-open: the watcher must never block
        _log({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
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
