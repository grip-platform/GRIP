#!/usr/bin/env python3
"""PostToolUse auto-mirror hook.

A newly-created harness task, if OPEN + SAFE, is mirrored to the operator's
PRIVATE grip-tasks repo as one GitHub issue (idempotent). The sensitivity gate
lives in the push tool (``should_mirror``) — this hook is a thin forwarder, so
there is ONE source of truth for what is safe. Client / legal / financial / PII
tasks are HELD and never pushed.

Design invariants:
  * FAIL-OPEN — any error exits 0; a task op is NEVER blocked by this hook.
  * NON-BLOCKING — the push is fire-and-forget in a detached child; combined
    with ``"async": true`` in settings.json it adds ~0 latency to TaskCreate.
  * IDEMPOTENT — the push tool's id->issue map means a task mirrors at most once.

Repo target: env ``GRIP_TASK_MIRROR_REPO`` (default ``operator/grip-tasks``).
Test/no-op: set ``GRIP_TASK_MIRROR_DRYRUN=1`` to forward with --dry-run (no issue).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = os.environ.get("GRIP_TASK_MIRROR_REPO", "operator/grip-tasks")
_CLAUDE = Path(__file__).resolve().parent.parent


def _read_payload():
    try:
        return json.load(sys.stdin)
    except Exception:
        return None


def _extract_task(payload):
    """Return a mirrorable task dict from a TaskCreate PostToolUse payload, else None.

    Only TaskCreate is mirrored (the push tool creates issues; it does not update
    them, so a TaskUpdate has nothing new to create). Requires both an id (from the
    tool response) and a subject (from the tool input)."""
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "TaskCreate":
        return None
    ti = payload.get("tool_input") or {}
    tr = payload.get("tool_response") or {}
    tid = None
    if isinstance(tr, dict):
        tid = tr.get("id") or tr.get("taskId") or (tr.get("task") or {}).get("id")
    subject = (ti.get("subject") or "").strip()
    if not tid or not subject:
        return None
    return {
        "id": str(tid),
        "subject": subject,
        "description": ti.get("description") or "",
        "status": "pending",
    }


def _forward(task):
    """Fire-and-forget the single task to the push tool. Never raises."""
    py = _CLAUDE / "venv" / "bin" / "python"
    interp = str(py) if py.exists() else sys.executable
    bin_path = _CLAUDE / "bin" / "grip-task-mirror-push"
    if not bin_path.exists():
        return
    cmd = [interp, str(bin_path), "--repo", REPO]
    if os.environ.get("GRIP_TASK_MIRROR_DRYRUN"):
        cmd.append("--dry-run")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        proc.stdin.write(json.dumps([task]).encode("utf-8"))
        proc.stdin.close()
        # Deliberately do NOT wait — the push (should_mirror gate + gh create +
        # idempotency-map write) runs detached. Safety + dedup are downstream.
    except Exception:
        pass


def main():
    task = _extract_task(_read_payload())
    if task is not None:
        _forward(task)
    sys.exit(0)  # always fail-open


if __name__ == "__main__":
    main()
