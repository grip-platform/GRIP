#!/usr/bin/env python3
"""Stale branch detector for GRIP.

Finds and reports stale/orphan branches across the remote.
Pure Python, no external dependencies.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class BranchInfo:
    """Information about a single remote branch."""

    name: str
    last_commit_date: datetime
    age_days: int
    is_merged: bool = False
    pr_number: Optional[int] = None
    pr_state: Optional[str] = None
    classification: str = "unknown"

    @property
    def is_orphan(self) -> bool:
        """A branch is an orphan if it has no PR, is not merged, and is stale or ancient."""
        return (
            self.pr_number is None
            and not self.is_merged
            and self.classification in ("stale", "ancient")
        )


@dataclass
class BranchReport:
    """Aggregated report of all branches."""

    branches: List[BranchInfo] = field(default_factory=list)
    stale_count: int = 0
    orphan_count: int = 0
    active_count: int = 0
    ancient_count: int = 0
    suggestions: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def _run_git(args: List[str], repo_dir: Optional[str] = None) -> str:
    """Run a git command and return stdout. Returns empty string on failure."""
    cmd = ["git"]
    if repo_dir:
        cmd.extend(["-C", repo_dir])
    cmd.extend(args)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def _run_gh(args: List[str]) -> Optional[str]:
    """Run a gh CLI command. Returns None if gh is missing or command fails."""
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

# Branches to always skip in reports
SKIP_BRANCHES = frozenset({"origin/HEAD", "origin/main", "origin/master"})


def parse_branch_line(line: str, now: Optional[datetime] = None) -> Optional[BranchInfo]:
    """Parse a single line from git branch -r --format output.

    Expected format: 'origin/branch-name|2026-02-20 10:30:00 +0200'
    Returns None if the line is unparseable or should be skipped.
    """
    if not line or "|" not in line:
        return None

    parts = line.split("|", 1)
    if len(parts) != 2:
        return None

    name = parts[0].strip()
    date_str = parts[1].strip()

    if name in SKIP_BRANCHES:
        return None

    if now is None:
        now = datetime.now(timezone.utc)

    try:
        # git iso format: '2026-02-20 10:30:00 +0200'
        commit_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        # Try ISO 8601 with T separator
        try:
            commit_date = datetime.fromisoformat(date_str)
        except ValueError:
            return None

    age = (now - commit_date).days
    if age < 0:
        age = 0

    return BranchInfo(
        name=name,
        last_commit_date=commit_date,
        age_days=age,
        classification="unknown",
    )


def list_remote_branches(repo_dir: Optional[str] = None) -> List[BranchInfo]:
    """List all remote branches with their last commit dates."""
    output = _run_git(
        ["branch", "-r", "--format=%(refname:short)|%(committerdate:iso)"],
        repo_dir=repo_dir,
    )
    if not output:
        return []

    now = datetime.now(timezone.utc)
    branches = []
    for line in output.splitlines():
        info = parse_branch_line(line, now=now)
        if info is not None:
            branches.append(info)

    return branches


def get_merged_branches(repo_dir: Optional[str] = None) -> set:
    """Get set of branch names that are merged into main."""
    output = _run_git(
        ["branch", "-r", "--merged", "main"],
        repo_dir=repo_dir,
    )
    if not output:
        return set()

    merged = set()
    for line in output.splitlines():
        name = line.strip()
        # Remove '-> origin/main' pointer entries
        if " -> " in name:
            continue
        if name:
            merged.add(name)
    return merged


def classify_branch(
    branch: BranchInfo,
    merged_branches: set,
    active_threshold: int = 7,
    stale_threshold: int = 30,
) -> str:
    """Classify a branch by age.

    Returns: 'active', 'stale', or 'ancient'.
    Also sets is_merged on the branch.
    """
    branch.is_merged = branch.name in merged_branches

    if branch.age_days < active_threshold:
        branch.classification = "active"
    elif branch.age_days < stale_threshold:
        branch.classification = "stale"
    else:
        branch.classification = "ancient"

    return branch.classification


def check_pr_status(branch_name: str) -> Optional[Dict]:
    """Check if a branch has an associated PR via gh CLI.

    Returns dict with 'number' and 'state' keys, or None if no PR / gh unavailable.
    """
    # Strip 'origin/' prefix for PR lookup
    head = branch_name
    if head.startswith("origin/"):
        head = head[len("origin/"):]

    output = _run_gh(["pr", "list", "--head", head, "--json", "number,state", "--limit", "1"])
    if not output:
        return None

    try:
        prs = json.loads(output)
        if prs and len(prs) > 0:
            pr = prs[0]
            return {"number": pr.get("number"), "state": pr.get("state", "").upper()}
    except (json.JSONDecodeError, KeyError, IndexError):
        pass

    return None


def enrich_with_pr_status(branches: List[BranchInfo], skip_pr_check: bool = False) -> None:
    """Enrich branch info with PR status. Modifies branches in-place."""
    if skip_pr_check:
        return
    for branch in branches:
        if branch.classification in ("stale", "ancient") and not branch.is_merged:
            pr = check_pr_status(branch.name)
            if pr:
                branch.pr_number = pr.get("number")
                branch.pr_state = pr.get("state")


def generate_report(
    branches: List[BranchInfo],
    active_threshold: int = 7,
    stale_threshold: int = 30,
) -> BranchReport:
    """Generate a full report from classified branches."""
    report = BranchReport()
    report.branches = sorted(branches, key=lambda b: b.age_days, reverse=True)

    for b in report.branches:
        if b.classification == "active":
            report.active_count += 1
        elif b.classification == "stale":
            report.stale_count += 1
        elif b.classification == "ancient":
            report.ancient_count += 1

        if b.is_orphan:
            report.orphan_count += 1

    # Generate suggestions
    for b in report.branches:
        if b.is_orphan and b.classification == "ancient":
            report.suggestions.append(
                f"DELETE {b.name} -- ancient orphan ({b.age_days}d, no PR, not merged)"
            )
        elif b.is_merged and b.classification in ("stale", "ancient"):
            report.suggestions.append(
                f"DELETE {b.name} -- already merged into main ({b.age_days}d old)"
            )
        elif b.is_orphan and b.classification == "stale":
            report.suggestions.append(
                f"REVIEW {b.name} -- stale orphan ({b.age_days}d, no PR, not merged)"
            )
        elif b.pr_state == "CLOSED" and not b.is_merged:
            report.suggestions.append(
                f"DELETE {b.name} -- PR #{b.pr_number} closed, not merged ({b.age_days}d old)"
            )

    return report


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "active": "[*]",    # active
    "stale": "[!]",     # warning
    "ancient": "[x]",   # danger
}

_MERGED_ICON = "[M]"
_ORPHAN_ICON = "[O]"
_PR_ICON = "[PR]"


def _format_branch_line(b: BranchInfo) -> str:
    """Format a single branch as an ASCII line."""
    icon = _STATUS_ICONS.get(b.classification, "[ ]")
    flags = []
    if b.is_merged:
        flags.append(_MERGED_ICON)
    if b.is_orphan:
        flags.append(_ORPHAN_ICON)
    if b.pr_number is not None:
        state_str = b.pr_state.lower() if b.pr_state else "?"
        flags.append(f"{_PR_ICON}#{b.pr_number}({state_str})")

    flag_str = " ".join(flags)
    date_str = b.last_commit_date.strftime("%Y-%m-%d")
    return f"  {icon} {b.name:<50} {b.age_days:>4}d  {date_str}  {flag_str}"


def format_report(report: BranchReport, show_all: bool = False) -> str:
    """Format a BranchReport as an ASCII table."""
    lines = []
    lines.append("=" * 80)
    lines.append("STALE BRANCH REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(
        f"  Active: {report.active_count}  |  "
        f"Stale: {report.stale_count}  |  "
        f"Ancient: {report.ancient_count}  |  "
        f"Orphans: {report.orphan_count}"
    )
    lines.append("")

    if not report.branches:
        lines.append("  No remote branches found.")
        lines.append("")
        return "\n".join(lines)

    # Section: Ancient
    ancient = [b for b in report.branches if b.classification == "ancient"]
    if ancient:
        lines.append("--- Ancient (>30d) ---")
        for b in ancient:
            lines.append(_format_branch_line(b))
        lines.append("")

    # Section: Stale
    stale = [b for b in report.branches if b.classification == "stale"]
    if stale:
        lines.append("--- Stale (7-30d) ---")
        for b in stale:
            lines.append(_format_branch_line(b))
        lines.append("")

    # Section: Active (only if show_all)
    if show_all:
        active = [b for b in report.branches if b.classification == "active"]
        if active:
            lines.append("--- Active (<7d) ---")
            for b in active:
                lines.append(_format_branch_line(b))
            lines.append("")

    # Suggestions
    if report.suggestions:
        lines.append("--- Suggestions ---")
        for s in report.suggestions:
            lines.append(f"  -> {s}")
        lines.append("")

    lines.append("=" * 80)
    return "\n".join(lines)


def format_json(report: BranchReport) -> str:
    """Format a BranchReport as JSON."""

    def _serialize(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    data = {
        "summary": {
            "active": report.active_count,
            "stale": report.stale_count,
            "ancient": report.ancient_count,
            "orphans": report.orphan_count,
            "total": len(report.branches),
        },
        "branches": [asdict(b) for b in report.branches],
        "suggestions": report.suggestions,
    }
    return json.dumps(data, indent=2, default=_serialize)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Detect stale and orphan remote branches.",
        prog="stale-branch-detector",
    )
    parser.add_argument(
        "--age-threshold",
        type=int,
        default=30,
        help="Days before a branch is considered ancient (default: 30)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output as JSON instead of ASCII",
    )
    parser.add_argument(
        "--merged-only",
        action="store_true",
        help="Show only branches that are merged into main",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="show_all",
        help="Include active branches in the report",
    )
    parser.add_argument(
        "--skip-pr-check",
        action="store_true",
        help="Skip PR status check via gh CLI (faster)",
    )
    parser.add_argument(
        "--repo-dir",
        type=str,
        default=None,
        help="Path to git repository (default: current directory)",
    )

    args = parser.parse_args(argv)

    # Fetch branches
    branches = list_remote_branches(repo_dir=args.repo_dir)
    if not branches:
        print("No remote branches found. Run 'git fetch' first?", file=sys.stderr)
        return 1

    # Classify
    merged = get_merged_branches(repo_dir=args.repo_dir)
    active_threshold = 7
    stale_threshold = args.age_threshold

    for b in branches:
        classify_branch(b, merged, active_threshold=active_threshold, stale_threshold=stale_threshold)

    # PR enrichment
    enrich_with_pr_status(branches, skip_pr_check=args.skip_pr_check)

    # Filter merged-only
    if args.merged_only:
        branches = [b for b in branches if b.is_merged]

    # Report
    report = generate_report(branches, active_threshold=active_threshold, stale_threshold=stale_threshold)

    if args.output_json:
        print(format_json(report))
    else:
        print(format_report(report, show_all=args.show_all))

    return 0


if __name__ == "__main__":
    sys.exit(main())
