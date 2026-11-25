#!/usr/bin/env python3
"""RSI pre-merge and post-merge retest gates."""

import subprocess
import time
from dataclasses import dataclass

from .metrics import record_metric


@dataclass
class RetestResult:
    """Result of a pre-merge or post-merge retest."""
    passed: bool
    test_output: str = ""
    error: str = ""
    merge_conflict: bool = False
    duration_sec: float = 0.0


def pre_merge_retest(
    branch: str,
    test_command: str = "python3 -m pytest tests/ -x -q",
    timeout: int = 300,
) -> RetestResult:
    """Run test suite after test-merging latest main into the branch.

    Steps:
    1. Fetch latest origin/main
    2. Test-merge main into current branch (--no-commit)
    3. Run test suite on the merged state
    4. Abort the test merge (restore branch state)

    Args:
        branch: The feature branch being merged.
        test_command: Shell command to run tests.
        timeout: Test command timeout in seconds.

    Returns:
        RetestResult with pass/fail status and output.
    """
    start = time.time()

    # 1. Fetch latest main
    try:
        fetch = subprocess.run(
            ["git", "fetch", "origin", "main"],
            capture_output=True, text=True, timeout=30,
        )
        if fetch.returncode != 0:
            return RetestResult(
                passed=False,
                error=f"git fetch failed: {fetch.stderr.strip()}",
                duration_sec=time.time() - start,
            )
    except subprocess.TimeoutExpired:
        return RetestResult(
            passed=False, error="git fetch timed out",
            duration_sec=time.time() - start,
        )

    # 2. Test-merge main into branch (--no-commit, --no-ff)
    try:
        merge = subprocess.run(
            ["git", "merge", "origin/main", "--no-commit", "--no-ff"],
            capture_output=True, text=True, timeout=30,
        )
        if merge.returncode != 0:
            # Merge conflict — abort and report
            subprocess.run(
                ["git", "merge", "--abort"],
                capture_output=True, timeout=10,
            )
            return RetestResult(
                passed=False,
                merge_conflict=True,
                error=f"Merge conflict with main: {merge.stdout.strip()}",
                duration_sec=time.time() - start,
            )
    except subprocess.TimeoutExpired:
        subprocess.run(["git", "merge", "--abort"], capture_output=True, timeout=10)
        return RetestResult(
            passed=False, error="test merge timed out",
            duration_sec=time.time() - start,
        )

    # 3. Run tests on merged state
    try:
        import shlex
        test_result = subprocess.run(
            shlex.split(test_command),
            capture_output=True, text=True, timeout=timeout,
        )
        passed = test_result.returncode == 0
        output = test_result.stdout[-2000:] if test_result.stdout else ""
        if test_result.stderr:
            output += "\n" + test_result.stderr[-500:]
    except subprocess.TimeoutExpired:
        passed = False
        output = f"Tests timed out after {timeout}s"

    # 4. Abort test merge (restore clean branch state)
    subprocess.run(
        ["git", "merge", "--abort"],
        capture_output=True, timeout=10,
    )
    # If merge --abort fails (no merge in progress), reset
    subprocess.run(
        ["git", "reset", "--merge"],
        capture_output=True, timeout=10,
    )

    duration = time.time() - start
    record_metric("pre_merge_retest", {
        "branch": branch,
        "passed": passed,
        "merge_conflict": False,
        "duration_sec": round(duration, 1),
    })

    return RetestResult(
        passed=passed,
        test_output=output.strip(),
        duration_sec=duration,
    )


def post_merge_validate(
    test_command: str = "python3 -m pytest tests/ -x -q",
    timeout: int = 300,
) -> RetestResult:
    """Run test suite on main after merge to catch post-merge regressions.

    If tests fail, the caller should consider git revert.

    Args:
        test_command: Shell command to run tests.
        timeout: Test command timeout in seconds.

    Returns:
        RetestResult with pass/fail.
    """
    start = time.time()
    try:
        import shlex
        result = subprocess.run(
            shlex.split(test_command),
            capture_output=True, text=True, timeout=timeout,
        )
        passed = result.returncode == 0
        output = result.stdout[-2000:] if result.stdout else ""
        if result.stderr:
            output += "\n" + result.stderr[-500:]
    except subprocess.TimeoutExpired:
        passed = False
        output = f"Post-merge tests timed out after {timeout}s"

    duration = time.time() - start
    record_metric("post_merge_validate", {
        "passed": passed,
        "duration_sec": round(duration, 1),
    })

    return RetestResult(
        passed=passed,
        test_output=output.strip(),
        duration_sec=duration,
    )
