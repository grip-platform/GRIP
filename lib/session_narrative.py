"""Session Narrative — daily standup summaries from GRIP session data.

Generates human-readable standup reports from session metrics, PRs,
achievements, and key events. Supports Slack, Markdown, and plain text output.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass
class StandupEntry:
    """Single day's standup data."""

    date: str
    prs_merged: list[dict[str, Any]] = field(default_factory=list)
    tests_written: int = 0
    achievements_earned: list[str] = field(default_factory=list)
    key_events: list[str] = field(default_factory=list)
    hours_worked: float = 0.0


@dataclass
class StandupReport:
    """Collection of standup entries with formatting metadata."""

    entries: list[StandupEntry] = field(default_factory=list)
    period: str = "daily"  # daily | weekly
    format_type: str = "text"  # slack | markdown | text


def build_daily_entry(
    date: str,
    runs: list[dict[str, Any]] | None = None,
    prs: list[dict[str, Any]] | None = None,
    achievements: list[str] | None = None,
) -> StandupEntry:
    """Construct a StandupEntry from raw session data.

    Args:
        date: ISO date string (YYYY-MM-DD).
        runs: List of session run dicts, each with optional 'tests_written',
              'duration_hours', and 'events' keys.
        prs: List of PR dicts with 'number' and 'title' keys.
        achievements: List of achievement name strings.

    Returns:
        Populated StandupEntry.
    """
    runs = runs or []
    prs = prs or []
    achievements = achievements or []

    tests = 0
    hours = 0.0
    events: list[str] = []

    for run in runs:
        tests += run.get("tests_written", 0)
        hours += run.get("duration_hours", 0.0)
        for evt in run.get("events", []):
            if evt and evt not in events:
                events.append(evt)

    return StandupEntry(
        date=date,
        prs_merged=prs,
        tests_written=tests,
        achievements_earned=achievements,
        key_events=events,
        hours_worked=round(hours, 2),
    )


def build_weekly_summary(entries: list[StandupEntry]) -> StandupEntry:
    """Aggregate multiple daily entries into a single weekly summary.

    Args:
        entries: List of daily StandupEntry objects.

    Returns:
        Aggregated StandupEntry with combined metrics.

    Raises:
        ValueError: If entries list is empty.
    """
    if not entries:
        raise ValueError("Cannot build weekly summary from empty entries")

    all_prs: list[dict[str, Any]] = []
    total_tests = 0
    all_achievements: list[str] = []
    all_events: list[str] = []
    total_hours = 0.0

    seen_pr_numbers: set[int] = set()

    for entry in entries:
        for pr in entry.prs_merged:
            pr_num = pr.get("number")
            if pr_num not in seen_pr_numbers:
                all_prs.append(pr)
                seen_pr_numbers.add(pr_num)
        total_tests += entry.tests_written
        for ach in entry.achievements_earned:
            if ach not in all_achievements:
                all_achievements.append(ach)
        for evt in entry.key_events:
            if evt not in all_events:
                all_events.append(evt)
        total_hours += entry.hours_worked

    sorted_entries = sorted(entries, key=lambda e: e.date)
    date_range = f"{sorted_entries[0].date} to {sorted_entries[-1].date}"

    return StandupEntry(
        date=date_range,
        prs_merged=all_prs,
        tests_written=total_tests,
        achievements_earned=all_achievements,
        key_events=all_events,
        hours_worked=round(total_hours, 2),
    )


def generate_narrative(entry: StandupEntry) -> str:
    """Generate a human-readable paragraph describing the day's work.

    Args:
        entry: StandupEntry to narrate.

    Returns:
        Natural language summary paragraph.
    """
    parts: list[str] = []

    if entry.prs_merged:
        count = len(entry.prs_merged)
        titles = [pr.get("title", f"#{pr.get('number', '?')}") for pr in entry.prs_merged]
        if count == 1:
            parts.append(f"Merged 1 PR: {titles[0]}.")
        elif count <= 3:
            parts.append(f"Merged {count} PRs: {', '.join(titles)}.")
        else:
            parts.append(
                f"Merged {count} PRs including {titles[0]} and {titles[1]}."
            )

    if entry.tests_written > 0:
        parts.append(f"Wrote {entry.tests_written} tests.")

    if entry.achievements_earned:
        parts.append(f"Earned: {', '.join(entry.achievements_earned)}.")

    if entry.key_events:
        parts.append(f"Key: {'; '.join(entry.key_events)}.")

    if entry.hours_worked > 0:
        parts.append(f"Worked {entry.hours_worked:.1f}h.")

    if not parts:
        return "No activity recorded."

    return " ".join(parts)


