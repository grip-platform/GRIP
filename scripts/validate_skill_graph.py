#!/usr/bin/env python3
"""
GRIP skill graph validator.

Scans all `skills/<name>/SKILL.md` files, extracts cross-references between
skills, and reports three classes of structural problem:

1. Broken references — a skill mentions another skill that does not exist
2. Circular dependencies — A → B → C → A chains (cycles in the ref graph)
3. Orphans — skills that nothing else references (warn-only)

Usage:
    python scripts/validate_skill_graph.py [--json] [--strict]

Exit codes:
    0  No problems (or only warn-only orphans)
    1  Broken refs or cycles found AND --strict was passed
    2  Bad invocation / missing skills directory

Reference patterns recognised:
    skills/<name>/SKILL.md          # canonical relative path
    `skills/<name>/SKILL.md`        # markdown-fenced canonical path
    /<name>                         # slash-command form (skill or alias)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR") or Path.home() / ".claude")
SKILLS_DIR = CLAUDE_DIR / "skills"

SKILL_PATH_RE = re.compile(r"skills/([a-z][a-z0-9-]*)/SKILL\.md")
SLASH_RE = re.compile(r"(?<![A-Za-z0-9_./-])/([a-z][a-z0-9-]{1,})\b")


def list_skills() -> dict[str, Path]:
    """Return {skill_name: SKILL.md path} for every skill folder."""
    out: dict[str, Path] = {}
    if not SKILLS_DIR.is_dir():
        return out
    for child in sorted(SKILLS_DIR.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            out[child.name] = skill_md
    return out


def _extract_path_targets(text: str) -> set[str]:
    """All `skills/<name>/SKILL.md` targets, regardless of existence."""
    return set(SKILL_PATH_RE.findall(text))


def _extract_slash_targets(text: str, known: set[str]) -> set[str]:
    """`/<name>` slash-command targets, filtered to known skills."""
    return set(SLASH_RE.findall(text)) & known


def scan_all(skills: dict[str, Path]) -> dict[str, dict[str, set[str]]]:
    """Single pass: read each SKILL.md once, extract both ref kinds.

    Returns {skill_name: {"path_targets": {...}, "slash_targets": {...}}}.
    Downstream consumers derive graph / broken-refs / cycles / orphans from
    this single source — no file is read twice.
    """
    known = set(skills.keys())
    by_skill: dict[str, dict[str, set[str]]] = {}
    for name, md_path in skills.items():
        text = md_path.read_text(encoding="utf-8", errors="replace")
        by_skill[name] = {
            "path_targets": _extract_path_targets(text),
            "slash_targets": _extract_slash_targets(text, known),
        }
    return by_skill


def graph_from_scan(scan: dict[str, dict[str, set[str]]],
                    known: set[str]) -> dict[str, set[str]]:
    """Adjacency list of edges that point to existing skills.

    Self-edges removed. Path targets are filtered through `known` here too,
    so the graph contains only valid edges; broken refs surface separately.
    """
    graph: dict[str, set[str]] = {}
    for name, refs in scan.items():
        valid = (refs["path_targets"] & known) | refs["slash_targets"]
        valid.discard(name)
        graph[name] = valid
    return graph


def _broken_for_skill(name: str, refs: dict[str, set[str]],
                      known: set[str]) -> list[dict[str, str]]:
    """All broken-ref records produced by a single source skill."""
    unknown = sorted(refs["path_targets"] - known - {name})
    return [{"from": name, "to": t, "form": "skills/path"} for t in unknown]


def broken_refs_from_scan(scan: dict[str, dict[str, set[str]]],
                          known: set[str]) -> list[dict[str, str]]:
    """Path-form refs whose target name is not a real skill folder."""
    broken: list[dict[str, str]] = []
    for name, refs in scan.items():
        broken.extend(_broken_for_skill(name, refs, known))
    return broken


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """Return every simple cycle (A → ... → A) using DFS.

    Each cycle is reported as the path from the start node back to itself.
    Duplicate cycles (rotations of the same loop) are deduped via canonical
    sorted-tuple hashing of node membership.
    """
    cycles: list[list[str]] = []
    seen_keys: set[tuple[str, ...]] = set()

    def dfs(start: str, node: str, path: list[str], visiting: set[str]) -> None:
        for neighbour in sorted(graph.get(node, ())):
            if neighbour == start and len(path) >= 1:
                cycle = path + [start]
                key = tuple(sorted(set(cycle)))
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(cycle)
                continue
            if neighbour in visiting:
                continue
            visiting.add(neighbour)
            dfs(start, neighbour, path + [neighbour], visiting)
            visiting.remove(neighbour)

    for start in sorted(graph.keys()):
        dfs(start, start, [start], {start})
    return cycles


def find_orphans(graph: dict[str, set[str]]) -> list[str]:
    """Skills with zero inbound refs. Warn-only — many skills are entry points."""
    targets = set().union(*graph.values()) if graph else set()
    return sorted(graph.keys() - targets)


def render_human(report: dict[str, object]) -> str:
    lines: list[str] = []
    broken = report["broken_refs"]
    cycles = report["circular_deps"]
    orphans = report["orphans"]
    counts = report["counts"]
    lines.append(f"Skill graph: {counts['skills']} skills, {counts['edges']} edges")
    lines.append(f"  broken refs : {len(broken)}")
    lines.append(f"  cycles      : {len(cycles)}")
    lines.append(f"  orphans     : {len(orphans)} (warn-only)")
    if broken:
        lines.append("")
        lines.append("Broken refs (reference a non-existent skill):")
        lines.extend(
            f"  {entry['from']} → {entry['to']}  [{entry['form']}]"
            for entry in broken[:20]
        )
        if len(broken) > 20:
            lines.append(f"  ... and {len(broken) - 20} more")
    if cycles:
        lines.append("")
        lines.append("Cycles:")
        lines.extend(f"  {' → '.join(cycle)}" for cycle in cycles[:10])
        if len(cycles) > 10:
            lines.append(f"  ... and {len(cycles) - 10} more")
    return "\n".join(lines)


def validate(skills: dict[str, Path]) -> dict[str, object]:
    known = set(skills.keys())
    scan = scan_all(skills)
    graph = graph_from_scan(scan, known)
    return {
        "broken_refs": broken_refs_from_scan(scan, known),
        "circular_deps": find_cycles(graph),
        "orphans": find_orphans(graph),
        "counts": {
            "skills": len(skills),
            "edges": sum(len(v) for v in graph.values()),
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--json", action="store_true", help="emit JSON, not human report")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any broken refs or cycles found")
    args = parser.parse_args(list(argv) if argv is not None else None)

    skills = list_skills()
    if not skills:
        print(f"error: no skills found under {SKILLS_DIR}", file=sys.stderr)
        return 2

    report = validate(skills)
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render_human(report))

    if args.strict and (report["broken_refs"] or report["circular_deps"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
