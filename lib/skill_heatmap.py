#!/usr/bin/env python3
"""Skill usage heatmap generator for GRIP sessions.

Scans session JSONL files to analyze which skills are used most frequently,
time-of-day patterns, and skill co-occurrence across sessions.

Usage:
    python3 lib/skill_heatmap.py              # top 10 skills
    python3 lib/skill_heatmap.py --hours      # hour-of-day distribution
    python3 lib/skill_heatmap.py --pairs      # co-occurrence pairs
    python3 lib/skill_heatmap.py --json       # JSON output
    python3 lib/skill_heatmap.py --all-tools  # include all tool types, not just Skill
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class SkillUsage:
    """Aggregated usage data for a single skill."""

    skill_name: str
    count: int = 0
    sessions: set[str] = field(default_factory=set)
    last_used: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "count": self.count,
            "sessions": sorted(self.sessions),
            "session_count": len(self.sessions),
            "last_used": self.last_used.isoformat() if self.last_used else None,
        }


def _session_files(project_dir: str | None = None, limit: int = 20) -> list[Path]:
    """Find session JSONL files, sorted by mtime descending, excluding agent-* subagent files."""
    if project_dir:
        base = Path(project_dir)
    else:
        base = Path(os.environ.get("HOME", "~")) / ".claude" / "projects"

    candidates: list[Path] = []
    for jsonl in base.rglob("*.jsonl"):
        # Skip agent-* subagent files per GRIP convention
        if jsonl.name.startswith("agent-"):
            continue
        candidates.append(jsonl)

    # Sort by modification time, newest first
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:limit]


def _extract_tool_name(content_block: dict[str, Any]) -> str | None:
    """Extract skill/tool name from a tool_use content block."""
    if content_block.get("type") != "tool_use":
        return None
    return content_block.get("name")


def _extract_skill_name(content_block: dict[str, Any]) -> str | None:
    """Extract the specific skill name from a Skill tool_use block."""
    if content_block.get("type") != "tool_use":
        return None
    if content_block.get("name") != "Skill":
        return None
    inp = content_block.get("input", {})
    return inp.get("skill") or inp.get("skill_name")


def _parse_timestamp(ts: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp string to datetime."""
    if not ts:
        return None
    try:
        # Handle Z suffix and fractional seconds
        cleaned = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def scan_sessions(
    project_dir: str | None = None,
    limit: int = 20,
    all_tools: bool = False,
) -> list[dict[str, Any]]:
    """Scan recent session files for skill invocations.

    Args:
        project_dir: Directory containing session JSONL files. If None, scans
                     all project dirs under ~/.claude/projects/.
        limit: Maximum number of session files to scan.
        all_tools: If True, track all tool types (Bash, Read, Edit, etc.),
                   not just Skill calls.

    Returns:
        List of dicts with keys: skill_name, timestamp, session_id
    """
    invocations: list[dict[str, Any]] = []

    for path in _session_files(project_dir, limit):
        session_id = path.stem
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    if obj.get("type") != "assistant":
                        continue

                    ts = _parse_timestamp(obj.get("timestamp"))
                    message = obj.get("message", {})
                    content = message.get("content", [])

                    for block in content:
                        if not isinstance(block, dict):
                            continue

                        if all_tools:
                            tool_name = _extract_tool_name(block)
                            if tool_name:
                                invocations.append({
                                    "skill_name": tool_name,
                                    "timestamp": ts,
                                    "session_id": session_id,
                                })
                        else:
                            skill_name = _extract_skill_name(block)
                            if skill_name:
                                invocations.append({
                                    "skill_name": skill_name,
                                    "timestamp": ts,
                                    "session_id": session_id,
                                })
        except (OSError, PermissionError):
            continue

    return invocations


def build_heatmap(invocations: list[dict[str, Any]]) -> dict[str, SkillUsage]:
    """Aggregate invocations by skill name into SkillUsage objects."""
    heatmap: dict[str, SkillUsage] = {}

    for inv in invocations:
        name = inv["skill_name"]
        if name not in heatmap:
            heatmap[name] = SkillUsage(skill_name=name)

        usage = heatmap[name]
        usage.count += 1
        usage.sessions.add(inv["session_id"])

        ts = inv.get("timestamp")
        if ts and (usage.last_used is None or ts > usage.last_used):
            usage.last_used = ts

    return heatmap


def top_skills(heatmap: dict[str, SkillUsage], n: int = 10) -> list[SkillUsage]:
    """Return the top N skills sorted by count descending."""
    return sorted(heatmap.values(), key=lambda u: u.count, reverse=True)[:n]


def hour_distribution(invocations: list[dict[str, Any]]) -> dict[int, int]:
    """Count invocations by hour of day (0-23)."""
    dist: dict[int, int] = defaultdict(int)
    for inv in invocations:
        ts = inv.get("timestamp")
        if ts:
            dist[ts.hour] += 1
    return dict(dist)


