#!/usr/bin/env python3
"""
sprint_report.py -- Comprehensive sprint report generator.

Generates sprint reports from git history, GitHub PR data, and GRIP analytics.

Usage:
    python3 lib/sprint_report.py report
    python3 lib/sprint_report.py report --since 48h
    python3 lib/sprint_report.py report --since 7d
    python3 lib/sprint_report.py report --since 7d --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

# Keywords for PR classification
_CREATIVE_PREFIXES = ("feat", "feature", "add", "new", "create", "build", "implement")
_INFRA_PREFIXES = (
    "fix", "chore", "refactor", "ci", "perf", "style",
    "deps", "infra", "test", "docs",
)


@dataclass
class SprintReport:
    """Complete sprint analytics report."""

    period: str
    commits: int = 0
    prs_merged: int = 0
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    test_files_created: int = 0
    lib_files_created: int = 0
    test_ratio: float = 0.0
    creative_count: int = 0
    infra_count: int = 0
    creative_infra_ratio: str = "0:0"
    velocity_per_hour: float = 0.0
    top_files: list[str] = field(default_factory=list)
    # Defaults to None, not 0. Under this field's semantics 0 means "the
    # detector ran and found nothing", which a freshly-constructed report has
    # not earned. Defaulting to 0 would let any caller that builds a
    # SprintReport directly — bypassing generate_report — publish a clean bill
    # of health for a sprint nobody examined, which is the defect this field
    # exists to prevent.
    anomaly_count: int | None = None
    achievements_unlocked: int = 0


def parse_since(since: str) -> tuple[str, float]:
    """Parse a since parameter into a git-compatible string and hours float.

    Supported formats: "24h", "48h", "7d", "24 hours ago", etc.

    Returns:
        Tuple of (git_since_string, hours_float).
    """
    since = since.strip()

    # Already a git-compatible string like "24 hours ago"
    if "ago" in since:
        m = re.match(r"(\d+)\s*hours?\s*ago", since)
        if m:
            return since, float(m.group(1))
        m = re.match(r"(\d+)\s*days?\s*ago", since)
        if m:
            return since, float(m.group(1)) * 24
        return since, 24.0

    # Short format: "24h", "48h"
    m = re.match(r"^(\d+)\s*h$", since, re.IGNORECASE)
    if m:
        hours = float(m.group(1))
        return f"{int(hours)} hours ago", hours

    # Short format: "7d"
    m = re.match(r"^(\d+)\s*d$", since, re.IGNORECASE)
    if m:
        days = float(m.group(1))
        hours = days * 24
        return f"{int(days)} days ago", hours

    # Fallback: treat as git string, assume 24h
    return since, 24.0


def _run(cmd: list[str], cwd: str | Path | None = None) -> str:
    """Run a subprocess and return stdout. Returns empty string on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd or _ROOT,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def gather_git_stats(since: str = "24 hours ago") -> dict[str, Any]:
    """Gather commit statistics from git log.

    Returns dict with keys: commits, files_changed, lines_added, lines_removed, top_files.
    """
    log_output = _run(["git", "log", "--oneline", f"--since={since}"])
    commits = len(log_output.splitlines()) if log_output else 0

    numstat_output = _run([
        "git", "log", "--numstat", "--pretty=format:", f"--since={since}"
    ])

    files_changed_set: set[str] = set()
    lines_added = 0
    lines_removed = 0
    file_changes: dict[str, int] = {}

    for line in numstat_output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added_str, removed_str, filepath = parts
        # Binary files show "-" for added/removed
        if added_str == "-" or removed_str == "-":
            files_changed_set.add(filepath)
            file_changes[filepath] = file_changes.get(filepath, 0) + 1
            continue
        try:
            added = int(added_str)
            removed = int(removed_str)
        except ValueError:
            continue
        lines_added += added
        lines_removed += removed
        files_changed_set.add(filepath)
        file_changes[filepath] = file_changes.get(filepath, 0) + added + removed

    top_files = sorted(file_changes, key=file_changes.get, reverse=True)[:10]

    return {
        "commits": commits,
        "files_changed": len(files_changed_set),
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "top_files": top_files,
    }


