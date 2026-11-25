#!/usr/bin/env python3
"""PreToolUse Write/Edit scope gate — confines relay co-steerer spawns to a
documents directory (V>>'s "documents first", 2026-06-01).

Thin shim over ``lib/relay_write_scope.scope_verdict`` (syscall-doctrine: pure
logic in the lib, mechanical enforcement here). Fires ONLY when the drainer set
``GRIP_RELAY_WRITE_ROOT`` (a co-steerer spawn); for every other session
the env is unset and this is a pure no-op (allow) — it can never block the
operator (V>>), the main interactive session, or any non-relay claude.

untrusted-content-capability-lock.md: a co-steerer's untrusted WhatsApp content
may produce DOCUMENTS but must never overwrite GRIP code/rules/memory. Fail-open
ONLY on our own parse error (the env-gating already bounds the blast radius to
co-steerer spawns); fail-closed on the actual scope check (out-of-root → deny).
"""
import json
import sys
from pathlib import Path

import os  # noqa: E402 — used for the deny-reason env read

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from lib.relay_write_scope import scope_verdict  # noqa: E402
    from lib.hook_response import emit, pretool_decision  # noqa: E402
except Exception:  # noqa: BLE001 — lib missing → cannot enforce → no-op
    sys.exit(0)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # noqa: BLE001 — unparseable → allow (bounded by env-gate)
        return
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if scope_verdict(tool_name, tool_input) == "deny":
        root = os.environ.get("GRIP_RELAY_WRITE_ROOT", "")
        emit(pretool_decision("deny", (
            f"Co-steerer documents scope: {tool_name} target is outside the "
            f"permitted documents directory ({root}). As a co-steerer you may "
            f"create/edit DOCUMENTS only within it — not GRIP code, rules, or "
            f"memory. Write your document inside that directory instead."
        )))
    sys.exit(0)


if __name__ == "__main__":
    main()
