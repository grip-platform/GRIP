"""Unified subprocess wrappers for git and gh CLI commands.

Consolidates 16 duplicated _run_git/_run_gh helpers across the codebase
into two standardized interfaces: simple (str return) and structured (dict return).

Also provides run_with_kill() for long-running processes that may hang —
sends SIGKILL after a grace period if SIGTERM fails (#782).

Usage (standalone scripts):
    from lib.subprocess_utils import run_git, run_gh
    output = run_git(["log", "--oneline", "-5"])
    issues = run_gh(["issue", "list", "--json", "number,title"])

Usage (class methods needing structured results):
    from lib.subprocess_utils import run_git_structured, run_gh_structured
    result = run_git_structured(["status", "--short"], cwd="/path/to/repo")
    if result["ok"]:
        print(result["output"])

Usage (processes that may hang):
    from lib.subprocess_utils import run_with_kill
    result = run_with_kill(["python3", "long_script.py"], timeout=30)
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


def run_git(
    args: List[str],
    timeout: int = 15,
    cwd: Optional[str] = None,
) -> str:
    """Run a git command and return stdout, or empty string on failure.

    Args:
        args: Git subcommand and arguments (e.g. ["log", "--oneline"])
        timeout: Seconds before timeout (default 15)
        cwd: Working directory (default: CLAUDE_DIR)
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or str(CLAUDE_DIR),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def run_gh(
    args: List[str],
    timeout: int = 15,
) -> str:
    """Run a gh CLI command and return stdout, or empty string on failure.

    Args:
        args: gh subcommand and arguments (e.g. ["issue", "list"])
        timeout: Seconds before timeout (default 15)
    """
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def run_git_structured(
    args: List[str],
    timeout: int = 15,
    cwd: Optional[str] = None,
) -> Dict[str, str]:
    """Run a git command and return structured result dict.

    Returns:
        {"ok": True/False, "output": stdout, "stderr": stderr[:500]}
        On timeout/missing: {"ok": False, "error": description}
    """
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or str(CLAUDE_DIR),
        )
        return {
            "ok": result.returncode == 0,
            "output": result.stdout.strip(),
            "stderr": result.stderr.strip()[:500],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Git command timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "git not found"}


def run_gh_structured(
    args: List[str],
    timeout: int = 15,
) -> Dict[str, str]:
    """Run a gh CLI command and return structured result dict.

    Returns:
        {"ok": True/False, "output": stdout, "stderr": stderr[:500]}
        On timeout/missing: {"ok": False, "error": description}
    """
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "output": result.stdout.strip(),
            "stderr": result.stderr.strip()[:500],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "gh command timed out"}
    except FileNotFoundError:
        return {"ok": False, "error": "gh not found"}


def cmd_exists(cmd: str) -> bool:
    """Check if a command exists on PATH (cross-platform)."""
    return shutil.which(cmd) is not None


def run_with_kill(
    cmd: List[str],
    timeout: int = 30,
    kill_grace: int = 5,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Run a command with timeout and SIGKILL fallback (#782).

    If the process doesn't complete within `timeout` seconds:
    1. Send SIGTERM (polite shutdown request)
    2. Wait `kill_grace` seconds for graceful exit
    3. Send SIGKILL if still alive (forced termination)

    Cross-platform: process.kill() maps to TerminateProcess on Windows
    and SIGKILL on Unix.

    Args:
        cmd: Command and arguments to run.
        timeout: Seconds before SIGTERM (default 30).
        kill_grace: Seconds between SIGTERM and SIGKILL (default 5).
        cwd: Working directory.
        env: Environment variables (inherits current env if None).

    Returns:
        {"ok": bool, "output": stdout, "stderr": stderr[:500],
         "killed": bool, "returncode": int}
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            env=env,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"Command not found: {cmd[0]}", "killed": False}

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "output": stdout.strip(),
            "stderr": stderr.strip()[:500],
            "killed": False,
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        # Phase 1: SIGTERM
        logger.warning("Process %s timed out after %ds, sending SIGTERM", cmd[0], timeout)
        proc.terminate()

        try:
            stdout, stderr = proc.communicate(timeout=kill_grace)
            logger.info("Process %s terminated gracefully after SIGTERM", cmd[0])
            return {
                "ok": False,
                "output": (stdout or "").strip(),
                "stderr": (stderr or "").strip()[:500],
                "killed": False,
                "returncode": proc.returncode,
                "error": f"Timed out after {timeout}s, terminated via SIGTERM",
            }
        except subprocess.TimeoutExpired:
            # Phase 2: SIGKILL
            logger.warning(
                "Process %s ignored SIGTERM for %ds, sending SIGKILL",
                cmd[0], kill_grace,
            )
            proc.kill()
            stdout, stderr = proc.communicate()
            return {
                "ok": False,
                "output": (stdout or "").strip(),
                "stderr": (stderr or "").strip()[:500],
                "killed": True,
                "returncode": proc.returncode,
                "error": f"Timed out after {timeout}s, killed via SIGKILL after {kill_grace}s grace",
            }
