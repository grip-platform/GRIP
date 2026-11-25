#!/usr/bin/env python3
"""GRIP Changelog Generator -- structured release notes from merged PRs.

Generates changelogs by fetching merged PRs via gh, classifying them
by conventional commit type, and rendering grouped markdown or plain text.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence


# -- Data model ----------------------------------------------------------------

@dataclass
class PrEntry:
    """A single merged pull request."""
    number: int
    title: str
    pr_type: str  # feat/fix/chore/docs/refactor/test/perf
    summary: str
    merged_at: str  # ISO-8601 date string
    author: str


# -- Type constants ------------------------------------------------------------

KNOWN_TYPES = ("feat", "fix", "chore", "docs", "refactor", "test", "perf")

TYPE_LABELS = {
    "feat": "Features",
    "fix": "Fixes",
    "chore": "Infrastructure",
    "docs": "Documentation",
    "refactor": "Refactoring",
    "test": "Tests",
    "perf": "Performance",
}

# Conventional-commit prefix pattern: "type(scope): msg" or "type: msg"
_PREFIX_RE = re.compile(
    r"^(?P<type>" + "|".join(KNOWN_TYPES) + r")"
    r"(?:\([^)]*\))?"   # optional scope
    r":\s*",
    re.IGNORECASE,
)


# -- Classification ------------------------------------------------------------

def classify_pr(title: str) -> str:
    """Extract the conventional-commit type from a PR title.

    Returns the lowercase type string, or chore when no known prefix
    is found.
    """
    m = _PREFIX_RE.match(title)
    if m:
        return m.group("type").lower()
    return "chore"


# -- Fetching ------------------------------------------------------------------

def fetch_merged_prs(
    since: Optional[str] = None,
    limit: int = 100,
    *,
    _runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> List[PrEntry]:
    """Fetch merged PRs via gh pr list.

    Parameters
    ----------
    since:
        ISO-8601 date string.  Only PRs merged on or after this date are
        returned.  When None, no date filtering is applied at fetch time.
    limit:
        Maximum number of PRs to retrieve.
    _runner:
        Testing hook -- callable with the same signature as
        subprocess.run.  When provided, it is called instead of the
        real subprocess.run so tests never invoke gh.
    """
    run = _runner or subprocess.run

    cmd = [
        "gh", "pr", "list",
        "--state", "merged",
        "--limit", str(limit),
        "--json", "number,title,mergedAt,author,body",
    ]

    result = run(cmd, capture_output=True, text=True, check=True)
    raw: list[dict] = json.loads(result.stdout)

    entries: List[PrEntry] = []
    for item in raw:
        title = item.get("title", "")
        pr_type = classify_pr(title)
        merged_at = item.get("mergedAt", "")
        author_data = item.get("author", {})
        author = (
            author_data.get("login", "")
            if isinstance(author_data, dict)
            else str(author_data)
        )

        entry = PrEntry(
            number=item.get("number", 0),
            title=title,
            pr_type=pr_type,
            summary=item.get("body", ""),
            merged_at=merged_at,
            author=author,
        )
        entries.append(entry)

    if since:
        entries = filter_since(entries, since)

    return entries


# -- Grouping / filtering -----------------------------------------------------

def group_by_type(entries: Sequence[PrEntry]) -> Dict[str, List[PrEntry]]:
    """Group entries by pr_type, preserving insertion order."""
    groups: Dict[str, List[PrEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.pr_type, []).append(entry)
    return groups


def filter_since(entries: Sequence[PrEntry], since_date: str) -> List[PrEntry]:
    """Return entries merged on or after since_date (ISO-8601 date)."""
    cutoff = _parse_date(since_date)
    return [e for e in entries if _parse_date(e.merged_at[:10]) >= cutoff]


def _parse_date(date_str: str) -> datetime:
    """Parse an ISO-8601 date (YYYY-MM-DD) into a datetime."""
    return datetime.strptime(date_str[:10], "%Y-%m-%d")


# -- Formatting ----------------------------------------------------------------

def format_markdown(
    groups: Dict[str, List[PrEntry]],
    title: str = "Changelog",
) -> str:
    """Render grouped PRs as a markdown changelog."""
    lines: list[str] = [f"## {title}", ""]

    # Sort sections in KNOWN_TYPES order, then any unknown types alphabetically
    ordered_types = [t for t in KNOWN_TYPES if t in groups]
    extra = sorted(set(groups) - set(KNOWN_TYPES))
    ordered_types.extend(extra)

    for pr_type in ordered_types:
        label = TYPE_LABELS.get(pr_type, pr_type.title())
        entries = groups[pr_type]
        lines.append(f"### {label}")
        for e in entries:
            lines.append(f"- {e.title} (#{e.number})")
        lines.append("")

    return "\n".join(lines)


def format_text(groups: Dict[str, List[PrEntry]]) -> str:
    """Render grouped PRs as plain text."""
    lines: list[str] = []

    ordered_types = [t for t in KNOWN_TYPES if t in groups]
    extra = sorted(set(groups) - set(KNOWN_TYPES))
    ordered_types.extend(extra)

    for pr_type in ordered_types:
        label = TYPE_LABELS.get(pr_type, pr_type.title())
        entries = groups[pr_type]
        lines.append(f"{label}:")
        for e in entries:
            lines.append(f"  - {e.title} (#{e.number})")
        lines.append("")

    return "\n".join(lines)


def format_wave(entries: Sequence[PrEntry], wave_number: int) -> str:
    """Format a single sprint wave as markdown."""
    groups = group_by_type(entries)
    return format_markdown(groups, title=f"Wave {wave_number}")


# -- End-to-end ---------------------------------------------------------------

def generate_changelog(
    since: Optional[str] = None,
    limit: int = 100,
    fmt: str = "markdown",
    *,
    _runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> str:
    """End-to-end changelog generation.

    Parameters
    ----------
    since:
        ISO date filter passed to fetch_merged_prs.
    limit:
        Max PRs to fetch.
    fmt:
        markdown or text.
    _runner:
        Testing hook forwarded to fetch_merged_prs.
    """
    entries = fetch_merged_prs(since=since, limit=limit, _runner=_runner)
    groups = group_by_type(entries)

    if fmt == "text":
        return format_text(groups)
    return format_markdown(groups)


# -- CLI -----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="changelog_generator",
        description="Generate structured release notes from merged PRs.",
    )
    sub = parser.add_subparsers(dest="command")

    # generate
    gen_p = sub.add_parser("generate", help="Generate changelog")
    gen_p.add_argument("--limit", type=int, default=100)
    gen_p.add_argument("--format", dest="fmt", choices=["markdown", "text"],
                       default="markdown")
    gen_p.add_argument("--since", default=None,
                       help="ISO date (YYYY-MM-DD)")

    # since
    since_p = sub.add_parser("since", help="Changelog since date")
    since_p.add_argument("date", help="ISO date (YYYY-MM-DD)")
    since_p.add_argument("--format", dest="fmt", choices=["markdown", "text"],
                         default="markdown")
    since_p.add_argument("--limit", type=int, default=100)

    # wave
    wave_p = sub.add_parser("wave", help="Format a sprint wave")
    wave_p.add_argument("number", type=int, help="Wave number")
    wave_p.add_argument("--limit", type=int, default=100)
    wave_p.add_argument("--since", default=None)

    # format
    fmt_p = sub.add_parser("format", help="Set output format")
    fmt_p.add_argument("fmt", choices=["markdown", "text"])
    fmt_p.add_argument("--limit", type=int, default=100)
    fmt_p.add_argument("--since", default=None)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "generate":
        print(generate_changelog(since=args.since, limit=args.limit,
                                 fmt=args.fmt))

    elif args.command == "since":
        print(generate_changelog(since=args.date, limit=args.limit,
                                 fmt=args.fmt))

    elif args.command == "wave":
        entries = fetch_merged_prs(since=args.since, limit=args.limit)
        print(format_wave(entries, args.number))

    elif args.command == "format":
        print(generate_changelog(since=args.since, limit=args.limit,
                                 fmt=args.fmt))


if __name__ == "__main__":
    main()
