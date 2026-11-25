"""HAL Git Operations — git and GitHub abstraction for standalone operation.

Wraps git CLI and gh CLI calls in a clean interface. Used by MCP apps,
context assembler, and session management. No external harness needed.

Usage:
    from lib.hal.git_ops import GitOps

    git = GitOps()
    print(git.branch())
    print(git.log(limit=5))
    print(git.status())
    issues = git.gh_issues(state="open", limit=10)
"""
import json
import subprocess
from typing import Optional


class GitRepo:
    """Local git repository operations (7 methods)."""

    def __init__(self, cwd: Optional[str] = None):
        self._cwd = cwd

    def branch(self) -> str:
        return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], self._cwd)

    def status(self, short: bool = True) -> str:
        args = ["git", "status"] + (["--short"] if short else [])
        return _run(args, self._cwd)

    def log(self, limit: int = 5) -> str:
        return _run(["git", "log", f"-{limit}", "--oneline"], self._cwd)

    def diff(self, staged: bool = False) -> str:
        args = ["git", "diff"] + (["--cached"] if staged else [])
        return _run(args, self._cwd)

    def changed_files(self, base: str = "HEAD~1") -> list[str]:
        raw = _run(["git", "diff", "--name-only", base], self._cwd)
        return [f for f in raw.split("\n") if f.strip()]

    def is_repo(self) -> bool:
        return bool(_run(["git", "rev-parse", "--git-dir"], self._cwd))

    def remote_url(self) -> str:
        return _run(["git", "remote", "get-url", "origin"], self._cwd)


class GitHubOps:
    """GitHub CLI operations (4 methods)."""

    def __init__(self, cwd: Optional[str] = None):
        self._cwd = cwd

    def issues(self, state: str = "open", limit: int = 10, labels: str = "") -> list[dict]:
        args = ["gh", "issue", "list", "--state", state, "--limit", str(limit),
                "--json", "number,title,state,labels,assignees"]
        if labels:
            args.extend(["--label", labels])
        return _run_json(args, self._cwd)

    def prs(self, state: str = "open", limit: int = 10) -> list[dict]:
        return _run_json(["gh", "pr", "list", "--state", state,
                          "--limit", str(limit),
                          "--json", "number,title,state,author,headRefName"], self._cwd)

    def create_pr(self, title: str, body: str, base: str = "main") -> str:
        return _run(["gh", "pr", "create", "--title", title, "--body", body, "--base", base], self._cwd)

    def create_issue(self, title: str, body: str, labels: str = "") -> str:
        args = ["gh", "issue", "create", "--title", title, "--body", body]
        if labels:
            args.extend(["--label", labels])
        return _run(args, self._cwd)


def _run_json(cmd: list[str], cwd: Optional[str] = None) -> list[dict]:
    """Run command and parse JSON output."""
    raw = _run(cmd, cwd)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _run(cmd: list[str], cwd: Optional[str] = None) -> str:
    """Run command, return stdout. Empty string on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, cwd=cwd,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
