"""
Deliverable Report Generator -- client-facing work summaries from git/PR data.

Generates professional reports for standups, weekly reviews, sprint reviews,
and client updates. Focuses on EXTERNAL communication -- what to tell stakeholders.

Usage:
    python3 lib/deliverable_generator.py --type standup
    python3 lib/deliverable_generator.py --type weekly --format markdown
    python3 lib/deliverable_generator.py --type sprint --period "Sprint 14" --format slack
    python3 lib/deliverable_generator.py --type client --format json
    echo "$(git log --oneline -20)" | python3 lib/deliverable_generator.py --type standup
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class WorkItem:
    """A single deliverable work item."""
    title: str
    category: str  # "feature", "bugfix", "improvement", "infrastructure", "docs"
    status: str  # "completed", "in_progress", "blocked"
    pr_number: int = 0
    issue_number: int = 0
    impact: str = ""  # user-facing impact description
    demo_url: str = ""


@dataclass
class DeliverableReport:
    """Structured deliverable report for external consumption."""
    report_type: str  # "standup", "weekly", "sprint", "client"
    period: str  # "Today", "This Week", "Sprint 14", etc.
    date: str
    highlights: list = field(default_factory=list)
    work_items: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    next_steps: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    team_members: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {"feature", "bugfix", "improvement", "infrastructure", "docs"}
VALID_STATUSES = {"completed", "in_progress", "blocked"}
VALID_REPORT_TYPES = {"standup", "weekly", "sprint", "client"}

CATEGORY_MAP = {
    "feat": "feature",
    "feature": "feature",
    "add": "feature",
    "new": "feature",
    "fix": "bugfix",
    "hotfix": "bugfix",
    "bugfix": "bugfix",
    "refactor": "improvement",
    "perf": "improvement",
    "improve": "improvement",
    "style": "improvement",
    "chore": "infrastructure",
    "ci": "infrastructure",
    "build": "infrastructure",
    "infra": "infrastructure",
    "docs": "docs",
    "doc": "docs",
    "test": "infrastructure",
}

# Internal content patterns to filter from client reports
INTERNAL_PATTERNS = [
    r"(?i)\brsi\b",
    r"(?i)\bgoodhart\b",
    r"(?i)\bgrip\s+hook",
    r"(?i)\bgate\s+[a-z]\b",
    r"(?i)\bkono\b",
    r"(?i)\bbroly\b",
    r"(?i)\bmeta[-\s]?work",
    r"(?i)\bralph\b",
    r"(?i)\bwiggum\b",
    r"(?i)\bcontext[-\s]?gate",
    r"(?i)\bforging[-\s]?skills",
    r"(?i)\banti[-\s]?drift",
    r"(?i)\btoken\s+budget",
    r"(?i)\bsession\s+continuity",
    r"(?i)\bPARAMOUNT\b",
    r"(?i)\bhook\s+(output|error|gate)",
    r"(?i)\bpre[-\s]?commit\s+hook",
    r"(?i)\bpost[-\s]?tool[-\s]?use",
]

# Category priority for highlight ranking (higher = more important for clients)
CATEGORY_PRIORITY = {
    "feature": 5,
    "bugfix": 4,
    "improvement": 3,
    "docs": 2,
    "infrastructure": 1,
}

MAX_HIGHLIGHTS = 5


# ---------------------------------------------------------------------------
# Parsing functions
# ---------------------------------------------------------------------------

def _classify_commit_message(message: str) -> tuple:
    """Classify a commit message into (category, clean_title)."""
    message = message.strip()
    if not message:
        return ("infrastructure", "")

    # Match conventional commit format: type(scope): description
    match = re.match(r'^(\w+)(?:\([^)]*\))?[!]?:\s*(.*)', message)
    if match:
        prefix = match.group(1).lower()
        title = match.group(2).strip()
        category = CATEGORY_MAP.get(prefix, "infrastructure")
        if title:
            title = title[0].upper() + title[1:]
        return (category, title)

    # Merge commit patterns
    merge_match = re.match(r'^Merge\s+(pull request|branch)\s+', message, re.IGNORECASE)
    if merge_match:
        # Extract the last path segment after the final org/user separator
        # e.g. "from CodeTonight-SA/feat/evolutionary-refactor" -> "feat/evolutionary-refactor"
        from_match = re.search(r'from\s+[\w.-]+/(.+)', message)
        if from_match:
            branch = from_match.group(1).strip()
            # Convert branch to readable title (strip type prefix for display)
            display_branch = re.sub(r'^[\w]+/', '', branch)
            title = display_branch.replace("-", " ").replace("_", " ")
            title = title[0].upper() + title[1:] if title else message
            branch_lower = branch.lower()
            for prefix, cat in CATEGORY_MAP.items():
                if branch_lower.startswith(prefix + "/") or branch_lower.startswith(prefix + "-"):
                    return (cat, title)
            return ("improvement", title)
        return ("improvement", message)

    # Non-conventional: capitalize and treat as improvement
    title = message[0].upper() + message[1:] if message else message
    return ("improvement", title)


def _extract_pr_number(message: str) -> int:
    """Extract PR number from commit message (#NNN)."""
    match = re.search(r'#(\d+)', message)
    return int(match.group(1)) if match else 0


def _extract_issue_number(message: str) -> int:
    """Extract issue number from closes/fixes/resolves #NNN patterns."""
    match = re.search(r'(?:closes?|fixes?|resolves?)\s+#(\d+)', message, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def parse_git_log_for_deliverables(log_text: str) -> list:
    """Extract WorkItems from git log output.

    Accepts git log --oneline format (hash + message) or plain messages.
    """
    if not log_text or not log_text.strip():
        return []

    items = []
    seen_titles = set()

    for line in log_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Strip leading hash (7-40 hex chars + space)
        message = re.sub(r'^[0-9a-f]{7,40}\s+', '', line)
        if not message:
            continue

        category, title = _classify_commit_message(message)
        if not title:
            continue

        # Dedup by normalized title
        title_key = title.lower().strip()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        pr_number = _extract_pr_number(message)
        issue_number = _extract_issue_number(message)

        items.append(WorkItem(
            title=title,
            category=category,
            status="completed",
            pr_number=pr_number,
            issue_number=issue_number,
        ))

    return items


# ---------------------------------------------------------------------------
# Report generation helpers
# ---------------------------------------------------------------------------

def generate_highlights(items: list) -> list:
    """Auto-generate highlights from highest-impact items.

    Priority: features > bugfixes > improvements > docs > infrastructure.
    Max 5, deduplicated.
    """
    if not items:
        return []

    completed = [i for i in items if i.status == "completed"]
    if not completed:
        completed = list(items)

    def sort_key(item):
        priority = CATEGORY_PRIORITY.get(item.category, 0)
        has_impact = 1 if item.impact else 0
        return (priority, has_impact)

    sorted_items = sorted(completed, key=sort_key, reverse=True)

    highlights = []
    seen = set()
    for item in sorted_items:
        if len(highlights) >= MAX_HIGHLIGHTS:
            break
        text = item.impact if item.impact else item.title
        text_key = text.lower().strip()
        if text_key not in seen:
            seen.add(text_key)
            highlights.append(text)

    return highlights


def generate_next_steps(items: list, blockers: list = None) -> list:
    """Suggest next steps from in-progress items and blockers.

    - In-progress -> "Continue: ..."
    - Blocked -> "Unblock: ..."
    - Each blocker -> "Resolve: ..."
    """
    steps = []
    blockers = blockers or []
    seen = set()

    for item in items:
        if item.status == "in_progress":
            step = f"Continue: {item.title}"
            step_key = step.lower()
            if step_key not in seen:
                seen.add(step_key)
                steps.append(step)

    for item in items:
        if item.status == "blocked":
            step = f"Unblock: {item.title}"
            step_key = step.lower()
            if step_key not in seen:
                seen.add(step_key)
                steps.append(step)

    for blocker in blockers:
        step = f"Resolve: {blocker}"
        step_key = step.lower()
        if step_key not in seen:
            seen.add(step_key)
            steps.append(step)

    if not steps:
        completed = [i for i in items if i.status == "completed"]
        if completed:
            steps.append("Review and verify completed deliverables")
            feature_count = sum(1 for i in completed if i.category == "feature")
            if feature_count > 0:
                steps.append(f"Plan follow-up for {feature_count} shipped feature(s)")

    return steps


def _is_internal(text: str) -> bool:
    """Check if text contains internal infrastructure references."""
    for pattern in INTERNAL_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def filter_internal(items: list) -> list:
    """Remove internal/infrastructure items for client reports.

    Filters items matching INTERNAL_PATTERNS and infrastructure category.
    """
    result = []
    for item in items:
        if _is_internal(item.title):
            continue
        if item.impact and _is_internal(item.impact):
            continue
        if item.category == "infrastructure":
            continue
        result.append(item)
    return result


def _calculate_metrics(items: list) -> dict:
    """Calculate metrics from work items."""
    total = len(items)
    completed = sum(1 for i in items if i.status == "completed")
    in_progress = sum(1 for i in items if i.status == "in_progress")
    blocked = sum(1 for i in items if i.status == "blocked")
    prs = sum(1 for i in items if i.pr_number > 0)
    issues = sum(1 for i in items if i.issue_number > 0)

    by_category = {}
    for item in items:
        by_category[item.category] = by_category.get(item.category, 0) + 1

    return {
        "total_items": total,
        "completed": completed,
        "in_progress": in_progress,
        "blocked": blocked,
        "prs_referenced": prs,
        "issues_referenced": issues,
        "by_category": by_category,
    }


# ---------------------------------------------------------------------------
# Report composition
# ---------------------------------------------------------------------------

def compose_report(
    items: list,
    report_type: str,
    period: str,
    blockers: list = None,
) -> DeliverableReport:
    """Compose a full deliverable report.

    For client reports, internal items are automatically filtered.
    """
    blockers = blockers or []

    if report_type == "client":
        report_items = filter_internal(items)
    else:
        report_items = list(items)

    highlights = generate_highlights(report_items)
    next_steps = generate_next_steps(report_items, blockers)
    metrics = _calculate_metrics(report_items)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return DeliverableReport(
        report_type=report_type,
        period=period,
        date=date,
        highlights=highlights,
        work_items=report_items,
        blockers=blockers,
        next_steps=next_steps,
        metrics=metrics,
        team_members=[],
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_text(report: DeliverableReport) -> str:
    """Format report as clean terminal output."""
    lines = []
    divider = "-" * 60

    lines.append(divider)
    lines.append(f"  {report.report_type.upper()} REPORT | {report.period}")
    lines.append(f"  Date: {report.date}")
    lines.append(divider)

    if report.highlights:
        lines.append("")
        lines.append("  HIGHLIGHTS")
        for i, h in enumerate(report.highlights, 1):
            lines.append(f"  {i}. {h}")

    completed = [i for i in report.work_items if i.status == "completed"]
    in_progress = [i for i in report.work_items if i.status == "in_progress"]
    blocked = [i for i in report.work_items if i.status == "blocked"]

    if completed:
        lines.append("")
        lines.append("  COMPLETED")
        for item in completed:
            pr_ref = f" (PR #{item.pr_number})" if item.pr_number else ""
            lines.append(f"  - [{item.category}] {item.title}{pr_ref}")

    if in_progress:
        lines.append("")
        lines.append("  IN PROGRESS")
        for item in in_progress:
            pr_ref = f" (PR #{item.pr_number})" if item.pr_number else ""
            lines.append(f"  - [{item.category}] {item.title}{pr_ref}")

    if blocked:
        lines.append("")
        lines.append("  BLOCKED")
        for item in blocked:
            lines.append(f"  - [{item.category}] {item.title}")

    if report.blockers:
        lines.append("")
        lines.append("  BLOCKERS")
        for b in report.blockers:
            lines.append(f"  ! {b}")

    if report.next_steps:
        lines.append("")
        lines.append("  NEXT STEPS")
        for step in report.next_steps:
            lines.append(f"  > {step}")

    metrics = report.metrics
    if metrics:
        lines.append("")
        lines.append("  METRICS")
        lines.append(f"  Total: {metrics.get('total_items', 0)} items | "
                     f"Completed: {metrics.get('completed', 0)} | "
                     f"In Progress: {metrics.get('in_progress', 0)} | "
                     f"Blocked: {metrics.get('blocked', 0)}")
        if metrics.get("prs_referenced"):
            lines.append(f"  PRs: {metrics['prs_referenced']} | "
                        f"Issues: {metrics.get('issues_referenced', 0)}")

    lines.append("")
    lines.append(divider)
    return "\n".join(lines)


def format_slack(report: DeliverableReport) -> str:
    """Format report as Slack mrkdwn.

    Uses *bold* (not **bold**), _italic_ (not *italic*).
    """
    lines = []

    type_label = report.report_type.capitalize()
    lines.append(f"*{type_label} Report* | {report.period} | {report.date}")
    lines.append("")

    if report.highlights:
        lines.append("*Highlights*")
        for h in report.highlights:
            lines.append(f"  - {h}")
        lines.append("")

    completed = [i for i in report.work_items if i.status == "completed"]
    in_progress = [i for i in report.work_items if i.status == "in_progress"]
    blocked = [i for i in report.work_items if i.status == "blocked"]

    if completed:
        lines.append("*Completed*")
        for item in completed:
            pr_ref = f" (PR #{item.pr_number})" if item.pr_number else ""
            lines.append(f"  - {item.title}{pr_ref}")
        lines.append("")

    if in_progress:
        lines.append("*In Progress*")
        for item in in_progress:
            pr_ref = f" (PR #{item.pr_number})" if item.pr_number else ""
            lines.append(f"  - {item.title}{pr_ref}")
        lines.append("")

    if blocked:
        lines.append("*Blocked*")
        for item in blocked:
            lines.append(f"  - {item.title}")
        lines.append("")

    if report.blockers:
        lines.append("*Blockers*")
        for b in report.blockers:
            lines.append(f"  - {b}")
        lines.append("")

    if report.next_steps:
        lines.append("*Next Steps*")
        for step in report.next_steps:
            lines.append(f"  - {step}")
        lines.append("")

    m = report.metrics
    if m:
        lines.append(f"_Items: {m.get('total_items', 0)} | "
                     f"Completed: {m.get('completed', 0)} | "
                     f"PRs: {m.get('prs_referenced', 0)}_")

    return "\n".join(lines)


def format_slack_blocks(report: DeliverableReport) -> list:
    """Format report as Slack Block Kit blocks."""
    try:
        from aide.integrations import text_to_blocks
        return text_to_blocks(format_slack(report))
    except (ImportError, Exception):
        return [{"type": "section", "text": {"type": "mrkdwn", "text": format_slack(report)}}]


def format_json(report: DeliverableReport) -> str:
    """Format report as JSON."""
    data = asdict(report)
    return json.dumps(data, indent=2, default=str)


def format_markdown(report: DeliverableReport) -> str:
    """Format report as GitHub-flavored markdown."""
    lines = []

    type_label = report.report_type.capitalize()
    lines.append(f"# {type_label} Report: {report.period}")
    lines.append("")
    lines.append(f"**Date**: {report.date}")
    lines.append("")

    if report.highlights:
        lines.append("## Highlights")
        lines.append("")
        for h in report.highlights:
            lines.append(f"- {h}")
        lines.append("")

    completed = [i for i in report.work_items if i.status == "completed"]
    in_progress = [i for i in report.work_items if i.status == "in_progress"]
    blocked = [i for i in report.work_items if i.status == "blocked"]

    if completed:
        lines.append("## Completed")
        lines.append("")
        for item in completed:
            pr_ref = f" (PR #{item.pr_number})" if item.pr_number else ""
            lines.append(f"- **[{item.category}]** {item.title}{pr_ref}")
        lines.append("")

    if in_progress:
        lines.append("## In Progress")
        lines.append("")
        for item in in_progress:
            pr_ref = f" (PR #{item.pr_number})" if item.pr_number else ""
            lines.append(f"- **[{item.category}]** {item.title}{pr_ref}")
        lines.append("")

    if blocked:
        lines.append("## Blocked")
        lines.append("")
        for item in blocked:
            lines.append(f"- **[{item.category}]** {item.title}")
        lines.append("")

    if report.blockers:
        lines.append("## Blockers")
        lines.append("")
        for b in report.blockers:
            lines.append(f"- {b}")
        lines.append("")

    if report.next_steps:
        lines.append("## Next Steps")
        lines.append("")
        for step in report.next_steps:
            lines.append(f"- {step}")
        lines.append("")

    m = report.metrics
    if m:
        lines.append("## Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Items | {m.get('total_items', 0)} |")
        lines.append(f"| Completed | {m.get('completed', 0)} |")
        lines.append(f"| In Progress | {m.get('in_progress', 0)} |")
        lines.append(f"| Blocked | {m.get('blocked', 0)} |")
        lines.append(f"| PRs Referenced | {m.get('prs_referenced', 0)} |")
        lines.append(f"| Issues Referenced | {m.get('issues_referenced', 0)} |")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated {report.date}*")

    return "\n".join(lines)


def format_digest(report: DeliverableReport) -> dict:
    """Format report as dict for grip_digest MCP app.

    Returns cards per section for dashboard rendering.
    """
    cards = []

    type_label = report.report_type.capitalize()
    cards.append({
        "type": "header",
        "title": f"{type_label} Report",
        "subtitle": f"{report.period} | {report.date}",
    })

    if report.highlights:
        cards.append({
            "type": "highlights",
            "title": "Highlights",
            "items": report.highlights,
        })

    completed = [i for i in report.work_items if i.status == "completed"]
    if completed:
        cards.append({
            "type": "items",
            "title": "Completed",
            "items": [
                {
                    "text": item.title,
                    "category": item.category,
                    "pr": item.pr_number,
                    "issue": item.issue_number,
                }
                for item in completed
            ],
        })

    in_progress = [i for i in report.work_items if i.status == "in_progress"]
    if in_progress:
        cards.append({
            "type": "items",
            "title": "In Progress",
            "items": [
                {
                    "text": item.title,
                    "category": item.category,
                    "pr": item.pr_number,
                }
                for item in in_progress
            ],
        })

    blocked = [i for i in report.work_items if i.status == "blocked"]
    if blocked:
        cards.append({
            "type": "items",
            "title": "Blocked",
            "items": [
                {"text": item.title, "category": item.category}
                for item in blocked
            ],
        })

    if report.blockers:
        cards.append({
            "type": "blockers",
            "title": "Blockers",
            "items": report.blockers,
        })

    if report.next_steps:
        cards.append({
            "type": "next_steps",
            "title": "Next Steps",
            "items": report.next_steps,
        })

    if report.metrics:
        cards.append({
            "type": "metrics",
            "title": "Metrics",
            "data": report.metrics,
        })

    return {
        "report_type": report.report_type,
        "period": report.period,
        "date": report.date,
        "cards": cards,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate deliverable reports from git log data (stdin)."
    )
    parser.add_argument(
        "--type",
        choices=["standup", "weekly", "sprint", "client"],
        default="standup",
        help="Report type (default: standup)",
    )
    parser.add_argument(
        "--period",
        default="",
        help="Period label (e.g. 'Sprint 14'). Auto-generated if omitted.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "markdown", "digest"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--blocker",
        action="append",
        default=[],
        help="Add a blocker (repeatable)",
    )

    args = parser.parse_args()

    # Read git log from stdin
    if not sys.stdin.isatty():
        log_text = sys.stdin.read()
    else:
        log_text = ""

    # Auto-generate period label
    period = args.period
    if not period:
        period_map = {
            "standup": "Today",
            "weekly": "This Week",
            "sprint": "Current Sprint",
            "client": "Delivery Period",
        }
        period = period_map.get(args.type, "Today")

    # Parse and compose
    items = parse_git_log_for_deliverables(log_text)
    report = compose_report(items, args.type, period, args.blocker)

    # Format and output
    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "markdown": format_markdown,
        "digest": lambda r: json.dumps(format_digest(r), indent=2),
    }
    formatter = formatters[args.format]
    print(formatter(report))


if __name__ == "__main__":
    main()
