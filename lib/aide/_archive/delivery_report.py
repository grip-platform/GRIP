"""
Client Delivery Report Generator — git-verified delivery reports.

Generates professional client-facing reports from git commits, merged PRs,
closed issues, and optional KONO memories. Filters internal content.

Usage:
    python3 lib/copilot/delivery_report.py generate --since 2026-02-01 --until 2026-02-16
    python3 lib/copilot/delivery_report.py generate --since 2026-02-01 --format json
    python3 lib/copilot/delivery_report.py generate --since 2026-02-01 --format slack
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO_SLUG = os.environ.get("GRIP_REPO", "grip-platform/GRIP")

# Internal content patterns to filter from client reports
INTERNAL_PATTERNS = [
    r"(?i)\brsi\b", r"(?i)\bgoodhart\b", r"(?i)\bgrip\s+hook",
    r"(?i)\bgate\s+[a-z]\b", r"(?i)\bkono\b", r"(?i)\bbroly\b",
    r"(?i)\bmeta[-\s]?work", r"(?i)\bralph\b", r"(?i)\bwiggum\b",
    r"(?i)\bcontext[-\s]?gate", r"(?i)\bforging[-\s]?skills",
    r"(?i)\banti[-\s]?drift", r"(?i)\btoken\s+budget",
    r"(?i)\bsession\s+continuity", r"(?i)\bPARAMOUNT",
]

# PR classification (imported pattern from metrics_tracker)
FEATURE_PREFIXES = ["feat:", "feature:", "add:", "new:"]
FIX_PREFIXES = ["fix:", "hotfix:", "bugfix:"]
CHORE_PREFIXES = ["chore:", "refactor:", "test:", "docs:", "ci:", "build:", "style:"]


@dataclass
class PREntry:
    """A merged pull request."""
    number: int
    title: str
    merged_at: str
    category: str = ""  # feature, fix, chore
    client_title: str = ""


@dataclass
class IssueEntry:
    """A closed issue."""
    number: int
    title: str
    closed_at: str
    labels: list = field(default_factory=list)


@dataclass
class DeliveryReport:
    """Structured delivery report."""
    period_start: str
    period_end: str
    generated_at: str = ""
    features: list = field(default_factory=list)
    fixes: list = field(default_factory=list)
    maintenance: list = field(default_factory=list)
    issues_closed: int = 0
    prs_merged: int = 0
    commits_total: int = 0
    highlights: list = field(default_factory=list)
    next_sprint: list = field(default_factory=list)


def is_internal(text: str) -> bool:
    """Check if text contains internal GRIP infrastructure references."""
    for pattern in INTERNAL_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def classify_pr_title(title: str) -> str:
    """Classify a PR title as feature, fix, or chore."""
    title_lower = title.lower().strip()
    for prefix in FEATURE_PREFIXES:
        if title_lower.startswith(prefix):
            return "feature"
    for prefix in FIX_PREFIXES:
        if title_lower.startswith(prefix):
            return "fix"
    for prefix in CHORE_PREFIXES:
        if title_lower.startswith(prefix):
            return "chore"
    # Default: if it starts with fix: it's user-facing
    if title_lower.startswith("fix:"):
        return "fix"
    return "chore"


def make_client_safe(title: str) -> str:
    """Transform a PR/issue title into client-safe language.

    Strips conventional commit prefixes and internal jargon.
    """
    # Remove conventional commit prefix
    cleaned = re.sub(r"^(feat|fix|chore|docs|test|refactor|ci|build|style|perf)(\([^)]*\))?:\s*", "", title)
    # Capitalize first letter
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def get_merged_prs(since: str, until: str) -> list[PREntry]:
    """Fetch merged PRs in date range via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "-R", REPO_SLUG,
             "--state", "merged", "--limit", "100",
             "--json", "number,title,mergedAt"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []

        prs = json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []

    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))

    entries = []
    for pr in prs:
        merged = pr.get("mergedAt", "")
        if not merged:
            continue
        try:
            merged_dt = datetime.fromisoformat(merged.replace("Z", "+00:00"))
        except ValueError:
            continue

        if since_dt <= merged_dt <= until_dt:
            category = classify_pr_title(pr["title"])
            entries.append(PREntry(
                number=pr["number"],
                title=pr["title"],
                merged_at=merged,
                category=category,
                client_title=make_client_safe(pr["title"]),
            ))

    return entries


