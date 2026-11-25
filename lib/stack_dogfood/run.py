"""Subprocess runner for the stack-dogfood probes and installers.

One small, total helper: never raises for a missing binary or a timeout, so a
product adapter can probe a harness that is not installed without a try/except
at every call site. A missing executable is exit 127; a timeout is exit 124.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

TIMEOUT_MISSING = 127
TIMEOUT_EXPIRED = 124


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0


def run(argv, *, timeout: float = 20.0, input_text: str = "",
        cwd: str | None = None) -> Result:
    """Run ``argv``, capturing text output. Total — never raises on the common
    failures. ``input_text`` is written to stdin (``""`` sends EOF). ``cwd`` is
    the folder the command runs in; ``None`` keeps the caller's."""
    args = [str(a) for a in argv]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=input_text,
            cwd=cwd,
        )
    except FileNotFoundError:
        return Result(tuple(args), TIMEOUT_MISSING, "", "executable not found")
    except subprocess.TimeoutExpired:
        return Result(tuple(args), TIMEOUT_EXPIRED, "", f"timed out after {timeout}s")
    return Result(tuple(args), proc.returncode, proc.stdout or "", proc.stderr or "")
