#!/usr/bin/env python3
"""RSI Issue Monitor - Check GitHub for open ralph-fix issues.

Usage:
    python3 scripts/rsi-issue-monitor.py [--repo OWNER/REPO] [--format json|text]

Checks for open issues labeled 'ralph-fix' and outputs them sorted by priority.
Can be called from copilot-cli.py or the copilot scheduler.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone


DEFAULT_REPO = "grip-platform/GRIP"

PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "low": 3}


def fetch_ralph_issues(repo: str) -> list[dict]:
    """Fetch open issues with ralph-fix label via gh CLI."""
    cmd = [
        "gh", "issue", "list",
        "--repo", repo,
        "--label", "ralph-fix",
        "--state", "open",
        "--json", "number,title,body,createdAt,labels,assignees",
        "--limit", "50",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"Error fetching issues: {result.stderr}", file=sys.stderr)
            return []
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return []


def extract_priority(issue: dict) -> str:
    """Extract priority from issue body or labels."""
    body = issue.get("body", "")
    # Check body for priority line
    for line in body.split("\n"):
        lower = line.lower()
        if "priority" in lower:
            for p in ("critical", "high", "normal", "low"):
                if p in lower:
                    return p
    # Check labels
    for label in issue.get("labels", []):
        name = label.get("name", "").lower()
        for p in ("critical", "high", "normal", "low"):
            if p in name:
                return p
    return "normal"


def extract_pr_number(issue: dict) -> str:
    """Extract referenced PR number from issue title or body."""
    import re
    title = issue.get("title", "")
    match = re.search(r"PR #(\d+)", title)
    if match:
        return match.group(1)
    body = issue.get("body", "")
    match = re.search(r"\*\*PR\*\*: #(\d+)", body)
    if match:
        return match.group(1)
    return ""


def extract_score(issue: dict) -> str:
    """Extract Ralph score from issue title or body."""
    import re
    title = issue.get("title", "")
    match = re.search(r"score:\s*(\d+)", title)
    if match:
        return match.group(1)
    body = issue.get("body", "")
    match = re.search(r"Score:\s*(\d+)", body)
    if match:
        return match.group(1)
    return ""


def compute_age(created_at: str) -> str:
    """Compute human-readable age from ISO timestamp."""
    try:
        if not created_at:
            return "?"
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - created
        hours = delta.total_seconds() / 3600
        if hours < 1:
            return f"{int(delta.total_seconds() / 60)}m"
        if hours < 24:
            return f"{int(hours)}h"
        return f"{int(hours / 24)}d"
    except (ValueError, TypeError):
        return "?"


def process_issues(issues: list[dict]) -> list[dict]:
    """Process raw issues into structured format sorted by priority."""
    processed = []
    for issue in issues:
        entry = {
            "number": issue.get("number", 0),
            "title": issue.get("title", ""),
            "priority": extract_priority(issue),
            "pr": extract_pr_number(issue),
            "score": extract_score(issue),
            "age": compute_age(issue.get("createdAt", "")),
            "assignees": [a.get("login", "") for a in issue.get("assignees", [])],
        }
        processed.append(entry)

    # Sort by priority order
    processed.sort(key=lambda x: PRIORITY_ORDER.get(x["priority"], 99))
    return processed


def format_text(issues: list[dict]) -> str:
    """Format issues as human-readable text."""
    if not issues:
        return "No open ralph-fix issues."

    lines = [f"Open ralph-fix issues: {len(issues)}", ""]
    for i in issues:
        assignee = ", ".join(i["assignees"]) if i["assignees"] else "unassigned"
        pr_info = f" (PR #{i['pr']})" if i["pr"] else ""
        score_info = f" score:{i['score']}" if i["score"] else ""
        lines.append(
            f"  #{i['number']} [{i['priority']}] {i['title']}{pr_info}{score_info} "
            f"({i['age']} old, {assignee})"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Monitor ralph-fix GitHub issues")
    parser.add_argument(
        "--repo", default=DEFAULT_REPO, help=f"GitHub repo (default: {DEFAULT_REPO})"
    )
    parser.add_argument(
        "--format", choices=["json", "text"], default="text",
        help="Output format (default: text)"
    )
    args = parser.parse_args()

    issues = fetch_ralph_issues(args.repo)
    processed = process_issues(issues)

    if args.format == "json":
        print(json.dumps(processed, indent=2))
    else:
        print(format_text(processed))


if __name__ == "__main__":
    main()
