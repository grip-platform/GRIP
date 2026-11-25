"""
Backlog Re-Prioritisation Engine for GRIP (Issue #166).

Multi-signal scoring system that automatically prioritises GitHub issues
using 7 weighted dimensions. Assigns P0-P3 priority labels and generates
ranked backlog views.

Scoring Dimensions (max 100 points):
  1. User Impact (25pts)  - Is this user-facing? Does it improve UX?
  2. Dependencies (20pts) - How many items does this unblock?
  3. Freshness (15pts)    - Recently created/updated = more relevant
  4. Effort (15pts)       - Smaller effort = higher priority (quick wins)
  5. Alignment (10pts)    - Matches current sprint/roadmap goals?
  6. Bug Severity (10pts) - Bugs with "bug" label get severity boost
  7. Staleness (5pts)     - Penalty for issues open > 30 days without progress

Usage:
    python3 lib/backlog_prioritiser.py rank               # Ranked backlog
    python3 lib/backlog_prioritiser.py score 170           # Score single issue
    python3 lib/backlog_prioritiser.py label               # Apply P0-P3 labels
    python3 lib/backlog_prioritiser.py slack               # Slack-formatted report
    python3 lib/backlog_prioritiser.py check-dup "title"   # Check for duplicate issues
"""

import json
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from lib.subprocess_utils import run_gh as _run_gh

REPO_SLUG = os.environ.get("GRIP_REPO", "grip-platform/GRIP")

# Priority thresholds
P0_THRESHOLD = 75  # Critical: do now
P1_THRESHOLD = 55  # High: do this sprint
P2_THRESHOLD = 35  # Medium: backlog
P3_THRESHOLD = 0   # Low: nice to have

# Feature keywords that signal user-facing impact
USER_FACING_KEYWORDS = [
    "dashboard", "ui", "ux", "frontend", "app", "mobile", "user",
    "command", "skill", "mode", "copilot", "mcp", "workflow",
    "notification", "report", "deploy", "onboard", "wizard",
]

# Infrastructure keywords (lower user impact)
INFRA_KEYWORDS = [
    "hook", "gate", "internal", "refactor", "ci", "cd", "meta",
    "rsi", "autonomous", "test", "lint", "cleanup", "debt",
]

# Effort estimation keywords
SMALL_EFFORT_KEYWORDS = ["fix", "typo", "small", "quick", "simple", "minor"]
LARGE_EFFORT_KEYWORDS = ["epic", "redesign", "architecture", "migration", "rewrite"]

# Current sprint goals (configurable)
SPRINT_GOALS = [
    "user-facing features",
    "copilot improvements",
    "mcp apps",
    "deploy pipeline",
    "adaptive learning",
]


@dataclass
class ScoredIssue:
    """An issue with its priority score breakdown."""
    number: int
    title: str
    labels: list = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    comments: int = 0
    body: str = ""

    # Score dimensions
    user_impact: float = 0.0
    dependencies: float = 0.0
    freshness: float = 0.0
    effort: float = 0.0
    alignment: float = 0.0
    bug_severity: float = 0.0
    staleness_penalty: float = 0.0

    # Computed
    total_score: float = 0.0
    priority: str = "P3"  # P0, P1, P2, P3
    reasoning: str = ""


def _ensure_own_lib() -> None:
    """Put this module's own lib folder on sys.path once, never the shared tree's (GRIP#9857)."""
    own_lib = str(Path(__file__).resolve().parent)
    if own_lib not in sys.path:
        sys.path.insert(0, own_lib)


def fetch_open_issues(limit: int = 100) -> list:
    """Fetch open issues from GitHub."""
    output = _run_gh([
        "issue", "list", "-R", REPO_SLUG, "--state", "open",
        "--json", "number,title,labels,createdAt,updatedAt,comments,body",
        "--limit", str(limit),
    ])
    if not output:
        return []
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return []


