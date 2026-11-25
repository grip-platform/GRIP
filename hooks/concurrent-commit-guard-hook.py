#!/usr/bin/env python3
"""PreToolUse hook: concurrent-session commit-guard (WARN-only).

Mechanical enforcement for the recurring friction class captured in
``memory/feedback_concurrent_session_shared_tree_commit_protocol.md``:
under >=2 concurrent GRIP ``claude`` sessions on the shared git-crypt
``~/.claude`` tree, a *different* session can switch ``.git/HEAD`` between
this session's tool calls. A subsequent ``git commit`` then lands on an
unintended branch (the 2026-05-15 e485f711 / PR #2687 incident, recurring
51x in learning/insights.jsonl).

Gate O (context-gate) blocks *this* session's ``git checkout`` — it cannot
block another session's. This hook closes that gap by re-reading the LIVE
HEAD branch in the same instant the commit is about to run and surfacing a
plain-language WARN when the topology is collision-prone and HEAD is not on
a deliberate working branch (``main`` or a ``feat/``/``fix/`` branch).

WARN-only by construction: a false positive is one ignorable advisory
line, never a blocked commit. Fail-open: any parse/import/subprocess error
exits 0 with an empty decision. An advisory guard must never break a commit.

Doctrine: rules/efficiency-rules.md Rule 13 (worktree-first isolation),
memory feedback_concurrent_session_shared_tree_commit_protocol.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

_CLAUDE_DIR = os.path.join(os.environ.get("HOME", "/tmp"), ".claude")
# A commit is "intent-safe" on main or a conventional working branch the
# operator/agent deliberately created. Anything else under multi-session is
# the concurrent-switch shape worth a heads-up.
_SAFE_BRANCH_RE = re.compile(r"^(main|master|feat/|fix/|chore/|hotfix/|release/)")
_COMMIT_RE = re.compile(r"\bgit\b[^|&;]*\bcommit\b")


def _parse_stdin() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _is_claude_dir_commit(command: str) -> bool:
    """True if the Bash command commits in the shared ~/.claude tree."""
    if not _COMMIT_RE.search(command):
        return False
    # Explicit -C elsewhere => not the shared tree we guard.
    m = re.search(r"git\s+-C\s+(\S+)", command)
    if m:
        target = os.path.expanduser(m.group(1).strip("\"'"))
        return os.path.realpath(target) == os.path.realpath(_CLAUDE_DIR)
    # No -C: governed by cwd. The hook runs with the session's cwd; treat a
    # commit as ~/.claude-relevant only when cwd resolves under it.
    try:
        return os.path.realpath(os.getcwd()).startswith(
            os.path.realpath(_CLAUDE_DIR)
        )
    except OSError:
        return False


def _live_branch() -> str:
    try:
        r = subprocess.run(
            ["git", "-C", _CLAUDE_DIR, "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _session_count() -> int:
    """Reuse the canonical detector; fail safe to 1 (no escalation)."""
    try:
        sys.path.insert(0, _CLAUDE_DIR)
        from lib.gates.session_isolation import count_claude_sessions
        return count_claude_sessions()
    except Exception:
        return 1


def main() -> int:
    try:
        data = _parse_stdin()
        if data.get("tool_name") != "Bash":
            print("{}")
            return 0

        command = str(data.get("tool_input", {}).get("command", ""))
        if not _is_claude_dir_commit(command):
            print("{}")
            return 0

        if _session_count() < 2:
            print("{}")
            return 0

        branch = _live_branch()
        if branch and not _SAFE_BRANCH_RE.match(branch):
            sys.stderr.write(
                "[CONCURRENT_COMMIT_GUARD] "
                f"{_session_count()} concurrent claude sessions and the shared "
                f"~/.claude HEAD is on '{branch}' (not main / not a feat|fix "
                "branch you created). A parallel session may have switched it "
                "between your tool calls. Apply the verify-at-commit protocol: "
                "re-check HEAD in THIS same Bash call before committing, then "
                "pin the SHA to a dedicated feat/<thing>-<date> branch and "
                "push immediately (confirm via git ls-remote origin). See "
                "memory feedback_concurrent_session_shared_tree_commit_protocol.\n"
            )
    except Exception:
        # Fail-open: an advisory guard must never break a commit.
        pass

    print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