def _format_pr_list(prs: list[dict[str, Any]], style: str) -> str:
    """Format PR list according to output style.

    Args:
        prs: List of PR dicts.
        style: One of 'slack', 'markdown', 'text'.

    Returns:
        Formatted PR string.
    """
    if not prs:
        return ""

    items = []
    for pr in prs:
        num = pr.get("number", "?")
        title = pr.get("title", "untitled")
        if style == "slack":
            items.append(f"#{num} ({title})")
        elif style == "markdown":
            items.append(f"#`{num}` {title}")
        else:
            items.append(f"#{num} ({title})")

    return ", ".join(items)


def format_slack(report: StandupReport) -> str:
    """Format report as Slack mrkdwn.

    Args:
        report: StandupReport to format.

    Returns:
        Slack-formatted string with bold, bullets, and emoji.
    """
    lines: list[str] = []

    for entry in report.entries:
        if report.period == "weekly":
            lines.append(f"*Weekly Summary — {entry.date}*")
        else:
            lines.append(f"*Daily Standup — {entry.date}*")

        if entry.prs_merged:
            pr_str = _format_pr_list(entry.prs_merged, "slack")
            count = len(entry.prs_merged)
            lines.append(f":rocket: *{count} PR{'s' if count != 1 else ''} merged*: {pr_str}")

        if entry.tests_written > 0:
            lines.append(f":test_tube: *{entry.tests_written} tests* written")

        if entry.achievements_earned:
            lines.append(f":trophy: Earned: {', '.join(entry.achievements_earned)}")

        if entry.key_events:
            for evt in entry.key_events:
                lines.append(f"> Key: {evt}")

        if entry.hours_worked > 0:
            lines.append(f":clock1: {entry.hours_worked:.1f}h worked")

        if not (entry.prs_merged or entry.tests_written or entry.achievements_earned or entry.key_events):
            lines.append("_No activity recorded._")

        lines.append("")  # blank line between entries

    return "\n".join(lines).rstrip()


def format_markdown(report: StandupReport) -> str:
    """Format report as GitHub-flavoured markdown.

    Args:
        report: StandupReport to format.

    Returns:
        Markdown-formatted string.
    """
    lines: list[str] = []

    for entry in report.entries:
        if report.period == "weekly":
            lines.append(f"## Weekly Summary — {entry.date}")
        else:
            lines.append(f"## Daily Standup — {entry.date}")
        lines.append("")

        if entry.prs_merged:
            count = len(entry.prs_merged)
            lines.append(f"**{count} PR{'s' if count != 1 else ''} merged:**")
            for pr in entry.prs_merged:
                num = pr.get("number", "?")
                title = pr.get("title", "untitled")
                lines.append(f"- #{num} — {title}")
            lines.append("")

        if entry.tests_written > 0:
            lines.append(f"**Tests written:** {entry.tests_written}")
            lines.append("")

        if entry.achievements_earned:
            lines.append(f"**Achievements:** {', '.join(entry.achievements_earned)}")
            lines.append("")

        if entry.key_events:
            lines.append("**Key events:**")
            for evt in entry.key_events:
                lines.append(f"- {evt}")
            lines.append("")

        if entry.hours_worked > 0:
            lines.append(f"**Hours worked:** {entry.hours_worked:.1f}")
            lines.append("")

        if not (entry.prs_merged or entry.tests_written or entry.achievements_earned or entry.key_events):
            lines.append("No activity recorded.")
            lines.append("")

    return "\n".join(lines).rstrip()


def format_text(report: StandupReport) -> str:
    """Format report as plain text.

    Args:
        report: StandupReport to format.

    Returns:
        Plain text string.
    """
    lines: list[str] = []

    for entry in report.entries:
        if report.period == "weekly":
            lines.append(f"Weekly Summary — {entry.date}")
        else:
            lines.append(f"Daily Standup — {entry.date}")
        lines.append("-" * 40)

        if entry.prs_merged:
            count = len(entry.prs_merged)
            lines.append(f"PRs merged: {count}")
            for pr in entry.prs_merged:
                num = pr.get("number", "?")
                title = pr.get("title", "untitled")
                lines.append(f"  #{num} — {title}")

        if entry.tests_written > 0:
            lines.append(f"Tests written: {entry.tests_written}")

        if entry.achievements_earned:
            lines.append(f"Achievements: {', '.join(entry.achievements_earned)}")

        if entry.key_events:
            lines.append("Key events:")
            for evt in entry.key_events:
                lines.append(f"  - {evt}")

        if entry.hours_worked > 0:
            lines.append(f"Hours worked: {entry.hours_worked:.1f}")

        if not (entry.prs_merged or entry.tests_written or entry.achievements_earned or entry.key_events):
            lines.append("No activity recorded.")

        lines.append("")

    return "\n".join(lines).rstrip()


