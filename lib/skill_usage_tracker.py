#!/usr/bin/env python3
"""Track GRIP skill invocations across Claude Code session JSONL files.

Parses session JSONL files from ~/.claude/projects/*/ for Skill tool
invocations, aggregates usage statistics, identifies unused skills,
and provides trend analysis.

Zero external dependencies -- stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SKILLS_DIR = CLAUDE_DIR / "skills"

SKIP_DIRS = frozenset({
    "node_modules", ".next", "dist", "build", "__pycache__",
    "venv", "target", "vendor", "Pods", "_archived",
})

ISO_FMT = "%Y-%m-%dT%H:%M:%S"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class SkillRecord:
    """Accumulator for a single skill's usage data."""

    __slots__ = ("name", "count", "last_used", "session_ids", "timestamps")

    def __init__(self, name: str) -> None:
        self.name = name
        self.count: int = 0
        self.last_used: str = ""
        self.session_ids: set[str] = set()
        self.timestamps: list[str] = []

    def add(self, timestamp: str, session_id: str) -> None:
        self.count += 1
        self.session_ids.add(session_id)
        self.timestamps.append(timestamp)
        if not self.last_used or timestamp > self.last_used:
            self.last_used = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "last_used": self.last_used,
            "sessions_used_in": len(self.session_ids),
        }


# ---------------------------------------------------------------------------
# ISO timestamp utilities
# ---------------------------------------------------------------------------

def parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp to a tz-aware datetime.

    Handles formats like:
    - 2026-02-27T22:38:51.521Z
    - 2026-02-27T22:38:51+00:00
    - 2026-02-27T22:38:51
    """
    ts = ts.replace("Z", "+00:00")
    # Strip fractional seconds but preserve timezone
    if "." in ts:
        base, rest = ts.split(".", 1)
        tz_part = ""
        for sep in ("+", "-"):
            if sep in rest:
                idx = rest.index(sep)
                tz_part = rest[idx:]
                break
        ts = base + tz_part if tz_part else base + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        clean = ts[:19]
        return datetime.strptime(clean, ISO_FMT).replace(tzinfo=timezone.utc)


def week_key(dt: datetime) -> str:
    """Return ISO year-week string like '2026-W09'."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def month_key(dt: datetime) -> str:
    """Return year-month string like '2026-02'."""
    return dt.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