def score_user_impact(issue: dict) -> float:
    """Score user-facing impact (0-25 points).

    User-facing features get higher scores. Infrastructure gets lower.
    Labels like 'enhancement' or 'feature' boost the score.
    """
    score = 10.0  # Base score
    title_lower = issue.get("title", "").lower()
    body_lower = issue.get("body", "").lower()
    text = title_lower + " " + body_lower
    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]

    # Label signals
    if any(l in ("feature", "enhancement", "user-facing") for l in labels):
        score += 10.0
    if any(l in ("infra", "internal", "chore") for l in labels):
        score -= 5.0

    # Keyword signals
    user_hits = sum(1 for kw in USER_FACING_KEYWORDS if kw in text)
    infra_hits = sum(1 for kw in INFRA_KEYWORDS if kw in text)

    score += min(user_hits * 2.0, 10.0)
    score -= min(infra_hits * 1.5, 8.0)

    # Title prefix signals
    if title_lower.startswith("feat:") or title_lower.startswith("feat("):
        score += 5.0

    return max(0.0, min(25.0, score))


def score_dependencies(issue: dict, all_issues: list) -> float:
    """Score how many items this issue unblocks (0-20 points).

    Issues referenced by others (via #N) score higher.
    """
    number = issue.get("number", 0)
    blocked_count = 0

    for other in all_issues:
        if other.get("number") == number:
            continue
        body = other.get("body", "") or ""
        # Count references to this issue
        if f"#{number}" in body:
            blocked_count += 1

    # 5 points per blocked issue, max 20
    return min(blocked_count * 5.0, 20.0)


def score_freshness(issue: dict) -> float:
    """Score recency (0-15 points). Recently created/updated = higher."""
    now = datetime.now(timezone.utc)

    updated = issue.get("updatedAt", "")
    if updated:
        try:
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            days_since = (now - updated_dt).days
            # Full score if updated today, decays over 30 days
            return max(0.0, 15.0 - (days_since * 0.5))
        except (ValueError, TypeError):
            pass

    return 5.0  # Default mid-score


def score_effort(issue: dict) -> float:
    """Score effort inversely (0-15 points). Smaller = higher score (quick wins)."""
    title_lower = issue.get("title", "").lower()
    body_lower = (issue.get("body", "") or "").lower()
    text = title_lower + " " + body_lower
    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]

    # Size labels
    if any(l in ("size: s", "small", "quick-win") for l in labels):
        return 15.0
    if any(l in ("size: m", "medium") for l in labels):
        return 10.0
    if any(l in ("size: l", "large", "epic") for l in labels):
        return 5.0
    if any(l in ("size: xl",) for l in labels):
        return 2.0

    # Keyword estimation
    small_hits = sum(1 for kw in SMALL_EFFORT_KEYWORDS if kw in text)
    large_hits = sum(1 for kw in LARGE_EFFORT_KEYWORDS if kw in text)

    if small_hits > large_hits:
        return 12.0
    if large_hits > small_hits:
        return 5.0

    # Body length as proxy (shorter description = simpler task)
    body_len = len(issue.get("body", "") or "")
    if body_len < 200:
        return 12.0
    if body_len < 500:
        return 10.0
    if body_len < 1000:
        return 8.0
    return 5.0


def score_alignment(issue: dict) -> float:
    """Score alignment with current sprint goals (0-10 points)."""
    title_lower = issue.get("title", "").lower()
    body_lower = (issue.get("body", "") or "").lower()
    text = title_lower + " " + body_lower

    hits = sum(1 for goal in SPRINT_GOALS if goal in text)
    return min(hits * 3.0, 10.0)


def score_bug_severity(issue: dict) -> float:
    """Score bug severity (0-10 points). Bugs get a priority boost."""
    labels = [l.get("name", "").lower() for l in issue.get("labels", [])]

    if "bug" not in labels:
        return 0.0

    title_lower = issue.get("title", "").lower()

    # Critical bugs
    if any(kw in title_lower for kw in ("crash", "broken", "security", "data loss", "blocking")):
        return 10.0
    # Normal bugs
    if any(kw in title_lower for kw in ("error", "fail", "wrong", "missing")):
        return 7.0
    # Minor bugs
    return 5.0


