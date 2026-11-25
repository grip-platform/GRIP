"""
grip-anywhere — Recursive Self-Improvement, turned outward.

Renamed from lib.mirror to avoid name collision with another GRIP MIRROR.
The legacy import path lib.mirror remains as a deprecation shim until 2026-08-01.

Every other AI coding tool is stateless at the repository level.
grip-anywhere is proactive and persistent: it wakes up every day and
continues improving a target codebase from where it left off yesterday,
without any human prompting.

The engine: converge(repo_state, quality_gate, improve_repo)

Components:
    analyzer.py   — Repository health analysis via rg + gh CLI
    runner.py     — Orchestrates convergence on a target repository

Quick start:
    from lib.grip_anywhere import GripAnywhere
    grip_anywhere = GripAnywhere("owner/repo")
    result = grip_anywhere.run(max_depth=3)
    print(result.summary())

The legacy class names Mirror / MirrorState remain importable as
deprecation aliases via lib.mirror until 2026-08-01.
"""

from lib.grip_anywhere.analyzer import RepoHealthDimensions
from lib.grip_anywhere.runner import GripAnywhere, GripAnywhereState, CONVERGENCE_WAVE_MAP

# Backward-compat aliases — the public symbol was renamed Mirror -> GripAnywhere.
# Kept on the new module (not only the lib.mirror shim) so any direct
# `from lib.grip_anywhere import Mirror` import also resolves.
Mirror = GripAnywhere
MirrorState = GripAnywhereState

__all__ = [
    "GripAnywhere",
    "GripAnywhereState",
    "RepoHealthDimensions",
    "CONVERGENCE_WAVE_MAP",
    # legacy aliases (deprecated)
    "Mirror",
    "MirrorState",
]
