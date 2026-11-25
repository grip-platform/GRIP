#!/usr/bin/env python3
"""Context rule staleness checker.

Parses YAML frontmatter from context rules in a project's
.claude/rules/features/ directory, checks git log for commits
matching the rule's paths since last_validated, and outputs
a markdown report of stale rules.

Usage:
    python3 context_staleness.py <rules-dir> [--project-root <path>]

Examples:
    python3 context_staleness.py /path/to/project/.claude/rules/features/
    python3 context_staleness.py ./rules/features/ --project-root /path/to/project
"""

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path


def parse_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter from a markdown file.

    Returns dict with paths, last_validated, validation_threshold,
    or None if frontmatter is missing/invalid.
    """
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None

    end = text.find("---", 3)
    if end == -1:
        return None

    fm_text = text[3:end].strip()
    result = {}
    current_key = None
    current_list = []

    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # List item under current key
        if stripped.startswith("- ") and current_key:
            value = stripped[2:].strip().strip('"').strip("'")
            current_list.append(value)
            result[current_key] = current_list
            continue

        # Key-value pair
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")

            if current_key and current_list:
                result[current_key] = current_list

            current_key = key
            current_list = []

            if val:
                result[key] = val
                current_key = key
                current_list = []

    return result if result else None


def count_commits_since(
    project_root: Path, paths: list[str], since_date: str
) -> int:
    """Count git commits touching any of the given paths since a date."""
    cmd = [
        "git", "log", "--oneline",
        f"--since={since_date}",
        "--",
    ] + paths

    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=30,
        )
        if out.returncode != 0:
            return 0
        lines = [l for l in out.stdout.strip().splitlines() if l.strip()]
        return len(lines)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0


def check_staleness(rules_dir: Path, project_root: Path | None = None) -> list[dict]:
    """Check all context rules for staleness.

    Returns list of dicts with keys:
        name, file, paths, last_validated, threshold, commits, stale
    """
    if project_root is None:
        # Walk up from rules_dir to find .git
        candidate = rules_dir.resolve()
        while candidate != candidate.parent:
            if (candidate / ".git").exists():
                project_root = candidate
                break
            candidate = candidate.parent

    if project_root is None:
        print("Error: could not find git root from rules directory", file=sys.stderr)
        sys.exit(1)

    results = []

    if not rules_dir.is_dir():
        print(f"Error: {rules_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    for md_file in sorted(rules_dir.glob("*.md")):
        fm = parse_frontmatter(md_file)
        if fm is None:
            continue

        paths = fm.get("paths", [])
        if isinstance(paths, str):
            paths = [paths]

        last_validated = fm.get("last_validated", "")
        threshold_raw = fm.get("validation_threshold", "10")
        try:
            threshold = int(threshold_raw)
        except (ValueError, TypeError):
            threshold = 10

        if not paths or not last_validated or last_validated == "YYYY-MM-DD":
            continue

        commits = count_commits_since(project_root, paths, last_validated)
        stale = commits > threshold

        results.append({
            "name": md_file.stem,
            "file": str(md_file),
            "paths": paths,
            "last_validated": last_validated,
            "threshold": threshold,
            "commits": commits,
            "stale": stale,
        })

    return results


def format_report(results: list[dict]) -> str:
    """Format staleness results as a markdown report."""
    if not results:
        return "No context rules found with valid frontmatter."

    lines = [
        "# Context Rule Staleness Report",
        "",
        f"Checked {len(results)} rules on {date.today().isoformat()}",
        "",
        "| Rule | Last Validated | Commits Since | Threshold | Status |",
        "|------|---------------|---------------|-----------|--------|",
    ]

    stale_count = 0
    for r in results:
        status = "STALE" if r["stale"] else "OK"
        if r["stale"]:
            stale_count += 1
        lines.append(
            f"| {r['name']} | {r['last_validated']} | {r['commits']} "
            f"| {r['threshold']} | {status} |"
        )

    lines.append("")
    if stale_count:
        lines.append(
            f"**{stale_count} stale rule(s)** -- "
            "run `/context-map refresh` to update."
        )
    else:
        lines.append("All rules are current.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Check context rules for staleness based on git activity"
    )
    parser.add_argument(
        "rules_dir",
        help="Path to the features/ directory containing context rule .md files",
    )
    parser.add_argument(
        "--project-root",
        help="Git project root (auto-detected if omitted)",
        default=None,
    )

    args = parser.parse_args()
    rules_dir = Path(args.rules_dir)
    project_root = Path(args.project_root) if args.project_root else None

    results = check_staleness(rules_dir, project_root)
    print(format_report(results))

    # Exit 1 if any rules are stale (useful for CI)
    if any(r["stale"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