def score_staleness_penalty(issue: dict) -> float:
    """Penalty for stale issues (0 to -5 points)."""
    now = datetime.now(timezone.utc)
    created = issue.get("createdAt", "")
    updated = issue.get("updatedAt", "")
    comments = issue.get("comments", [])
    comment_count = len(comments) if isinstance(comments, list) else int(comments or 0)

    if not created:
        return 0.0

    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        days_open = (now - created_dt).days

        # No penalty if less than 30 days old
        if days_open < 30:
            return 0.0

        # Penalty increases with age, but discussion (comments) reduces it
        base_penalty = min((days_open - 30) * 0.1, 5.0)
        comment_reduction = min(comment_count * 0.5, 3.0)

        return -max(0.0, base_penalty - comment_reduction)
    except (ValueError, TypeError):
        return 0.0


def score_issue(issue: dict, all_issues: list = None) -> ScoredIssue:
    """Score a single issue across all dimensions.

    Returns a ScoredIssue with breakdown and total score.
    """
    all_issues = all_issues or []

    scored = ScoredIssue(
        number=issue.get("number", 0),
        title=issue.get("title", ""),
        labels=[l.get("name", "") for l in issue.get("labels", [])],
        created_at=issue.get("createdAt", ""),
        updated_at=issue.get("updatedAt", ""),
        comments=len(issue.get("comments", [])) if isinstance(issue.get("comments"), list) else int(issue.get("comments", 0) or 0),
        body=issue.get("body", "") or "",
    )

    scored.user_impact = score_user_impact(issue)
    scored.dependencies = score_dependencies(issue, all_issues)
    scored.freshness = score_freshness(issue)
    scored.effort = score_effort(issue)
    scored.alignment = score_alignment(issue)
    scored.bug_severity = score_bug_severity(issue)
    scored.staleness_penalty = score_staleness_penalty(issue)

    scored.total_score = (
        scored.user_impact +
        scored.dependencies +
        scored.freshness +
        scored.effort +
        scored.alignment +
        scored.bug_severity +
        scored.staleness_penalty
    )

    # Assign priority tier
    if scored.total_score >= P0_THRESHOLD:
        scored.priority = "P0"
    elif scored.total_score >= P1_THRESHOLD:
        scored.priority = "P1"
    elif scored.total_score >= P2_THRESHOLD:
        scored.priority = "P2"
    else:
        scored.priority = "P3"

    # Generate reasoning
    top_dims = sorted([
        ("User Impact", scored.user_impact, 25),
        ("Dependencies", scored.dependencies, 20),
        ("Freshness", scored.freshness, 15),
        ("Effort", scored.effort, 15),
        ("Alignment", scored.alignment, 10),
        ("Bug Severity", scored.bug_severity, 10),
    ], key=lambda x: x[1], reverse=True)

    top_reasons = [f"{name}: {val:.0f}/{mx}" for name, val, mx in top_dims[:3] if val > 0]
    scored.reasoning = ", ".join(top_reasons)

    return scored


def rank_backlog(issues: list = None) -> list:
    """Score and rank all open issues.

    Returns list of ScoredIssue sorted by total_score descending.
    """
    if issues is None:
        issues = fetch_open_issues()

    scored = [score_issue(issue, issues) for issue in issues]
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored


def format_ranked_text(scored: list) -> str:
    """Format ranked backlog as text."""
    lines = [
        "Backlog Priority Ranking",
        "=" * 60,
        "",
    ]

    for i, s in enumerate(scored[:30], 1):
        label_str = ", ".join(s.labels[:3]) if s.labels else "none"
        lines.append(
            f"  {i:2d}. [{s.priority}] #{s.number}: {s.title[:55]}"
        )
        lines.append(
            f"      Score: {s.total_score:.0f}/100 | {s.reasoning}"
        )

    lines.append("")
    lines.append(f"Total: {len(scored)} issues ranked")

    # Summary by priority
    p_counts = defaultdict(int)
    for s in scored:
        p_counts[s.priority] += 1
    lines.append(f"  P0 (Critical): {p_counts.get('P0', 0)}")
    lines.append(f"  P1 (High):     {p_counts.get('P1', 0)}")
    lines.append(f"  P2 (Medium):   {p_counts.get('P2', 0)}")
    lines.append(f"  P3 (Low):      {p_counts.get('P3', 0)}")

    return "\n".join(lines)


