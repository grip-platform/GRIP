"""
Auto Sprint Retrospective Generator.

Analyzes git history, PRs, and issues to generate sprint retrospectives.
Outputs in text, Slack mrkdwn, JSON, or grip_retrospective MCP app format.
"""

import json
import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from collections import Counter
from typing import Optional
import argparse
import sys


@dataclass
class RetroItem:
    text: str
    category: str       # "positive", "negative", "action"
    source: str         # "pr", "commit", "issue", "pattern"
    reference: str = ""  # PR #123, commit abc1234, Issue #45
    votes: int = 0


@dataclass
class SprintRetro:
    sprint_name: str
    period: str
    went_well: list = field(default_factory=list)
    didnt_go_well: list = field(default_factory=list)
    action_items: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    generated_at: str = ""


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def parse_git_log(log_text: str) -> list:
    """Parse git log --format='%H|%an|%ad|%s' --date=short output."""
    commits = []
    if not log_text or not log_text.strip():
        return commits
    for line in log_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        sha, author, date, subject = parts
        commits.append({
            "sha": sha.strip(),
            "author": author.strip(),
            "date": date.strip(),
            "subject": subject.strip(),
        })
    return commits


def parse_pr_list(pr_text: str) -> list:
    """Parse gh pr list --json output (JSON array)."""
    prs = []
    if not pr_text or not pr_text.strip():
        return prs
    try:
        raw = json.loads(pr_text)
    except (json.JSONDecodeError, ValueError):
        return prs
    for item in raw:
        pr = {
            "number": item.get("number", 0),
            "title": item.get("title", ""),
            "state": item.get("state", "OPEN"),
            "author": item.get("author", {}).get("login", "") if isinstance(item.get("author"), dict) else str(item.get("author", "")),
            "createdAt": item.get("createdAt", ""),
            "mergedAt": item.get("mergedAt", ""),
            "labels": [l.get("name", "") if isinstance(l, dict) else str(l) for l in item.get("labels", [])],
            "url": item.get("url", ""),
            "headRefName": item.get("headRefName", ""),
        }
        prs.append(pr)
    return prs


def parse_issue_list(issue_text: str) -> list:
    """Parse gh issue list --json output (JSON array)."""
    issues = []
    if not issue_text or not issue_text.strip():
        return issues
    try:
        raw = json.loads(issue_text)
    except (json.JSONDecodeError, ValueError):
        return issues
    for item in raw:
        issue = {
            "number": item.get("number", 0),
            "title": item.get("title", ""),
            "state": item.get("state", "OPEN"),
            "labels": [l.get("name", "") if isinstance(l, dict) else str(l) for l in item.get("labels", [])],
            "assignees": [a.get("login", "") if isinstance(a, dict) else str(a) for a in item.get("assignees", [])],
            "createdAt": item.get("createdAt", ""),
            "url": item.get("url", ""),
        }
        issues.append(issue)
    return issues


# ---------------------------------------------------------------------------
# Detection: Positives
# ---------------------------------------------------------------------------

def _extract_scope(subject: str) -> str:
    """Extract scope from conventional commit, e.g. 'feat(auth): ...' -> 'auth'."""
    m = re.match(r"^\w+\(([^)]+)\)", subject)
    return m.group(1) if m else ""


def _strip_prefix(subject: str) -> str:
    """Strip conventional commit prefix: 'feat: add X' -> 'add X'."""
    m = re.match(r"^\w+(?:\([^)]*\))?:\s*(.+)", subject)
    return m.group(1) if m else subject