def iter_jsonl_lines(path: Path):
    """Yield parsed JSON objects from a JSONL file, skipping bad lines."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
    except (OSError, PermissionError):
        return


def extract_skill_calls(obj: dict) -> list[tuple[str, str, str]]:
    """Return list of (skill_name, timestamp, session_id) from one JSONL object.

    Looks for tool_use blocks with name == "Skill" and extracts the
    skill name from the input.skill field.
    """
    results: list[tuple[str, str, str]] = []
    message = obj.get("message")
    if not isinstance(message, dict):
        return results
    content = message.get("content")
    if not isinstance(content, list):
        return results

    timestamp = obj.get("timestamp", "")
    session_id = obj.get("sessionId", "")

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        if block.get("name") != "Skill":
            continue
        inp = block.get("input")
        if not isinstance(inp, dict):
            continue
        skill_name = inp.get("skill", "")
        if skill_name:
            results.append((skill_name, timestamp, session_id))

    return results


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def find_session_files(
    projects_dir: Path,
    since: datetime | None = None,
) -> list[Path]:
    """Return all session JSONL files, optionally filtered by mtime.

    Walks the projects directory looking for *.jsonl files.
    Skips empty files and directories in SKIP_DIRS.
    When ``since`` is given, only files modified after that time are returned.
    """
    files: list[Path] = []
    if not projects_dir.is_dir():
        return files

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        if project_dir.name in SKIP_DIRS:
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            if not jsonl_file.is_file():
                continue
            try:
                size = jsonl_file.stat().st_size
            except OSError:
                continue
            if size == 0:
                continue
            if since is not None:
                try:
                    mtime = datetime.fromtimestamp(
                        jsonl_file.stat().st_mtime, tz=timezone.utc
                    )
                except OSError:
                    continue
                if mtime < since:
                    continue
            files.append(jsonl_file)

    return sorted(files)


# ---------------------------------------------------------------------------
# Skill directory listing
# ---------------------------------------------------------------------------

def list_known_skills(skills_dir: Path) -> set[str]:
    """Return set of skill directory names (excluding archived/hidden)."""
    skills: set[str] = set()
    if not skills_dir.is_dir():
        return skills
    for entry in skills_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        skills.add(entry.name)
    return skills


# ---------------------------------------------------------------------------
# Core aggregation
# ---------------------------------------------------------------------------

def aggregate_usage(
    session_files: list[Path],
    since: datetime | None = None,
) -> dict[str, SkillRecord]:
    """Parse all session files and aggregate skill usage.

    If ``since`` is provided, individual invocations are filtered by their
    actual ISO timestamp (not just the file mtime).
    """
    records: dict[str, SkillRecord] = {}

    for path in session_files:
        for obj in iter_jsonl_lines(path):
            calls = extract_skill_calls(obj)
            for skill_name, timestamp, session_id in calls:
                if since is not None and timestamp:
                    try:
                        ts_dt = parse_iso(timestamp)
                        if ts_dt < since:
                            continue
                    except (ValueError, TypeError):
                        pass

                if skill_name not in records:
                    records[skill_name] = SkillRecord(skill_name)
                records[skill_name].add(timestamp, session_id)

    return records


def find_unused_skills(
    records: dict[str, SkillRecord],
    known_skills: set[str],
) -> list[str]:
    """Return skill names that exist on disk but have zero invocations."""
    used = set(records.keys())
    return sorted(known_skills - used)


def top_skills(
    records: dict[str, SkillRecord],
    n: int,
) -> list[SkillRecord]:
    """Return the top N most-used skills by invocation count."""
    ranked = sorted(records.values(), key=lambda r: (-r.count, r.name))
    return ranked[:n]


# ---------------------------------------------------------------------------
# Trend analysis
# ---------------------------------------------------------------------------

def compute_trends(
    records: dict[str, SkillRecord],
    bucket: str = "week",
) -> dict[str, dict[str, int]]:
    """Bucket skill usage by week or month.

    Returns ``{skill_name: {bucket_key: count, ...}, ...}``.
    """
    key_fn = week_key if bucket == "week" else month_key
    trends: dict[str, dict[str, int]] = {}

    for rec in records.values():
        skill_buckets: dict[str, int] = defaultdict(int)
        for ts in rec.timestamps:
            if not ts:
                continue
            try:
                dt = parse_iso(ts)
                bk = key_fn(dt)
                skill_buckets[bk] += 1
            except (ValueError, TypeError):
                continue
        if skill_buckets:
            trends[rec.name] = dict(sorted(skill_buckets.items()))

    return trends


def trend_direction(buckets: dict[str, int]) -> str:
    """Classify trend as 'rising', 'declining', or 'stable'.

    Compares the last two buckets.
    """
    if len(buckets) < 2:
        return "stable"
    keys = sorted(buckets.keys())
    prev = buckets[keys[-2]]
    curr = buckets[keys[-1]]
    if curr > prev:
        return "rising"
    elif curr < prev:
        return "declining"
    return "stable"


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(
    records: dict[str, SkillRecord],
    unused: list[str] | None = None,
    trends: dict[str, dict[str, int]] | None = None,
    top_n: int | None = None,
) -> str:
    """Format results as a markdown table."""
    lines: list[str] = []

    items = sorted(records.values(), key=lambda r: (-r.count, r.name))
    if top_n is not None:
        items = items[:top_n]

    lines.append("# Skill Usage Report")
    lines.append("")
    lines.append(f"Total skills invoked: {len(records)}")
    lines.append(f"Total invocations: {sum(r.count for r in records.values())}")
    lines.append("")

    if items:
        lines.append("## Usage by Skill")
        lines.append("")
        lines.append("| Skill | Count | Sessions | Last Used |")
        lines.append("|-------|------:|--------:|-----------|")
        for rec in items:
            last = rec.last_used[:19] if rec.last_used else "n/a"
            lines.append(
                f"| {rec.name} | {rec.count} | "
                f"{len(rec.session_ids)} | {last} |"
            )
        lines.append("")

    if unused is not None:
        lines.append("## Unused Skills")
        lines.append("")
        if unused:
            lines.append(f"{len(unused)} skills have never been invoked:")
            lines.append("")
            for s in unused:
                lines.append(f"- {s}")
        else:
            lines.append("All skills have been invoked at least once.")
        lines.append("")

    if trends is not None:
        lines.append("## Trends")
        lines.append("")
        for skill_name in sorted(trends.keys()):
            buckets = trends[skill_name]
            direction = trend_direction(buckets)
            bucket_str = ", ".join(
                f"{k}: {v}" for k, v in buckets.items()
            )
            lines.append(f"- **{skill_name}** ({direction}): {bucket_str}")
        lines.append("")

    return "\n".join(lines)


def format_json(
    records: dict[str, SkillRecord],
    unused: list[str] | None = None,
    trends: dict[str, dict[str, int]] | None = None,
    top_n: int | None = None,
) -> str:
    """Format results as JSON."""
    items = sorted(records.values(), key=lambda r: (-r.count, r.name))
    if top_n is not None:
        items = items[:top_n]

    data: dict[str, Any] = {
        "total_skills_invoked": len(records),
        "total_invocations": sum(r.count for r in records.values()),
        "skills": [r.to_dict() for r in items],
    }
    if unused is not None:
        data["unused_skills"] = unused
    if trends is not None:
        trend_list = []
        for name in sorted(trends.keys()):
            buckets = trends[name]
            trend_list.append({
                "skill": name,
                "direction": trend_direction(buckets),
                "buckets": buckets,
            })
        data["trends"] = trend_list
    return json.dumps(data, indent=2)


def format_plain(
    records: dict[str, SkillRecord],
    unused: list[str] | None = None,
    trends: dict[str, dict[str, int]] | None = None,
    top_n: int | None = None,
) -> str:
    """Format results as plain text."""
    lines: list[str] = []
    items = sorted(records.values(), key=lambda r: (-r.count, r.name))
    if top_n is not None:
        items = items[:top_n]

    lines.append("Skill Usage Report")
    lines.append(f"Total skills invoked: {len(records)}")
    lines.append(f"Total invocations: {sum(r.count for r in records.values())}")
    lines.append("")

    for rec in items:
        last = rec.last_used[:19] if rec.last_used else "n/a"
        lines.append(
            f"  {rec.name:40s}  count={rec.count:4d}  "
            f"sessions={len(rec.session_ids):3d}  last={last}"
        )

    if unused is not None:
        lines.append("")
        lines.append(f"Unused skills ({len(unused)}):")
        for s in unused:
            lines.append(f"  - {s}")

    if trends is not None:
        lines.append("")
        lines.append("Trends:")
        for name in sorted(trends.keys()):
            buckets = trends[name]
            direction = trend_direction(buckets)
            bucket_str = ", ".join(f"{k}:{v}" for k, v in buckets.items())
            lines.append(f"  {name}: {direction} [{bucket_str}]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill_usage_tracker",
        description="Track GRIP skill invocations across sessions.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON instead of markdown",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Output as plain text instead of markdown",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include invocations after this date",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Show only the top N skills",
    )
    parser.add_argument(
        "--unused",
        action="store_true",
        help="Show unused skills (cross-ref with skills/ directory)",
    )
    parser.add_argument(
        "--trends",
        nargs="?",
        const="week",
        default=None,
        choices=["week", "month"],
        help="Show usage trends bucketed by week (default) or month",
    )
    parser.add_argument(
        "--projects-dir",
        type=str,
        default=None,
        help="Override projects directory (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--skills-dir",
        type=str,
        default=None,
        help="Override skills directory (default: ~/.claude/skills)",
    )
    return parser


def parse_since(value: str) -> datetime:
    """Parse a YYYY-MM-DD string into a tz-aware datetime."""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date format: {value!r} (expected YYYY-MM-DD)"
        ) from exc


def run(args: argparse.Namespace) -> str:
    """Execute the tracker with parsed args and return formatted output."""
    projects_dir = Path(args.projects_dir) if args.projects_dir else PROJECTS_DIR
    skills_dir = Path(args.skills_dir) if args.skills_dir else SKILLS_DIR

    since: datetime | None = None
    if args.since:
        since = parse_since(args.since)

    session_files = find_session_files(projects_dir, since=since)
    records = aggregate_usage(session_files, since=since)

    unused: list[str] | None = None
    if args.unused:
        known = list_known_skills(skills_dir)
        unused = find_unused_skills(records, known)

    trends: dict[str, dict[str, int]] | None = None
    if args.trends:
        trends = compute_trends(records, bucket=args.trends)

    if args.json_output:
        return format_json(records, unused=unused, trends=trends, top_n=args.top)
    elif args.plain:
        return format_plain(records, unused=unused, trends=trends, top_n=args.top)
    else:
        return format_markdown(records, unused=unused, trends=trends, top_n=args.top)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    output = run(args)
    print(output)


if __name__ == "__main__":
    main()