def format_ranked_slack(scored: list) -> str:
    """Format ranked backlog as Slack mrkdwn."""
    lines = [
        "*Backlog Priority Ranking*",
        "",
    ]

    current_priority = None
    for s in scored[:20]:
        if s.priority != current_priority:
            current_priority = s.priority
            priority_label = {
                "P0": "P0 - Critical (do now)",
                "P1": "P1 - High (this sprint)",
                "P2": "P2 - Medium (backlog)",
                "P3": "P3 - Low (nice to have)",
            }.get(s.priority, s.priority)
            lines.append(f"\n*{priority_label}*")

        lines.append(f"  - #{s.number}: {s.title[:50]} _({s.total_score:.0f}pts)_")

    p_counts = defaultdict(int)
    for s in scored:
        p_counts[s.priority] += 1
    lines.append(f"\n_{len(scored)} issues: {p_counts.get('P0', 0)} critical, "
                 f"{p_counts.get('P1', 0)} high, {p_counts.get('P2', 0)} medium, "
                 f"{p_counts.get('P3', 0)} low_")

    return "\n".join(lines)


def format_ranked_json(scored: list) -> str:
    """Format ranked backlog as JSON."""
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_issues": len(scored),
        "by_priority": {},
        "issues": [],
    }

    p_counts = defaultdict(int)
    for s in scored:
        p_counts[s.priority] += 1
        data["issues"].append({
            "number": s.number,
            "title": s.title,
            "priority": s.priority,
            "total_score": round(s.total_score, 1),
            "breakdown": {
                "user_impact": round(s.user_impact, 1),
                "dependencies": round(s.dependencies, 1),
                "freshness": round(s.freshness, 1),
                "effort": round(s.effort, 1),
                "alignment": round(s.alignment, 1),
                "bug_severity": round(s.bug_severity, 1),
                "staleness_penalty": round(s.staleness_penalty, 1),
            },
            "reasoning": s.reasoning,
            "labels": s.labels,
        })

    data["by_priority"] = dict(p_counts)
    return json.dumps(data, indent=2)


def apply_priority_labels(scored: list, dry_run: bool = True) -> list:
    """Apply P0-P3 labels to issues on GitHub.

    Args:
        scored: List of ScoredIssue
        dry_run: If True, only print what would be done

    Returns:
        List of (number, old_priority, new_priority) tuples
    """
    changes = []
    priority_labels = {"P0", "P1", "P2", "P3"}

    for s in scored:
        current_priorities = [l for l in s.labels if l in priority_labels]
        current = current_priorities[0] if current_priorities else None

        if current == s.priority:
            continue  # No change needed

        changes.append((s.number, current, s.priority))

        if not dry_run:
            # Remove old priority label if present
            if current:
                _run_gh([
                    "issue", "edit", str(s.number),
                    "-R", REPO_SLUG,
                    "--remove-label", current,
                ])

            # Add new priority label
            _run_gh([
                "issue", "edit", str(s.number),
                "-R", REPO_SLUG,
                "--add-label", s.priority,
            ])

    return changes


