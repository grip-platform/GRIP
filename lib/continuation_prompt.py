#!/usr/bin/env python3
"""
continuation_prompt.py — Generate a continuation prompt for session handoff.

When a session hits the context gate (85%), generates a structured prompt
that can be pasted into a fresh session to resume work seamlessly.

Usage:
    python3 lib/continuation_prompt.py                  # Generate from current state
    python3 lib/continuation_prompt.py --output FILE    # Write to specific file
    python3 lib/continuation_prompt.py --json           # Machine-readable output

Addresses #931: context overflow auto-save with continuation prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_OUTPUT = CLAUDE_DIR / "cache" / "continuation-prompt.md"


def _get_git_state() -> dict:
    """Get current git branch and recent commits."""
    state = {"branch": "unknown", "recent_commits": [], "dirty_files": 0}
    try:
        r = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, timeout=5,
            cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0:
            state["branch"] = r.stdout.strip()

        r = subprocess.run(
            ["git", "log", "--oneline", "-5", "--format=%h %s"],
            capture_output=True, text=True, timeout=5,
            cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0:
            state["recent_commits"] = [
                line.strip() for line in r.stdout.strip().split("\n") if line.strip()
            ]

        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
            cwd=str(CLAUDE_DIR),
        )
        if r.returncode == 0:
            state["dirty_files"] = len([
                l for l in r.stdout.strip().split("\n") if l.strip()
            ])
    except Exception:
        pass
    return state


def _get_open_prs() -> list[str]:
    """Get open PRs created in this session."""
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--author", "@me", "--state", "open",
             "--json", "number,title", "--limit", "5"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            prs = json.loads(r.stdout)
            return [f"#{p['number']}: {p['title']}" for p in prs]
    except Exception:
        pass
    return []


def _get_open_issues() -> list[str]:
    """Get sprint-loop issues for continuation."""
    try:
        r = subprocess.run(
            ["gh", "issue", "list", "--label", "sprint-loop", "--state", "open",
             "--json", "number,title", "--limit", "10"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            issues = json.loads(r.stdout)
            return [f"#{i['number']}: {i['title']}" for i in issues]
    except Exception:
        pass
    return []


def _read_memory_brief() -> str:
    """Read current work focus from MEMORY.md."""
    cwd = os.getcwd()
    encoded = cwd.replace("/", "-").replace(".", "-")
    mem_path = CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md"
    if mem_path.exists():
        content = mem_path.read_text()
        # Extract just the Current Work section
        lines = content.split("\n")
        in_current = False
        current_work = []
        for line in lines:
            if line.startswith("## Current Work"):
                in_current = True
                continue
            if in_current and line.startswith("## ") and not line.startswith("## Current"):
                break
            if in_current:
                current_work.append(line)
        if current_work:
            return "\n".join(current_work).strip()[:500]
    return ""


def generate_prompt(
    completed: list[str] | None = None,
    remaining: list[str] | None = None,
    next_step: str | None = None,
) -> str:
    """Generate a structured continuation prompt.

    Args:
        completed: List of completed items this session.
        remaining: List of remaining items.
        next_step: Specific next action to take.

    Returns:
        Formatted continuation prompt as a string.
    """
    git = _get_git_state()
    prs = _get_open_prs()
    issues = _get_open_issues()
    memory = _read_memory_brief()

    sections = []
    sections.append("Continue the previous session's work. Context below.\n")

    # Git state
    sections.append(f"## Git State")
    sections.append(f"Branch: `{git['branch']}` | Dirty files: {git['dirty_files']}")
    if git["recent_commits"]:
        sections.append("Recent commits:")
        for c in git["recent_commits"]:
            sections.append(f"  - {c}")

    # Open PRs
    if prs:
        sections.append(f"\n## Open PRs")
        for pr in prs:
            sections.append(f"  - {pr}")

    # Sprint issues
    if issues:
        sections.append(f"\n## Sprint Backlog (open)")
        for iss in issues:
            sections.append(f"  - {iss}")

    # Session progress
    if completed:
        sections.append(f"\n## Completed This Session")
        for item in completed:
            sections.append(f"  - {item}")

    if remaining:
        sections.append(f"\n## Remaining")
        for item in remaining:
            sections.append(f"  - {item}")

    # Current work context from MEMORY.md
    if memory:
        sections.append(f"\n## Current Work Context")
        sections.append(memory)

    # Next step
    if next_step:
        sections.append(f"\n## Next Action")
        sections.append(next_step)

    return "\n".join(sections)


def save_prompt(prompt: str, output_path: Path | None = None) -> Path:
    """Save the continuation prompt to a file."""
    path = output_path or DEFAULT_OUTPUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt)
    return path


if __name__ == "__main__":
    as_json = "--json" in sys.argv
    output = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output = Path(sys.argv[idx + 1])

    prompt = generate_prompt()

    if as_json:
        print(json.dumps({"prompt": prompt, "path": str(output or DEFAULT_OUTPUT)}))
    else:
        path = save_prompt(prompt, output)
        print(f"Continuation prompt saved to: {path}")
        print("---")
        print(prompt)