def gather_pr_stats(since_hours: int = 24) -> dict[str, Any]:
    """Gather merged PR statistics from GitHub CLI.

    Returns dict with keys: prs_merged, creative_count, infra_count, titles.
    """
    output = _run([
        "gh", "pr", "list",
        "--state", "merged",
        "--limit", "100",
        "--json", "title,mergedAt",
    ])

    empty = {"prs_merged": 0, "creative_count": 0, "infra_count": 0, "titles": []}

    if not output:
        return empty

    try:
        prs = json.loads(output)
    except json.JSONDecodeError:
        return empty

    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    recent: list[str] = []

    for pr in prs:
        merged_at = pr.get("mergedAt", "")
        if not merged_at:
            continue
        try:
            merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            if merged_dt >= cutoff:
                recent.append(pr.get("title", ""))
        except (ValueError, TypeError):
            continue

    creative = sum(1 for t in recent if classify_pr(t) == "creative")
    infra = sum(1 for t in recent if classify_pr(t) == "infra")

    return {
        "prs_merged": len(recent),
        "creative_count": creative,
        "infra_count": infra,
        "titles": recent,
    }


def classify_pr(title: str) -> str:
    """Classify a PR title as 'creative' or 'infra'.

    Creative: new features (feat, add, new, create, build, implement).
    Infra: maintenance (fix, chore, refactor, ci, perf, style, deps, test, docs).
    """
    normalized = title.lower().strip()

    # Conventional commit prefix (e.g., "feat: ..." or "fix(scope): ...")
    prefix_match = re.match(r"^(\w+)[\(:]", normalized)
    if prefix_match:
        prefix = prefix_match.group(1)
        if prefix in _CREATIVE_PREFIXES:
            return "creative"
        if prefix in _INFRA_PREFIXES:
            return "infra"

    # Fallback: check if title starts with any keyword
    for kw in _CREATIVE_PREFIXES:
        if normalized.startswith(kw):
            return "creative"
    for kw in _INFRA_PREFIXES:
        if normalized.startswith(kw):
            return "infra"

    # Default to creative (new work assumed)
    return "creative"


def calculate_velocity(commits: int, hours: float) -> float:
    """Calculate commits per hour. Returns 0.0 if hours is zero or negative."""
    if hours <= 0:
        return 0.0
    return round(commits / hours, 2)


def gather_test_stats() -> dict[str, int]:
    """Count test files and lib files in the repository.

    Returns dict with keys: test_files, lib_files.
    """
    tests_dir = _ROOT / "tests"
    lib_dir = _ROOT / "lib"

    test_files = 0
    if tests_dir.exists():
        test_files = sum(
            1 for f in tests_dir.rglob("*.py")
            if f.name.startswith("test_") and f.is_file()
        )

    lib_files = 0
    if lib_dir.exists():
        lib_files = sum(
            1 for f in lib_dir.rglob("*.py")
            if f.is_file()
        )

    return {"test_files": test_files, "lib_files": lib_files}


