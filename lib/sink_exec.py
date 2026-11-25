#!/usr/bin/env python3
"""E15: Output sink shim — fire-and-forget command execution.

Runs a shell command and returns a compact summary (exit code + elapsed time).
On failure, appends the last 500 characters of stderr so errors are visible.

Usage:
    python3 ~/.claude/lib/sink_exec.py "git push -u origin feat/branch"
    python3 ~/.claude/lib/sink_exec.py "pip install -r requirements.txt"

Exit code mirrors the subprocess exit code. Non-zero indicates failure.
Syscall-doctrine shim (rules/syscall-doctrine.md) — one stable API over
all fire-and-forget shell operations where only exit status matters.
"""
from __future__ import annotations

import subprocess
import sys
import time

_STDERR_TAIL = 500


def run_sink(command: str) -> int:
    start = time.monotonic()
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    elapsed = time.monotonic() - start
    code = result.returncode

    if code == 0:
        print(f"ok ({elapsed:.1f}s, exit=0)")
    else:
        tail = (result.stderr or result.stdout or "")[-_STDERR_TAIL:].strip()
        print(f"FAILED exit={code} ({elapsed:.1f}s)")
        if tail:
            print(f"  stderr tail: {tail}")

    return code


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: sink_exec.py <command>", file=sys.stderr)
        sys.exit(1)
    sys.exit(run_sink(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
