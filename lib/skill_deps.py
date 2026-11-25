"""Skill dependency mapper — analyses GRIP skill interconnections.

Scans all SKILL.md files in the skills/ directory, extracts references
to other skills, modes, and libraries, then builds a dependency graph
with analysis utilities (orphans, hubs, circular refs, chains).

Usage:
    from lib.skill_deps import SkillDependencyGraph
    graph = SkillDependencyGraph()
    graph.scan(Path("skills"))
    print(graph.format_graph_report())
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass
class SkillNode:
    """A single skill and its dependency metadata."""

    name: str
    path: Path
    references_skills: list[str] = field(default_factory=list)
    references_modes: list[str] = field(default_factory=list)
    references_libs: list[str] = field(default_factory=list)
    referenced_by: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference extraction patterns
# ---------------------------------------------------------------------------

# YAML frontmatter see_also / integrates list items (indented "- value")
_YAML_LIST_ITEM = re.compile(r"^\s+-\s+(.+)$", re.MULTILINE)

# Slash-prefixed skill references like /converge, /mode, /save
_SLASH_CMD = re.compile(r"(?<!\w)/([a-z][a-z0-9_-]+)")

# Explicit skills/<name> path references
_SKILLS_PATH = re.compile(r"skills/([a-z][a-z0-9_-]+)")

# Library references like lib/<name>.py or lib/<name>.sh
_LIB_REF = re.compile(r"lib/([a-z][a-z0-9_]+\.(?:py|sh))")

# /mode <name> invocations
_MODE_REF = re.compile(r"/mode\s+([a-z][a-z0-9_-]+)")

# YAML section boundary
_YAML_FENCE = re.compile(r"^---\s*$", re.MULTILINE)


def _extract_yaml_block(content: str) -> str | None:
    """Return the YAML frontmatter block (between first two --- lines)."""
    fences = list(_YAML_FENCE.finditer(content))
    if len(fences) >= 2:
        return content[fences[0].end() : fences[1].start()]
    return None


def _extract_yaml_list(yaml_block: str, key: str) -> list[str]:
    """Extract list values under a YAML key (simple indent-based parse)."""
    results: list[str] = []
    in_key = False
    for line in yaml_block.splitlines():
        stripped = line.strip()
        # Check for our key
        if stripped.startswith(f"{key}:"):
            in_key = True
            continue
        # If we're inside the list, collect items
        if in_key:
            m = _YAML_LIST_ITEM.match(line)
            if m:
                results.append(m.group(1).strip())
            elif stripped and not stripped.startswith("-"):
                # Hit a new key — stop
                break
    return results


def _extract_references(content: str, known_skills: set[str]) -> tuple[
    list[str], list[str], list[str]
]:
    """Extract skill, mode, and lib references from SKILL.md content.

    Returns (skills, modes, libs) where each is a deduplicated list.
    """
    skills: set[str] = set()
    modes: set[str] = set()
    libs: set[str] = set()

    # --- YAML frontmatter structured references ---
    yaml_block = _extract_yaml_block(content)
    if yaml_block:
        for item in _extract_yaml_list(yaml_block, "see_also"):
            skills.add(item)
        for item in _extract_yaml_list(yaml_block, "integrates"):
            # integrates can reference lib files or other skill docs
            if _LIB_REF.search(item) or item.endswith((".py", ".sh")):
                libs.add(item if "/" in item else item)
            elif "/SKILL.md" in item:
                # e.g. converge/SKILL.md -> converge
                skill_name = item.split("/")[0]
                skills.add(skill_name)

    # --- Body text references ---
    body = content
    if yaml_block:
        # Use content after the frontmatter
        second_fence = list(_YAML_FENCE.finditer(content))
        if len(second_fence) >= 2:
            body = content[second_fence[1].end() :]

    # skills/<name> path references
    for m in _SKILLS_PATH.finditer(body):
        candidate = m.group(1)
        if candidate in known_skills:
            skills.add(candidate)

    # lib/<name>.py references
    for m in _LIB_REF.finditer(body):
        libs.add(m.group(1))

    # /mode <name> references
    for m in _MODE_REF.finditer(content):
        modes.add(m.group(1))

    # Slash commands that match known skills (body only, to avoid trigger noise)
    for m in _SLASH_CMD.finditer(body):
        candidate = m.group(1)
        if candidate in known_skills and candidate not in ("mode",):
            skills.add(candidate)

    return sorted(skills), sorted(modes), sorted(libs)


class SkillDependencyGraph:
    """Dependency graph for GRIP skills."""

    def __init__(self) -> None:
        self._nodes: dict[str, SkillNode] = {}
        self._scanned = False

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(self, skills_dir: Path) -> None:
        """Scan all SKILL.md files and build the dependency graph."""
        skills_dir = Path(skills_dir)
        if not skills_dir.is_dir():
            raise FileNotFoundError(f"Skills directory not found: {skills_dir}")

        # Phase 1: discover all skill names
        known_skills: set[str] = set()
        skill_paths: dict[str, Path] = {}
        for skill_md in self._iter_skill_files(skills_dir):
            name = skill_md.parent.name
            known_skills.add(name)
            skill_paths[name] = skill_md

        # Phase 2: extract references
        for name, path in sorted(skill_paths.items()):
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                # Graceful handling of unreadable files
                content = ""

            ref_skills, ref_modes, ref_libs = _extract_references(
                content, known_skills
            )

            # Remove self-references
            ref_skills = [s for s in ref_skills if s != name]

            self._nodes[name] = SkillNode(
                name=name,
                path=path,
                references_skills=ref_skills,
                references_modes=ref_modes,
                references_libs=ref_libs,
            )

        # Phase 3: build reverse references
        for name, node in self._nodes.items():
            for ref in node.references_skills:
                if ref in self._nodes:
                    self._nodes[ref].referenced_by.append(name)

        # Sort referenced_by for determinism
        for node in self._nodes.values():
            node.referenced_by.sort()

        self._scanned = True

    @staticmethod
    def _iter_skill_files(skills_dir: Path) -> Iterator[Path]:
        """Yield all SKILL.md files, skipping archived skills."""
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_"):
                continue
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                yield skill_md

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_node(self, name: str) -> SkillNode | None:
        """Return a skill node by name, or None if not found."""
        return self._nodes.get(name)

    @property
    def nodes(self) -> dict[str, SkillNode]:
        """All scanned nodes."""
        return dict(self._nodes)

    @property
    def total_edges(self) -> int:
        """Total number of skill-to-skill edges."""
        return sum(len(n.references_skills) for n in self._nodes.values())

    def orphans(self) -> list[str]:
        """Skills that no other skill references (pruning candidates)."""
        return sorted(
            name
            for name, node in self._nodes.items()
            if not node.referenced_by and not node.references_skills
        )

    def unreferenced(self) -> list[str]:
        """Skills that no other skill references (but may reference others)."""
        return sorted(
            name for name, node in self._nodes.items() if not node.referenced_by
        )

    def hubs(self) -> list[tuple[str, int]]:
        """Most-referenced skills, sorted by reference count descending."""
        counts = [
            (name, len(node.referenced_by))
            for name, node in self._nodes.items()
            if node.referenced_by
        ]
        return sorted(counts, key=lambda x: (-x[1], x[0]))

    def circular_refs(self) -> list[tuple[str, str]]:
        """Find pairs of skills that reference each other (A->B and B->A)."""
        pairs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for name, node in self._nodes.items():
            for ref in node.references_skills:
                ref_node = self._nodes.get(ref)
                if ref_node and name in ref_node.references_skills:
                    pair = tuple(sorted([name, ref]))
                    if pair not in seen:
                        seen.add(pair)
                        pairs.append(pair)
        return sorted(pairs)

    def dependency_chains(self, max_depth: int = 5) -> list[list[str]]:
        """Find longest dependency chains via DFS (acyclic paths only).

        Returns up to 10 longest chains, sorted by length descending.
        """
        all_chains: list[list[str]] = []

        def _dfs(node_name: str, path: list[str], visited: set[str]) -> None:
            if len(path) > max_depth:
                return
            refs = self._nodes[node_name].references_skills
            extended = False
            for ref in refs:
                if ref in self._nodes and ref not in visited:
                    extended = True
                    visited.add(ref)
                    path.append(ref)
                    _dfs(ref, path, visited)
                    path.pop()
                    visited.discard(ref)
            if not extended and len(path) > 1:
                all_chains.append(list(path))

        for name in self._nodes:
            _dfs(name, [name], {name})

        # Return top 10 longest chains
        all_chains.sort(key=lambda c: (-len(c), c[0]))
        return all_chains[:10]

    # ------------------------------------------------------------------
    # Formatters
    # ------------------------------------------------------------------

    def format_graph_report(self) -> str:
        """Generate an ASCII report of the dependency graph."""
        lines: list[str] = []
        total = len(self._nodes)
        edges = self.total_edges
        orphan_list = self.orphans()
        hub_list = self.hubs()
        circulars = self.circular_refs()
        chains = self.dependency_chains()

        lines.append("GRIP Skill Dependency Report")
        lines.append("=" * 40)
        lines.append("")
        lines.append(f"Total skills: {total}")
        lines.append(f"Total edges:  {edges}")
        lines.append(f"Orphans:      {len(orphan_list)}")
        lines.append(f"Circular:     {len(circulars)}")
        lines.append("")

        # Hub skills bar chart (top 10)
        lines.append("Top Hub Skills (most referenced)")
        lines.append("-" * 40)
        if hub_list:
            top_hubs = hub_list[:10]
            max_count = top_hubs[0][1] if top_hubs else 1
            bar_width = 30
            for name, count in top_hubs:
                bar_len = max(1, int((count / max_count) * bar_width))
                bar = "#" * bar_len
                lines.append(f"  {name:<30} {bar} ({count})")
        else:
            lines.append("  (none)")
        lines.append("")

        # Orphan skills
        lines.append(f"Orphan Skills ({len(orphan_list)} — pruning candidates)")
        lines.append("-" * 40)
        if orphan_list:
            for name in orphan_list:
                lines.append(f"  - {name}")
        else:
            lines.append("  (none)")
        lines.append("")

        # Circular references
        lines.append(f"Circular References ({len(circulars)})")
        lines.append("-" * 40)
        if circulars:
            for a, b in circulars:
                lines.append(f"  {a} <-> {b}")
        else:
            lines.append("  (none)")
        lines.append("")

        # Dependency chains
        lines.append(f"Longest Dependency Chains (top {min(len(chains), 5)})")
        lines.append("-" * 40)
        if chains:
            for chain in chains[:5]:
                lines.append(f"  [{len(chain)}] {' -> '.join(chain)}")
        else:
            lines.append("  (none)")

        return "\n".join(lines)

    def to_dot(self) -> str:
        """Generate Graphviz DOT format for external visualisation."""
        lines: list[str] = []
        lines.append("digraph grip_skills {")
        lines.append('  rankdir=LR;')
        lines.append('  node [shape=box, style=rounded, fontsize=10];')
        lines.append("")

        # Highlight hub nodes
        hub_names = {h[0] for h in self.hubs()[:10]}
        for name in sorted(hub_names):
            lines.append(f'  "{name}" [style="rounded,filled", fillcolor="#ffddaa"];')
        lines.append("")

        # Highlight orphan nodes
        orphan_names = set(self.orphans())
        for name in sorted(orphan_names):
            lines.append(f'  "{name}" [style="rounded,filled", fillcolor="#dddddd"];')
        lines.append("")

        # Edges
        for name, node in sorted(self._nodes.items()):
            for ref in node.references_skills:
                if ref in self._nodes:
                    lines.append(f'  "{name}" -> "{ref}";')

        lines.append("}")
        return "\n".join(lines)

    def format_compact(self) -> str:
        """Single-line summary for statusline integration."""
        total = len(self._nodes)
        edges = self.total_edges
        orphan_count = len(self.orphans())
        top_hubs = self.hubs()[:3]
        hub_names = ",".join(h[0] for h in top_hubs) if top_hubs else "none"
        return f"skills:{total} edges:{edges} orphans:{orphan_count} hubs:{hub_names}"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the skill dependency mapper from the command line."""
    claude_dir = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
    skills_dir = claude_dir / "skills"

    import sys

    graph = SkillDependencyGraph()
    try:
        graph.scan(skills_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if "--compact" in sys.argv:
        print(graph.format_compact())
    elif "--dot" in sys.argv:
        print(graph.to_dot())
    else:
        print(graph.format_graph_report())


if __name__ == "__main__":
    main()
