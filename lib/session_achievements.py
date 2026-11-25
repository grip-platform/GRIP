"""Session achievement tracker - analyzes git activity and generates session report cards."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class Achievement:
    category: str  # "commit", "pr", "issue", "test", "feature", "fix", "docs"
    title: str
    detail: str
    impact: str  # "high", "medium", "low"
    timestamp: str  # ISO format


@dataclass
class SessionReport:
    session_id: str
    start_time: str
    end_time: str
    duration_minutes: int
    achievements: list[Achievement]
    commits_count: int
    files_changed: int
    lines_added: int
    lines_removed: int
    tests_added: int
    prs_created: int
    issues_created: int
    languages: dict[str, int] = field(default_factory=dict)
    productivity_score: float = 0.0
    streak_days: int = 0
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

EXTENSION_LANGUAGE_MAP = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C",
    ".hpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".md": "Markdown",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sql": "SQL",
    ".lua": "Lua",
    ".r": "R",
    ".php": "PHP",
}

TEST_FILE_PATTERNS = [
    re.compile(r"test_[^/]*\.py$"),
    re.compile(r"[^/]*_test\.py$"),
    re.compile(r"[^/]*\.test\.[jt]sx?$"),
    re.compile(r"[^/]*\.spec\.[jt]sx?$"),
    re.compile(r"[^/]*_test\.go$"),
    re.compile(r"[^/]*Test\.java$"),
    re.compile(r"[^/]*_spec\.rb$"),
    re.compile(r"tests?/[^/]*\.[^/]+$"),
]


def _language_for_path(path: str) -> Optional[str]:
    for ext, lang in EXTENSION_LANGUAGE_MAP.items():
        if path.endswith(ext):
            return lang
    return None


def _is_test_file(path: str) -> bool:
    for pattern in TEST_FILE_PATTERNS:
        if pattern.search(path):
            return True
    return False


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

_COMMIT_HEADER_RE = re.compile(
    r"^COMMIT_START\|([a-f0-9]+)\|(.+?)\|(.+?)\|(.*)$"
)


def parse_git_log(log_text: str) -> list[dict]:
    """Parse git log output produced with the format:

    git log --format='COMMIT_START|%H|%aI|%an|%s' --numstat

    Returns a list of commit dicts with keys:
        sha, timestamp, author, message, files (list of {added, removed, path})
    """
    if not log_text or not log_text.strip():
        return []

    commits: list[dict] = []
    current: Optional[dict] = None

    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header = _COMMIT_HEADER_RE.match(line)
        if header:
            current = {
                "sha": header.group(1),
                "timestamp": header.group(2),
                "author": header.group(3),
                "message": header.group(4),
                "files": [],
            }
            commits.append(current)
            continue

        if current is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                added_str, removed_str, path = parts
                try:
                    added = int(added_str) if added_str != "-" else 0
                    removed = int(removed_str) if removed_str != "-" else 0
                except ValueError:
                    added, removed = 0, 0
                current["files"].append(
                    {"added": added, "removed": removed, "path": path}
                )

    return commits


# ---------------------------------------------------------------------------
# Test detection
# ---------------------------------------------------------------------------


def detect_tests_added(commits: list[dict]) -> int:
    """Count lines added in test files across all commits."""
    total = 0
    for commit in commits:
        for f in commit.get("files", []):
            if _is_test_file(f["path"]):
                total += f["added"]
    return total


# ---------------------------------------------------------------------------
# Achievement detection
# ---------------------------------------------------------------------------

_CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|chore|docs|test|refactor|perf|style|ci|build)(?:\(.+?\))?[!]?:\s*(.+)",
    re.IGNORECASE,
)

_PR_PATTERNS = [
    re.compile(r"\(#(\d+)\)"),
    re.compile(r"merge pull request #(\d+)", re.IGNORECASE),
    re.compile(r"pr\s*#(\d+)", re.IGNORECASE),
]

_ISSUE_PATTERNS = [
    re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE),
    re.compile(r"issue\s*#(\d+)", re.IGNORECASE),
]


def _commit_total_lines(commit: dict) -> int:
    return sum(f["added"] + f["removed"] for f in commit.get("files", []))


def _achievement_from_type(
    commit_type: str | None, commit_desc: str, files: list, total_lines: int, ts: str,
) -> Achievement | None:
    """Create an achievement from a conventional commit type."""
    if commit_type == "feat":
        impact = "high" if total_lines > 200 else "medium" if total_lines > 50 else "low"
        return Achievement(
            category="feature",
            title="New feature: {0}".format(commit_desc),
            detail="{0} files, +{1}/-{2} lines".format(
                len(files),
                sum(f["added"] for f in files),
                sum(f["removed"] for f in files),
            ),
            impact=impact,
            timestamp=ts,
        )
    if commit_type == "fix":
        return Achievement(
            category="fix",
            title="Bug fix: {0}".format(commit_desc),
            detail="{0} files changed".format(len(files)),
            impact="medium",
            timestamp=ts,
        )
    if commit_type == "docs":
        return Achievement(
            category="docs",
            title="Documentation: {0}".format(commit_desc),
            detail="{0} files".format(len(files)),
            impact="low",
            timestamp=ts,
        )
    if commit_type == "test":
        test_lines = sum(f["added"] for f in files if _is_test_file(f["path"]))
        return Achievement(
            category="test",
            title="Tests: {0}".format(commit_desc),
            detail="{0} test lines added".format(test_lines),
            impact="medium" if test_lines > 50 else "low",
            timestamp=ts,
        )
    return None


def _detect_cross_cutting(
    commit_type: str | None, commit_desc: str, files: list, total_lines: int, ts: str,
) -> list[Achievement]:
    """Detect achievements that apply regardless of commit type."""
    results: list[Achievement] = []

    # Major refactor
    if total_lines > 300:
        if commit_type == "refactor" or (commit_type is None and total_lines > 500):
            results.append(Achievement(
                category="commit",
                title="Major refactor: {0}".format(commit_desc),
                detail="{0} lines churned across {1} files".format(total_lines, len(files)),
                impact="high",
                timestamp=ts,
            ))

    # Test file additions
    new_test_files = [f for f in files if _is_test_file(f["path"]) and f["added"] > 10]
    if new_test_files and commit_type != "test":
        results.append(Achievement(
            category="test",
            title="Tests added in: {0}".format(commit_desc),
            detail="{0} test files, {1} lines".format(
                len(new_test_files),
                sum(f["added"] for f in new_test_files),
            ),
            impact="medium",
            timestamp=ts,
        ))

    # Doc file changes
    doc_files = [f for f in files if f["path"].endswith((".md", ".rst", ".txt"))
                 and f["added"] > 5]
    if doc_files and commit_type != "docs":
        results.append(Achievement(
            category="docs",
            title="Documentation updated in: {0}".format(commit_desc),
            detail="{0} doc files".format(len(doc_files)),
            impact="low",
            timestamp=ts,
        ))

    return results


def detect_achievements(commits: list[dict]) -> list[Achievement]:
    """Extract achievements from commit data."""
    achievements: list[Achievement] = []
    seen_prs: set[str] = set()
    seen_issues: set[str] = set()

    for commit in commits:
        msg = commit.get("message", "")
        ts = commit.get("timestamp", "")
        files = commit.get("files", [])
        total_lines = _commit_total_lines(commit)

        conv = _CONVENTIONAL_RE.match(msg)
        commit_type = conv.group(1).lower() if conv else None
        commit_desc = conv.group(2).strip() if conv else msg

        typed = _achievement_from_type(commit_type, commit_desc, files, total_lines, ts)
        if typed:
            achievements.append(typed)

        achievements.extend(
            _detect_cross_cutting(commit_type, commit_desc, files, total_lines, ts)
        )

        # PR detection
        for pattern in _PR_PATTERNS:
            for match in pattern.finditer(msg):
                pr_num = match.group(1)
                if pr_num not in seen_prs:
                    seen_prs.add(pr_num)
                    achievements.append(Achievement(
                        category="pr",
                        title="PR #{0}: {1}".format(pr_num, commit_desc),
                        detail="Pull request activity",
                        impact="medium",
                        timestamp=ts,
                    ))

        # Issue detection
        for pattern in _ISSUE_PATTERNS:
            for match in pattern.finditer(msg):
                issue_num = match.group(1)
                if issue_num not in seen_issues:
                    seen_issues.add(issue_num)
                    achievements.append(Achievement(
                        category="issue",
                        title="Issue #{0} addressed".format(issue_num),
                        detail="Referenced in: {0}".format(commit_desc),
                        impact="medium",
                        timestamp=ts,
                    ))

    return achievements


# ---------------------------------------------------------------------------
# Productivity scoring
# ---------------------------------------------------------------------------


def calculate_productivity_score(report: SessionReport) -> float:
    """Score 0-100 based on output quality and balance."""
    score = 0.0

    # Commits: diminishing returns after 10 (max 30 pts)
    if report.commits_count > 0:
        commit_score = 30 * (1 - math.exp(-report.commits_count / 5))
        score += min(commit_score, 30.0)

    # Test coverage bonus (max 20 pts)
    if report.tests_added > 0:
        test_score = 20 * (1 - math.exp(-report.tests_added / 50))
        score += min(test_score, 20.0)

    # Feature vs infrastructure ratio (max 25 pts)
    feature_count = sum(1 for a in report.achievements if a.category == "feature")
    infra_count = sum(1 for a in report.achievements
                      if a.category in ("commit", "docs", "fix"))
    total_categorized = feature_count + infra_count
    if total_categorized > 0:
        feature_ratio = feature_count / total_categorized
        # Features are worth more but a balanced mix is ideal (60/40)
        if feature_ratio >= 0.4:
            score += 25.0
        else:
            score += 25.0 * (feature_ratio / 0.4)
    elif report.commits_count > 0:
        # Non-conventional commits still get partial credit
        score += 10.0

    # Code churn ratio: deletions show cleanup (max 15 pts)
    total_changes = report.lines_added + report.lines_removed
    if total_changes > 0:
        deletion_ratio = report.lines_removed / total_changes
        # Sweet spot: 15-50% deletions means healthy cleanup
        if 0.15 <= deletion_ratio <= 0.5:
            score += 15.0
        elif deletion_ratio > 0:
            score += 15.0 * min(deletion_ratio / 0.15, 1.0)

    # PR bonus (max 10 pts)
    if report.prs_created > 0:
        score += min(report.prs_created * 5.0, 10.0)

    return round(min(score, 100.0), 1)


# ---------------------------------------------------------------------------
# Streak calculation
# ---------------------------------------------------------------------------


def calculate_streak(log_text: str) -> int:
    """Count consecutive days with commits ending at the most recent commit day."""
    if not log_text or not log_text.strip():
        return 0

    dates: set[str] = set()
    for line in log_text.splitlines():
        header = _COMMIT_HEADER_RE.match(line.strip())
        if header:
            try:
                dt = datetime.fromisoformat(header.group(2))
                dates.add(dt.strftime("%Y-%m-%d"))
            except (ValueError, IndexError):
                continue

    if not dates:
        return 0

    sorted_dates = sorted(dates, reverse=True)
    streak = 1
    for i in range(len(sorted_dates) - 1):
        current = datetime.strptime(sorted_dates[i], "%Y-%m-%d")
        prev = datetime.strptime(sorted_dates[i + 1], "%Y-%m-%d")
        if (current - prev).days == 1:
            streak += 1
        else:
            break

    return streak


# ---------------------------------------------------------------------------
# Language analysis
# ---------------------------------------------------------------------------


def _analyze_languages(commits: list[dict]) -> dict[str, int]:
    """Aggregate lines changed per language."""
    lang_lines: dict[str, int] = {}
    for commit in commits:
        for f in commit.get("files", []):
            lang = _language_for_path(f["path"])
            if lang:
                lang_lines[lang] = lang_lines.get(lang, 0) + f["added"] + f["removed"]
    return dict(sorted(lang_lines.items(), key=lambda x: x[1], reverse=True))


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def generate_recommendations(report: SessionReport) -> list[str]:
    """Generate actionable suggestions based on the report."""
    recs: list[str] = []

    # Low productivity
    if report.productivity_score < 30:
        recs.append("Productivity is low. Consider breaking work into smaller, focused commits.")

    # No tests
    if report.tests_added == 0 and report.commits_count > 0:
        recs.append("No tests were added this session. Consider adding test coverage for new code.")

    # High infrastructure ratio
    feature_count = sum(1 for a in report.achievements if a.category == "feature")
    infra_count = sum(1 for a in report.achievements
                      if a.category in ("commit", "docs", "fix"))
    if infra_count > 0 and feature_count == 0:
        recs.append("All work was infrastructure/fixes. Schedule time for feature development.")

    # Too many commits (might indicate lack of planning)
    if report.commits_count > 20:
        recs.append(
            "High commit count ({0}). Consider squashing related changes.".format(
                report.commits_count
            )
        )

    # No PRs
    if report.prs_created == 0 and report.commits_count >= 5:
        recs.append("No PRs created despite multiple commits. Consider opening a PR for review.")

    # Code-heavy with no docs
    doc_achievements = sum(1 for a in report.achievements if a.category == "docs")
    if report.lines_added > 500 and doc_achievements == 0:
        recs.append("Significant code added without documentation updates. Consider adding docs.")

    # Low deletion ratio (only adding, never cleaning)
    if report.lines_added > 200 and report.lines_removed < 10:
        recs.append("Very few deletions. Look for dead code or tech debt to clean up.")

    # Streak encouragement
    if report.streak_days >= 7:
        recs.append(
            "Great streak of {0} consecutive days! Keep the momentum.".format(
                report.streak_days
            )
        )
    elif report.streak_days == 0 and report.commits_count > 0:
        recs.append("Start building a daily commit streak for consistent progress.")

    # Long session warning
    if report.duration_minutes > 480:
        recs.append("Session exceeded 8 hours. Take breaks to maintain code quality.")

    # Empty session
    if report.commits_count == 0:
        recs.append(
            "No commits in this session. If blocked, consider pairing or breaking the problem down."
        )

    return recs


# ---------------------------------------------------------------------------
# Full session analysis
# ---------------------------------------------------------------------------


def analyze_session(
    log_text: str,
    session_id: str = "",
    hours: int = 8,
) -> SessionReport:
    """Full session analysis from git log text."""
    commits = parse_git_log(log_text)

    # Time window
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)

    # Filter commits within window
    filtered: list[dict] = []
    for c in commits:
        try:
            ct = datetime.fromisoformat(c["timestamp"])
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
            if ct >= start:
                filtered.append(c)
        except (ValueError, KeyError):
            filtered.append(c)  # include if unparseable

    if not session_id:
        session_id = now.strftime("%Y%m%d-%H%M%S")

    # Aggregate stats
    all_paths: set[str] = set()
    total_added = 0
    total_removed = 0
    for c in filtered:
        for f in c.get("files", []):
            all_paths.add(f["path"])
            total_added += f["added"]
            total_removed += f["removed"]

    achievements = detect_achievements(filtered)
    tests_added = detect_tests_added(filtered)
    languages = _analyze_languages(filtered)

    # Count PRs and issues from achievements
    prs = len({a.title for a in achievements if a.category == "pr"})
    issues = len({a.title for a in achievements if a.category == "issue"})

    streak = calculate_streak(log_text)

    report = SessionReport(
        session_id=session_id,
        start_time=start.isoformat(),
        end_time=now.isoformat(),
        duration_minutes=hours * 60,
        achievements=achievements,
        commits_count=len(filtered),
        files_changed=len(all_paths),
        lines_added=total_added,
        lines_removed=total_removed,
        tests_added=tests_added,
        prs_created=prs,
        issues_created=issues,
        languages=languages,
        productivity_score=0.0,
        streak_days=streak,
        recommendations=[],
    )

    report.productivity_score = calculate_productivity_score(report)
    report.recommendations = generate_recommendations(report)

    return report


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

_BADGE_ART = {
    "feature": "[FEAT]",
    "fix": "[FIX]",
    "test": "[TEST]",
    "docs": "[DOCS]",
    "pr": "[PR]",
    "issue": "[ISSUE]",
    "commit": "[CODE]",
}

_IMPACT_INDICATOR = {
    "high": "***",
    "medium": "**",
    "low": "*",
}


def format_text(report: SessionReport) -> str:
    """Terminal output with ASCII art badges."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  SESSION REPORT CARD")
    lines.append("=" * 60)
    lines.append("")
    lines.append("  Session:  {0}".format(report.session_id))
    lines.append("  Period:   {0} -> {1}".format(report.start_time, report.end_time))
    lines.append("  Duration: {0} minutes".format(report.duration_minutes))
    lines.append("")

    # Stats
    lines.append("  STATS")
    lines.append("  " + "-" * 40)
    lines.append("  Commits:       {0}".format(report.commits_count))
    lines.append("  Files changed: {0}".format(report.files_changed))
    lines.append("  Lines:         +{0} / -{1}".format(report.lines_added, report.lines_removed))
    lines.append("  Tests added:   {0} lines".format(report.tests_added))
    lines.append("  PRs created:   {0}".format(report.prs_created))
    lines.append("  Issues:        {0}".format(report.issues_created))
    lines.append("  Streak:        {0} days".format(report.streak_days))
    lines.append("")

    # Languages
    if report.languages:
        lines.append("  LANGUAGES")
        lines.append("  " + "-" * 40)
        for lang, count in report.languages.items():
            bar = "#" * min(count // 10, 30)
            lines.append("  {0:<15} {1:>5} lines  {2}".format(lang, count, bar))
        lines.append("")

    # Achievements
    if report.achievements:
        lines.append("  ACHIEVEMENTS")
        lines.append("  " + "-" * 40)
        for a in report.achievements:
            badge = _BADGE_ART.get(a.category, "[???]")
            impact = _IMPACT_INDICATOR.get(a.impact, "*")
            lines.append("  {0} {1} {2}".format(badge, impact, a.title))
            lines.append("         {0}".format(a.detail))
        lines.append("")

    # Score
    lines.append("  PRODUCTIVITY SCORE")
    lines.append("  " + "-" * 40)
    bar_filled = int(report.productivity_score / 2)
    bar = "[" + "#" * bar_filled + "." * (50 - bar_filled) + "]"
    lines.append("  {0} {1}/100".format(bar, report.productivity_score))
    lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("  RECOMMENDATIONS")
        lines.append("  " + "-" * 40)
        for rec in report.recommendations:
            lines.append("  - {0}".format(rec))
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_slack(report: SessionReport) -> str:
    """Slack mrkdwn format (*bold* not **bold**, _italic_ not *italic*)."""
    lines: list[str] = []
    lines.append("*Session Report Card*")
    lines.append(
        "Session: `{0}` | Duration: {1}min".format(report.session_id, report.duration_minutes)
    )
    lines.append("")

    lines.append(
        "*Stats*: {0} commits | +{1}/-{2} lines | {3} files | "
        "{4} test lines | {5} PRs | Streak: {6}d".format(
            report.commits_count,
            report.lines_added,
            report.lines_removed,
            report.files_changed,
            report.tests_added,
            report.prs_created,
            report.streak_days,
        )
    )
    lines.append("")

    if report.languages:
        top_langs = list(report.languages.items())[:5]
        lang_str = ", ".join("{0} ({1})".format(lang, count) for lang, count in top_langs)
        lines.append("*Languages*: {0}".format(lang_str))
        lines.append("")

    if report.achievements:
        lines.append("*Achievements*")
        for a in report.achievements:
            emoji_map = {
                "feature": ":rocket:",
                "fix": ":wrench:",
                "test": ":test_tube:",
                "docs": ":page_facing_up:",
                "pr": ":git-pull-request:",
                "issue": ":ticket:",
                "commit": ":hammer:",
            }
            emoji = emoji_map.get(a.category, ":star:")
            impact_str = " _({0})_".format(a.impact) if a.impact != "low" else ""
            lines.append("{0} {1}{2}".format(emoji, a.title, impact_str))
        lines.append("")

    if report.productivity_score >= 70:
        score_indicator = ":large_green_circle:"
    elif report.productivity_score >= 40:
        score_indicator = ":large_yellow_circle:"
    else:
        score_indicator = ":red_circle:"
    lines.append(
        "{0} *Productivity*: {1}/100".format(score_indicator, report.productivity_score)
    )

    if report.recommendations:
        lines.append("")
        lines.append("*Recommendations*")
        for rec in report.recommendations:
            lines.append("- {0}".format(rec))

    return "\n".join(lines)


def format_json(report: SessionReport) -> str:
    """JSON serialization."""
    data = asdict(report)
    return json.dumps(data, indent=2, default=str)


def format_progress(report: SessionReport) -> dict:
    """Returns dict for grip_progress MCP app with tracks for commits, tests, features."""
    feature_count = sum(1 for a in report.achievements if a.category == "feature")
    test_count = sum(1 for a in report.achievements if a.category == "test")
    fix_count = sum(1 for a in report.achievements if a.category == "fix")

    return {
        "app": "grip_progress",
        "session_id": report.session_id,
        "tracks": [
            {
                "name": "commits",
                "current": report.commits_count,
                "target": max(report.commits_count, 10),
                "unit": "commits",
            },
            {
                "name": "tests",
                "current": report.tests_added,
                "target": max(report.tests_added, 50),
                "unit": "lines",
            },
            {
                "name": "features",
                "current": feature_count,
                "target": max(feature_count, 3),
                "unit": "features",
            },
            {
                "name": "fixes",
                "current": fix_count,
                "target": max(fix_count, 5),
                "unit": "fixes",
            },
            {
                "name": "test_achievements",
                "current": test_count,
                "target": max(test_count, 3),
                "unit": "test milestones",
            },
        ],
        "score": report.productivity_score,
        "streak": report.streak_days,
    }


def format_digest(report: SessionReport) -> dict:
    """Returns dict for grip_digest MCP app with cards per achievement category."""
    cards_by_category: dict[str, list[dict]] = {}
    for a in report.achievements:
        if a.category not in cards_by_category:
            cards_by_category[a.category] = []
        cards_by_category[a.category].append({
            "title": a.title,
            "detail": a.detail,
            "impact": a.impact,
            "timestamp": a.timestamp,
        })

    return {
        "app": "grip_digest",
        "session_id": report.session_id,
        "duration_minutes": report.duration_minutes,
        "summary": {
            "commits": report.commits_count,
            "files_changed": report.files_changed,
            "lines_added": report.lines_added,
            "lines_removed": report.lines_removed,
            "productivity_score": report.productivity_score,
        },
        "cards": [
            {
                "category": cat,
                "count": len(items),
                "items": items,
            }
            for cat, items in cards_by_category.items()
        ],
        "recommendations": report.recommendations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _get_git_log(hours: int) -> str:
    """Fetch git log for the given time window."""
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    result = subprocess.run(
        [
            "git", "log",
            "--since={0}".format(since),
            "--format=COMMIT_START|%H|%aI|%an|%s",
            "--numstat",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Session achievement tracker - analyze git activity"
    )
    parser.add_argument(
        "--hours", type=int, default=8,
        help="Time window in hours (default: 8)"
    )
    parser.add_argument(
        "--session-id", type=str, default="",
        help="Session identifier (default: auto-generated)"
    )
    parser.add_argument(
        "--format", type=str, default="text",
        choices=["text", "slack", "json", "progress", "digest"],
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--streak-hours", type=int, default=720,
        help="Hours to look back for streak calculation (default: 720 = 30 days)"
    )

    args = parser.parse_args(argv)

    log_text = _get_git_log(args.hours)

    # For streak, get a longer window
    if args.streak_hours > args.hours:
        streak_log = _get_git_log(args.streak_hours)
    else:
        streak_log = log_text

    report = analyze_session(log_text, session_id=args.session_id, hours=args.hours)

    # Override streak with longer window calculation
    report.streak_days = calculate_streak(streak_log)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "progress": format_progress,
        "digest": format_digest,
    }

    output = formatters[args.format](report)
    if isinstance(output, dict):
        print(json.dumps(output, indent=2))
    else:
        print(output)


if __name__ == "__main__":
    main()
