"""Documents-scope check for relay co-steerer spawns (V>>'s "documents first").

The Mac inbound drainer grants co-steerers (set in COSTEERER_WHATSAPP_JIDS,
currently none) Write/Edit (the co-steerer capability tier). Their WhatsApp content is UNTRUSTED — it may
legitimately produce DOCUMENTS but must never overwrite GRIP code / rules /
memory. This module is the pure, testable scope predicate; the thin PreToolUse
shim ``hooks/relay-write-scope-gate.py`` calls it and denies out-of-scope writes.

The capability boundary lives in this mechanical check, never in prompt trust
(untrusted-content-capability-lock.md). Paths are resolved (realpath) so a
``../`` traversal or a symlink cannot escape the documents root.
"""
from __future__ import annotations

import os
from pathlib import Path

# Tools that write to disk — the gated set. NotebookEdit included for parity.
GATED_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

# Env var the drainer sets on a co-steerer spawn to the documents root. Unset on
# every other session (operator V>>, main interactive, any non-relay claude) →
# the gate is a pure no-op there, so it can NEVER block normal work.
WRITE_ROOT_ENV = "GRIP_RELAY_WRITE_ROOT"


def target_path(tool_name: str, tool_input: dict) -> str:
    """Extract the on-disk target of a write tool. Write/Edit use ``file_path``;
    NotebookEdit uses ``notebook_path``. Empty string if absent."""
    return (tool_input.get("file_path")
            or tool_input.get("notebook_path")
            or "")


def is_within_root(root: str, target: str) -> bool:
    """True iff ``target`` resolves to a path inside (or equal to) ``root``.

    realpath both sides FIRST so ``root/../../etc/passwd`` and a symlinked
    target both resolve to their true location before the containment test —
    a lexical prefix check would be fooled by either. A non-existent target
    (a brand-new file to Write) still resolves (its parent is realpathed),
    so new-file writes inside the root are correctly allowed.
    """
    if not root or not target:
        return False
    try:
        root_real = Path(root).resolve()
        target_real = Path(target).resolve()
    except (OSError, RuntimeError, ValueError):
        return False  # cannot resolve → treat as outside (fail-closed on scope)
    try:
        target_real.relative_to(root_real)
        return True
    except ValueError:
        return False


def scope_verdict(tool_name: str, tool_input: dict, *, env: dict | None = None) -> str:
    """Return ``"allow"`` or ``"deny"`` for a Write/Edit under the co-steerer
    documents scope. ``allow`` when: the scope env is unset (not a co-steerer
    spawn — operator/main/etc.), the tool is not a write tool, or the target is
    within the documents root. ``deny`` only when the env IS set AND a write
    tool targets a path OUTSIDE the root. ``env`` is injected for tests."""
    env = os.environ if env is None else env
    root = (env.get(WRITE_ROOT_ENV) or "").strip()
    if not root:
        return "allow"  # not a scoped co-steerer spawn → no-op
    if tool_name not in GATED_TOOLS:
        return "allow"
    target = target_path(tool_name, tool_input)
    if not target:
        return "allow"  # nothing to scope
    return "allow" if is_within_root(root, target) else "deny"
