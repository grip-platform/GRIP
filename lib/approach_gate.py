#!/usr/bin/env python3
"""
approach_gate.py -- Approach verification gate for multi-step tasks (#141).

Determines whether a task should trigger approach verification before
implementation. Called by the agent or hooks to prevent wrong-approach errors.

Usage:
    from lib.approach_gate import should_verify, ApproachContext

    ctx = ApproachContext(
        task="Refactor authentication system",
        estimated_files=5,
        has_alternatives=True,
    )
    if should_verify(ctx):
        # Present alternatives via AskUserQuestion
        ...
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"


@dataclass
class ApproachContext:
    """Context for approach verification decision."""
    task: str = ""
    estimated_files: int = 0
    has_alternatives: bool = False
    user_rejected_last: bool = False
    is_autonomous: bool = False
    is_trivial: bool = False
    user_override: bool = False

    @property
    def should_verify(self) -> bool:
        """Determine if approach verification should trigger."""
        # Skip conditions
        if self.is_trivial:
            return False
        if self.user_override:
            return False
        if self.is_autonomous and not self.has_alternatives and self.estimated_files < 3:
            return False

        # Trigger conditions (any one sufficient)
        if self.estimated_files >= 3:
            return True
        if self.has_alternatives and self.estimated_files >= 2:
            return True
        if self.user_rejected_last:
            return True

        return False


def should_verify(ctx: ApproachContext) -> bool:
    """Check if approach verification should trigger for this context."""
    return ctx.should_verify


@dataclass
class ApproachOption:
    """A proposed implementation approach."""
    name: str
    description: str
    files: list[str] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    confidence: float = 0.5  # 0.0-1.0


def select_autonomous(options: list[ApproachOption]) -> ApproachOption | None:
    """In autonomous mode, select the highest-confidence approach."""
    if not options:
        return None
    return max(options, key=lambda o: o.confidence)


def main() -> None:
    """CLI: evaluate approach gate for a task description."""
    import sys
    if len(sys.argv) < 2:
        print("Usage: approach_gate.py <task> [--files N] [--alternatives] [--autonomous]")
        sys.exit(1)

    task = sys.argv[1]
    files = 1
    alternatives = False
    autonomous = False

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--files" and i + 1 < len(sys.argv):
            files = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--alternatives":
            alternatives = True
            i += 1
        elif sys.argv[i] == "--autonomous":
            autonomous = True
            i += 1
        else:
            i += 1

    ctx = ApproachContext(
        task=task,
        estimated_files=files,
        has_alternatives=alternatives,
        is_autonomous=autonomous,
    )

    if ctx.should_verify:
        print(f"[APPROACH GATE] Verification REQUIRED for: {task}")
        print(f"  Estimated files: {files}")
        print(f"  Alternatives exist: {alternatives}")
        print(f"  Mode: {'autonomous' if autonomous else 'interactive'}")
        sys.exit(1)
    else:
        print(f"[APPROACH GATE] Skip verification for: {task}")
        sys.exit(0)


if __name__ == "__main__":
    main()
