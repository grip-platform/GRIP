#!/usr/bin/env python3
"""Session Diff Summary -- one-command summary of all session changes.

Computes a structured diff from a starting commit or date to HEAD,
categorises files, scores impact, and renders an ASCII report.

Usage:
    python3 lib/session_diff.py [--since COMMIT|DATE] [--json] [--detailed]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileDiff:
    """One changed file."""

    path: str
    added_lines: int
    removed_lines: int
    is_new: bool
    is_deleted: bool
    category: str


@dataclass
class CategorySummary:
    """Aggregate per category."""

    name: str
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    new_files: int = 0
    deleted_files: int = 0


@dataclass
class ImpactScore:
    """Numeric impact 0-100 with per-category breakdown."""

    score: int
    breakdown: dict = field(default_factory=dict)


@dataclass
class SessionDiff:
    """Full session diff summary."""

    categories: List[CategorySummary] = field(default_factory=list)
    files: List[FileDiff] = field(default_factory=list)
    total_files: int = 0
    total_added: int = 0
    total_removed: int = 0
    commits: int = 0
    prs_merged: int = 0
    since: str = ""
    impact: Optional[ImpactScore] = None


# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

CATEGORY_PREFIXES = [
    ("lib/", "lib"),
    ("tests/", "tests"),
    ("skills/", "skills"),
    ("rules/", "rules"),
    ("docs/", "docs"),
    ("hooks/", "hooks"),
    ("config/", "config"),
    ("articles/", "articles"),
]

CATEGORY_WEIGHTS = {
    "lib": 1.0,
    "tests": 0.8,
    "skills": 0.6,
    "rules": 0.5,
    "docs": 0.3,
    "hooks": 0.7,
    "config": 0.4,
    "articles": 0.2,
    "other": 0.3,
}


def categorise_path(path: str) -> str:
    """Return the category string for a file path."""
    for prefix, cat in CATEGORY_PREFIXES:
        if path.startswith(prefix):
            return cat
    return "other"


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _run(cmd: List[str], cwd: Optional[str] = None) -> str:
    """Run a subprocess and return stdout, empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def parse_numstat(raw: str) -> List[FileDiff]:
    """Parse ``git diff --numstat`` output into FileDiff objects."""
    files: List[FileDiff] = []
    if not raw:
        return files
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        added_str, removed_str, path = parts
        # Binary files show as '-'
        added = int(added_str) if added_str != "-" else 0
        removed = int(removed_str) if removed_str != "-" else 0
        files.append(FileDiff(
            path=path,
            added_lines=added,
            removed_lines=removed,
            is_new=False,
            is_deleted=False,
            category=categorise_path(path),
        ))
    return files


def detect_new_deleted(files: List[FileDiff], since: str, cwd: Optional[str] = None) -> None:
    """Mark files as new or deleted using ``git diff --diff-filter``."""
    new_raw = _run(["git", "diff", "--diff-filter=A", "--name-only", since, "HEAD"], cwd=cwd)
    new_set = set(new_raw.splitlines()) if new_raw else set()
    del_raw = _run(["git", "diff", "--diff-filter=D", "--name-only", since, "HEAD"], cwd=cwd)
    del_set = set(del_raw.splitlines()) if del_raw else set()
    for f in files:
        if f.path in new_set:
            f.is_new = True
        if f.path in del_set:
            f.is_deleted = True


def count_commits(since: str, cwd: Optional[str] = None) -> int:
    """Count commits from *since* to HEAD."""
    raw = _run(["git", "rev-list", "--count", f"{since}..HEAD"], cwd=cwd)
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def count_prs_merged(since: str, cwd: Optional[str] = None) -> int:
    """Count PRs merged in the window via ``gh``."""
    # Determine the date string
    date_str = _resolve_date(since, cwd=cwd)
    if not date_str:
        return 0
    raw = _run(
        ["gh", "pr", "list", "--state", "merged", "--json", "mergedAt",
         "--search", f"merged:>={date_str}"],
        cwd=cwd,
    )
    if not raw:
        return 0
    try:
        prs = json.loads(raw)
        return len(prs)
    except (json.JSONDecodeError, TypeError):
        return 0


def _resolve_date(since: str, cwd: Optional[str] = None) -> str:
    """Convert *since* (commit SHA or date) to an ISO date string."""
    # If it looks like a date already, return it
    if re.match(r"^\d{4}-\d{2}-\d{2}", since):
        return since[:10]
    # Otherwise treat as commit and get its date
    raw = _run(["git", "log", "-1", "--format=%aI", since], cwd=cwd)
    if raw:
        return raw[:10]
    return ""


def resolve_since(since: Optional[str], cwd: Optional[str] = None) -> str:
    """Resolve the --since argument to a usable git ref.

    If *since* is None, default to the merge-base of HEAD and main/master.
    If it looks like a date, use it directly.
    If it looks like a commit, validate it.
    """
    if since is None:
        # Default: find merge-base with main
        for base_branch in ("main", "master"):
            mb = _run(["git", "merge-base", base_branch, "HEAD"], cwd=cwd)
            if mb:
                return mb
        # Fallback: first commit
        first = _run(["git", "rev-list", "--max-parents=0", "HEAD"], cwd=cwd)
        return first if first else "HEAD~10"

    # Date-like input
    if re.match(r"^\d{4}-\d{2}-\d{2}", since):
        return since

    # Commit-like input -- validate
    validated = _run(["git", "rev-parse", "--verify", since], cwd=cwd)
    return validated if validated else since


# ---------------------------------------------------------------------------
# Impact scoring
# ---------------------------------------------------------------------------