def get_closed_issues(since: str, until: str) -> list[IssueEntry]:
    """Fetch closed issues in date range via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "-R", REPO_SLUG,
             "--state", "closed", "--limit", "100",
             "--json", "number,title,closedAt,labels"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout) if result.stdout.strip() else []
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []

    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))

    entries = []
    for issue in issues:
        closed = issue.get("closedAt", "")
        if not closed:
            continue
        try:
            closed_dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
        except ValueError:
            continue

        if since_dt <= closed_dt <= until_dt:
            labels = [l.get("name", "") for l in issue.get("labels", [])]
            entries.append(IssueEntry(
                number=issue["number"],
                title=issue["title"],
                closed_at=closed,
                labels=labels,
            ))

    return entries


def get_commit_count(since: str, until: str) -> int:
    """Count commits in date range."""
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}", f"--until={until}",
             "--oneline", "--no-merges"],
            capture_output=True, text=True, timeout=10,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode != 0:
            return 0
        return len([l for l in result.stdout.strip().split("\n") if l.strip()])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0


def get_open_issues_for_next_sprint(limit: int = 5) -> list[dict]:
    """Fetch open issues for next sprint preview."""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "-R", REPO_SLUG,
             "--state", "open", "--limit", str(limit),
             "--json", "number,title,labels"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []
        issues = json.loads(result.stdout) if result.stdout.strip() else []
        return [
            {"number": i["number"], "title": make_client_safe(i["title"])}
            for i in issues
            if not is_internal(i["title"])
        ][:limit]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        return []


def generate_report(since: str, until: str) -> DeliveryReport:
    """Generate a full delivery report for the given date range."""
    prs = get_merged_prs(since, until)
    issues = get_closed_issues(since, until)
    commits = get_commit_count(since, until)
    next_sprint = get_open_issues_for_next_sprint()

    # Separate PRs by category, filtering internal ones
    features = []
    fixes = []
    maintenance = []

    for pr in prs:
        if is_internal(pr.title):
            maintenance.append({"number": pr.number, "title": pr.client_title})
            continue

        if pr.category == "feature":
            features.append({"number": pr.number, "title": pr.client_title})
        elif pr.category == "fix":
            fixes.append({"number": pr.number, "title": pr.client_title})
        else:
            maintenance.append({"number": pr.number, "title": pr.client_title})

    # Generate highlights from features
    highlights = [f["title"] for f in features[:3]]

    return DeliveryReport(
        period_start=since,
        period_end=until,
        generated_at=datetime.now(timezone.utc).isoformat(),
        features=features,
        fixes=fixes,
        maintenance=maintenance,
        issues_closed=len(issues),
        prs_merged=len(prs),
        commits_total=commits,
        highlights=highlights,
        next_sprint=next_sprint,
    )


def markdown_to_mrkdwn(text: str) -> str:
    """Convert GitHub-flavored markdown to Slack mrkdwn format.

    Slack uses different syntax:
    - Bold: *text* (not **text**)
    - Links: <url|text> (not [text](url))
    - Headers: *Header* with newline (not # Header)
    """
    # **bold** -> *bold*
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # [text](url) -> <url|text>
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<\2|\1>', text)
    # ## Header -> *Header*
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    return text


def format_slack(report: DeliveryReport) -> str:
    """Format report as a Slack mrkdwn message."""
    lines = [
        f":package: *Delivery Report* | {report.period_start[:10]} to {report.period_end[:10]}",
        "",
        f"*Summary*: {report.prs_merged} PRs merged, {report.issues_closed} issues closed, {report.commits_total} commits",
        "",
    ]

    if report.features:
        lines.append(":rocket: *Features Shipped*")
        for f in report.features:
            lines.append(f"  - PR #{f['number']}: {f['title']}")
        lines.append("")

    if report.fixes:
        lines.append(":wrench: *Bug Fixes*")
        for f in report.fixes:
            lines.append(f"  - PR #{f['number']}: {f['title']}")
        lines.append("")

    if report.maintenance:
        lines.append(":gear: *Technical Improvements*")
        count = len(report.maintenance)
        lines.append(f"  {count} infrastructure and maintenance items addressed")
        lines.append("")

    if report.next_sprint:
        lines.append(":calendar: *Next Sprint Preview*")
        for item in report.next_sprint[:3]:
            lines.append(f"  - #{item['number']}: {item['title']}")
        lines.append("")

    return "\n".join(lines)


def format_slack_blocks(report: DeliveryReport) -> list:
    """Format report as Slack Block Kit blocks for rich rendering.

    Returns a list of Block Kit blocks ready for chat.postMessage(blocks=...).
    The fallback text should be format_slack(report).
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Delivery Report: {report.period_start[:10]} to {report.period_end[:10]}"
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*PRs Merged*\n{report.prs_merged}"},
                {"type": "mrkdwn", "text": f"*Issues Closed*\n{report.issues_closed}"},
                {"type": "mrkdwn", "text": f"*Commits*\n{report.commits_total}"},
                {"type": "mrkdwn", "text": f"*Features*\n{len(report.features)}"},
            ]
        },
    ]

    if report.features:
        feature_text = "\n".join(
            f"- PR #{f['number']}: {f['title']}" for f in report.features[:8]
        )
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":rocket: *Features Shipped*\n{feature_text}"}
        })

    if report.fixes:
        fix_text = "\n".join(
            f"- PR #{f['number']}: {f['title']}" for f in report.fixes[:5]
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":wrench: *Bug Fixes*\n{fix_text}"}
        })

    if report.maintenance:
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f":gear: {len(report.maintenance)} technical improvements addressed"}
            ]
        })

    if report.next_sprint:
        sprint_text = "\n".join(
            f"- #{item['number']}: {item['title']}" for item in report.next_sprint[:3]
        )
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":calendar: *Next Sprint*\n{sprint_text}"}
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"_Generated {report.generated_at[:16]}Z_"}
        ]
    })

    return blocks


