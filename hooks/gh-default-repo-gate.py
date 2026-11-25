#!/usr/bin/env python3
"""
gh Default Repo Gate — PreToolUse Hook
=======================================
Prevents `gh` CLI commands from silently operating against the wrong
GitHub repository when the current repo has multiple GitHub remotes
and `gh repo set-default` resolved to a remote whose owner differs from
origin's owner.

Origin: Gen 510 session (2026-04-15). Two consecutive ultrado sessions
operated against `Charlie85270/Dorothy` (upstream) instead of
`grip-platform/GRIP-GUI` (origin) because `gh repo set-default` had been
interactively resolved to upstream on first use. Symptoms:
  - `gh pr list` returned Dorothy's PRs, not GRIP-GUI's
  - A polite closure comment was posted to the wrong Dorothy PR
  - The misconfig only surfaced when the comment URL showed
    `Charlie85270/Dorothy` instead of the expected origin

Gate logic (single responsibility):
  1. Fire only on Bash commands
  2. Match `gh <subcmd>` where subcmd is one of pr / issue / release /
     repo / api (these are the commands that use default-repo resolution)
  3. Skip if the command uses explicit `-R owner/repo` (user is explicit)
  4. Skip if the command starts with `cd ... && gh ...` (cwd override)
  5. Read `git config remote.origin.url` to get origin owner
  6. Read `git config --get-regexp remote\\..+\\.gh-resolved` to find the
     remote gh resolved to
  7. If resolved remote's owner differs from origin's owner -> DENY
  8. If no gh-resolved set and >=2 GitHub remotes -> DENY (interactive
     prompt will pick a remote non-deterministically)
  9. Anything else -> allow (return silently)

Scope bounds:
  - Does NOT parse shell compound commands beyond the leading cd prefix
  - Does NOT validate that the target repo matches any particular PR
  - Does NOT fire on non-Bash tools
  - Does NOT fire on `gh auth`, `gh config`, `gh extension` etc.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.hook_response import emit, pretool_decision  # noqa: E402

# Subcommands whose default-repo resolution matters
GATED_SUBCMDS = frozenset({"pr", "issue", "release", "repo", "api"})

# Leading `gh <subcmd>` anywhere in the command (inside a pipeline is fine,
# but we match on the first occurrence to keep the gate deterministic)
_GH_RE = re.compile(r'(?:^|\s|&&|\|\||;)\s*gh\s+(\w+)', re.IGNORECASE)

# Explicit `-R owner/repo` flag — bypasses default-repo resolution entirely
_EXPLICIT_R_RE = re.compile(r'(?:^|\s)-R\s+[\w.-]+/[\w.-]+')

# Command starts with `cd <path>` — user is overriding cwd, trust them
_CD_PREFIX_RE = re.compile(r'^\s*cd\s+\S')

# GitHub URL parsers
_GITHUB_SSH_RE = re.compile(r'git@github\.com:([\w.-]+)/([\w.-]+?)(?:\.git)?$')
_GITHUB_HTTPS_RE = re.compile(r'https?://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?$')

# Matches `remote.<name>.gh-resolved base` in git config --get-regexp output
_GH_RESOLVED_RE = re.compile(r'remote\.([^.\s]+)\.gh-resolved\s+base', re.MULTILINE)


def _git(args):
    """Run `git <args>` and return stripped stdout, or None on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def parse_owner_repo(url):
    """Extract (owner, repo) from a GitHub URL in SSH or HTTPS form."""
    if not url:
        return None
    for pattern in (_GITHUB_SSH_RE, _GITHUB_HTTPS_RE):
        match = pattern.match(url.strip())
        if match:
            return (match.group(1), match.group(2))
    return None


def resolve_gh_default():
    """Find which remote gh resolved to, return its (owner, repo) or None."""
    raw = _git(["config", "--get-regexp", r"remote\..+\.gh-resolved"])
    if not raw:
        return None
    match = _GH_RESOLVED_RE.search(raw)
    if not match:
        return None
    remote_name = match.group(1)
    url = _git(["config", f"remote.{remote_name}.url"])
    return parse_owner_repo(url)


def count_github_remotes():
    """Count distinct remotes pointing at github.com."""
    raw = _git(["remote", "-v"])
    if not raw:
        return 0
    seen = set()
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "github.com" in parts[1]:
            seen.add(parts[0])
    return len(seen)


def should_gate(command):
    """Return the gh subcommand if the command should be gated, else None."""
    if not command:
        return None
    if _CD_PREFIX_RE.match(command):
        return None
    if _EXPLICIT_R_RE.search(command):
        return None
    match = _GH_RE.search(command)
    if not match:
        return None
    subcmd = match.group(1).lower()
    if subcmd not in GATED_SUBCMDS:
        return None
    return subcmd


def evaluate(command):
    """Return a deny reason string if the command should be blocked, else None."""
    subcmd = should_gate(command)
    if subcmd is None:
        return None

    origin_url = _git(["config", "remote.origin.url"])
    origin = parse_owner_repo(origin_url)
    if not origin:
        # Not a GitHub origin; gh default resolution is not the concern
        return None

    resolved = resolve_gh_default()

    if resolved is None:
        # No gh-resolved set. If >=2 GitHub remotes, gh will either prompt
        # interactively (breaks non-interactive sessions) or pick arbitrarily.
        if count_github_remotes() >= 2:
            return (
                f"[GRIP] gh has no default repo set and this repo has "
                f"multiple GitHub remotes. `gh {subcmd}` may prompt or "
                f"pick the wrong remote. Fix:\n"
                f"  gh repo set-default {origin[0]}/{origin[1]}"
            )
        return None

    if resolved[0] != origin[0] or resolved[1] != origin[1]:
        return (
            f"[GRIP] gh default repo is {resolved[0]}/{resolved[1]} "
            f"but origin is {origin[0]}/{origin[1]}. `gh {subcmd}` "
            f"would operate on the wrong repository. Fix:\n"
            f"  gh repo set-default {origin[0]}/{origin[1]}"
        )

    return None


def main():
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    if event.get("tool_name") != "Bash":
        return

    command = event.get("tool_input", {}).get("command", "")
    reason = evaluate(command)
    if reason:
        emit(pretool_decision("deny", reason))
        sys.exit(0)


if __name__ == "__main__":
    main()
