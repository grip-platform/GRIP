"""Shared subprocess helpers for GRIP lib modules.

Extracted from knowledge_graph.py, sprint_forecast.py, dependency_health.py,
and pr_impact_analyzer.py to eliminate duplication (DRY).
"""

from __future__ import annotations

import shlex
import subprocess


def run_git(cmd: str) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True,
            timeout=30,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def run_cmd(cmd: str, cwd: str | None = None) -> str:
    """Run any command and return stdout, with optional working directory."""
    try:
        result = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True,
            timeout=60, cwd=cwd,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def count_output_lines(cmd: str) -> int:
    """Run a git command and return the number of output lines."""
    output = run_git(cmd)
    return len(output.splitlines()) if output else 0
