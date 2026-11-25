"""
Copilot Command Palette — contextual action suggestions.

Analyzes the current git state, time of day, recent activity, and
project context to suggest the most relevant GRIP commands and actions.
Ranked by predicted usefulness.

Usage:
    python3 lib/copilot/command_palette.py suggest      # Contextual suggestions
    python3 lib/copilot/command_palette.py actions       # All available actions
    python3 lib/copilot/command_palette.py history       # Recent action history
"""

import json
import os
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


@dataclass
class SuggestedAction:
    """A suggested action with relevance score."""
    name: str
    command: str
    description: str
    relevance: float = 0.0    # 0.0 - 1.0
    category: str = "general"  # general, git, pr, deploy, report, copilot
    reason: str = ""           # Why this is suggested now


@dataclass
class PaletteContext:
    """Current context used for suggestion ranking."""
    hour: int = 0
    day_of_week: int = 0       # 0=Monday, 6=Sunday
    branch: str = ""
    has_uncommitted: bool = False
    has_staged: bool = False
    recent_commits: int = 0
    open_prs: int = 0
    stale_prs: int = 0
    test_failures: int = 0
    days_since_release: int = 0


# === Action Registry ===

ACTIONS = [
    # Git workflow
    SuggestedAction("Commit Changes", "git commit", "Stage and commit current changes",
                    category="git"),
    SuggestedAction("Create PR", "/create-pr", "Create pull request from current branch",
                    category="pr"),
    SuggestedAction("Sprint Report", "python3 lib/sprint_report.py", "Generate sprint velocity report",
                    category="report"),
    SuggestedAction("Changelog", "python3 lib/changelog_generator.py", "Generate changelog from recent commits",
                    category="report"),
    SuggestedAction("Backlog Ranking", "python3 lib/backlog_prioritiser.py rank", "Rank open issues by priority",
                    category="report"),
    SuggestedAction("Health Dashboard", "python3 lib/project_health.py summary", "Project health overview",
                    category="report"),
    SuggestedAction("Convergence Scan", "python3 lib/insight_convergence.py scan", "Scan for insight convergence",
                    category="copilot"),
    SuggestedAction("Run Tests", "python3 -m pytest tests/ --tb=short", "Run test suite",
                    category="git"),
    SuggestedAction("Branch Cleanup", "/prune-branches", "Clean up merged branches",
                    category="git"),
    SuggestedAction("Deliverable Report", "python3 lib/deliverable_generator.py weekly_summary",
                    "Generate weekly summary", category="report"),
    SuggestedAction("Standup Notes", "python3 lib/deliverable_generator.py standup_notes",
                    "Generate standup talking points", category="report"),
    SuggestedAction("Update Wiki", "python3 lib/wiki_generator.py", "Regenerate wiki pages",
                    category="deploy"),
    SuggestedAction("Deploy Frontend", "vercel deploy", "Deploy to Vercel",
                    category="deploy"),
    SuggestedAction("Feature Check", "python3 lib/feature_first_enforcer.py check",
                    "Check feature-to-infra ratio", category="copilot"),
    SuggestedAction("Skill Recommendation", "python3 lib/skill_recommender.py",
                    "Get skill suggestions for current context", category="copilot"),
    SuggestedAction("Refresh Context", "/refresh-context", "Rebuild mental model",
                    category="copilot"),
    SuggestedAction("Red Team Review", "python3 lib/red_team_reporter.py",
                    "Generate red-team analysis", category="copilot"),
    SuggestedAction("Review PR", "/review-pr", "Review an open pull request",
                    category="pr"),
    SuggestedAction("Client Report", "python3 lib/deliverable_generator.py client_report",
                    "Generate client-facing report", category="report"),
    SuggestedAction("Session Achievements", "python3 lib/session_achievements.py summary",
                    "View session achievement progress", category="copilot"),
]


