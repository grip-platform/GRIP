"""stack_dogfood — dogfood the CodeTonight AI suite onto itself.

The four products of the stack (GRASP, HAPPI, HAL, GRIP) are installed into
every harness and then used, inside those harnesses, to improve all four. This
package is the shared framework; one small adapter per product lives under
``products/``.

Installing the stack is an idempotent fold: ``model.InstallDomain`` plugs into
``lib/dogfood_kernel.py`` so a full install is a provable fixed point and a
re-install is a provable no-op (Banach criterion, generic_proof).

Pure stdlib. No network at import. The overnight loop never publishes; only a
human-run or runner-run step pushes, and only after the leak sweep and tests
pass (see ``loop.py``).
"""
from __future__ import annotations

from .model import (
    Cell,
    Census,
    Check,
    Finding,
    InstallDomain,
    Report,
    Status,
)
from .run import Result, run

VERSION = "0.1.0"

__all__ = [
    "VERSION",
    "Cell",
    "Census",
    "Check",
    "Finding",
    "InstallDomain",
    "Report",
    "Status",
    "Result",
    "run",
]