def detect_positives(commits: list, prs: list, issues: list) -> list:
    """Find what went well."""
    items = []
    seen_refs = set()

    # Merged PRs
    for pr in prs:
        if pr.get("mergedAt") or pr.get("state") == "MERGED":
            ref = f"PR #{pr['number']}"
            if ref not in seen_refs:
                seen_refs.add(ref)
                items.append(RetroItem(
                    text=f"Shipped {ref}: {pr['title']}",
                    category="positive",
                    source="pr",
                    reference=ref,
                ))

    # Feature commits
    for c in commits:
        subj = c["subject"]
        if subj.lower().startswith("feat"):
            desc = _strip_prefix(subj)
            ref = f"commit {c['sha'][:7]}"
            items.append(RetroItem(
                text=f"New feature: {desc}",
                category="positive",
                source="commit",
                reference=ref,
            ))

    # Test additions
    test_files = set()
    for c in commits:
        subj = c["subject"].lower()
        if "test" in subj:
            m = re.search(r"test[_\s]+(\w+)", subj)
            module = m.group(1) if m else "modules"
            if module not in test_files:
                test_files.add(module)
                items.append(RetroItem(
                    text=f"Added tests for {module}",
                    category="positive",
                    source="commit",
                    reference=f"commit {c['sha'][:7]}",
                ))

    # Documentation
    for c in commits:
        subj = c["subject"].lower()
        if subj.startswith("docs:") or subj.startswith("doc:"):
            desc = _strip_prefix(c["subject"])
            items.append(RetroItem(
                text=f"Improved docs: {desc}",
                category="positive",
                source="commit",
                reference=f"commit {c['sha'][:7]}",
            ))

    # Performance
    for c in commits:
        subj = c["subject"].lower()
        if subj.startswith("perf:") or subj.startswith("perf("):
            desc = _strip_prefix(c["subject"])
            items.append(RetroItem(
                text=f"Performance: {desc}",
                category="positive",
                source="commit",
                reference=f"commit {c['sha'][:7]}",
            ))

    # High-velocity days (3+ commits in a day)
    date_counts = Counter(c["date"] for c in commits)
    for date, count in date_counts.items():
        if count >= 3:
            items.append(RetroItem(
                text=f"Productive day: {count} commits on {date}",
                category="positive",
                source="pattern",
                reference=date,
            ))

    # Closed issues (not bugs)
    for issue in issues:
        if issue.get("state") == "CLOSED":
            labels = [l.lower() for l in issue.get("labels", [])]
            if "bug" not in labels:
                ref = f"Issue #{issue['number']}"
                if ref not in seen_refs:
                    seen_refs.add(ref)
                    items.append(RetroItem(
                        text=f"Resolved: {ref} {issue['title']}",
                        category="positive",
                        source="issue",
                        reference=ref,
                    ))

    return items[:10]


# ---------------------------------------------------------------------------
# Detection: Negatives
# ---------------------------------------------------------------------------

def _detect_reverts(commits: list) -> list:
    """Detect reverted commits."""
    items = []
    for c in commits:
        subj = c["subject"]
        if subj.lower().startswith("revert") or "revert " in subj.lower():
            desc = _strip_prefix(subj) if ":" in subj else subj
            items.append(RetroItem(
                text=f"Had to revert: {desc}",
                category="negative",
                source="commit",
                reference=f"commit {c['sha'][:7]}",
            ))
    return items


def _detect_bugs_in_new_features(commits: list) -> list:
    """Detect fix commits that follow feat commits on the same scope."""
    items = []
    feat_scopes: set = set()
    for c in commits:
        subj = c["subject"].lower()
        scope = _extract_scope(c["subject"])
        if subj.startswith("feat"):
            if scope:
                feat_scopes.add(scope)
        elif subj.startswith("fix") and scope and scope in feat_scopes:
            desc = _strip_prefix(c["subject"])
            items.append(RetroItem(
                text=f"Bug in new feature ({scope}): {desc}",
                category="negative",
                source="commit",
                reference=f"commit {c['sha'][:7]}",
            ))
    return items


def _detect_stale_prs(prs: list, seen_refs: set) -> list:
    """Detect PRs open longer than 3 days."""
    items = []
    now = datetime.now(tz=None)
    for pr in prs:
        state = pr.get("state", "OPEN")
        if state == "OPEN" or (state != "MERGED" and not pr.get("mergedAt")):
            created = pr.get("createdAt", "")
            if created:
                try:
                    created_dt = datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    age_days = (now - created_dt).days
                    if age_days > 3:
                        ref = f"PR #{pr['number']}"
                        if ref not in seen_refs:
                            seen_refs.add(ref)
                            items.append(RetroItem(
                                text=f"Stale PR: #{pr['number']} open {age_days} days",
                                category="negative",
                                source="pr",
                                reference=ref,
                            ))
                except (ValueError, TypeError):
                    pass
    return items


