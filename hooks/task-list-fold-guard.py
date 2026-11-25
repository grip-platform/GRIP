#!/usr/bin/env python3
"""
task-list-fold-guard.py — PostToolUse WARN when live task count grows past threshold.

Sibling of `operational-context-fold-guard.py` (same shape, different metric).
Fires on PostToolUse for TaskCreate / TaskUpdate. Reads the canonical digest
written by `lib/task_pruner.py` (the only authoritative count we have without
calling MCP from inside a hook). When `keep_count > MAX_LIVE`, emits a single
`[TASK-FOLD WARN]` line to stderr suggesting the prune command.

Never blocks — pure WARN (mirrors operational-context-fold-guard which only
emits `systemMessage`).

Threshold MAX_LIVE = 75 (tunable; chosen ~50% above the post-W1 baseline of
45 live entries so steady-state work never trips it).

Per rules/knowledge-maturation-functor.md this hook is rule-now-N=1
mechanical enforcement (the cost of token bloat justifies early shimming).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DIGEST_PATH = CLAUDE_DIR / "state" / "tasks" / "digest.json"
LOG_PATH = CLAUDE_DIR / "logs" / "task-list-fold-guard.jsonl"

# Threshold tuned to ~50% above post-W1 baseline (45 live). Avoids tripping
# on steady-state work; fires when reminder-bloat returns.
MAX_LIVE = 75


def _emit_warn(live_count: int) -> None:
    """Print compactor suggestion to stderr; never blocks."""
    msg = (
        f"[TASK-FOLD WARN] live task count = {live_count} > {MAX_LIVE}. "
        f"Reminder bloat will return. Suggest: /prune-tasks "
        f"(or: python3 {CLAUDE_DIR}/lib/task_pruner.py < snapshot.txt --print-deletion-list)"
    )
    print(msg, file=sys.stderr)


def _audit_log(live_count: int) -> None:
    """Append one JSONL row to the audit log. Best-effort; never raises."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": int(time.time()),
            "ts_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "live_count": live_count,
            "threshold": MAX_LIVE,
        }
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    except Exception:
        # Logging is best-effort; never block a tool call on disk failure.
        pass


def _read_live_count_from_digest() -> int | None:
    """Read keep_count from the canonical digest. Returns None if unknown."""
    if not DIGEST_PATH.exists():
        return None
    try:
        data = json.loads(DIGEST_PATH.read_text())
        kc = data.get("keep_count", -1)
        return int(kc) if isinstance(kc, int) and kc >= 0 else None
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def main() -> int:
    """Hook entry point. Reads the PostToolUse JSON from stdin but doesn't
    actually need it — we only consume our own digest. The stdin read keeps
    hook-protocol compatibility (CC discards uncaught stdin).
    """
    try:
        _ = sys.stdin.read()  # drain to avoid SIGPIPE
    except Exception:
        pass

    live = _read_live_count_from_digest()
    if live is None:
        # No digest yet — pruner has never run. Silently no-op (don't
        # spam every TaskCreate with "no digest"; the first /prune-tasks
        # will create it).
        return 0

    if live > MAX_LIVE:
        _emit_warn(live)
        _audit_log(live)

    return 0


if __name__ == "__main__":
    sys.exit(main())
