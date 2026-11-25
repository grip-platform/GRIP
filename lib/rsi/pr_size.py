#!/usr/bin/env python3
"""RSI PR size gate — analyze and split large PRs by domain."""

import subprocess
import time
from pathlib import Path

MAX_PR_FILES = 20
MAX_PR_LINES = 5000


def analyze_pr_size() -> dict:
    """Analyze staged/unstaged changes for PR size gate.

    Returns dict with file_count, line_count, groups (by directory), and
    whether splitting is recommended.
    """
    result = subprocess.run(
        ["git", "diff", "--stat", "--cached", "HEAD"],
        capture_output=True, text=True, cwd=str(Path.home() / ".claude"),
    )
    if result.returncode != 0:
        # Try unstaged
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, cwd=str(Path.home() / ".claude"),
        )

    lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
    if not lines:
        return {"file_count": 0, "line_count": 0, "groups": {}, "should_split": False}

    # Parse stat output: last line is summary
    files = []
    total_insertions = 0
    for line in lines[:-1]:
        parts = line.strip().split("|")
        if len(parts) >= 2:
            fname = parts[0].strip()
            files.append(fname)
            nums = parts[1].strip().split()
            if nums and nums[0].isdigit():
                total_insertions += int(nums[0])

    # Group by top-level directory
    groups: dict[str, list[str]] = {}
    for f in files:
        domain = f.split("/")[0] if "/" in f else "root"
        groups.setdefault(domain, []).append(f)

    file_count = len(files)
    should_split = file_count > MAX_PR_FILES or total_insertions > MAX_PR_LINES

    return {
        "file_count": file_count,
        "line_count": total_insertions,
        "groups": groups,
        "should_split": should_split,
        "recommendation": (
            f"Split into {len(groups)} PRs by domain: {', '.join(sorted(groups.keys()))}"
            if should_split else "Size OK — single PR"
        ),
    }


def split_pr_by_domain(base_branch: str = "main") -> list[dict]:
    """Split staged changes into domain-grouped PRs.

    Returns list of created PRs with {branch, domain, files, pr_number}.
    """
    analysis = analyze_pr_size()
    if not analysis["should_split"]:
        return []

    created = []
    for domain, files in analysis["groups"].items():
        branch = f"rsi/split-{domain}-{int(time.time()) % 10000}"
        # Would create branch, cherry-pick domain files, create PR
        # For now, return the plan without executing
        created.append({
            "domain": domain,
            "branch": branch,
            "files": files,
            "file_count": len(files),
        })

    return created
