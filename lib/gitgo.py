#!/usr/bin/env python3
"""gitgo — The ONE git command to rule them all.

Fast diagnostic: uncommitted work, unpushed commits, PR candidates,
broken state, suboptimal config, recoverable work. Target: < 2 seconds.
"""

import subprocess
import sys
from pathlib import Path

PROTECTED_BRANCHES = {"main", "master", "develop", "production", "staging"}


def run(cmd, timeout=10):
    """Run command, return (stdout, returncode)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", 1


def count(text):
    """Count non-empty lines."""
    return len([l for l in text.split("\n") if l.strip()]) if text else 0


# -- Checks ---------------------------------------------------------------


def check_state():
    """Broken state: merge/rebase in progress, detached HEAD, stale lock."""
    issues = []
    git_dir_out, _ = run(["git", "rev-parse", "--git-dir"])
    gd = Path(git_dir_out) if git_dir_out else Path(".git")

    branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if branch == "HEAD":
        issues.append(("CRITICAL", "Detached HEAD — commits will be lost",
                        "git checkout -b <branch-name>"))

    if (gd / "MERGE_HEAD").exists():
        issues.append(("CRITICAL", "Merge in progress",
                        "git merge --continue  OR  git merge --abort"))

    if (gd / "rebase-merge").exists() or (gd / "rebase-apply").exists():
        issues.append(("CRITICAL", "Rebase in progress",
                        "git rebase --continue  OR  git rebase --abort"))

    if (gd / "CHERRY_PICK_HEAD").exists():
        issues.append(("CRITICAL", "Cherry-pick in progress",
                        "git cherry-pick --continue  OR  --abort"))

    lock = gd / "index.lock"
    if lock.exists():
        issues.append(("CRITICAL", "Stale index.lock — blocking all git ops",
                        f"rm {lock}"))

    return issues


def check_uncommitted():
    """Staged not committed, modified not staged, untracked."""
    issues = []
    branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    protected = branch in PROTECTED_BRANCHES

    # Staged but not committed
    out, _ = run(["git", "diff", "--cached", "--name-only"])
    if out:
        n = count(out)
        if protected:
            issues.append(("CRITICAL",
                           f"{n} file(s) staged on protected branch '{branch}'",
                           f"git checkout -b feat/<name> && git commit"))
        else:
            issues.append(("CRITICAL",
                           f"{n} file(s) staged but NOT committed",
                           "git commit -m '...'"))

    # Modified but not staged
    out, _ = run(["git", "diff", "--name-only"])
    if out:
        n = count(out)
        if protected and n >= 5:
            issues.append(("WARNING",
                           f"{n} modified file(s) on '{branch}' — consider a feature branch",
                           f"git checkout -b feat/<name> && git add -A && git commit"))
        else:
            issues.append(("WARNING",
                           f"{n} file(s) modified but not staged",
                           "git add <files>"))

    # Untracked
    out, _ = run(["git", "ls-files", "--others", "--exclude-standard"])
    if out:
        n = count(out)
        if n > 0:
            issues.append(("INFO", f"{n} untracked file(s)",
                           "git add <files>  OR  update .gitignore"))

    return issues


def check_unpushed():
    """Local commits not pushed. Recommends PR for large changesets."""
    issues = []
    branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        return issues

    remote, rc = run(["git", "config", f"branch.{branch}.remote"])
    if rc != 0:
        issues.append(("WARNING", f"'{branch}' has no upstream tracking",
                        f"git push -u origin {branch}"))
        return issues

    out, _ = run(["git", "log", f"{remote}/{branch}..HEAD", "--oneline"])
    if not out:
        return issues

    commits = count(out)
    # Estimate total lines changed for PR detection
    stat, _ = run(["git", "diff", f"{remote}/{branch}..HEAD", "--shortstat"])
    lines = 0
    if stat:
        for part in stat.split(","):
            part = part.strip()
            if "insertion" in part or "deletion" in part:
                lines += int(part.split()[0])

    if commits >= 5 or lines >= 300:
        issues.append(("WARNING",
                       f"{commits} unpushed commit(s), ~{lines} lines — PR recommended",
                       "gh pr create"))
    else:
        issues.append(("INFO",
                       f"{commits} unpushed commit(s)",
                       f"git push origin {branch}"))

    return issues


def check_divergence():
    """Branch behind remote."""
    issues = []
    branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        return issues

    remote, rc = run(["git", "config", f"branch.{branch}.remote"])
    if rc != 0:
        return issues

    out, _ = run(["git", "log", f"HEAD..{remote}/{branch}", "--oneline"])
    if out:
        behind = count(out)
        if behind > 0:
            issues.append(("WARNING",
                           f"{behind} commit(s) behind {remote}/{branch}",
                           "git pull --rebase"))

    return issues


def check_stashes():
    """Stashed work that might be forgotten."""
    issues = []
    out, _ = run(["git", "stash", "list"])
    if out:
        n = count(out)
        word = "entry" if n == 1 else "entries"
        issues.append(("INFO", f"{n} stash {word}",
                       "git stash list  ->  git stash pop"))
    return issues


def check_config():
    """Suboptimal git configuration."""
    issues = []
    out, rc = run(["git", "config", "fetch.prune"])
    if rc != 0 or out != "true":
        issues.append(("INFO", "fetch.prune not enabled (stale refs accumulate)",
                       "git config fetch.prune true"))
    return issues


def check_recoverable():
    """Lost work detectable via reflog."""
    issues = []
    out, _ = run(["git", "reflog", "--format=%gs", "-n", "20"])
    if out:
        resets = sum(1 for l in out.split("\n") if l.startswith("reset:"))
        if resets >= 2:
            issues.append(("INFO",
                           f"{resets} recent reset(s) — work may be recoverable",
                           "git reflog  ->  git cherry-pick <hash>"))
    return issues


# Files that should typically be gitignored
SHOULD_GITIGNORE = [
    ".env", ".env.local", ".env.production",
    "*.pem", "*.key", "id_rsa", "id_ed25519",
    "node_modules/", ".next/", "dist/", "build/",
    "__pycache__/", "*.pyc", "venv/", ".venv/",
    "*.sqlite", "*.db",
]

# Patterns indicating secrets in staged content
SECRET_PATTERNS = [
    "PRIVATE KEY", "BEGIN RSA", "BEGIN EC PRIVATE",
    "AKIA",  # AWS access key prefix
    "sk-",   # OpenAI/Anthropic key prefix
    "ghp_", "ghs_", "gho_",  # GitHub tokens
    "xoxb-", "xoxp-",  # Slack tokens
]


def check_gitignore():
    """Check for common files that should be gitignored but are tracked."""
    issues = []
    toplevel, _ = run(["git", "rev-parse", "--show-toplevel"])
    if not toplevel:
        return issues

    root = Path(toplevel)
    missing = []
    for pattern in SHOULD_GITIGNORE:
        if pattern.endswith("/"):
            dirname = pattern.rstrip("/")
            target = root / dirname
            if target.is_dir():
                # Check if it's tracked (not gitignored)
                out, rc = run(["git", "check-ignore", "-q", str(target)])
                if rc != 0:  # NOT ignored
                    missing.append(dirname)
        elif "*" not in pattern:
            target = root / pattern
            if target.exists():
                out, rc = run(["git", "check-ignore", "-q", str(target)])
                if rc != 0:
                    missing.append(pattern)

    if missing:
        issues.append(("WARNING",
                       f"{len(missing)} file(s) should be gitignored: {', '.join(missing[:5])}",
                       "echo '<pattern>' >> .gitignore"))
    return issues


def check_staged_secrets():
    """Scan staged diff for accidental secret exposure."""
    issues = []
    out, _ = run(["git", "diff", "--cached"])
    if not out:
        return issues

    found = []
    for line in out.split("\n"):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern in line:
                # Truncate for display, don't leak the actual secret
                snippet = line[1:40].strip()
                found.append(f"'{pattern}' near: {snippet}...")
                break

    if found:
        issues.append(("CRITICAL",
                       f"{len(found)} potential secret(s) in staged changes",
                       "git reset HEAD <file>  — review before committing"))
    return issues


# -- Output ----------------------------------------------------------------

USE_COLOUR = sys.stdout.isatty()
C = {
    "CRITICAL": "\033[1;31m", "WARNING": "\033[1;33m",
    "INFO": "\033[0;36m", "OK": "\033[0;32m",
    "BOLD": "\033[1m", "DIM": "\033[2m", "R": "\033[0m",
}


def styled(tag, text):
    return f"{C.get(tag, '')}{text}{C['R']}" if USE_COLOUR else text


OK_LABELS = {
    "check_state": "No broken state",
    "check_uncommitted": "Working tree clean",
    "check_unpushed": "All pushed",
    "check_divergence": "Up to date with remote",
    "check_stashes": "No stashes",
    "check_config": "Config optimal",
    "check_recoverable": "No lost work detected",
    "check_gitignore": "No gitignore gaps",
    "check_staged_secrets": "No secrets in staged changes",
}

ALL_CHECKS = [
    check_state, check_staged_secrets, check_uncommitted, check_unpushed,
    check_divergence, check_stashes, check_gitignore, check_config,
    check_recoverable,
]


def _reject_stray_argv() -> None:
    """Loud, never silent: /gitgo takes no arguments, and an operator who
    typed some deserves to know they reached nothing (wiring-perimeter)."""
    if len(sys.argv) > 1:
        print(f"gitgo: takes no arguments (ignored: {' '.join(sys.argv[1:])})",
              file=sys.stderr)


def main():
    _reject_stray_argv()
    _, rc = run(["git", "rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        print("Not a git repository.")
        sys.exit(1)

    toplevel, _ = run(["git", "rev-parse", "--show-toplevel"])
    repo = Path(toplevel).name if toplevel else "unknown"
    branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])

    print(styled("BOLD", f"\n{'=' * 50}"))
    print(styled("BOLD", f"  GITGO  {repo} ({branch})"))
    print(styled("BOLD", f"{'=' * 50}\n"))

    all_issues = []

    for fn in ALL_CHECKS:
        try:
            issues = fn()
            if issues:
                all_issues.extend(issues)
                for level, msg, fix in issues:
                    pad = " " * max(1, 10 - len(level))
                    print(f"  {styled(level, f'[{level}]')}{pad}{msg}")
                    print(f"              {styled('DIM', f'-> {fix}')}")
            else:
                print(f"  {styled('OK', '[OK]')}       {OK_LABELS.get(fn.__name__, 'OK')}")
        except Exception as e:
            print(f"  {styled('WARNING', '[SKIP]')}     {fn.__name__}: {e}")

    crit = sum(1 for l, _, _ in all_issues if l == "CRITICAL")
    warn = sum(1 for l, _, _ in all_issues if l == "WARNING")
    info = sum(1 for l, _, _ in all_issues if l == "INFO")

    print(styled("BOLD", f"\n{'-' * 50}"))
    if crit:
        print(styled("CRITICAL", f"  {crit} critical | {warn} warning(s) | {info} info"))
    elif warn:
        print(styled("WARNING", f"  {warn} warning(s) | {info} info"))
    elif info:
        print(styled("INFO", f"  {info} info item(s)"))
    else:
        print(styled("OK", "  All clear! Repository is healthy."))
    print(styled("BOLD", f"{'-' * 50}\n"))

    sys.exit(1 if crit else 0)


if __name__ == "__main__":
    main()
