#!/usr/bin/env python3
"""
grip_closeout.py -- Session-end automation orchestrator.

Automates the session-end checklist (12 steps):
1. Git status check
2. Commit staged changes (conventional commit, no AI attribution)
3. Push to current branch (halts on protected branches)
4. Save all GRIP memory subsystems (via grip_save.py)
5. Sync session context for peer sessions (via grip_session_sync.py)
6. Calibrate friction patterns (via evo.py)
7. Run full evolution pipeline (via evolve.py)
8. Exit review council — gated on session complexity (via exit_review.py)
9. Release RSI lock if held
10. Stop caffeinate if running
11. List outstanding TODOs
12. Post Slack summary (optional)

Usage:
    python3 lib/grip_closeout.py                         # Auto closeout
    python3 lib/grip_closeout.py "finished auth refactor" # With achievement
    python3 lib/grip_closeout.py --no-push               # Skip push
    python3 lib/grip_closeout.py --slack C0EXAMPLE01     # Post to Slack
    python3 lib/grip_closeout.py --from-exit              # Called from /exit hook
    python3 lib/grip_closeout.py --no-evolve              # Skip evo/evolve steps

Called by: /closeout command, /exit Stop hook
Issue: https://github.com/grip-platform/GRIP/issues/779
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

PROTECTED_BRANCHES = {"main", "master", "production", "release"}

# Cross-platform venv resolution (follows _compat.py pattern)
if sys.platform == "win32":
    _venv_candidates = [
        CLAUDE_DIR / "venv" / "Scripts" / "python.exe",
        CLAUDE_DIR / "venv" / "Scripts" / "python3.exe",
    ]
else:
    _venv_candidates = [CLAUDE_DIR / "venv" / "bin" / "python3"]

GRIP_PYTHON = "python3"  # fallback
for _candidate in _venv_candidates:
    if _candidate.is_file():
        GRIP_PYTHON = str(_candidate)
        break

# Regex to strip emoji from commit messages (commit-standards.md: no emojis).
# Wide Unicode ranges are intentional — we strip ALL emoji categories to keep
# commit messages plain-text. CodeQL py/overly-permissive-regex suppressed.
_EMOJI_RE = re.compile(  # codeql[py/overly-permissive-regex-range] Intentionally broad: strips all emoji from commit messages per commit-standards.md
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "]+",
    flags=re.UNICODE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 30, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess with timeout. Returns CompletedProcess."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd or str(CLAUDE_DIR),
    )


def _step_result(name: str, ok: bool, message: str) -> dict:
    """Build a step result dict."""
    return {"step": name, "ok": ok, "message": message}


def _sanitize_commit_message(msg: str) -> str:
    """Strip emojis and truncate commit messages (commit-standards.md compliance).

    Matches session-end.sh sanitization: strip artifacts, hard truncate at 80 chars.
    """
    msg = _EMOJI_RE.sub("", msg)
    # Strip table/JSON artifacts (matches session-end.sh pattern)
    msg = re.sub(r'[|"(){}\[\]].*$', "", msg)
    msg = msg.strip()
    return msg[:80]


# ---------------------------------------------------------------------------
# Step 1: Git status check
# ---------------------------------------------------------------------------

def check_git_status() -> tuple[bool, list[str]]:
    """Check for uncommitted changes. Returns (has_changes, changed_files)."""
    try:
        r = _run(["git", "status", "--porcelain"])
        if r.returncode != 0:
            return False, []
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return len(lines) > 0, lines
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, []


def get_current_branch() -> str:
    """Get the current git branch name."""
    try:
        r = _run(["git", "branch", "--show-current"])
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def is_protected_branch(branch: str) -> bool:
    """Check if a branch is protected."""
    return branch.lower() in PROTECTED_BRANCHES


# ---------------------------------------------------------------------------
# Step 2: Commit changes
# ---------------------------------------------------------------------------

def detect_commit_type(changed_files: list[str]) -> str:
    """Auto-detect conventional commit type from changed files."""
    file_text = " ".join(changed_files).lower()

    if "test" in file_text:
        return "test"
    if "readme" in file_text or "doc" in file_text or "changelog" in file_text:
        return "docs"
    if ".md" in file_text and "skill" in file_text:
        return "feat"
    if "fix" in file_text:
        return "fix"

    # Default to chore for mixed/session-end changes
    return "chore"


def check_staged_files() -> list[str]:
    """Check for already-staged files (git index). Returns list of staged files."""
    try:
        r = _run(["git", "diff", "--cached", "--name-status"])
        if r.returncode != 0:
            return []
        return [l for l in r.stdout.strip().splitlines() if l.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _stage_changes() -> tuple[bool, list[str]]:
    """Stage unstaged changes. Returns (ok, changed_files)."""
    has_changes, changed_files = check_git_status()
    if not has_changes:
        return False, []
    try:
        r = _run(["git", "add", "-A"])
        if r.returncode != 0:
            raise OSError(f"git add failed: {r.stderr.strip()[:80]}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise OSError(f"git add error: {e}") from e
    return True, changed_files


def _build_commit_msg(changed_files: list[str], achievement: str | None) -> str:
    """Build a conventional commit message."""
    commit_type = detect_commit_type(changed_files)
    if achievement:
        return f"{commit_type}: {achievement}"
    n = len(changed_files)
    return f"{commit_type}: session closeout ({n} file{'s' if n != 1 else ''} changed)"


def commit_changes(achievement: str | None = None) -> dict:
    """Commit changes with a conventional commit message.

    Staging behavior:
    - If files are already staged: commit only those (respects user intent)
    - If nothing staged but tree is dirty: stage all with git add -A, then commit
    - If clean: skip
    """
    if achievement and not achievement.strip():
        achievement = None
    if achievement:
        achievement = _sanitize_commit_message(achievement) or None

    staged_files = check_staged_files()
    if staged_files:
        changed_files = staged_files
    else:
        try:
            ok, changed_files = _stage_changes()
        except OSError as e:
            return _step_result("commit", False, str(e))
        if not ok:
            return _step_result("commit", True, "Working tree clean, nothing to commit")

    commit_msg = _build_commit_msg(changed_files, achievement)
    try:
        r = _run(["git", "commit", "-m", commit_msg])
        if r.returncode != 0:
            stderr = r.stderr.strip()
            if "nothing to commit" in stderr:
                return _step_result("commit", True, "Nothing to commit after staging")
            return _step_result("commit", False, f"git commit failed: {stderr[:80]}")
        return _step_result("commit", True, f"Committed: {commit_msg}")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return _step_result("commit", False, f"git commit error: {e}")


# ---------------------------------------------------------------------------
# Step 3: Push to branch
# ---------------------------------------------------------------------------

def push_branch(skip: bool = False) -> dict:
    """Push to the current branch remote."""
    if skip:
        return _step_result("push", True, "Push skipped (--no-push)")

    branch = get_current_branch()
    if not branch:
        return _step_result("push", False, "Could not determine current branch")

    if is_protected_branch(branch):
        return _step_result("push", False,
                            f"HALT: Branch '{branch}' is protected. "
                            "Push manually or confirm via AskUserQuestion.")

    # Check if remote exists
    try:
        r = _run(["git", "remote", "-v"])
        if r.returncode != 0 or not r.stdout.strip():
            return _step_result("push", False, "No git remote configured")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return _step_result("push", False, "Could not check git remote")

    # Check if upstream is set
    try:
        r = _run(["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
        has_upstream = r.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        has_upstream = False

    push_cmd = ["git", "push"]
    if not has_upstream:
        push_cmd = ["git", "push", "-u", "origin", branch]

    try:
        r = _run(push_cmd, timeout=120)
        if r.returncode == 0:
            return _step_result("push", True, f"Pushed to {branch}")
        # Non-zero returncode -- check for known benign messages
        stderr = r.stderr.strip()
        if "Everything up-to-date" in stderr or "up to date" in stderr.lower():
            return _step_result("push", True, "Already up to date")
        return _step_result("push", False, f"git push failed: {stderr[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("push", False, "git push timed out (120s)")
    except FileNotFoundError:
        return _step_result("push", False, "git not found")


# ---------------------------------------------------------------------------
# Step 4: Save session memory
# ---------------------------------------------------------------------------

def save_memory(achievement: str | None = None) -> dict:
    """Save all GRIP memory subsystems via grip_save.py."""
    save_script = LIB_DIR / "grip_save.py"
    if not save_script.exists():
        return _step_result("memory", False, "grip_save.py not found")

    # GRIP_PYTHON, not bare "python3": grip_save.py signs an IDR record, and
    # `python3` on this machine resolves to an interpreter without
    # `cryptography` -- which does not fail, it writes a forgeable
    # demo-sealed record. Every other spawn in this file already does this.
    cmd = [GRIP_PYTHON, str(save_script), "--quiet"]
    if achievement:
        cmd.append(achievement)

    try:
        r = _run(cmd, timeout=60)
        if r.returncode == 0:
            return _step_result("memory", True, "All memory subsystems saved")
        return _step_result("memory", False, f"Memory save partial: {r.stderr.strip()[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("memory", False, "Memory save timed out (60s)")
    except FileNotFoundError:
        return _step_result("memory", False, "python3 not found")


# ---------------------------------------------------------------------------
# Step 5: Sync session context
# ---------------------------------------------------------------------------

def sync_session() -> dict:
    """Write session sync payload for peer sessions."""
    sync_script = LIB_DIR / "grip_session_sync.py"
    if not sync_script.exists():
        return _step_result("sync", True, "grip_session_sync.py not found (skip)")

    try:
        r = _run([GRIP_PYTHON, str(sync_script), "write"], timeout=10)
        if r.returncode == 0:
            return _step_result("sync", True, "Session context synced for peers")
        return _step_result("sync", False, f"Sync failed: {r.stderr.strip()[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("sync", False, "Sync timed out (10s)")
    except FileNotFoundError:
        return _step_result("sync", True, "Python not found (skip)")


# ---------------------------------------------------------------------------
# Step 6: Calibrate friction (evo)
# ---------------------------------------------------------------------------

def run_evo() -> dict:
    """Run /evo to calibrate friction patterns from CC facet analyses."""
    evo_script = LIB_DIR / "evo.py"
    if not evo_script.exists():
        return _step_result("evo", True, "evo.py not found (skip)")

    try:
        r = _run([GRIP_PYTHON, str(evo_script)], timeout=30)
        if r.returncode == 0:
            return _step_result("evo", True, "Friction patterns calibrated")
        return _step_result("evo", False, f"Evo partial: {r.stderr.strip()[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("evo", False, "Evo timed out (30s)")
    except FileNotFoundError:
        return _step_result("evo", True, "Python not found (skip)")


# ---------------------------------------------------------------------------
# Step 7: Evolution pipeline (evolve)
# ---------------------------------------------------------------------------

def run_evolve() -> dict:
    """Run full evolution pipeline: observe, learn, measure, genome, propose."""
    evolve_script = LIB_DIR / "evolve.py"
    if not evolve_script.exists():
        return _step_result("evolve", True, "evolve.py not found (skip)")

    try:
        r = _run([GRIP_PYTHON, str(evolve_script), "closeout"], timeout=60)
        if r.returncode == 0:
            return _step_result("evolve", True, "Evolution pipeline complete")
        return _step_result("evolve", False, f"Evolve partial: {r.stderr.strip()[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("evolve", False, "Evolve timed out (60s)")
    except FileNotFoundError:
        return _step_result("evolve", True, "Python not found (skip)")


# ---------------------------------------------------------------------------
# Step 8: Exit review council (gated)
# ---------------------------------------------------------------------------

def run_exit_review() -> dict:
    """Lightweight multi-model review of session claims. Gated on complexity."""
    review_script = LIB_DIR / "exit_review.py"
    if not review_script.exists():
        return _step_result("exit_review", True, "exit_review.py not found (skip)")

    try:
        r = _run([GRIP_PYTHON, str(review_script)], timeout=30)
        if r.returncode == 0:
            output = r.stdout.strip()
            if "SKIPPED" in output:
                return _step_result("exit_review", True, "Council skipped (session below threshold)")
            return _step_result("exit_review", True, f"Council review complete: {output[:80]}")
        return _step_result("exit_review", True, f"Council skipped: {r.stderr.strip()[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("exit_review", True, "Council timed out (skip)")
    except FileNotFoundError:
        return _step_result("exit_review", True, "Python not found (skip)")


# ---------------------------------------------------------------------------
# Step 9: RSI lock cleanup
# ---------------------------------------------------------------------------

def cleanup_rsi_lock() -> dict:
    """Release RSI lock if held by this process."""
    try:
        from rsi.lock import release_rsi_lock, check_rsi_lock

        status = check_rsi_lock()
        if not status.get("locked"):
            return _step_result("rsi_lock", True, "No RSI lock held")

        released = release_rsi_lock()
        if released:
            return _step_result("rsi_lock", True, "RSI lock released")
        return _step_result("rsi_lock", True,
                            "RSI lock held by another process, left intact")
    except ImportError:
        return _step_result("rsi_lock", True, "RSI lock module not available (skip)")
    except Exception as e:
        return _step_result("rsi_lock", False, f"RSI lock cleanup error: {e}")


# ---------------------------------------------------------------------------
# Step 6: Stop caffeinate
# ---------------------------------------------------------------------------

# Stop the keep-awake, then print a marker and its status. The script exits with
# the stop's own status, so a status probe that fails cannot turn a stop into a
# failure: set +e keeps that true even if the sourced script turns errexit on. The
# step reads only the line after the marker, so nothing else the script prints can
# stand in for the status (GRIP#9029). The script's path arrives as $1, never
# inside the command text. The twin is in lib/closeout.py, which /closeout runs.
_STATUS_MARKER = "@@caffeinate-status@@"
_CAFFEINATE_STOP_THEN_STATUS = (
    'source "$1" || exit 1; set +e; caffeinate_stop_if_safe; rc=$?; '
    f'echo "{_STATUS_MARKER}"; caffeinate_status; exit "$rc"'
)


def _caffeinate_outcome(status_output: str) -> str:
    """Say what the stop did, from the caffeinate_status line printed after it.

    caffeinate_stop_if_safe returns 0 when it stops the keep-awake, and also when
    it leaves it running: while a live loop holds the RSI lock, or when ps cannot
    confirm the recorded process is GRIP's. Only the status tells them apart, and a
    status that cannot be read is reported as such, never as stopped. The twin is
    _caffeinate_outcome in lib/closeout.py, which /closeout runs.
    """
    status = _caffeinate_status_line(status_output)
    if status.startswith("active"):
        return f"Caffeinate left running: {status}"
    if status.startswith("inactive"):
        return "Caffeinate stopped"
    return "Caffeinate stop ran, but its status could not be read"


def _caffeinate_status_line(output: str) -> str:
    """The line caffeinate_status printed: the first line after the last marker, when
    it begins with a status word, or "" otherwise.

    Nothing else the script prints, before or after, can stand in for the status. The
    twin is in lib/closeout.py.
    """
    _, found, after = output.rpartition(_STATUS_MARKER)
    lines = after.strip().splitlines() if found else []
    status = lines[0].strip() if lines else ""
    return status if status.startswith(("active", "inactive")) else ""


def stop_caffeinate() -> dict:
    """Stop intelligent caffeinate if running (macOS only, no-op on Windows)."""
    if sys.platform == "win32":
        return _step_result("caffeinate", True, "Caffeinate not supported on Windows (skip)")

    caff_script = LIB_DIR / "intelligent-caffeinate.sh"
    if not caff_script.exists():
        return _step_result("caffeinate", True, "Caffeinate script not found (skip)")

    try:
        r = _run(
            ["bash", "-c", _CAFFEINATE_STOP_THEN_STATUS, "caffeinate-stop", str(caff_script)],
            timeout=10,
        )
        if r.returncode == 0:
            return _step_result("caffeinate", True, _caffeinate_outcome(r.stdout))
        status = _caffeinate_status_line(r.stdout)
        return _step_result("caffeinate", True,
                            "Caffeinate stop returned non-zero (may not have been running)"
                            + (f"; status: {status}" if status else ""))
    except subprocess.TimeoutExpired:
        return _step_result("caffeinate", False, "Caffeinate stop timed out")
    except FileNotFoundError:
        return _step_result("caffeinate", True, "bash not available (skip)")


# ---------------------------------------------------------------------------
# Step 7: List open TODOs
# ---------------------------------------------------------------------------

def list_open_todos() -> dict:
    """Scan for outstanding work items."""
    todos: list[str] = []

    # Check recent git log for WIP commits
    try:
        r = _run(["git", "log", "--oneline", "-10", "--format=%s"])
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                lower = line.lower()
                if "wip" in lower or "todo" in lower or "fixme" in lower:
                    todos.append(f"WIP commit: {line.strip()[:60]}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Check MEMORY.md for tracked items
    cwd = os.getcwd()
    encoded = cwd.replace("/", "-").replace("\\", "-").replace(".", "-")
    mem_path = CLAUDE_DIR / "projects" / encoded / "memory" / "MEMORY.md"
    if mem_path.exists():
        try:
            content = mem_path.read_text()
            in_todo_section = False
            for line in content.splitlines():
                if "## TODO" in line or "## Remaining" in line or "## Outstanding" in line:
                    in_todo_section = True
                    continue
                if line.startswith("## "):
                    # New section -- exit current TODO section but continue scanning
                    in_todo_section = False
                    continue
                if in_todo_section and line.strip().startswith("- "):
                    item = line.strip()[2:].strip()
                    if item:
                        todos.append(f"MEMORY: {item[:60]}")
        except OSError:
            pass

    if todos:
        todo_list = "; ".join(todos[:5])
        suffix = f" (+{len(todos) - 5} more)" if len(todos) > 5 else ""
        return _step_result("todos", True, f"{len(todos)} open items: {todo_list}{suffix}")
    return _step_result("todos", True, "No outstanding TODOs found")


# ---------------------------------------------------------------------------
# Step 8: Slack summary (optional)
# ---------------------------------------------------------------------------

_PII_PATTERNS = [
    re.compile(r"/Users/\w+", re.IGNORECASE),
    re.compile(r"/home/\w+", re.IGNORECASE),
    re.compile(r"C:\\Users\\\w+", re.IGNORECASE),
    re.compile(r"\b\d{10,13}@\S+"),  # WhatsApp JIDs
    re.compile(r"xox[bpras]-\S+"),  # Slack tokens
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # API keys
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),  # GitHub PATs
]


def _sanitise_for_slack(text: str) -> str:
    """Remove PII and sensitive data before posting to Slack."""
    for pattern in _PII_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def post_slack_summary(channel_id: str | None = None) -> dict:
    """Post session digest to Slack. Sanitises PII before sending."""
    if not channel_id:
        return _step_result("slack", True, "Slack posting skipped (no channel)")

    save_script = LIB_DIR / "grip_save.py"
    if not save_script.exists():
        return _step_result("slack", False, "grip_save.py not found for Slack posting")

    try:
        r = _run([GRIP_PYTHON, str(save_script), "--slack", channel_id, "--quiet"], timeout=30)
        if r.returncode == 0:
            return _step_result("slack", True, f"Digest posted to {channel_id}")
        return _step_result("slack", False, f"Slack post failed: {r.stderr.strip()[:80]}")
    except subprocess.TimeoutExpired:
        return _step_result("slack", False, "Slack post timed out (30s)")
    except FileNotFoundError:
        return _step_result("slack", False, "python3 not found")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def closeout(
    achievement: str | None = None,
    push: bool = True,
    slack_channel: str | None = None,
    evolve: bool = True,
    from_exit: bool = False,
) -> dict:
    """Run the full closeout sequence. Returns results dict."""
    results: list[dict] = []

    # Step 1 + 2: Check and commit
    results.append(commit_changes(achievement))

    # Step 3: Push
    results.append(push_branch(skip=not push))

    # Step 4: Save memory
    results.append(save_memory(achievement))

    # Step 5: Sync session context
    results.append(sync_session())

    # Step 6-8: Evolution chain (skippable with --no-evolve)
    if evolve:
        results.append(run_evo())
        results.append(run_evolve())
        results.append(run_exit_review())
    else:
        results.append(_step_result("evo", True, "Skipped (--no-evolve)"))
        results.append(_step_result("evolve", True, "Skipped (--no-evolve)"))
        results.append(_step_result("exit_review", True, "Skipped (--no-evolve)"))

    # Step 9: RSI lock cleanup
    results.append(cleanup_rsi_lock())

    # Step 10: Stop caffeinate
    results.append(stop_caffeinate())

    # Step 11: List open TODOs
    results.append(list_open_todos())

    # Step 12: Slack summary (mandatory to #grip-updates)
    channel = slack_channel or "C0EXAMPLE01"  # Default: CodeTonight #grip-updates
    results.append(post_slack_summary(channel))

    # Write dedup flag so session-end.sh skips duplicate work
    if from_exit:
        try:
            flag = CLAUDE_DIR / "state" / "closeout-ran.flag"
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text(str(int(time.time())))
        except OSError:
            pass

    # Summary — plain language output
    passed = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    total = len(results)

    # Plain language step names (no jargon)
    _PLAIN_NAMES = {
        "commit": "Save code changes",
        "push": "Upload to GitHub",
        "memory": "Save session memory",
        "sync": "Share context with other sessions",
        "evo": "Analyse what went well and what didn't",
        "evolve": "Update learning from this session",
        "exit_review": "Quick quality check on session claims",
        "rsi_lock": "Release automation lock",
        "caffeinate": "Allow computer to sleep again",
        "todos": "Check for outstanding work",
        "slack": "Post session summary to team",
    }

    print("\nSession closeout:")
    for r in results:
        mark = "x" if r["ok"] else " "
        name = _PLAIN_NAMES.get(r["step"], r["step"])
        print(f"  [{mark}] {name}: {r['message']}")

    if failed == 0:
        print(f"\nAll {total} steps completed successfully.")
    else:
        print(f"\n{passed}/{total} steps completed. {failed} need attention.")

    return {"results": results, "passed": passed, "failed": failed}


# ---------------------------------------------------------------------------
# Self-verification (--verify flag)
# ---------------------------------------------------------------------------

def verify_dependencies() -> dict:
    """Verify all dependencies required by /closeout exist. Returns results dict."""
    checks: list[tuple[str, bool]] = []

    # Check 1: git available
    try:
        r = _run(["git", "--version"], timeout=5)
        checks.append(("git available", r.returncode == 0))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        checks.append(("git available", False))

    # Check 2: grip_save.py exists
    checks.append(("grip_save.py exists", (LIB_DIR / "grip_save.py").is_file()))

    # Check 3: rsi/lock.py exists
    checks.append(("rsi/lock.py exists", (LIB_DIR / "rsi" / "lock.py").is_file()))

    # Check 4: intelligent-caffeinate.sh exists (macOS) or skip (Windows)
    if sys.platform == "win32":
        checks.append(("caffeinate (Windows skip)", True))
    else:
        checks.append(("intelligent-caffeinate.sh exists",
                        (LIB_DIR / "intelligent-caffeinate.sh").is_file()))

    # Check 5: GRIP Python venv
    checks.append(("GRIP Python venv", Path(GRIP_PYTHON).is_file() or GRIP_PYTHON == "python3"))

    # Check 6: commands/closeout.md exists
    checks.append(("commands/closeout.md exists",
                    (CLAUDE_DIR / "commands" / "closeout.md").is_file()))

    passed = sum(1 for _, ok in checks if ok)
    failed = sum(1 for _, ok in checks if not ok)

    print("\nCloseout Dependency Verification:")
    for name, ok in checks:
        mark = "x" if ok else " "
        print(f"  [{mark}] {name}")
    print(f"\nResult: {passed} passed, {failed} failed")

    return {"checks": checks, "passed": passed, "failed": failed}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Session-end automation orchestrator.",
        prog="grip_closeout",
    )
    parser.add_argument(
        "achievement", nargs="*",
        help="Achievement description (auto-detected if omitted)",
    )
    parser.add_argument(
        "--no-push", action="store_true",
        help="Skip git push step",
    )
    parser.add_argument(
        "--slack", metavar="CHANNEL_ID", default=None,
        help="Post digest to Slack channel",
    )
    parser.add_argument(
        "--no-evolve", action="store_true",
        help="Skip evo/evolve/review steps (faster exit)",
    )
    parser.add_argument(
        "--from-exit", action="store_true",
        help="Called from /exit hook (writes dedup flag for session-end.sh)",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Run dependency verification (self-test)",
    )
    return parser


if __name__ == "__main__":
    parser = build_parser()
    parsed = parser.parse_args()

    # Self-verification mode
    if parsed.verify:
        result = verify_dependencies()
        sys.exit(0 if result["failed"] == 0 else 1)

    ach_parts = parsed.achievement
    ach = " ".join(ach_parts) if ach_parts else None

    result = closeout(
        achievement=ach,
        push=not parsed.no_push,
        slack_channel=parsed.slack,
        evolve=not parsed.no_evolve,
        from_exit=parsed.from_exit,
    )

    sys.exit(0 if result["failed"] == 0 else 1)
