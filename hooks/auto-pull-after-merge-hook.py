#!/usr/bin/env python3
"""
PostToolUse hook: Auto-pull after PR merge.

Watches Bash tool output for gh pr merge success patterns. When detected,
executes git pull --rebase origin main to prevent local/remote drift.

Origin: 4 divergence incidents in one session (2026-03-15/16). PRs #1242,
#1253, #1255, #1246 merged but local main diverged each time. Behavioural
rule existed but was not mechanically enforced.

Edge cases handled:
- Worktree isolation: skip (worktree is disposable)
- Non-main branch: skip (only pull on main)
- Network failure: warn via systemMessage
- Already up to date: silent

Version: 1.0.0
"""

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hook_utils import parse_hook_input, respond, extract_tool_info

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
# The hook log the other Python hooks share (state_paths.HOOK_LOG).
HOOK_LOG = CLAUDE_DIR / "logs" / "hooks.log"

# Patterns that indicate a successful PR merge
MERGE_PATTERNS = [
    re.compile(r"Merged\s+pull request\s+#\d+", re.IGNORECASE),
    re.compile(r"✓\s+Merged\s+pull request\s+#\d+", re.IGNORECASE),
    re.compile(r"Pull request\s+#\d+\s+merged", re.IGNORECASE),
]

# Pattern to extract PR number
PR_NUMBER_PATTERN = re.compile(r"#(\d+)")


def log(level: str, msg: str) -> None:
    """Append to hooks log."""
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)  # logs/ is untracked
        with open(HOOK_LOG, "a") as f:
            f.write(f"{ts} [auto-pull][{level}] {msg}\n")
    except OSError:
        pass


def is_worktree() -> bool:
    """Check if current working directory is a git worktree (not main tree)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5, cwd=str(CLAUDE_DIR)
        )
        common = result.stdout.strip()
        result2 = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, timeout=5, cwd=str(CLAUDE_DIR)
        )
        gitdir = result2.stdout.strip()
        # In a worktree, git-dir != git-common-dir
        return common != gitdir and common != ""
    except (subprocess.TimeoutExpired, OSError):
        return False


def get_current_branch() -> str:
    """Get current branch name."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=str(CLAUDE_DIR)
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return ""


def detect_merge(output: str) -> str:
    """Check if output contains a PR merge success pattern.

    Returns PR number string if detected, empty string otherwise.
    """
    for pattern in MERGE_PATTERNS:
        match = pattern.search(output)
        if match:
            pr_match = PR_NUMBER_PATTERN.search(match.group())
            return pr_match.group(1) if pr_match else "unknown"
    return ""


def do_pull() -> tuple[bool, str]:
    """Execute git pull --rebase origin main.

    Returns (success, message).
    """
    try:
        result = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            capture_output=True, text=True, timeout=30, cwd=str(CLAUDE_DIR)
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return True, output
        return False, output
    except subprocess.TimeoutExpired:
        return False, "git pull timed out after 30s"
    except OSError as e:
        return False, str(e)


def _merged_pr(hook_input) -> str:
    """PR number of a merge reported in Bash output, or empty string."""
    if not hook_input:
        return ""
    tool_name, _tool_input, tool_response = extract_tool_info(hook_input)
    # Only process Bash tool output
    if tool_name != "Bash":
        return ""
    return detect_merge(tool_response)


def _pull_message(pr_number: str, success: bool, output: str) -> str:
    """systemMessage for a pull of main; empty when main was already current."""
    if not success:
        return (
            f"[AUTO-PULL] PR #{pr_number} merged but git pull failed: {output}. "
            f"Run `git pull --rebase origin main` manually."
        )
    if "Already up to date" in output:
        return ""
    return (
        f"[AUTO-PULL] PR #{pr_number} merged. "
        f"Auto-pulled main: {output.splitlines()[0] if output else 'OK'}"
    )


def _pull_after_merge(pr_number: str) -> str:
    """Pull main after a detected merge. Returns the systemMessage, possibly empty."""
    # Edge case: worktree isolation
    if is_worktree():
        log("INFO", "Skipping auto-pull: running in worktree")
        return ""

    # Edge case: not on main
    branch = get_current_branch()
    if branch and branch != "main":
        log("INFO", f"Skipping auto-pull: on branch '{branch}', not main")
        return (
            f"[AUTO-PULL] PR #{pr_number} merged but you're on '{branch}'. "
            f"Run `git pull --rebase origin main` when you switch back to main."
        )

    success, output = do_pull()
    if success:
        log("INFO", f"Auto-pulled after PR #{pr_number} merge: {output}")
    else:
        log("WARN", f"Auto-pull failed after PR #{pr_number}: {output}")
    return _pull_message(pr_number, success, output)


def main() -> None:
    pr_number = _merged_pr(parse_hook_input())
    if not pr_number:
        respond()
        return

    log("INFO", f"PR merge detected: #{pr_number}")
    respond(_pull_after_merge(pr_number))


if __name__ == "__main__":
    main()