def compute_impact(categories: List[CategorySummary]) -> ImpactScore:
    """Score 0-100 based on weighted category contributions."""
    breakdown: dict = {}
    raw_total = 0.0
    for cat in categories:
        weight = CATEGORY_WEIGHTS.get(cat.name, 0.3)
        churn = cat.lines_added + cat.lines_removed
        novelty_bonus = cat.new_files * 20
        raw = (churn + novelty_bonus) * weight
        breakdown[cat.name] = round(raw, 1)
        raw_total += raw

    # Logarithmic scaling: 100 = ~2000 weighted churn
    if raw_total <= 0:
        return ImpactScore(score=0, breakdown=breakdown)
    score = int(min(100, 100 * math.log1p(raw_total) / math.log1p(2000)))
    return ImpactScore(score=score, breakdown=breakdown)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyse(since: Optional[str] = None, cwd: Optional[str] = None) -> SessionDiff:
    """Run the full analysis and return a SessionDiff."""
    ref = resolve_since(since, cwd=cwd)

    # Numstat diff
    numstat_raw = _run(["git", "diff", "--numstat", ref, "HEAD"], cwd=cwd)
    files = parse_numstat(numstat_raw)
    detect_new_deleted(files, ref, cwd=cwd)

    # Build category summaries
    cat_map: dict[str, CategorySummary] = {}
    for f in files:
        if f.category not in cat_map:
            cat_map[f.category] = CategorySummary(name=f.category)
        cs = cat_map[f.category]
        cs.files_changed += 1
        cs.lines_added += f.added_lines
        cs.lines_removed += f.removed_lines
        if f.is_new:
            cs.new_files += 1
        if f.is_deleted:
            cs.deleted_files += 1

    # Sort categories by churn descending
    categories = sorted(cat_map.values(), key=lambda c: c.lines_added + c.lines_removed, reverse=True)

    total_added = sum(f.added_lines for f in files)
    total_removed = sum(f.removed_lines for f in files)
    commits = count_commits(ref, cwd=cwd)
    prs = count_prs_merged(ref, cwd=cwd)
    impact = compute_impact(categories)

    return SessionDiff(
        categories=categories,
        files=files,
        total_files=len(files),
        total_added=total_added,
        total_removed=total_removed,
        commits=commits,
        prs_merged=prs,
        since=ref,
        impact=impact,
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _bar(value: int, max_value: int, width: int = 30) -> str:
    """Render a simple ASCII bar."""
    if max_value <= 0:
        return ""
    filled = int(round(value / max_value * width))
    filled = min(filled, width)
    return "#" * filled + "." * (width - filled)


def format_report(diff: SessionDiff, detailed: bool = False) -> str:
    """Render an ASCII summary report."""
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("  SESSION DIFF SUMMARY")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Since:    {diff.since}")
    lines.append(f"  Commits:  {diff.commits}")
    lines.append(f"  PRs:      {diff.prs_merged}")
    lines.append(f"  Files:    {diff.total_files}")
    lines.append(f"  Added:    +{diff.total_added}")
    lines.append(f"  Removed:  -{diff.total_removed}")
    if diff.impact:
        lines.append(f"  Impact:   {diff.impact.score}/100")
    lines.append("")
    lines.append("-" * 60)
    lines.append("  CATEGORY BREAKDOWN")
    lines.append("-" * 60)

    max_churn = max((c.lines_added + c.lines_removed for c in diff.categories), default=1)
    for cat in diff.categories:
        churn = cat.lines_added + cat.lines_removed
        bar = _bar(churn, max_churn, 25)
        new_marker = f" (+{cat.new_files} new)" if cat.new_files else ""
        del_marker = f" (-{cat.deleted_files} del)" if cat.deleted_files else ""
        lines.append(
            f"  {cat.name:<10} [{bar}] "
            f"{cat.files_changed} files  +{cat.lines_added}/-{cat.lines_removed}"
            f"{new_marker}{del_marker}"
        )

    if detailed and diff.files:
        lines.append("")
        lines.append("-" * 60)
        lines.append("  FILE DETAILS")
        lines.append("-" * 60)
        for f in sorted(diff.files, key=lambda x: x.added_lines + x.removed_lines, reverse=True):
            flags = ""
            if f.is_new:
                flags += " [NEW]"
            if f.is_deleted:
                flags += " [DEL]"
            lines.append(f"  +{f.added_lines:<5} -{f.removed_lines:<5} {f.path}{flags}")

    if diff.impact and diff.impact.breakdown:
        lines.append("")
        lines.append("-" * 60)
        lines.append("  IMPACT BREAKDOWN")
        lines.append("-" * 60)
        for name, raw in sorted(diff.impact.breakdown.items(), key=lambda x: -x[1]):
            lines.append(f"  {name:<10} {raw:>8.1f}")
        lines.append(f"  {'TOTAL':<10} {diff.impact.score:>8}/100")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def format_json(diff: SessionDiff) -> str:
    """Return a JSON string of the diff."""
    data = asdict(diff)
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Session Diff Summary -- one-command session change summary",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Starting commit SHA or ISO date (default: merge-base with main)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )
    parser.add_argument(
        "--detailed",
        action="store_true",
        help="Include per-file details in ASCII report",
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help="Git repository path (default: current directory)",
    )
    args = parser.parse_args(argv)

    diff = analyse(since=args.since, cwd=args.cwd)

    if args.as_json:
        print(format_json(diff))
    else:
        print(format_report(diff, detailed=args.detailed))


if __name__ == "__main__":
    main()