def _detect_repeated_fixes(commits: list) -> list:
    """Detect scopes with 2+ fix commits."""
    items = []
    fix_scopes: Counter = Counter()
    for c in commits:
        subj = c["subject"].lower()
        if subj.startswith("fix"):
            scope = _extract_scope(c["subject"]) or _strip_prefix(c["subject"]).split()[0] if _strip_prefix(c["subject"]) else ""
            if scope:
                fix_scopes[scope] += 1
    for scope, count in fix_scopes.items():
        if count >= 2:
            items.append(RetroItem(
                text=f"Repeated fixes ({count}x) on {scope}",
                category="negative",
                source="pattern",
                reference=f"{count} fix commits",
            ))
    return items


def detect_negatives(commits: list, prs: list, issues: list) -> list:
    """Find what didn't go well."""
    items: list = []
    seen_refs: set = set()

    items.extend(_detect_reverts(commits))
    items.extend(_detect_bugs_in_new_features(commits))
    items.extend(_detect_stale_prs(prs, seen_refs))

    # Bug issues
    for issue in issues:
        labels = [l.lower() for l in issue.get("labels", [])]
        if "bug" in labels:
            ref = f"Issue #{issue['number']}"
            if ref not in seen_refs:
                seen_refs.add(ref)
                items.append(RetroItem(
                    text=f"Bug reported: #{issue['number']} {issue['title']}",
                    category="negative",
                    source="issue",
                    reference=ref,
                ))

    # Force pushes
    for c in commits:
        subj = c["subject"].lower()
        if "force push" in subj or "force-push" in subj:
            items.append(RetroItem(
                text=f"Force push needed on {_extract_scope(c['subject']) or 'branch'}",
                category="negative",
                source="commit",
                reference=f"commit {c['sha'][:7]}",
            ))

    items.extend(_detect_repeated_fixes(commits))
    return items[:10]


# ---------------------------------------------------------------------------
# Detection: Action Items
# ---------------------------------------------------------------------------

def detect_actions(commits: list, prs: list, issues: list, negatives: list) -> list:
    """Generate action items from data and negatives."""
    items = []
    seen_refs = set()

    # Open issues not assigned
    for issue in issues:
        if issue.get("state") == "OPEN" and not issue.get("assignees"):
            ref = f"Issue #{issue['number']}"
            if ref not in seen_refs:
                seen_refs.add(ref)
                items.append(RetroItem(
                    text=f"Triage: Issue #{issue['number']} - {issue['title']}",
                    category="action",
                    source="issue",
                    reference=ref,
                ))

    # Open PRs needing review
    for pr in prs:
        state = pr.get("state", "OPEN")
        if state == "OPEN" or (state != "MERGED" and not pr.get("mergedAt")):
            ref = f"PR #{pr['number']}"
            if ref not in seen_refs:
                seen_refs.add(ref)
                items.append(RetroItem(
                    text=f"Review PR #{pr['number']}: {pr['title']}",
                    category="action",
                    source="pr",
                    reference=ref,
                ))

    # From negatives: reverts -> add regression tests
    revert_scopes = set()
    stale_pr_count = 0
    repeated_fix_scopes = set()
    for neg in negatives:
        if "revert" in neg.text.lower():
            scope = neg.text.split(":")[-1].strip().split()[0] if ":" in neg.text else ""
            if scope and scope not in revert_scopes:
                revert_scopes.add(scope)
                items.append(RetroItem(
                    text=f"Add regression tests for {scope}",
                    category="action",
                    source="pattern",
                    reference=neg.reference,
                ))
        if "stale pr" in neg.text.lower():
            stale_pr_count += 1
        if "repeated fixes" in neg.text.lower():
            m = re.search(r"on\s+(.+)$", neg.text)
            scope = m.group(1) if m else ""
            if scope and scope not in repeated_fix_scopes:
                repeated_fix_scopes.add(scope)
                items.append(RetroItem(
                    text=f"Refactor {scope} to prevent recurring bugs",
                    category="action",
                    source="pattern",
                    reference=neg.reference,
                ))

    if stale_pr_count > 0:
        items.append(RetroItem(
            text="Close or revive stale PRs",
            category="action",
            source="pattern",
            reference=f"{stale_pr_count} stale PRs",
        ))

    return items[:10]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def calculate_stats(commits: list, prs: list, issues: list) -> dict:
    """Sprint summary statistics."""
    merged_prs = [p for p in prs if p.get("mergedAt") or p.get("state") == "MERGED"]
    open_prs = [p for p in prs if p.get("state") == "OPEN" or (p.get("state") != "MERGED" and not p.get("mergedAt"))]
    open_issues = [i for i in issues if i.get("state") == "OPEN"]
    closed_issues = [i for i in issues if i.get("state") == "CLOSED"]
    bug_issues = [i for i in issues if "bug" in [l.lower() for l in i.get("labels", [])]]

    authors = set(c.get("author", "") for c in commits if c.get("author"))
    dates = set(c.get("date", "") for c in commits if c.get("date"))

    feat_count = sum(1 for c in commits if c["subject"].lower().startswith("feat"))
    fix_count = sum(1 for c in commits if c["subject"].lower().startswith("fix"))
    revert_count = sum(1 for c in commits if c["subject"].lower().startswith("revert"))

    return {
        "total_commits": len(commits),
        "total_prs": len(prs),
        "merged_prs": len(merged_prs),
        "open_prs": len(open_prs),
        "total_issues": len(issues),
        "open_issues": len(open_issues),
        "closed_issues": len(closed_issues),
        "bug_issues": len(bug_issues),
        "contributors": len(authors),
        "active_days": len(dates),
        "features": feat_count,
        "fixes": fix_count,
        "reverts": revert_count,
    }


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def compose_retro(sprint_name: str, period: str, commits: list, prs: list, issues: list) -> SprintRetro:
    """Build full retrospective from parsed data."""
    went_well = detect_positives(commits, prs, issues)
    didnt_go_well = detect_negatives(commits, prs, issues)
    action_items = detect_actions(commits, prs, issues, didnt_go_well)
    stats = calculate_stats(commits, prs, issues)

    return SprintRetro(
        sprint_name=sprint_name,
        period=period,
        went_well=went_well,
        didnt_go_well=didnt_go_well,
        action_items=action_items,
        stats=stats,
        generated_at=datetime.now(tz=None).isoformat() + "Z",
    )


