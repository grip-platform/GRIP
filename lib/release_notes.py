"""
Release Notes Composer -- polished, audience-appropriate release notes from git history.

Unlike a changelog (comprehensive), release notes are curated and user-facing.
Filters noise, detects highlights, and formats for multiple targets.

Usage:
    python3 lib/release_notes.py /path/to/repo --since v1.0.0 --version v1.1.0
    python3 lib/release_notes.py . --since 2026-02-01 --until 2026-02-17 --format slack
    python3 lib/release_notes.py . --since v1.0.0 --format json
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# --- Dataclasses ---


@dataclass
class ReleaseItem:
    """A single release-worthy change."""
    title: str
    description: str = ""
    category: str = "Improvements"  # New Features, Improvements, Bug Fixes, Breaking Changes
    impact: str = "minor"  # minor, moderate, major
    pr_number: Optional[int] = None


@dataclass
class ReleaseHighlight:
    """A top-level highlight for the release summary."""
    title: str
    description: str = ""
    icon: str = ">"


@dataclass
class ReleaseNotes:
    """Complete release notes structure."""
    version: str
    date: str
    highlights: list = field(default_factory=list)
    items_by_category: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    breaking_changes: list = field(default_factory=list)
    migration_notes: list = field(default_factory=list)
    contributors: list = field(default_factory=list)


# --- Noise filters ---

NOISE_PATTERNS = [
    r"^Merge\s+(branch|pull\s+request|remote)",
    r"^Merge\s+['\"]",
    r"^fixup!\s+",
    r"^squash!\s+",
    r"^wip[\s:]",
    r"^WIP[\s:]",
    r"^chore:",
    r"^chore\(",
    r"^ci:",
    r"^ci\(",
    r"^build:",
    r"^build\(",
    r"^style:",
    r"^style\(",
]

HIGHLIGHT_KEYWORDS = [
    "new", "major", "redesign", "overhaul", "rewrite",
    "breaking", "migration", "introduce", "launch",
]

CATEGORY_MAP = {
    "feat": "New Features",
    "feature": "New Features",
    "add": "New Features",
    "new": "New Features",
    "fix": "Bug Fixes",
    "hotfix": "Bug Fixes",
    "bugfix": "Bug Fixes",
    "refactor": "Improvements",
    "perf": "Improvements",
    "improve": "Improvements",
    "update": "Improvements",
    "enhance": "Improvements",
    "docs": "Improvements",
    "test": "Improvements",
}


# --- Parsing ---


def _is_noise(message: str) -> bool:
    """Check if a commit message is noise that should be filtered."""
    if not message or not message.strip():
        return True
    for pattern in NOISE_PATTERNS:
        if re.match(pattern, message.strip(), re.IGNORECASE):
            return True
    return False


def _extract_pr_number(message: str) -> Optional[int]:
    """Extract PR number from commit message like '(#123)' or 'PR #123'."""
    match = re.search(r"\(#(\d+)\)", message)
    if match:
        return int(match.group(1))
    match = re.search(r"(?:PR|pull request)\s*#(\d+)", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _detect_breaking(message: str, body: str = "") -> bool:
    """Detect if a commit introduces breaking changes."""
    combined = f"{message}\n{body}".lower()
    if "breaking change" in combined:
        return True
    if "BREAKING CHANGE" in f"{message}\n{body}":
        return True
    if message.strip().startswith("feat") and "!" in message.split(":")[0]:
        return True
    return False


def _categorize(prefix: str) -> str:
    """Map a conventional commit prefix to a release category."""
    return CATEGORY_MAP.get(prefix.lower(), "Improvements")


def _parse_commit(message: str, body: str = "") -> Optional[ReleaseItem]:
    """Parse a commit message into a ReleaseItem, or None if noise."""
    if _is_noise(message):
        return None

    message = message.strip()
    pr_number = _extract_pr_number(message)
    is_breaking = _detect_breaking(message, body)

    # Extract conventional commit prefix
    prefix_match = re.match(r"^(\w+)(?:\([^)]*\))?!?:\s*(.*)", message)
    if prefix_match:
        prefix = prefix_match.group(1)
        title = prefix_match.group(2).strip()
        category = "Breaking Changes" if is_breaking else _categorize(prefix)
    else:
        title = message
        category = "Breaking Changes" if is_breaking else "Improvements"

    # Clean up title -- remove trailing PR reference for cleaner display
    title = re.sub(r"\s*\(#\d+\)\s*$", "", title).strip()
    if not title:
        return None

    # Capitalize first letter
    title = title[0].upper() + title[1:] if title else title

    # Determine impact
    impact = "major" if is_breaking else "minor"
    if any(kw in message.lower() for kw in HIGHLIGHT_KEYWORDS):
        impact = "moderate" if impact != "major" else impact

    description = body.strip() if body else ""

    return ReleaseItem(
        title=title,
        description=description,
        category=category,
        impact=impact,
        pr_number=pr_number,
    )


def _detect_highlights(items: list, max_highlights: int = 5) -> list:
    """Select top highlights from release items based on impact and keywords."""
    scored = []
    for item in items:
        score = 0
        if item.impact == "major":
            score += 10
        elif item.impact == "moderate":
            score += 5
        if item.category == "Breaking Changes":
            score += 15
        if item.category == "New Features":
            score += 3
        lower_title = item.title.lower()
        for kw in HIGHLIGHT_KEYWORDS:
            if kw in lower_title:
                score += 2
        if item.pr_number:
            score += 1
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    highlights = []
    for score, item in scored[:max_highlights]:
        if score <= 0:
            continue
        icon = "!"
        if item.category == "Breaking Changes":
            icon = "!!"
        elif item.category == "New Features":
            icon = "+"
        elif item.category == "Bug Fixes":
            icon = "~"
        highlights.append(ReleaseHighlight(
            title=item.title,
            description=item.description,
            icon=icon,
        ))

    return highlights


def _compute_stats(
    commits_count: int = 0,
    contributors: list = None,
    files_changed: int = 0,
    lines_added: int = 0,
    lines_removed: int = 0,
) -> dict:
    """Build stats dict."""
    return {
        "commits": commits_count,
        "contributors": len(contributors) if contributors else 0,
        "files_changed": files_changed,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
    }


# --- Compose ---


def compose_from_data(
    commits: list,
    version: str = "0.0.0",
    date: str = "",
    contributors: list = None,
    files_changed: int = 0,
    lines_added: int = 0,
    lines_removed: int = 0,
) -> ReleaseNotes:
    """Compose release notes from pre-parsed commit data.

    Args:
        commits: List of dicts with 'message' and optional 'body' keys.
        version: Semantic version string.
        date: Release date string (defaults to today).
        contributors: List of contributor names.
        files_changed: Total files changed.
        lines_added: Total lines added.
        lines_removed: Total lines removed.

    Returns:
        ReleaseNotes dataclass.
    """
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if contributors is None:
        contributors = []

    items = []
    for commit in commits:
        msg = commit.get("message", "")
        body = commit.get("body", "")
        item = _parse_commit(msg, body)
        if item:
            items.append(item)

    # Group by category
    items_by_category = {}
    breaking = []
    for item in items:
        if item.category == "Breaking Changes":
            breaking.append(item)
        cat = item.category
        if cat not in items_by_category:
            items_by_category[cat] = []
        items_by_category[cat].append(item)

    highlights = _detect_highlights(items)
    stats = _compute_stats(
        commits_count=len(commits),
        contributors=contributors,
        files_changed=files_changed,
        lines_added=lines_added,
        lines_removed=lines_removed,
    )

    return ReleaseNotes(
        version=version,
        date=date,
        highlights=highlights,
        items_by_category=items_by_category,
        stats=stats,
        breaking_changes=breaking,
        migration_notes=[],
        contributors=contributors,
    )


def compose_release_notes(
    path: str = ".",
    since: str = "",
    until: str = "",
    version: str = "0.0.0",
) -> ReleaseNotes:
    """Compose release notes from git history.

    Args:
        path: Path to git repository.
        since: Git ref or date for start (tag, commit, date).
        until: Git ref or date for end (default: HEAD).
        version: Version string for these release notes.

    Returns:
        ReleaseNotes dataclass.
    """
    repo = Path(path).resolve()

    # Build git log command
    log_format = "%H%n%s%n%b%n---END---"
    cmd = ["git", "-C", str(repo), "log", f"--format={log_format}"]
    if since and until:
        cmd.append(f"{since}..{until}")
    elif since:
        cmd.append(f"{since}..HEAD")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        raw = result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raw = ""

    # Parse raw log
    commits = []
    if raw.strip():
        entries = raw.split("---END---")
        for entry in entries:
            lines = entry.strip().split("\n")
            if len(lines) < 2:
                continue
            _hash = lines[0].strip()
            message = lines[1].strip()
            body = "\n".join(lines[2:]).strip()
            if message:
                commits.append({"message": message, "body": body})

    # Get contributors
    contrib_cmd = ["git", "-C", str(repo), "log", "--format=%aN"]
    if since and until:
        contrib_cmd.append(f"{since}..{until}")
    elif since:
        contrib_cmd.append(f"{since}..HEAD")

    contributors = []
    try:
        result = subprocess.run(
            contrib_cmd, capture_output=True, text=True, timeout=30,
        )
        if result.stdout.strip():
            contributors = sorted(set(result.stdout.strip().split("\n")))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Get diffstat
    stat_cmd = ["git", "-C", str(repo), "diff", "--stat"]
    if since and until:
        stat_cmd.append(f"{since}..{until}")
    elif since:
        stat_cmd.append(f"{since}..HEAD")

    files_changed = 0
    lines_added = 0
    lines_removed = 0
    try:
        shortstat_cmd = stat_cmd + ["--shortstat"]
        result = subprocess.run(
            shortstat_cmd, capture_output=True, text=True, timeout=30,
        )
        stat_line = result.stdout.strip()
        m = re.search(r"(\d+)\s+files?\s+changed", stat_line)
        if m:
            files_changed = int(m.group(1))
        m = re.search(r"(\d+)\s+insertions?\(\+\)", stat_line)
        if m:
            lines_added = int(m.group(1))
        m = re.search(r"(\d+)\s+deletions?\(-\)", stat_line)
        if m:
            lines_removed = int(m.group(1))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return compose_from_data(
        commits=commits,
        version=version,
        date=date,
        contributors=contributors,
        files_changed=files_changed,
        lines_added=lines_added,
        lines_removed=lines_removed,
    )


# --- Formatters ---


def format_notes_json(notes: ReleaseNotes) -> str:
    """Format release notes as JSON."""
    data = {
        "version": notes.version,
        "date": notes.date,
        "highlights": [asdict(h) for h in notes.highlights],
        "items_by_category": {},
        "stats": notes.stats,
        "breaking_changes": [asdict(b) for b in notes.breaking_changes],
        "migration_notes": notes.migration_notes,
        "contributors": notes.contributors,
    }
    for cat, items in notes.items_by_category.items():
        data["items_by_category"][cat] = [asdict(i) for i in items]
    return json.dumps(data, indent=2)


def format_notes_digest(notes: ReleaseNotes) -> dict:
    """Format release notes as a dict for the grip_digest MCP app.

    Returns dict with 'title' and 'cards' matching grip_digest schema.
    """
    cards = []

    # Highlights card
    if notes.highlights:
        highlight_detail = "\n".join(
            f"- {h.title}" + (f": {h.description}" if h.description else "")
            for h in notes.highlights
        )
        cards.append({
            "id": "highlights",
            "title": "Highlights",
            "summary": f"{len(notes.highlights)} key changes in this release",
            "detail": highlight_detail,
            "status": "info",
            "icon": "+",
        })

    # Breaking changes card
    if notes.breaking_changes:
        breaking_detail = "\n".join(
            f"- {item.title}" for item in notes.breaking_changes
        )
        cards.append({
            "id": "breaking",
            "title": "Breaking Changes",
            "summary": f"{len(notes.breaking_changes)} breaking changes require attention",
            "detail": breaking_detail,
            "status": "warning",
            "icon": "!",
        })

    # Category cards
    category_order = ["New Features", "Improvements", "Bug Fixes"]
    status_map = {
        "New Features": "success",
        "Improvements": "info",
        "Bug Fixes": "success",
    }
    icon_map = {
        "New Features": "+",
        "Improvements": ">",
        "Bug Fixes": "~",
    }
    for cat in category_order:
        items = notes.items_by_category.get(cat, [])
        if not items:
            continue
        detail = "\n".join(f"- {item.title}" for item in items)
        card_id = cat.lower().replace(" ", "_")
        cards.append({
            "id": card_id,
            "title": cat,
            "summary": f"{len(items)} {cat.lower()}",
            "detail": detail,
            "status": status_map.get(cat, "info"),
            "icon": icon_map.get(cat, ">"),
        })

    # Stats card
    if notes.stats and any(v for v in notes.stats.values()):
        s = notes.stats
        stat_parts = []
        if s.get("commits"):
            stat_parts.append(f"Commits: {s['commits']}")
        if s.get("contributors"):
            stat_parts.append(f"Contributors: {s['contributors']}")
        if s.get("files_changed"):
            stat_parts.append(f"Files changed: {s['files_changed']}")
        if s.get("lines_added"):
            stat_parts.append(f"Lines added: +{s['lines_added']}")
        if s.get("lines_removed"):
            stat_parts.append(f"Lines removed: -{s['lines_removed']}")
        cards.append({
            "id": "stats",
            "title": "Release Stats",
            "summary": f"{s.get('commits', 0)} commits by {s.get('contributors', 0)} contributors",
            "detail": "\n".join(stat_parts),
            "status": "info",
            "icon": "#",
        })

    return {
        "title": f"Release {notes.version} ({notes.date})",
        "cards": cards,
    }


# --- CLI ---


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Compose release notes from git history"
    )
    parser.add_argument(
        "path", nargs="?", default=".",
        help="Path to git repository (default: current directory)"
    )
    parser.add_argument(
        "--since", default="",
        help="Start ref (tag, commit hash, or date)"
    )
    parser.add_argument(
        "--until", default="",
        help="End ref (tag, commit hash, or date; default: HEAD)"
    )
    parser.add_argument(
        "--version", default="0.0.0",
        help="Version string for the release (default: 0.0.0)"
    )
    parser.add_argument(
        "--format", dest="fmt", default="json",
        choices=["json", "digest"],
        help="Output format (default: json)"
    )

    args = parser.parse_args()

    notes = compose_release_notes(
        path=args.path,
        since=args.since,
        until=args.until,
        version=args.version,
    )

    formatters = {
        "json": format_notes_json,
        "digest": lambda n: json.dumps(format_notes_digest(n), indent=2),
    }

    output = formatters[args.fmt](notes)
    print(output)


if __name__ == "__main__":
    main()