def format_report(report: StandupReport) -> str:
    """Dispatch to the appropriate formatter based on report.format_type.

    Args:
        report: StandupReport with format_type set.

    Returns:
        Formatted string.

    Raises:
        ValueError: If format_type is not recognized.
    """
    formatters = {
        "slack": format_slack,
        "markdown": format_markdown,
        "text": format_text,
    }
    formatter = formatters.get(report.format_type)
    if formatter is None:
        raise ValueError(
            f"Unknown format type '{report.format_type}'. "
            f"Valid: {', '.join(sorted(formatters))}"
        )
    return formatter(report)


def load_session_data(
    date: str,
    metrics_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load session run data for a specific date.

    Expects a JSON file with a 'runs' key containing a list of run objects,
    each with an optional 'date' field for filtering.

    Args:
        date: ISO date string (YYYY-MM-DD) to filter by.
        metrics_path: Path to the metrics JSON file. Defaults to
                      data/session-metrics.json relative to the repo root.

    Returns:
        List of run dicts matching the given date.

    Raises:
        FileNotFoundError: If metrics file does not exist.
        json.JSONDecodeError: If metrics file is invalid JSON.
    """
    if metrics_path is None:
        repo_root = Path(__file__).resolve().parent.parent
        metrics_path = repo_root / "data" / "session-metrics.json"
    else:
        metrics_path = Path(metrics_path)

    if not metrics_path.exists():
        raise FileNotFoundError(f"Metrics file not found: {metrics_path}")

    with open(metrics_path, "r") as f:
        data = json.load(f)

    runs = data.get("runs", [])
    return [r for r in runs if r.get("date") == date]


def standup_for_date(
    date: str,
    metrics_path: str | Path | None = None,
    prs: list[dict[str, Any]] | None = None,
    achievements: list[str] | None = None,
    fmt: str = "text",
) -> str:
    """High-level: generate a formatted standup for a single date.

    Args:
        date: ISO date string.
        metrics_path: Optional path to metrics JSON.
        prs: Optional PR list (if not loading from metrics).
        achievements: Optional achievements list.
        fmt: Output format (slack/markdown/text).

    Returns:
        Formatted standup string.
    """
    try:
        runs = load_session_data(date, metrics_path)
    except FileNotFoundError:
        runs = []

    entry = build_daily_entry(date, runs=runs, prs=prs, achievements=achievements)
    report = StandupReport(entries=[entry], period="daily", format_type=fmt)
    return format_report(report)


def standup_for_week(
    end_date: str,
    metrics_path: str | Path | None = None,
    prs_by_date: dict[str, list[dict[str, Any]]] | None = None,
    achievements_by_date: dict[str, list[str]] | None = None,
    fmt: str = "text",
) -> str:
    """High-level: generate a formatted weekly standup summary.

    Args:
        end_date: ISO date string for the last day of the week.
        metrics_path: Optional path to metrics JSON.
        prs_by_date: Optional dict mapping dates to PR lists.
        achievements_by_date: Optional dict mapping dates to achievements.
        fmt: Output format (slack/markdown/text).

    Returns:
        Formatted weekly standup string.
    """
    prs_by_date = prs_by_date or {}
    achievements_by_date = achievements_by_date or {}

    end = datetime.strptime(end_date, "%Y-%m-%d")
    entries = []

    for i in range(6, -1, -1):
        day = end - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        try:
            runs = load_session_data(day_str, metrics_path)
        except FileNotFoundError:
            runs = []
        prs = prs_by_date.get(day_str, [])
        achievements = achievements_by_date.get(day_str, [])
        entry = build_daily_entry(day_str, runs=runs, prs=prs, achievements=achievements)
        if entry.prs_merged or entry.tests_written or entry.achievements_earned or entry.key_events:
            entries.append(entry)

    if not entries:
        empty_entry = StandupEntry(
            date=f"{(end - timedelta(days=6)).strftime('%Y-%m-%d')} to {end_date}"
        )
        report = StandupReport(entries=[empty_entry], period="weekly", format_type=fmt)
        return format_report(report)

    summary = build_weekly_summary(entries)
    report = StandupReport(entries=[summary], period="weekly", format_type=fmt)
    return format_report(report)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for standup generation.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        prog="standup",
        description="Generate daily standup summaries from GRIP session data.",
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Date for the standup (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--format",
        choices=["slack", "markdown", "text"],
        default="text",
        dest="fmt",
        help="Output format. Default: text.",
    )
    parser.add_argument(
        "--week",
        action="store_true",
        help="Generate weekly summary ending on --date.",
    )
    parser.add_argument(
        "--metrics",
        default=None,
        help="Path to session metrics JSON file.",
    )

    args = parser.parse_args(argv)

    try:
        if args.week:
            output = standup_for_week(args.date, metrics_path=args.metrics, fmt=args.fmt)
        else:
            output = standup_for_date(args.date, metrics_path=args.metrics, fmt=args.fmt)
        print(output)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