def gather_anomaly_count() -> int | None:
    """Get anomaly count from converge_anomaly if available.

    Returns the number of anomalies, or ``None`` when the count could not be
    established because the detector was unavailable or raised.

    ``0`` and ``None`` mean opposite things. ``0`` is a detector that ran and
    found nothing; ``None`` is a detector that never ran. Collapsing the second
    into the first made the report print "Anomalies: 0" — a clean bill of health
    — on precisely the runs where nothing had been checked.

    A missing metrics file is the same class as a missing detector: there was
    nothing for the detector to read, so no run was ever graded. It reads as
    unknown, not as clean. This path still returned ``0`` until AGORA caught it
    on PR #4458 (issue #4505) — the one input the original fix left behind.
    """
    try:
        # Import relative to project root. Guarded because this runs on every
        # call: an unguarded insert appended a duplicate entry per invocation
        # (50 calls measured, 50 entries added), growing an interpreter-wide
        # list without bound. AGORA raised this on PR #4458 (issue #4505).
        lib_dir = str(_ROOT / "lib")
        if lib_dir not in sys.path:
            sys.path.insert(0, lib_dir)
        from converge_anomaly import detect_anomalies

        metrics_file = _ROOT / "learning" / "converge-metrics.jsonl"
        if not metrics_file.exists():
            return None

        runs: list[dict] = []
        with open(metrics_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        runs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        report = detect_anomalies(runs)
        return len(report.anomalies)
    except Exception:
        # `except (ImportError, Exception)` was redundant — ImportError is an
        # Exception, so the tuple only ever behaved as the broad arm.
        return None


def format_anomaly_count(count: int | None) -> str:
    """Render an anomaly count, keeping "none found" distinct from "not checked".

    A bare ``0`` reads as a clean bill of health, so an unknown count must never
    borrow that rendering.
    """
    return "unknown (detector unavailable)" if count is None else str(count)


def generate_report(since: str = "24h") -> SprintReport:
    """Generate a complete sprint report for the given time window."""
    git_since, hours = parse_since(since)

    git_stats = gather_git_stats(git_since)
    pr_stats = gather_pr_stats(since_hours=int(hours))
    test_stats = gather_test_stats()
    anomaly_count = gather_anomaly_count()

    commits = git_stats["commits"]
    velocity = calculate_velocity(commits, hours)

    test_files = test_stats["test_files"]
    lib_files = test_stats["lib_files"]
    test_ratio = round(test_files / lib_files, 2) if lib_files > 0 else 0.0

    creative = pr_stats["creative_count"]
    infra = pr_stats["infra_count"]
    ratio_str = f"{creative}:{infra}"

    return SprintReport(
        period=since,
        commits=commits,
        prs_merged=pr_stats["prs_merged"],
        files_changed=git_stats["files_changed"],
        lines_added=git_stats["lines_added"],
        lines_removed=git_stats["lines_removed"],
        test_files_created=test_files,
        lib_files_created=lib_files,
        test_ratio=test_ratio,
        creative_count=creative,
        infra_count=infra,
        creative_infra_ratio=ratio_str,
        velocity_per_hour=velocity,
        top_files=git_stats["top_files"],
        anomaly_count=anomaly_count,
        achievements_unlocked=0,
    )


def _bar(value: int, max_value: int, width: int = 30) -> str:
    """Render an ASCII progress bar."""
    if max_value <= 0:
        return "[" + " " * width + "]"
    filled = min(int((value / max_value) * width), width)
    return "[" + "#" * filled + " " * (width - filled) + "]"


def format_report(report: SprintReport) -> str:
    """Format a SprintReport as a human-readable string with ASCII bars."""
    lines: list[str] = []

    lines.append("=" * 60)
    lines.append(f"  SPRINT REPORT  |  Period: {report.period}")
    lines.append("=" * 60)

    # Section 1: Summary
    lines.append("")
    lines.append("--- Summary ---")
    lines.append(f"  Commits:        {report.commits}")
    lines.append(f"  PRs merged:     {report.prs_merged}")
    lines.append(f"  Velocity:       {report.velocity_per_hour} commits/hour")
    lines.append("")

    # Section 2: Code Metrics
    lines.append("--- Code Metrics ---")
    total_lines = report.lines_added + report.lines_removed
    lines.append(f"  Lines added:    +{report.lines_added}")
    lines.append(f"  Lines removed:  -{report.lines_removed}")
    lines.append(f"  Net:            {report.lines_added - report.lines_removed:+d}")
    lines.append(f"  Files changed:  {report.files_changed}")

    if total_lines > 0:
        add_pct = int((report.lines_added / total_lines) * 100)
        lines.append(
            f"  Add/Remove:     {_bar(report.lines_added, total_lines)} {add_pct}% adds"
        )

    lines.append(f"  Test files:     {report.test_files_created}")
    lines.append(f"  Lib files:      {report.lib_files_created}")
    lines.append(f"  Test ratio:     {report.test_ratio:.2f} (test/lib)")
    lines.append("")

    # Section 3: Quality
    lines.append("--- Quality ---")
    lines.append(f"  Creative PRs:   {report.creative_count}")
    lines.append(f"  Infra PRs:      {report.infra_count}")
    lines.append(f"  Ratio:          {report.creative_infra_ratio} (creative:infra)")
    lines.append(f"  Anomalies:      {format_anomaly_count(report.anomaly_count)}")
    lines.append("")

    # Section 4: Top Changed Files
    lines.append("--- Top Changed Files ---")
    if report.top_files:
        for i, f in enumerate(report.top_files, 1):
            lines.append(f"  {i:2d}. {f}")
    else:
        lines.append("  (no file changes)")
    lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GRIP Sprint Report Generator",
        prog="sprint_report",
    )
    sub = parser.add_subparsers(dest="command")

    report_parser = sub.add_parser("report", help="Generate sprint report")
    report_parser.add_argument(
        "--since",
        default="24h",
        help="Time window: 24h, 48h, 7d (default: 24h)",
    )
    report_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Output as JSON",
    )

    args = parser.parse_args()

    if args.command != "report":
        parser.print_help()
        sys.exit(1)

    report = generate_report(args.since)

    if args.as_json:
        print(json.dumps(asdict(report), indent=2))
    else:
        print(format_report(report))


if __name__ == "__main__":
    main()