def format_markdown(report: DeliveryReport) -> str:
    """Format report as Markdown."""
    lines = [
        f"# Delivery Report: {report.period_start[:10]} to {report.period_end[:10]}",
        "",
        f"**Summary**: {report.prs_merged} PRs merged | {report.issues_closed} issues closed | {report.commits_total} commits",
        "",
    ]

    if report.features:
        lines.append("## Features Shipped")
        lines.append("")
        for f in report.features:
            lines.append(f"- **PR #{f['number']}**: {f['title']}")
        lines.append("")

    if report.fixes:
        lines.append("## Bug Fixes")
        lines.append("")
        for f in report.fixes:
            lines.append(f"- **PR #{f['number']}**: {f['title']}")
        lines.append("")

    if report.maintenance:
        lines.append("## Technical Improvements")
        lines.append("")
        lines.append(f"{len(report.maintenance)} infrastructure and maintenance items addressed.")
        lines.append("")

    if report.next_sprint:
        lines.append("## Next Sprint Preview")
        lines.append("")
        for item in report.next_sprint[:5]:
            lines.append(f"- #{item['number']}: {item['title']}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"*Generated {report.generated_at[:16]}Z*")

    return "\n".join(lines)


def format_json(report: DeliveryReport) -> str:
    """Format report as JSON."""
    return json.dumps(asdict(report), indent=2, default=str)


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: delivery_report.py generate --since DATE [--until DATE] [--format slack|json|markdown]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd != "generate":
        print(f"Unknown command: {cmd}")
        sys.exit(1)

    # Parse args
    since = None
    until = datetime.now(timezone.utc).isoformat()
    fmt = "slack"

    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]
            if "T" not in since:
                since = f"{since}T00:00:00+00:00"
            i += 2
        elif args[i] == "--until" and i + 1 < len(args):
            until = args[i + 1]
            if "T" not in until:
                until = f"{until}T23:59:59+00:00"
            i += 2
        elif args[i] == "--format" and i + 1 < len(args):
            fmt = args[i + 1]
            i += 2
        else:
            i += 1

    if not since:
        # Default: last 14 days
        since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

    report = generate_report(since, until)

    if fmt == "json":
        print(format_json(report))
    elif fmt == "markdown":
        print(format_markdown(report))
    else:
        print(format_slack(report))


if __name__ == "__main__":
    main()
