#!/usr/bin/env python3
"""
pair_intercept.py -- Intercept protocol orchestrator for pair-mode v3.0.

Manages Grip/Guard role transitions, context summaries, and genome breeding
during pair-mode intercepts. Kept separate from session_control_server.py
to maintain single-responsibility (KISS).

Message types added to session control protocol:
  - intercept_request:  Guard requests the Grip role
  - intercept_accepted: Grip accepts the swap
  - intercept_complete: Swap finished, roles updated
  - role_update:        Broadcast new role assignments

Version: 1.0.0
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


class PairRole(Enum):
    """Pair-mode roles."""
    GRIP = "grip"           # Active builder
    GUARD = "guard"         # Navigator/reviewer
    DISCONNECTED = "disconnected"


@dataclass
class InterceptState:
    """Tracks current pair-mode intercept state."""
    local_role: PairRole = PairRole.DISCONNECTED
    remote_role: PairRole = PairRole.DISCONNECTED
    local_operator: str = ""
    remote_operator: str = ""
    intercept_count: int = 0
    session_start: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_intercept: Optional[str] = None

    def swap_roles(self) -> None:
        """Atomically swap local and remote roles."""
        self.local_role, self.remote_role = self.remote_role, self.local_role
        self.intercept_count += 1
        self.last_intercept = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        """Serialise state for protocol messages."""
        d = asdict(self)
        d["local_role"] = self.local_role.value
        d["remote_role"] = self.remote_role.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> InterceptState:
        """Deserialise from protocol message."""
        return cls(
            local_role=PairRole(data.get("local_role", "disconnected")),
            remote_role=PairRole(data.get("remote_role", "disconnected")),
            local_operator=data.get("local_operator", ""),
            remote_operator=data.get("remote_operator", ""),
            intercept_count=data.get("intercept_count", 0),
            session_start=data.get("session_start", ""),
            last_intercept=data.get("last_intercept"),
        )


def generate_context_summary(project_dir: Optional[str] = None) -> dict:
    """Generate a context summary for handoff during intercept.

    Captures current working state so the incoming Grip has full context.

    Returns:
        dict with keys: current_file, git_diff_stat, intent, blockers
    """
    summary = {
        "current_file": "",
        "git_diff_stat": "",
        "intent": "",
        "blockers": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    cwd = project_dir or os.getcwd()

    # Git diff --stat for recent changes
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=10, cwd=cwd,
        )
        if result.returncode == 0 and result.stdout.strip():
            summary["git_diff_stat"] = result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    # Current branch
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5, cwd=cwd,
        )
        if result.returncode == 0:
            summary["current_branch"] = result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        pass

    return summary


def execute_intercept(
    state: InterceptState,
    project_dir: Optional[str] = None,
    breed: bool = True,
    dry_run: bool = False,
) -> dict:
    """Execute the full intercept sequence.

    Steps:
        1. Generate context summary (file, diff, intent, blockers)
        2. Trigger genome breed between operators (directed pairing)
        3. Swap roles atomically

    Args:
        state: Current InterceptState.
        project_dir: Working directory for git operations.
        breed: Whether to run genome breeding during intercept.
        dry_run: If True, return plan without executing.

    Returns:
        dict with intercept results including context summary and breed output.
    """
    result = {
        "status": "pending",
        "context_summary": {},
        "breed_result": None,
        "new_roles": {},
    }

    # Step 1: Context summary
    result["context_summary"] = generate_context_summary(project_dir)

    if dry_run:
        result["status"] = "dry_run"
        result["new_roles"] = {
            "local": state.remote_role.value,
            "remote": state.local_role.value,
        }
        return result

    # Step 2: Genome breed (directed pairing between operators)
    if breed and state.local_operator and state.remote_operator:
        result["breed_result"] = _run_genome_breed(
            state.local_operator, state.remote_operator
        )

    # Step 3: Swap roles
    state.swap_roles()
    result["new_roles"] = {
        "local": state.local_role.value,
        "remote": state.remote_role.value,
    }
    result["intercept_count"] = state.intercept_count
    result["status"] = "complete"

    return result


def _run_genome_breed(operator_a: str, operator_b: str) -> dict:
    """Run directed genome breed between two operators.

    Delegates to genome_breeder.py breed --pair.

    Returns:
        Breed result dict or error dict.
    """
    try:
        breeder_path = CLAUDE_DIR / "lib" / "genome_breeder.py"
        result = subprocess.run(
            [
                sys.executable, str(breeder_path),
                "breed", "--strategy", "directed",
                "--pair", operator_a, operator_b,
                "--force",
            ],
            capture_output=True, text=True, timeout=30,
            cwd=str(CLAUDE_DIR),
        )
        return {
            "status": "bred" if result.returncode == 0 else "error",
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip() if result.returncode != 0 else "",
        }
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        return {"status": "error", "message": str(e)}


def request_intercept(state: InterceptState, urgent: bool = False) -> dict:
    """Create an intercept request message for the session protocol.

    Args:
        state: Current InterceptState.
        urgent: If True, creates an urgent intercept (blocks Grip immediately).

    Returns:
        Protocol message dict for session_control_server.
    """
    return {
        "type": "intercept_request",
        "requester": state.local_operator,
        "requester_role": state.local_role.value,
        "urgent": urgent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def accept_intercept(state: InterceptState) -> dict:
    """Create an intercept acceptance message for the session protocol.

    Args:
        state: Current InterceptState.

    Returns:
        Protocol message dict for session_control_server.
    """
    return {
        "type": "intercept_accepted",
        "accepter": state.local_operator,
        "accepter_role": state.local_role.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def make_role_update(state: InterceptState) -> dict:
    """Create a role update broadcast message after intercept completes.

    Args:
        state: Current InterceptState (post-swap).

    Returns:
        Protocol message dict for broadcasting to all monitors.
    """
    return {
        "type": "role_update",
        "local_operator": state.local_operator,
        "local_role": state.local_role.value,
        "remote_operator": state.remote_operator,
        "remote_role": state.remote_role.value,
        "intercept_count": state.intercept_count,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
