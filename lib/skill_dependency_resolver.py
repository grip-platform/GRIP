#!/usr/bin/env python3
"""Skill Dependency Resolver -- parse SKILL.md @-references and analyze dependency health.

Builds a directed graph of skill -> dependency relationships from @rules/, @skills/,
@lib/, @docs/ references found in SKILL.md files. Detects missing, circular, and
orphan dependencies. Provides impact analysis for safe skill removal.

Usage:
    python3 lib/skill_dependency_resolver.py              # Full health report
    python3 lib/skill_dependency_resolver.py --skill X    # Single skill analysis
    python3 lib/skill_dependency_resolver.py --orphans    # List orphan skills
    python3 lib/skill_dependency_resolver.py --missing    # List missing dependencies
    python3 lib/skill_dependency_resolver.py --impact X   # Reverse dependencies
    python3 lib/skill_dependency_resolver.py --json       # JSON output
    python3 lib/skill_dependency_resolver.py --format flat # flat list output
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Reference parsing
# ---------------------------------------------------------------------------

# Matches @rules/foo.md, @skills/bar, @lib/baz.py, @docs/qux.md
# Also matches backtick-wrapped: `@rules/foo.md`
AT_REF_RE = re.compile(r"`?@(rules|skills|lib|docs)/([^\s`\)>\]]+)`?")

# Frontmatter see_also / integrates lists (YAML-style, one item per line)
FRONTMATTER_LIST_RE = re.compile(r"^\s*-\s+(.+)$", re.MULTILINE)
FRONTMATTER_SECTION_RE = re.compile(
    r"^(see_also|integrates):\s*\n((?:\s+-\s+.+\n?)+)", re.MULTILINE
)


def parse_at_references(content: str) -> list[dict[str, str]]:
    """Extract all @-prefixed references from SKILL.md content.

    Returns list of dicts: {"type": "rules"|"skills"|..., "path": "relative/path"}
    """
    refs = []
    seen = set()
    for match in AT_REF_RE.finditer(content):
        ref_type = match.group(1)
        ref_path = match.group(2).rstrip(".,;:)")
        key = (ref_type, ref_path)
        if key not in seen:
            seen.add(key)
            refs.append({"type": ref_type, "path": ref_path})
    return refs


def parse_frontmatter_refs(content: str) -> list[dict[str, str]]:
    """Extract see_also and integrates references from YAML frontmatter.

    see_also entries map to skills/, integrates entries map to lib/.
    """
    refs = []
    seen = set()
    for section_match in FRONTMATTER_SECTION_RE.finditer(content):
        section_name = section_match.group(1)
        block = section_match.group(2)
        for item_match in FRONTMATTER_LIST_RE.finditer(block):
            value = item_match.group(1).strip()
            if not value:
                continue
            if section_name == "see_also":
                ref_type = "skills"
                ref_path = value
            else:  # integrates
                # If the value has a file extension, treat as lib/; otherwise
                # it is a skill name (e.g., "riper-workflow" vs "converge.py")
                if "." in value.split("/")[-1]:
                    ref_type = "lib"
                else:
                    ref_type = "skills"
                ref_path = value
            key = (ref_type, ref_path)
            if key not in seen:
                seen.add(key)
                refs.append({"type": ref_type, "path": ref_path})
    return refs


def parse_all_references(content: str) -> list[dict[str, str]]:
    """Parse both @-references and frontmatter references, deduplicated."""
    seen = set()
    result = []
    for ref in parse_at_references(content) + parse_frontmatter_refs(content):
        key = (ref["type"], ref["path"])
        if key not in seen:
            seen.add(key)
            result.append(ref)
    return result


# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------


def discover_skills(grip_root: str) -> dict[str, dict[str, Any]]:
    """Walk skills/ directory and parse each SKILL.md.

    Returns {skill_name: {"path": str, "refs": list, "has_skillmd": bool, "content": str}}
    """
    skills_dir = os.path.join(grip_root, "skills")
    skills = {}

    if not os.path.isdir(skills_dir):
        return skills

    for entry in sorted(os.listdir(skills_dir)):
        entry_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry.startswith("_") or entry.startswith("."):
            continue

        skill_md = os.path.join(entry_path, "SKILL.md")
        if os.path.isfile(skill_md):
            try:
                content = Path(skill_md).read_text(encoding="utf-8", errors="replace")
                refs = parse_all_references(content)
            except Exception:
                content = ""
                refs = []
            skills[entry] = {
                "path": skill_md,
                "refs": refs,
                "has_skillmd": True,
                "content": content,
            }
        else:
            skills[entry] = {
                "path": entry_path,
                "refs": [],
                "has_skillmd": False,
                "content": "",
            }

    return skills


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------


class DependencyGraph:
    """Directed graph of skill dependencies with analysis methods."""

    def __init__(self, grip_root: str):
        self.grip_root = grip_root
        self.skills = discover_skills(grip_root)
        # Forward edges: skill -> set of (type, path) dependencies
        self.edges: dict[str, list[dict[str, str]]] = {}
        # Reverse edges: target -> set of source skills
        self.reverse: dict[str, set[str]] = defaultdict(set)
        self._build()

    def _build(self) -> None:
        """Build forward and reverse edge maps."""
        for name, info in self.skills.items():
            self.edges[name] = info["refs"]
            for ref in info["refs"]:
                target = self._ref_to_key(ref)
                self.reverse[target].add(name)

    @staticmethod
    def _ref_to_key(ref: dict[str, str]) -> str:
        """Convert a reference dict to a canonical key string."""
        return f"{ref['type']}/{ref['path']}"

    def _resolve_path(self, ref: dict[str, str]) -> str:
        """Resolve a reference to an absolute filesystem path."""
        return os.path.join(self.grip_root, ref["type"], ref["path"])

    # -- Analysis methods --

    def missing_dependencies(self) -> list[dict[str, Any]]:
        """Find references that point to nonexistent files/directories."""
        missing = []
        seen = set()
        for skill_name, refs in self.edges.items():
            for ref in refs:
                key = self._ref_to_key(ref)
                if key in seen:
                    continue
                resolved = self._resolve_path(ref)
                # For skills references, check directory or directory/SKILL.md
                if ref["type"] == "skills":
                    skill_dir = os.path.join(self.grip_root, "skills", ref["path"])
                    if not os.path.isdir(skill_dir):
                        seen.add(key)
                        missing.append({
                            "reference": key,
                            "referenced_by": skill_name,
                            "expected_path": skill_dir,
                        })
                # For lib references, check with and without extension
                elif ref["type"] == "lib":
                    lib_path = os.path.join(self.grip_root, "lib", ref["path"])
                    if not os.path.isfile(lib_path):
                        # Try adding .py extension
                        if not os.path.isfile(lib_path + ".py"):
                            seen.add(key)
                            missing.append({
                                "reference": key,
                                "referenced_by": skill_name,
                                "expected_path": lib_path,
                            })
                else:
                    if not os.path.exists(resolved):
                        seen.add(key)
                        missing.append({
                            "reference": key,
                            "referenced_by": skill_name,
                            "expected_path": resolved,
                        })
        return missing

    def find_cycles(self) -> list[list[str]]:
        """Detect circular dependency chains using DFS with back-edge detection.

        Only considers skill->skill edges (see_also references).
        Returns list of cycles, each cycle is a list of skill names forming the loop.
        """
        skill_deps: dict[str, set[str]] = defaultdict(set)
        skill_names = set(self.skills.keys())

        for name, refs in self.edges.items():
            for ref in refs:
                if ref["type"] == "skills" and ref["path"] in skill_names:
                    skill_deps[name].add(ref["path"])

        cycles = []
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {name: WHITE for name in skill_names}
        parent: dict[str, str | None] = {name: None for name in skill_names}
        path: list[str] = []

        def dfs(node: str) -> None:
            color[node] = GRAY
            path.append(node)
            for dep in sorted(skill_deps.get(node, set())):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    # Found cycle -- extract it
                    cycle_start = path.index(dep)
                    cycle = path[cycle_start:] + [dep]
                    cycles.append(cycle)
                elif color[dep] == WHITE:
                    parent[dep] = node
                    dfs(dep)
            path.pop()
            color[node] = BLACK

        for node in sorted(skill_names):
            if color[node] == WHITE:
                dfs(node)

        return cycles

    def orphan_skills(self) -> list[str]:
        """Skills that no other skill references (potential cleanup candidates).

        A skill is orphan if it never appears in any other skill's see_also,
        integrates, or @skills/ references.
        """
        referenced_skills = set()
        for name, refs in self.edges.items():
            for ref in refs:
                if ref["type"] == "skills":
                    referenced_skills.add(ref["path"])

        orphans = []
        for name in sorted(self.skills.keys()):
            if name not in referenced_skills:
                orphans.append(name)
        return orphans

    def impact_analysis(self, skill_name: str) -> dict[str, Any]:
        """Given a skill name, show what would break if removed.

        Checks both direct reverse deps (who references this skill)
        and transitive impact (who references those, etc.).
        """
        if skill_name not in self.skills:
            return {"error": f"Skill '{skill_name}' not found", "dependents": []}

        direct_key = f"skills/{skill_name}"
        direct_deps = sorted(self.reverse.get(direct_key, set()))

        # BFS for transitive impact
        visited = set()
        queue = list(direct_deps)
        transitive = []
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            transitive.append(current)
            # Who depends on current?
            current_key = f"skills/{current}"
            for dep in self.reverse.get(current_key, set()):
                if dep not in visited:
                    queue.append(dep)

        return {
            "skill": skill_name,
            "direct_dependents": direct_deps,
            "transitive_dependents": sorted(transitive),
            "total_impact": len(transitive),
        }

    def dependency_depth(self, skill_name: str, _stack: set[str] | None = None) -> int:
        """Compute the maximum dependency depth for a skill (skill->skill chains only).

        Uses memoization via _depth_cache to avoid exponential recomputation
        on DAGs with shared sub-graphs (e.g., diamond patterns).
        """
        # Return cached result if available
        if not hasattr(self, "_depth_cache"):
            self._depth_cache: dict[str, int] = {}
        if skill_name in self._depth_cache:
            return self._depth_cache[skill_name]

        if _stack is None:
            _stack = set()

        if skill_name in _stack:
            return 0  # Cycle guard

        _stack.add(skill_name)
        max_depth = 0

        for ref in self.edges.get(skill_name, []):
            if ref["type"] == "skills" and ref["path"] in self.skills:
                child_depth = self.dependency_depth(ref["path"], _stack)
                max_depth = max(max_depth, 1 + child_depth)

        _stack.discard(skill_name)
        self._depth_cache[skill_name] = max_depth
        return max_depth

    def skill_analysis(self, skill_name: str) -> dict[str, Any]:
        """Full analysis for a single skill."""
        if skill_name not in self.skills:
            return {"error": f"Skill '{skill_name}' not found"}

        info = self.skills[skill_name]
        impact = self.impact_analysis(skill_name)
        depth = self.dependency_depth(skill_name)

        return {
            "name": skill_name,
            "has_skillmd": info["has_skillmd"],
            "path": info["path"],
            "references": info["refs"],
            "reference_count": len(info["refs"]),
            "dependency_depth": depth,
            "direct_dependents": impact["direct_dependents"],
            "transitive_dependents": impact["transitive_dependents"],
            "total_impact": impact["total_impact"],
        }

    def health_report(self) -> dict[str, Any]:
        """Overall dependency health score with issues listed."""
        missing = self.missing_dependencies()
        cycles = self.find_cycles()
        orphans = self.orphan_skills()

        total_skills = len(self.skills)
        skills_with_refs = sum(1 for s in self.skills.values() if s["refs"])
        skills_without_md = sum(1 for s in self.skills.values() if not s["has_skillmd"])
        total_refs = sum(len(s["refs"]) for s in self.skills.values())

        # Depth stats
        depths = {
            name: self.dependency_depth(name) for name in self.skills
        }
        max_depth_skill = max(depths, key=depths.get) if depths else ""
        max_depth = depths.get(max_depth_skill, 0)

        # Health score: start at 100, deduct for issues
        score = 100.0
        score -= len(missing) * 2.0        # -2 per missing dep
        score -= len(cycles) * 10.0         # -10 per cycle
        score -= skills_without_md * 1.0    # -1 per missing SKILL.md
        # Orphan penalty is softer -- many skills are legitimately standalone
        score -= max(0, len(orphans) - total_skills * 0.3) * 0.5
        score = max(0.0, min(100.0, score))

        return {
            "score": round(score, 1),
            "total_skills": total_skills,
            "skills_with_references": skills_with_refs,
            "skills_without_skillmd": skills_without_md,
            "total_references": total_refs,
            "missing_dependencies": missing,
            "missing_count": len(missing),
            "circular_dependencies": cycles,
            "circular_count": len(cycles),
            "orphan_skills": orphans,
            "orphan_count": len(orphans),
            "max_dependency_depth": max_depth,
            "deepest_skill": max_depth_skill,
            "depth_distribution": dict(sorted(depths.items(), key=lambda x: -x[1])[:10]),
        }


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def format_tree(data: dict[str, Any], indent: int = 0) -> str:
    """Format a dict as an indented tree view."""
    lines = []
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(format_tree(value, indent + 1))
        elif isinstance(value, list):
            if not value:
                lines.append(f"{prefix}{key}: (none)")
            elif isinstance(value[0], dict):
                lines.append(f"{prefix}{key}:")
                for i, item in enumerate(value):
                    lines.append(f"{prefix}  [{i}]")
                    lines.append(format_tree(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}:")
                for item in value:
                    lines.append(f"{prefix}  - {item}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


def format_flat(data: dict[str, Any]) -> str:
    """Format as a flat key=value list."""
    lines = []

    def flatten(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                flatten(v, f"{prefix}{k}.")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                flatten(v, f"{prefix}{i}.")
        else:
            lines.append(f"{prefix.rstrip('.')}={obj}")

    flatten(data)
    return "\n".join(lines)


def format_output(data: dict[str, Any], fmt: str) -> str:
    """Format data in the requested output format."""
    if fmt == "json":
        return json.dumps(data, indent=2, default=str)
    elif fmt == "flat":
        return format_flat(data)
    else:  # tree
        return format_tree(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Skill Dependency Resolver -- analyze @-reference dependencies"
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("GRIP_ROOT", os.path.join(os.path.expanduser("~"), ".claude")),
        help="GRIP root directory (default: $GRIP_ROOT or ~/.claude)",
    )
    parser.add_argument(
        "--skill",
        help="Analyze a single skill by name",
    )
    parser.add_argument(
        "--orphans",
        action="store_true",
        help="List orphan skills (nothing references them)",
    )
    parser.add_argument(
        "--missing",
        action="store_true",
        help="List missing dependencies",
    )
    parser.add_argument(
        "--impact",
        help="Show what depends on a given skill",
    )
    parser.add_argument(
        "--cycles",
        action="store_true",
        help="List circular dependency chains",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format",
    )
    parser.add_argument(
        "--format",
        choices=["tree", "json", "flat"],
        default="tree",
        help="Output format (default: tree)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --json flag overrides --format
    output_fmt = "json" if args.json else args.format

    graph = DependencyGraph(args.root)

    if args.skill:
        data = graph.skill_analysis(args.skill)
    elif args.orphans:
        orphans = graph.orphan_skills()
        data = {"orphan_skills": orphans, "count": len(orphans)}
    elif args.missing:
        missing = graph.missing_dependencies()
        data = {"missing_dependencies": missing, "count": len(missing)}
    elif args.impact:
        data = graph.impact_analysis(args.impact)
    elif args.cycles:
        cycles = graph.find_cycles()
        data = {"circular_dependencies": cycles, "count": len(cycles)}
    else:
        data = graph.health_report()

    print(format_output(data, output_fmt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