def co_occurrence(invocations: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Find skills used in the same session. Returns sorted pairs with counts."""
    # Group skills by session
    session_skills: dict[str, set[str]] = defaultdict(set)
    for inv in invocations:
        session_skills[inv["session_id"]].add(inv["skill_name"])

    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for skills in session_skills.values():
        skills_list = sorted(skills)
        for i, a in enumerate(skills_list):
            for b in skills_list[i + 1 :]:
                pairs[(a, b)] += 1

    return dict(pairs)


def format_heatmap(heatmap: dict[str, SkillUsage], n: int = 10) -> str:
    """Format heatmap as aligned text with usage bars.

    Example output:
        mode             [||||||||||||] 13  (4 sessions)
        broly            [||||||||||  ] 10  (3 sessions)
    """
    ranked = top_skills(heatmap, n)
    if not ranked:
        return "No skill invocations found."

    max_count = ranked[0].count
    max_name_len = max(len(u.skill_name) for u in ranked)
    bar_width = 20
    lines: list[str] = []

    for usage in ranked:
        name_padded = usage.skill_name.ljust(max_name_len)
        filled = round(usage.count / max_count * bar_width) if max_count > 0 else 0
        bar = "|" * filled + " " * (bar_width - filled)
        sess_count = len(usage.sessions)
        lines.append(
            f"  {name_padded}  [{bar}] {usage.count:>3}  ({sess_count} session{'s' if sess_count != 1 else ''})"
        )

    return "\n".join(lines)


def format_hours(dist: dict[int, int]) -> str:
    """Format hour distribution as a vertical bar chart."""
    if not dist:
        return "No timestamped invocations found."

    max_count = max(dist.values()) if dist else 1
    lines: list[str] = []

    for hour in range(24):
        count = dist.get(hour, 0)
        bar_len = round(count / max_count * 30) if max_count > 0 else 0
        bar = "|" * bar_len
        label = f"{hour:02d}:00"
        lines.append(f"  {label}  {bar} {count}" if count else f"  {label}")

    return "\n".join(lines)


def format_pairs(pairs: dict[tuple[str, str], int], n: int = 10) -> str:
    """Format co-occurrence pairs."""
    if not pairs:
        return "No co-occurring skill pairs found."

    ranked = sorted(pairs.items(), key=lambda kv: kv[1], reverse=True)[:n]
    lines: list[str] = []
    for (a, b), count in ranked:
        lines.append(f"  {a} + {b}: {count} session{'s' if count != 1 else ''}")

    return "\n".join(lines)


def generate_heatmap_app(heatmap: dict[str, SkillUsage]) -> dict[str, Any]:
    """Generate MCP App format with cards for dashboard display."""
    ranked = top_skills(heatmap, 10)
    cards: list[dict[str, Any]] = []

    for usage in ranked:
        cards.append({
            "type": "metric",
            "title": usage.skill_name,
            "value": usage.count,
            "subtitle": f"{len(usage.sessions)} session{'s' if len(usage.sessions) != 1 else ''}",
            "last_used": usage.last_used.isoformat() if usage.last_used else None,
        })

    return {
        "app": "skill-heatmap",
        "version": "1.0.0",
        "title": "Skill Usage Heatmap",
        "cards": cards,
        "total_skills": len(heatmap),
        "total_invocations": sum(u.count for u in heatmap.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP Skill Usage Heatmap")
    parser.add_argument("--hours", action="store_true", help="Show hour-of-day distribution")
    parser.add_argument("--pairs", action="store_true", help="Show co-occurrence pairs")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--all-tools", action="store_true", help="Track all tools, not just Skill")
    parser.add_argument("--limit", type=int, default=20, help="Max session files to scan")
    parser.add_argument("--top", type=int, default=10, help="Number of top skills to show")
    parser.add_argument("--project-dir", type=str, default=None, help="Project directory to scan")
    args = parser.parse_args()

    invocations = scan_sessions(
        project_dir=args.project_dir,
        limit=args.limit,
        all_tools=args.all_tools,
    )

    if args.json:
        hmap = build_heatmap(invocations)
        output: dict[str, Any] = {
            "heatmap": {k: v.to_dict() for k, v in hmap.items()},
            "top": [u.to_dict() for u in top_skills(hmap, args.top)],
            "hour_distribution": hour_distribution(invocations),
            "co_occurrence": {
                f"{a}+{b}": c for (a, b), c in co_occurrence(invocations).items()
            },
        }
        json.dump(output, sys.stdout, indent=2, default=str)
        print()
    elif args.hours:
        dist = hour_distribution(invocations)
        print("Hour-of-Day Distribution")
        print("=" * 50)
        print(format_hours(dist))
    elif args.pairs:
        pairs = co_occurrence(invocations)
        print("Skill Co-Occurrence (same session)")
        print("=" * 50)
        print(format_pairs(pairs, args.top))
    else:
        hmap = build_heatmap(invocations)
        label = "Tool" if args.all_tools else "Skill"
        print(f"Top {args.top} {label}s by Usage")
        print("=" * 50)
        print(format_heatmap(hmap, args.top))
        total = sum(u.count for u in hmap.values())
        print(f"\n  Total: {total} invocations across {len(hmap)} {label.lower()}s")


if __name__ == "__main__":
    main()