def _run_cmd(args: list, timeout: int = 10) -> str:
    """Run a shell command."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            cwd=str(CLAUDE_DIR),
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def gather_context() -> PaletteContext:
    """Gather current context for ranking suggestions."""
    now = datetime.now()
    ctx = PaletteContext(
        hour=now.hour,
        day_of_week=now.weekday(),
    )

    # Git state
    status = _run_cmd(["git", "status", "--porcelain"])
    if status:
        lines = status.split("\n")
        ctx.has_uncommitted = any(l.startswith(" M") or l.startswith("??") for l in lines if l)
        ctx.has_staged = any(l[0] in "MADR" for l in lines if l)

    branch = _run_cmd(["git", "branch", "--show-current"])
    ctx.branch = branch

    # Recent commit count (today)
    today = now.strftime("%Y-%m-%d")
    log = _run_cmd(["git", "log", "--oneline", f"--since={today}"])
    ctx.recent_commits = len(log.split("\n")) if log else 0

    return ctx


def rank_suggestions(context: PaletteContext, actions: list = None) -> list:
    """Rank actions by relevance to current context.

    Returns list of SuggestedAction sorted by relevance descending.
    """
    if actions is None:
        actions = ACTIONS.copy()

    scored = []
    for action in actions:
        relevance = 0.3  # Base relevance

        # Time-based signals
        if 8 <= context.hour <= 10:
            # Morning: standup, planning
            if action.name in ("Standup Notes", "Sprint Report", "Backlog Ranking"):
                relevance += 0.4
                action.reason = "Morning routine"
        elif 11 <= context.hour <= 16:
            # Work hours: coding, reviewing
            if action.category in ("git", "pr"):
                relevance += 0.2
                action.reason = "Active development hours"
        elif 16 <= context.hour <= 18:
            # End of day: reports, cleanup
            if action.name in ("Deliverable Report", "Branch Cleanup", "Client Report"):
                relevance += 0.4
                action.reason = "End-of-day wrap-up"

        # Friday = review day
        if context.day_of_week == 4:
            if action.name in ("Sprint Report", "Red Team Review", "Review PR"):
                relevance += 0.3
                action.reason = "Friday review routine"

        # Git state signals
        if context.has_uncommitted:
            if action.name == "Commit Changes":
                relevance += 0.5
                action.reason = "Uncommitted changes detected"
            elif action.name == "Run Tests":
                relevance += 0.3
                action.reason = "Test before committing"

        if context.has_staged:
            if action.name == "Commit Changes":
                relevance += 0.6
                action.reason = "Staged changes ready to commit"

        # Branch signals
        if context.branch and context.branch != "main":
            if action.name == "Create PR":
                relevance += 0.4
                action.reason = f"On feature branch '{context.branch}'"
            if action.name == "Run Tests":
                relevance += 0.2

        # Productivity signals
        if context.recent_commits >= 5:
            if action.name in ("Sprint Report", "Changelog", "Session Achievements"):
                relevance += 0.3
                action.reason = f"Productive day ({context.recent_commits} commits)"

        if context.recent_commits == 0:
            if action.name in ("Backlog Ranking", "Refresh Context"):
                relevance += 0.3
                action.reason = "No commits yet today"

        action.relevance = min(relevance, 1.0)
        scored.append(action)

    scored.sort(key=lambda a: a.relevance, reverse=True)
    return scored


def format_suggestions_text(suggestions: list, limit: int = 10) -> str:
    """Format suggestions as plain text."""
    lines = ["Suggested Actions", "=" * 50, ""]

    for i, s in enumerate(suggestions[:limit], 1):
        pct = f"{s.relevance:.0%}"
        reason_str = f" — {s.reason}" if s.reason else ""
        lines.append(f"  {i:2d}. [{pct:>4s}] {s.name}")
        lines.append(f"      {s.command}")
        if s.reason:
            lines.append(f"      Reason: {s.reason}")
        lines.append("")

    return "\n".join(lines)


def format_suggestions_slack(suggestions: list, limit: int = 8) -> str:
    """Format suggestions as Slack mrkdwn."""
    lines = ["*Suggested Actions*", ""]

    current_category = None
    for s in suggestions[:limit]:
        if s.relevance < 0.3:
            continue

        reason_str = f" — _{s.reason}_" if s.reason else ""
        lines.append(f"  - *{s.name}* ({s.relevance:.0%}){reason_str}")
        lines.append(f"    `{s.command}`")

    return "\n".join(lines)


def format_suggestions_json(suggestions: list, limit: int = 15) -> str:
    """Format suggestions as JSON."""
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suggestions": [
            {
                "name": s.name,
                "command": s.command,
                "description": s.description,
                "relevance": round(s.relevance, 2),
                "category": s.category,
                "reason": s.reason,
            }
            for s in suggestions[:limit]
        ],
    }
    return json.dumps(data, indent=2)


FORMATTERS = {
    "text": format_suggestions_text,
    "slack": format_suggestions_slack,
    "json": format_suggestions_json,
}


def main():
    """CLI entry point."""
    cmd = sys.argv[1] if len(sys.argv) > 1 else "suggest"

    if cmd == "suggest":
        fmt = sys.argv[2] if len(sys.argv) > 2 else "text"
        context = gather_context()
        ranked = rank_suggestions(context)
        formatter = FORMATTERS.get(fmt, format_suggestions_text)
        print(formatter(ranked))

    elif cmd == "actions":
        for action in ACTIONS:
            print(f"  [{action.category:>8s}] {action.name}: {action.command}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: suggest [text|slack|json], actions")
        sys.exit(1)


if __name__ == "__main__":
    main()