def _title_similarity(a: str, b: str) -> float:
    """Jaccard similarity on lowercased word sets."""
    wa = set(re.findall(r'\w+', a.lower()))
    wb = set(re.findall(r'\w+', b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def check_duplicate(title: str, body: str = None, threshold: float = 0.8) -> list:
    """Check for duplicate issues before creation.

    Queries KONO semantic memory and existing GitHub issues for similarity.

    Args:
        title: Proposed issue title
        body: Optional issue body for additional context
        threshold: Similarity threshold (0.0-1.0). Default 0.8

    Returns:
        List of dicts: {source, number, title, similarity, url}
    """
    matches = []
    query = f"{title} {body or ''}".strip()

    # KONO semantic search
    try:
        _ensure_own_lib()
        from kono_substrate import MemorySubstrate
        substrate = MemorySubstrate()
        results = substrate.recall(query=query, limit=5)
        for r in results:
            score = float(r.get("score", 0))
            if score >= threshold:
                matches.append({
                    "source": "kono",
                    "number": r.get("id", ""),
                    "title": r.get("content", "")[:80],
                    "similarity": round(score, 3),
                    "url": "",
                })
    except (ImportError, Exception):
        pass

    # GitHub exact + fuzzy title match
    output = _run_gh([
        "issue", "list", "-R", REPO_SLUG, "--state", "all",
        "--json", "number,title,url", "--limit", "50",
    ])
    if output:
        try:
            gh_issues = json.loads(output)
            for issue in gh_issues:
                sim = _title_similarity(title, issue.get("title", ""))
                if sim >= threshold:
                    matches.append({
                        "source": "github",
                        "number": issue.get("number", ""),
                        "title": issue.get("title", ""),
                        "similarity": round(sim, 3),
                        "url": issue.get("url", ""),
                    })
        except json.JSONDecodeError:
            pass

    matches.sort(key=lambda x: x["similarity"], reverse=True)
    return matches


def main():
    """CLI entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "rank"

    if cmd == "rank":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "text"
        scored = rank_backlog()
        if fmt == "text":
            print(format_ranked_text(scored))
        elif fmt == "slack":
            print(format_ranked_slack(scored))
        elif fmt == "json":
            print(format_ranked_json(scored))
        else:
            print(f"Unknown format: {fmt}")
            sys.exit(1)

    elif cmd == "score":
        if len(sys.argv) < 3:
            print("Usage: backlog_prioritiser.py score <issue_number>")
            sys.exit(1)
        number = int(sys.argv[2])
        issues = fetch_open_issues()
        target = next((i for i in issues if i.get("number") == number), None)
        if not target:
            print(f"Issue #{number} not found in open issues")
            sys.exit(1)
        scored = score_issue(target, issues)
        print(f"Issue #{scored.number}: {scored.title}")
        print(f"  Priority: {scored.priority} ({scored.total_score:.0f}/100)")
        print(f"  User Impact:   {scored.user_impact:.0f}/25")
        print(f"  Dependencies:  {scored.dependencies:.0f}/20")
        print(f"  Freshness:     {scored.freshness:.0f}/15")
        print(f"  Effort:        {scored.effort:.0f}/15")
        print(f"  Alignment:     {scored.alignment:.0f}/10")
        print(f"  Bug Severity:  {scored.bug_severity:.0f}/10")
        print(f"  Staleness:     {scored.staleness_penalty:.0f}")
        print(f"  Reasoning: {scored.reasoning}")

    elif cmd == "label":
        dry_run = "--apply" not in sys.argv
        scored = rank_backlog()
        changes = apply_priority_labels(scored, dry_run=dry_run)
        if dry_run:
            print("DRY RUN (use --apply to execute):")
        for number, old, new in changes:
            old_str = old or "none"
            print(f"  #{number}: {old_str} -> {new}")
        if not changes:
            print("  No label changes needed")

    elif cmd == "slack":
        scored = rank_backlog()
        print(format_ranked_slack(scored))

    elif cmd == "check-dup":
        if len(sys.argv) < 3:
            print("Usage: backlog_prioritiser.py check-dup \"issue title\" [threshold]")
            sys.exit(1)
        dup_title = sys.argv[2]
        dup_threshold = float(sys.argv[3]) if len(sys.argv) > 3 else 0.8
        dupes = check_duplicate(dup_title, threshold=dup_threshold)
        if not dupes:
            print(f"No duplicates found above threshold {dup_threshold}")
        else:
            print(f"Potential duplicates for: \"{dup_title}\"")
            print(f"  Threshold: {dup_threshold}")
            print()
            for d in dupes:
                ref = f"#{d['number']}" if d["number"] else "(kono)"
                url = f"  {d['url']}" if d["url"] else ""
                print(f"  [{d['source']}] {ref}: {d['title']}")
                print(f"    Similarity: {d['similarity']:.3f}{url}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: rank [text|slack|json], score <N>, label [--apply], slack, check-dup \"title\" [threshold]")
        sys.exit(1)


if __name__ == "__main__":
    main()
