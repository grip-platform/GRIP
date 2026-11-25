#!/usr/bin/env python3
"""Verify-runner — extract a task's verify block, run it, register hypothesis.

Pipeline:
1. `extract_verify_block(task_md_path)` (from `verify_extractor`)
2. `bash -c <block>` via subprocess; capture combined stdout+stderr
3. Optionally write output to a log path
4. On rc == 0: hypothesis_engine.register(...) → status forced to CONFIRMED
5. On rc != 0: hypothesis_engine.register(...) → status forced to REFUTED
   and tail of the log is appended as evidence in the `notes` field.

The hypothesis is registered with `metric="verify_pass"`, baseline=0, and
its status is set in-place via `_update_hypothesis` so verification is
synchronous (no deadline-based polling).

Public API:
    run_task(task_md_path: str, log_path: str | None = None) -> int
        Returns the verify block's exit code (0 == pass, non-0 == fail).
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure lib/ is importable when invoked as a script
_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from verify_extractor import extract_verify_block  # noqa: E402
import hypothesis_engine as he  # noqa: E402

_TITLE_RE = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)
_LOG_TAIL_LINES = 20


def _read_title(task_md_path: str) -> str:
    """Extract the `title:` line from frontmatter; fall back to filename stem."""
    text = Path(task_md_path).read_text(encoding="utf-8", errors="replace")
    m = _TITLE_RE.search(text)
    return m.group(1).strip() if m else Path(task_md_path).stem


def _run_block(block: str, log_path: str | None) -> tuple[int, str]:
    """Execute the verify block via `bash -c`. Return (rc, combined_output)."""
    proc = subprocess.run(
        ["bash", "-c", block],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if log_path:
        Path(log_path).write_text(combined, encoding="utf-8")
    return proc.returncode, combined


def _log_tail(combined_output: str, n: int = _LOG_TAIL_LINES) -> str:
    """Last `n` lines of combined output, for evidence in REFUTED records."""
    lines = combined_output.rstrip("\n").splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def _record_hypothesis(title: str, rc: int, combined: str) -> str:
    """Register the verify outcome as a hypothesis; return final status."""
    prediction = "pass within session" if rc == 0 else "fail within session"
    h = he.register(
        claim=title,
        metric="verify_pass",
        prediction=prediction,
        pr_number=0,
        baseline=0.0,
    )
    h.actual_value = 1.0 if rc == 0 else 0.0
    h.verification_date = datetime.now(timezone.utc).isoformat()
    h.status = "confirmed" if rc == 0 else "refuted"
    h.effect_size = h.actual_value
    if rc != 0:
        h.notes = _log_tail(combined)
    he._update_hypothesis(h)
    he._log_verification(h)
    return h.status


def run_task(task_md_path: str, log_path: str | None = None) -> int:
    """Run a task's verify block and record the outcome as a hypothesis.

    Returns the verify block's exit code. Caller (queue tick) decides
    file movement based on rc.
    """
    block = extract_verify_block(task_md_path)
    if block is None:
        print(f"error: no `verify: |` block in {task_md_path}", file=sys.stderr)
        return 3
    title = _read_title(task_md_path)
    rc, combined = _run_block(block, log_path)
    status = _record_hypothesis(title, rc, combined)
    print(f"verify: rc={rc} status={status} title={title!r}")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: verify_runner.py <task.md> [log.path]", file=sys.stderr)
        sys.exit(2)
    log = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(run_task(sys.argv[1], log))
