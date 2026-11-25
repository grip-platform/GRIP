"""
Project Health Dashboard — interactive health metrics for GRIP projects.

Collects data from git, GitHub, and local metrics to generate
interactive MCP App dashboards accessible on mobile via Claude.ai.

Usage:
    python3 lib/project_health.py summary          # Text summary
    python3 lib/project_health.py circuit           # Circuit dashboard data
    python3 lib/project_health.py kanban            # Sprint kanban data
    python3 lib/project_health.py progress          # Progress tracker data
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from lib.subprocess_utils import run_git as _run_git, run_gh as _run_gh


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
REPO_SLUG = os.environ.get("GRIP_REPO", "grip-platform/GRIP")


@dataclass
class HealthMetrics:
    """Project health snapshot."""
    # PR metrics
    prs_open: int = 0
    prs_merged_7d: int = 0
    prs_stale: int = 0  # open > 7 days
    avg_ralph_score: float = 0.0
    ralph_score_stddev: float = 0.0

    # Issue metrics
    issues_open: int = 0
    issues_closed_7d: int = 0
    issues_labeled_bug: int = 0

    # Commit metrics
    commits_7d: int = 0
    files_changed_7d: int = 0

    # Feature health
    feature_count_7d: int = 0
    infra_count_7d: int = 0
    feature_ratio: float = 0.0  # feature / (feature + infra)

    # Test health
    test_count: int = 0
    test_pass_rate: float = 100.0

    # Session health
    sessions_7d: int = 0
    avg_session_achievement: str = "unknown"

    # Computed
    overall_score: int = 0  # 0-100
    status: str = "unknown"  # healthy, warning, critical


def classify_pr(title: str) -> str:
    """Classify a PR as 'feature' or 'infra'."""
    title_lower = title.lower()
    feature_signals = ["feat:", "feature:", "add ", "new ", "implement", "ui ", "dashboard", "mcp"]
    infra_signals = ["chore:", "refactor:", "fix:", "test:", "ci:", "docs:", "perf:",
                     "hook", "gate", "rsi", "meta-", "internal"]

    for signal in feature_signals:
        if signal in title_lower:
            return "feature"
    for signal in infra_signals:
        if signal in title_lower:
            return "infra"
    return "feature"  # default to feature (benefit of the doubt)


def _collect_pr_metrics(m: HealthMetrics, since_7d: str) -> None:
    """Collect open/merged PR counts and feature ratio."""
    open_prs_json = _run_gh([
        "pr", "list", "-R", REPO_SLUG, "--state", "open",
        "--json", "number,title,createdAt,labels", "--limit", "50"
    ])
    if open_prs_json:
        try:
            open_prs = json.loads(open_prs_json)
            m.prs_open = len(open_prs)
            now = datetime.now(timezone.utc)
            for pr in open_prs:
                created = datetime.fromisoformat(pr["createdAt"].replace("Z", "+00:00"))
                if (now - created).days > 7:
                    m.prs_stale += 1
        except json.JSONDecodeError:
            pass

    merged_prs_json = _run_gh([
        "pr", "list", "-R", REPO_SLUG, "--state", "merged",
        "--json", "number,title,mergedAt", "--limit", "50"
    ])
    if merged_prs_json:
        try:
            merged_prs = json.loads(merged_prs_json)
            recent = [p for p in merged_prs
                      if p.get("mergedAt", "") >= since_7d]
            m.prs_merged_7d = len(recent)
            for pr in recent:
                cls = classify_pr(pr.get("title", ""))
                if cls == "feature":
                    m.feature_count_7d += 1
                else:
                    m.infra_count_7d += 1
            total = m.feature_count_7d + m.infra_count_7d
            m.feature_ratio = m.feature_count_7d / total if total > 0 else 0.0
        except json.JSONDecodeError:
            pass


def _collect_issue_metrics(m: HealthMetrics, since_7d: str) -> None:
    """Collect open/closed issue counts and bug labels."""
    issues_json = _run_gh([
        "issue", "list", "-R", REPO_SLUG, "--state", "open",
        "--json", "number,labels", "--limit", "100"
    ])
    if issues_json:
        try:
            issues = json.loads(issues_json)
            m.issues_open = len(issues)
            m.issues_labeled_bug = sum(
                1 for i in issues
                if any(l.get("name") == "bug" for l in i.get("labels", []))
            )
        except json.JSONDecodeError:
            pass

    closed_issues_json = _run_gh([
        "issue", "list", "-R", REPO_SLUG, "--state", "closed",
        "--json", "number,closedAt", "--limit", "50"
    ])
    if closed_issues_json:
        try:
            closed = json.loads(closed_issues_json)
            m.issues_closed_7d = sum(
                1 for i in closed
                if i.get("closedAt", "") >= since_7d
            )
        except json.JSONDecodeError:
            pass


def _collect_commit_metrics(m: HealthMetrics, since_7d: str) -> None:
    """Collect commit count and files changed from git log."""
    log_output = _run_git(["log", f"--since={since_7d}", "--oneline", "--no-merges"])
    if log_output:
        m.commits_7d = len(log_output.strip().split("\n"))

    files_output = _run_git(["log", f"--since={since_7d}", "--name-only", "--format=", "--no-merges"])
    if files_output:
        m.files_changed_7d = len(set(f for f in files_output.strip().split("\n") if f))


def _collect_test_metrics(m: HealthMetrics) -> None:
    """Collect test count via pytest --co."""
    import subprocess
    try:
        result = subprocess.run(
            ["python3", "-m", "pytest", "tests/", "--co", "-q"],
            capture_output=True, text=True, timeout=30,
            cwd=str(CLAUDE_DIR),
        )
        if result.returncode == 0:
            lines = [l for l in result.stdout.strip().split("\n") if l.startswith("tests/")]
            m.test_count = len(lines)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def _compute_health_score(m: HealthMetrics) -> None:
    """Compute overall score and status from collected metrics."""
    score = 50  # base
    if m.prs_merged_7d > 0:
        score += min(15, m.prs_merged_7d * 3)
    if m.feature_ratio > 0.3:
        score += 10
    if m.feature_ratio > 0.5:
        score += 5
    if m.prs_stale > 3:
        score -= 10
    if m.issues_labeled_bug > 5:
        score -= 10
    if m.test_count > 1000:
        score += 10
    if m.commits_7d > 20:
        score += 5
    if m.issues_closed_7d > m.issues_open * 0.1:
        score += 5

    m.overall_score = max(0, min(100, score))
    if m.overall_score >= 80:
        m.status = "healthy"
    elif m.overall_score >= 60:
        m.status = "warning"
    else:
        m.status = "critical"


def collect_metrics() -> HealthMetrics:
    """Collect all health metrics from git, GitHub, and local data."""
    m = HealthMetrics()
    since_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    _collect_pr_metrics(m, since_7d)
    _collect_issue_metrics(m, since_7d)
    _collect_commit_metrics(m, since_7d)
    _collect_test_metrics(m)
    _compute_health_score(m)

    return m


def generate_circuit_dashboard(m: HealthMetrics) -> dict:
    """Generate MCP grip_circuit_dashboard data from metrics."""
    def status_for(value, good_threshold, warn_threshold):
        if value >= good_threshold:
            return "nominal"
        if value >= warn_threshold:
            return "degraded"
        return "critical"

    return {
        "title": "GRIP Project Health",
        "description": f"Overall: {m.overall_score}/100 ({m.status})",
        "circuits": [
            {
                "id": "pr_flow",
                "label": "PR Flow",
                "status": status_for(m.prs_merged_7d, 5, 2),
                "value": f"{m.prs_merged_7d} merged / {m.prs_open} open",
                "detail": f"{m.prs_stale} stale (>7 days)"
            },
            {
                "id": "feature_ratio",
                "label": "Feature Ratio",
                "status": status_for(m.feature_ratio * 100, 40, 25),
                "value": f"{m.feature_ratio:.0%} features",
                "detail": f"{m.feature_count_7d} features / {m.infra_count_7d} infra"
            },
            {
                "id": "issue_health",
                "label": "Issue Health",
                "status": "nominal" if m.issues_labeled_bug < 3 else "degraded",
                "value": f"{m.issues_open} open / {m.issues_closed_7d} closed",
                "detail": f"{m.issues_labeled_bug} bugs"
            },
            {
                "id": "velocity",
                "label": "Velocity",
                "status": status_for(m.commits_7d, 20, 10),
                "value": f"{m.commits_7d} commits",
                "detail": f"{m.files_changed_7d} files touched"
            },
            {
                "id": "test_health",
                "label": "Test Suite",
                "status": "nominal" if m.test_count > 1000 else "degraded",
                "value": f"{m.test_count} tests",
                "detail": f"{m.test_pass_rate:.0f}% pass rate"
            },
            {
                "id": "overall",
                "label": "Overall Health",
                "status": {"healthy": "nominal", "warning": "degraded", "critical": "critical"}.get(m.status, "degraded"),
                "value": f"{m.overall_score}/100",
                "detail": m.status.upper()
            },
        ]
    }


def generate_kanban_data(m: HealthMetrics) -> dict:
    """Generate MCP grip_kanban data showing sprint items by status."""
    # Fetch actual open issues for the kanban
    issues_json = _run_gh([
        "issue", "list", "-R", REPO_SLUG, "--state", "open",
        "--json", "number,title,labels", "--limit", "20"
    ])

    cards_todo = []
    cards_doing = []
    cards_done = []

    if issues_json:
        try:
            issues = json.loads(issues_json)
            for issue in issues[:15]:
                labels = [l.get("name", "") for l in issue.get("labels", [])]
                priority = "high" if "bug" in labels else "medium"
                tags = [l for l in labels if l not in ("bug", "enhancement", "sprint-loop")]
                card = {
                    "id": str(issue["number"]),
                    "title": f"#{issue['number']}: {issue['title'][:50]}",
                    "description": ", ".join(tags) if tags else "",
                    "priority": priority,
                    "tags": labels[:2],
                }
                if "sprint-loop" in labels:
                    cards_doing.append(card)
                else:
                    cards_todo.append(card)
        except json.JSONDecodeError:
            pass

    return {
        "title": "Sprint Board",
        "columns": [
            {"id": "todo", "heading": "Backlog", "color": "#3b82f6", "cards": cards_todo[:8]},
            {"id": "doing", "heading": "In Progress", "color": "#eab308", "cards": cards_doing[:5]},
            {"id": "done", "heading": "Done (7d)", "color": "#22c55e", "cards": cards_done},
        ]
    }


def generate_progress_data(m: HealthMetrics) -> dict:
    """Generate MCP grip_progress data showing sprint completion."""
    total_items = m.prs_merged_7d + m.prs_open + m.issues_open
    completed = m.prs_merged_7d + m.issues_closed_7d
    percentage = int((completed / max(total_items, 1)) * 100)

    return {
        "title": "Sprint Progress",
        "percentage": min(percentage, 100),
        "milestones": [
            {"label": f"{m.prs_merged_7d} PRs Merged", "reached": m.prs_merged_7d > 0},
            {"label": f"{m.issues_closed_7d} Issues Closed", "reached": m.issues_closed_7d > 0},
            {"label": f"Feature Ratio > 40%", "reached": m.feature_ratio > 0.4},
            {"label": f"Test Suite > 1500", "reached": m.test_count > 1500},
            {"label": f"Zero Stale PRs", "reached": m.prs_stale == 0},
        ],
        "detail": f"Overall Health: {m.overall_score}/100 ({m.status})"
    }


def generate_data_table(m: HealthMetrics) -> dict:
    """Generate MCP grip_data_table showing metrics breakdown."""
    return {
        "title": "Health Metrics Detail",
        "columns": [
            {"id": "metric", "label": "Metric", "sortable": True},
            {"id": "value", "label": "Value", "sortable": True},
            {"id": "status", "label": "Status"},
            {"id": "target", "label": "Target"},
        ],
        "rows": [
            {"metric": "PRs Merged (7d)", "value": str(m.prs_merged_7d), "status": "good" if m.prs_merged_7d >= 5 else "warn", "target": ">= 5"},
            {"metric": "Open PRs", "value": str(m.prs_open), "status": "good" if m.prs_open < 10 else "warn", "target": "< 10"},
            {"metric": "Stale PRs", "value": str(m.prs_stale), "status": "good" if m.prs_stale == 0 else "warn", "target": "0"},
            {"metric": "Feature Ratio", "value": f"{m.feature_ratio:.0%}", "status": "good" if m.feature_ratio > 0.4 else "warn", "target": "> 40%"},
            {"metric": "Open Issues", "value": str(m.issues_open), "status": "info", "target": "Tracking"},
            {"metric": "Bugs Open", "value": str(m.issues_labeled_bug), "status": "good" if m.issues_labeled_bug < 3 else "warn", "target": "< 3"},
            {"metric": "Commits (7d)", "value": str(m.commits_7d), "status": "good" if m.commits_7d >= 20 else "warn", "target": ">= 20"},
            {"metric": "Files Changed (7d)", "value": str(m.files_changed_7d), "status": "info", "target": "Tracking"},
            {"metric": "Test Count", "value": str(m.test_count), "status": "good" if m.test_count > 1000 else "warn", "target": "> 1000"},
            {"metric": "Overall Score", "value": f"{m.overall_score}/100", "status": "good" if m.overall_score >= 80 else "warn", "target": ">= 80"},
        ]
    }


def text_summary(m: HealthMetrics) -> str:
    """Generate a human-readable text summary."""
    lines = [
        f"Project Health: {m.overall_score}/100 ({m.status.upper()})",
        "",
        f"PRs: {m.prs_merged_7d} merged, {m.prs_open} open ({m.prs_stale} stale)",
        f"Issues: {m.issues_open} open, {m.issues_closed_7d} closed, {m.issues_labeled_bug} bugs",
        f"Commits: {m.commits_7d} (7d), {m.files_changed_7d} files touched",
        f"Feature ratio: {m.feature_ratio:.0%} ({m.feature_count_7d} features / {m.infra_count_7d} infra)",
        f"Tests: {m.test_count}",
    ]
    return "\n".join(lines)


def main():
    """CLI entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"
    metrics = collect_metrics()

    if cmd == "summary":
        print(text_summary(metrics))
    elif cmd == "circuit":
        print(json.dumps(generate_circuit_dashboard(metrics), indent=2))
    elif cmd == "kanban":
        print(json.dumps(generate_kanban_data(metrics), indent=2))
    elif cmd == "progress":
        print(json.dumps(generate_progress_data(metrics), indent=2))
    elif cmd == "table":
        print(json.dumps(generate_data_table(metrics), indent=2))
    elif cmd == "all":
        print(json.dumps({
            "summary": text_summary(metrics),
            "circuit": generate_circuit_dashboard(metrics),
            "kanban": generate_kanban_data(metrics),
            "progress": generate_progress_data(metrics),
            "table": generate_data_table(metrics),
        }, indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print("Usage: project_health.py [summary|circuit|kanban|progress|table|all]")
        sys.exit(1)


if __name__ == "__main__":
    main()