def analyze_retro(
    sprint_name: str = "Current Sprint",
    period: Optional[str] = None,
    days: int = 7,
) -> SprintRetro:
    """Run full retrospective by calling git/gh commands."""
    end = datetime.now()
    start = end - timedelta(days=days)
    if period is None:
        period = f"{start.strftime('%b %d')}-{end.strftime('%d, %Y')}"

    since = start.strftime("%Y-%m-%d")

    # Git log
    try:
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%H|%an|%ad|%s", "--date=short"],
            capture_output=True, text=True, timeout=30,
        )
        commits = parse_git_log(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        commits = []

    # PRs
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state=all", "--limit=100",
             "--json=number,title,state,author,createdAt,mergedAt,labels,url,headRefName"],
            capture_output=True, text=True, timeout=30,
        )
        prs = parse_pr_list(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        prs = []

    # Issues
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--state=all", "--limit=100",
             "--json=number,title,state,labels,assignees,createdAt,url"],
            capture_output=True, text=True, timeout=30,
        )
        issues = parse_issue_list(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        issues = []

    return compose_retro(sprint_name, period, commits, prs, issues)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _retro_item_to_text(item: RetroItem, bullet: str = "-") -> str:
    ref = f" [{item.reference}]" if item.reference else ""
    return f"  {bullet} {item.text}{ref}"


def format_text(retro: SprintRetro) -> str:
    """Plain text format."""
    lines = []
    lines.append(f"Sprint Retrospective: {retro.sprint_name}")
    lines.append(f"Period: {retro.period}")
    lines.append(f"Generated: {retro.generated_at}")
    lines.append("")

    lines.append("== What Went Well ==")
    if retro.went_well:
        for item in retro.went_well:
            lines.append(_retro_item_to_text(item, "+"))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("== What Didn't Go Well ==")
    if retro.didnt_go_well:
        for item in retro.didnt_go_well:
            lines.append(_retro_item_to_text(item, "-"))
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("== Action Items ==")
    if retro.action_items:
        for item in retro.action_items:
            lines.append(_retro_item_to_text(item, "!"))
    else:
        lines.append("  (none)")
    lines.append("")

    # Stats
    s = retro.stats
    if s:
        lines.append("== Stats ==")
        lines.append(f"  Commits: {s.get('total_commits', 0)}")
        lines.append(f"  PRs merged: {s.get('merged_prs', 0)} / {s.get('total_prs', 0)}")
        lines.append(f"  Issues closed: {s.get('closed_issues', 0)} / {s.get('total_issues', 0)}")
        lines.append(f"  Contributors: {s.get('contributors', 0)}")
        lines.append(f"  Active days: {s.get('active_days', 0)}")
        lines.append(f"  Features: {s.get('features', 0)} | Fixes: {s.get('fixes', 0)} | Reverts: {s.get('reverts', 0)}")

    return "\n".join(lines)


def format_slack(retro: SprintRetro) -> str:
    """Slack mrkdwn format (*bold* not **bold**)."""
    lines = []
    lines.append(f"*Sprint Retrospective: {retro.sprint_name}*")
    lines.append(f"_{retro.period}_")
    lines.append("")

    lines.append("*What Went Well*")
    if retro.went_well:
        for item in retro.went_well:
            ref = f" `{item.reference}`" if item.reference else ""
            lines.append(f"  + {item.text}{ref}")
    else:
        lines.append("  _(none)_")
    lines.append("")

    lines.append("*What Didn't Go Well*")
    if retro.didnt_go_well:
        for item in retro.didnt_go_well:
            ref = f" `{item.reference}`" if item.reference else ""
            lines.append(f"  - {item.text}{ref}")
    else:
        lines.append("  _(none)_")
    lines.append("")

    lines.append("*Action Items*")
    if retro.action_items:
        for item in retro.action_items:
            ref = f" `{item.reference}`" if item.reference else ""
            lines.append(f"  ! {item.text}{ref}")
    else:
        lines.append("  _(none)_")
    lines.append("")

    s = retro.stats
    if s:
        lines.append(f"_Commits: {s.get('total_commits', 0)} | PRs: {s.get('merged_prs', 0)}/{s.get('total_prs', 0)} | Issues: {s.get('closed_issues', 0)}/{s.get('total_issues', 0)} | Contributors: {s.get('contributors', 0)}_")

    return "\n".join(lines)


def format_json(retro: SprintRetro) -> str:
    """JSON format."""
    data = {
        "sprint_name": retro.sprint_name,
        "period": retro.period,
        "generated_at": retro.generated_at,
        "went_well": [asdict(i) for i in retro.went_well],
        "didnt_go_well": [asdict(i) for i in retro.didnt_go_well],
        "action_items": [asdict(i) for i in retro.action_items],
        "stats": retro.stats,
    }
    return json.dumps(data, indent=2)


def format_retrospective(retro: SprintRetro) -> dict:
    """Returns dict for grip_retrospective MCP app."""
    return {
        "title": f"Sprint Retro: {retro.sprint_name} ({retro.period})",
        "columns": [
            {
                "id": "went-well",
                "heading": "Went Well",
                "icon": "+",
                "color": "#22c55e",
                "items": [
                    {"id": f"pos-{i}", "text": item.text, "votes": item.votes}
                    for i, item in enumerate(retro.went_well)
                ],
            },
            {
                "id": "didnt-go-well",
                "heading": "Didn't Go Well",
                "icon": "-",
                "color": "#ef4444",
                "items": [
                    {"id": f"neg-{i}", "text": item.text, "votes": item.votes}
                    for i, item in enumerate(retro.didnt_go_well)
                ],
            },
            {
                "id": "action-items",
                "heading": "Action Items",
                "icon": "!",
                "color": "#3b82f6",
                "items": [
                    {"id": f"act-{i}", "text": item.text, "votes": item.votes}
                    for i, item in enumerate(retro.action_items)
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Auto Sprint Retrospective Generator")
    parser.add_argument("--sprint", default="Current Sprint", help="Sprint name")
    parser.add_argument("--days", type=int, default=7, help="Number of days to analyze")
    parser.add_argument("--period", default=None, help="Period label (e.g. 'Feb 10-17, 2026')")
    parser.add_argument("--format", choices=["text", "slack", "json", "retrospective"], default="text", help="Output format")
    args = parser.parse_args()

    retro = analyze_retro(sprint_name=args.sprint, period=args.period, days=args.days)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "retrospective": lambda r: json.dumps(format_retrospective(r), indent=2),
    }
    print(formatters[args.format](retro))


if __name__ == "__main__":
    main()
