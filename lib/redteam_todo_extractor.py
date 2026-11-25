"""
Red-Team Article TODO Extractor for GRIP.

Reads red-team articles (from GitHub issues) and extracts actionable TODOs
to be added to MEMORY.md and as new GitHub issues for the RSI loop.

This closes the feedback loop: articles critique progress -> TODOs are
created -> RSI loop executes TODOs -> next article evaluates results.

Usage:
    python3 lib/redteam_todo_extractor.py extract          # Extract from latest article
    python3 lib/redteam_todo_extractor.py extract --issue 121  # From specific issue
    python3 lib/redteam_todo_extractor.py sync             # Sync article TODOs to MEMORY

Issue #116: Periodic red-team article automation
Version: 1.0.0
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from insights_issue_budget import create_issue

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO = "grip-platform/GRIP"


def _gh_json(cmd_args: list, timeout: int = 15) -> list | dict:
    """Run gh CLI and parse JSON output."""
    try:
        result = subprocess.run(
            ["gh"] + cmd_args,
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip()}
        return json.loads(result.stdout) if result.stdout.strip() else []
    except Exception as e:
        return {"error": str(e)}


def get_redteam_articles(limit: int = 5) -> list[dict]:
    """Fetch recent red-team articles from GitHub issues.

    Returns:
        List of {number, title, body, created_at} for red-team issues.
    """
    issues = _gh_json([
        "issue", "list", "-R", REPO,
        "--search", "RSI Loop Quality Assessment",
        "--json", "number,title,body,createdAt",
        "--limit", str(limit),
    ])
    if isinstance(issues, dict) and "error" in issues:
        return []
    return issues


def extract_todos(article_body: str) -> list[dict]:
    """Extract actionable TODOs from a red-team article.

    Parses:
    - Red-Team Concerns sections for problems to fix
    - Recommendations sections for actionable items
    - Metrics table for risk items (RISK/FAIL assessments)

    Returns:
        List of {action, source, priority, category}
    """
    todos = []

    # Extract concerns (numbered sections after "Red-Team Concerns")
    concerns_match = re.search(
        r'### Red-Team Concerns[^\n]*\n(.*?)(?=\n### [A-Z]|$)',
        article_body, re.DOTALL
    )
    if concerns_match:
        concern_text = concerns_match.group(1)
        # Extract bold items (may be prefixed with #### N. or just numbered)
        for match in re.finditer(r'\*\*([^*]+)\*\*:\s*([^\n]+)', concern_text):
            label = match.group(1).strip()
            detail = match.group(2).strip()
            todos.append({
                "action": f"Fix: {label} - {detail[:100]}",
                "source": "red-team-concern",
                "priority": "high",
                "category": "quality",
            })

    # Extract recommendations
    recs_match = re.search(
        r'### Recommendations\n(.*?)(?=### |$)',
        article_body, re.DOTALL
    )
    if recs_match:
        rec_text = recs_match.group(1)
        for match in re.finditer(r'\d+\.\s+\*\*([^*]+)\*\*:\s*([^\n]+)', rec_text):
            label = match.group(1).strip()
            detail = match.group(2).strip()
            todos.append({
                "action": f"Implement: {label} - {detail[:100]}",
                "source": "red-team-recommendation",
                "priority": "medium",
                "category": "improvement",
            })

    # Extract RISK/FAIL items from metrics table
    for match in re.finditer(r'\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*(RISK|FAIL|Action needed)\s*\|', article_body):
        metric = match.group(1).strip()
        value = match.group(2).strip()
        assessment = match.group(3).strip()
        todos.append({
            "action": f"Address {assessment}: {metric} = {value}",
            "source": "red-team-metrics",
            "priority": "high" if assessment == "FAIL" else "medium",
            "category": "metrics",
        })

    # Extract score
    score_match = re.search(r'Red-team score\s*\|\s*(\d+)/100', article_body)
    if score_match:
        score = int(score_match.group(1))
        if score < 70:
            todos.append({
                "action": f"Critical: Red-team score {score}/100 - immediate course correction needed",
                "source": "red-team-score",
                "priority": "critical",
                "category": "quality",
            })

    return todos


def format_todos(todos: list[dict], issue_number: int = 0) -> str:
    """Format extracted TODOs as readable text."""
    if not todos:
        return "No actionable TODOs extracted from article."

    lines = [f"## Red-Team TODOs (from Issue #{issue_number})" if issue_number else "## Red-Team TODOs"]
    lines.append("")

    by_priority = {"critical": [], "high": [], "medium": [], "low": []}
    for todo in todos:
        by_priority.get(todo["priority"], by_priority["medium"]).append(todo)

    for priority in ["critical", "high", "medium", "low"]:
        items = by_priority[priority]
        if items:
            lines.append(f"### {priority.upper()}")
            for item in items:
                lines.append(f"- [{item['source']}] {item['action']}")
            lines.append("")

    return "\n".join(lines)


def create_issues_from_todos(
    todos: list[dict], source_issue: int = 0, *, runner=None, ledger_path=None
) -> list[dict]:
    """Create GitHub issues from high-priority red-team TODOs.

    Only creates issues for critical and high priority items within the shared budget.
    """
    results = []
    high_priority = [t for t in todos if t["priority"] in ("critical", "high")]

    for todo in high_priority[:3]:  # Max 3 issues per extraction
        title = f"fix: {todo['action'][:60]}"
        source_ref = f"Extracted from red-team Issue #{source_issue}" if source_issue else "Extracted from red-team article"
        body = (
            f"## Source\n\n{source_ref}\n\n"
            f"## Action Required\n\n{todo['action']}\n\n"
            f"## Priority\n\n{todo['priority'].upper()}\n\n"
            f"## Category\n\n{todo['category']}\n\n"
            f"---\n*Auto-generated by `lib/redteam_todo_extractor.py`*"
        )

        result = create_issue(title, body, runner=runner, ledger_path=ledger_path)
        results.append({"todo": todo["action"][:50], **result})

    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 lib/redteam_todo_extractor.py <extract|sync> [--issue N]")
        sys.exit(1)

    cmd = sys.argv[1]

    # Parse --issue flag
    issue_number = 0
    if "--issue" in sys.argv:
        idx = sys.argv.index("--issue")
        if idx + 1 < len(sys.argv):
            issue_number = int(sys.argv[idx + 1])

    if cmd == "extract":
        if issue_number:
            # Fetch specific issue
            issue = _gh_json([
                "issue", "view", str(issue_number), "-R", REPO,
                "--json", "number,title,body",
            ])
            if isinstance(issue, dict) and "error" not in issue:
                todos = extract_todos(issue.get("body", ""))
                print(format_todos(todos, issue_number))
            else:
                print(f"Failed to fetch issue #{issue_number}")
                sys.exit(1)
        else:
            # Use latest red-team article
            articles = get_redteam_articles(limit=1)
            if articles:
                article = articles[0]
                todos = extract_todos(article.get("body", ""))
                print(format_todos(todos, article.get("number", 0)))
            else:
                print("No red-team articles found.")
                sys.exit(1)

    elif cmd == "sync":
        # Extract TODOs and create issues
        articles = get_redteam_articles(limit=1)
        if not articles:
            print("No red-team articles found.")
            sys.exit(1)

        article = articles[0]
        todos = extract_todos(article.get("body", ""))
        print(format_todos(todos, article.get("number", 0)))

        if todos:
            print("\nCreating GitHub issues for high-priority items...")
            results = create_issues_from_todos(todos, article.get("number", 0))
            for r in results:
                if "issue_url" in r:
                    print(f"  Created: {r['issue_url']}")
                elif "skipped" in r:
                    print(f"  Skipped: {r['skipped']}")
                else:
                    print(f"  Failed: {r.get('error', 'unknown')}")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
