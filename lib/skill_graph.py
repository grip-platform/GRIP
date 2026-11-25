#!/usr/bin/env python3
"""Skill dependency graph -- map GRIP skill interconnections.

Analyzes SKILL.md files for cross-references and shared patterns to build
a relationship graph between skills.
"""

import argparse
import os
import re
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Fenced-block stripper
# ---------------------------------------------------------------------------


def _strip_fenced_blocks(text: str) -> str:
    """Remove all fenced code-block content from text. Returns prose only.

    Handles nested/mismatched fences gracefully by toggling on each ```.
    CSS at-rules, npm scoped packages, and shell commands inside fenced
    blocks are excluded from reference matching once stripped.
    """
    out: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _strip_code_spans(text: str) -> str:
    """Remove inline `code` spans (single-backtick, single-line) from text.

    Strips table cells and prose-embedded code like `skills/private-.*` so
    those tokens never reach broken-ref pattern matching. Backtick-wrapped
    skill names (e.g. `code-mode`) used as dependency hints are also
    stripped — callers that need backtick refs (BACKTICK_SKILL_PATTERN)
    should match BEFORE calling this function.
    """
    return re.sub(r"`[^`\n]+`", "", text)


# Valid skill name: starts lowercase, alphanumeric/hyphen, 2-60 chars.
_VALID_SKILL_NAME = re.compile(r"^[a-z][a-z0-9-]{1,60}$")


def _extract_see_also_items(content: str) -> list[str]:
    """Parse YAML frontmatter see_also field, returning valid skill names only.

    Handles both list form (`see_also:\\n  - foo\\n  - bar`) and scalar form
    (`see_also: foo`). Items that look like file paths (contain `/`, `.`,
    or whitespace) are skipped — those are not skill references.

    Returns:
        List of skill name strings, in YAML order.
    """
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        return []
    fm = fm_match.group(1)

    raw_items: list[str] = []
    list_block = re.search(
        r"^see_also:\s*\n((?:[ \t]+-[^\n]*\n?)*)", fm, re.MULTILINE
    )
    if list_block:
        # Capture each list item's value (everything after `-` and whitespace,
        # up to comment marker or newline).
        for raw in re.findall(r"[ \t]+-[ \t]*([^\n#]*)", list_block.group(1)):
            raw_items.append(raw.strip())
    else:
        scalar = re.search(r"^see_also:[ \t]*([^\n#]+)", fm, re.MULTILINE)
        if scalar:
            raw_items.append(scalar.group(1).strip())

    return [item for item in raw_items if item and _VALID_SKILL_NAME.match(item)]


def _extract_redirect_to(content: str) -> Optional[str]:
    """Parse the YAML frontmatter ``redirect_to:`` scalar.

    A redirect stub skill declares ``redirect_to: <other-skill>``. Returns
    the sanitised target skill name, or None when absent/malformed.

    #2801: a ``redirect_to`` whose target skill directory does not exist is
    a *dangling redirect* — a failure class ``validate_skill_graph`` could
    NOT previously see, because ``extract_explicit_refs`` parses only
    @skills/link/see_also and never the ``redirect_to`` key. Unvalidated,
    a dangling redirect is a silently-broken skill (the COMBINACIONES G3
    rubber-stamp #2801 exists to close).
    """
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        return None
    scalar = re.search(
        r"^redirect_to:[ \t]*([^\n#]+)", fm_match.group(1), re.MULTILINE
    )
    if not scalar:
        return None
    target = scalar.group(1).strip().strip("'\"")
    return target if target and _VALID_SKILL_NAME.match(target) else None


def _frontmatter_field(content: str, field: str) -> Optional[str]:
    """Raw scalar/inline value of a YAML frontmatter ``field:``, or None."""
    fm = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm:
        return None
    m = re.search(
        rf"^{re.escape(field)}:[ \t]*([^\n#]*)", fm.group(1), re.MULTILINE
    )
    return m.group(1).strip() if m else None


def _is_redirect_stub(content: str) -> bool:
    """#2801 (spec I2): a skill is a redirect stub iff ``status: Redirect``
    (case-insensitive) or it declares a ``redirect_to:`` target."""
    status = (_frontmatter_field(content, "status") or "").strip("'\"").lower()
    if status == "redirect":
        return True
    return _frontmatter_field(content, "redirect_to") is not None


def _has_nonempty_triggers(content: str) -> bool:
    """True iff frontmatter declares a NON-empty ``triggers:`` — inline
    list with items, a non-empty scalar, or a YAML ``- item`` block.
    ``triggers: []`` (the correctly re-homed stub) or absent ⇒ False
    (#2801 spec I3: a stub MUST carry no own triggers)."""
    fm = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm:
        return False
    body = fm.group(1)
    inline = re.search(
        r"^triggers:[ \t]*(\[[^\]]*\]|[^\n#]*)", body, re.MULTILINE
    )
    if inline:
        val = inline.group(1).strip()
        if val and val.replace(" ", "") not in ("[]",):
            return True
    return bool(
        re.search(
            r"^triggers:[ \t]*\n((?:[ \t]+-[^\n]*\n?)+)", body, re.MULTILINE
        )
    )


def _detect_double_homed(skills_dir: str) -> dict[str, int]:
    """#2801 (spec G3 §3.3): map each skill NAME appearing in >1 SKILL.md
    directory → its directory count. ``scan_skills`` is name-keyed and
    silently overwrites such collisions, so ``validate`` was blind to
    double-homing (the "single canonical dispatcher per cluster" guard)."""
    root = Path(skills_dir)
    if not root.is_dir():
        return {}
    homes: dict[str, set] = {}
    for md in root.rglob("SKILL.md"):
        rel = md.relative_to(root).parts
        # Skip non-active trees: any ancestor dir under `_` (the canonical
        # archived/meta convention, e.g. `_archived/<name>/SKILL.md`). A
        # live skill + its archived copy is NOT a double-home (spec G3 is
        # about *active* canonical dispatchers). Dedupe by resolved dir so
        # symlink traversal (grip-worktree) can never inflate the count.
        if any(p.startswith("_") for p in rel[:-1]):
            continue
        n = md.parent.name
        # Skip a nested self-named sub-copy: a SKILL.md whose skill-name
        # also appears as an ANCESTOR dir of its own path (e.g.
        # `X/X/SKILL.md`) is a packaged/duplicated sub-copy of skill `X`,
        # not an independent canonical home. Spec G3 "double-home" means
        # one name claimed by ≥2 DISTINCT cluster dispatchers — neither a
        # descendant of the other. (`rel[:-2]` = ancestor dirs above the
        # skill dir itself.)
        if n in rel[:-2]:
            continue
        if n and n != root.name:
            homes.setdefault(n, set()).add(str(md.parent.resolve()))
    return {n: len(dirs) for n, dirs in homes.items() if len(dirs) > 1}


@dataclass
class SkillNode:
    """A single skill in the graph."""

    name: str
    path: str
    references: list[str] = field(default_factory=list)
    referenced_by: list[str] = field(default_factory=list)


@dataclass
class SkillCluster:
    """A group of connected skills."""

    name: str
    members: list[str] = field(default_factory=list)
    hub: Optional[str] = None


@dataclass
class SkillGraph:
    """The full skill dependency graph."""

    nodes: dict[str, SkillNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    clusters: list[SkillCluster] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference extraction patterns
# ---------------------------------------------------------------------------

# Matches: /converge, /rsi, /mode, /learn etc (command-style refs)
COMMAND_REF_PATTERN = re.compile(r"(?<!\w)/([a-z][a-z0-9-]{1,40})(?!\w)")

# Matches: @skills/code-mode, @skills/converge-loop, skills/foo-bar
SKILL_AT_PATTERN = re.compile(r"@?skills/([a-z][a-z0-9-]{1,60})")

# Matches: `skill-name` in backticks when it looks like a skill reference
BACKTICK_SKILL_PATTERN = re.compile(r"`([a-z][a-z0-9-]{2,40})`")

# Matches: SKILL.md cross-file links like [text](../other-skill/SKILL.md)
LINK_PATTERN = re.compile(r"\]\(\.\./([a-z][a-z0-9-]{1,60})/SKILL\.md\)")


def scan_skills(skills_dir: str) -> dict[str, SkillNode]:
    """Find all SKILL.md files and extract metadata.

    Args:
        skills_dir: Path to the skills directory.

    Returns:
        Dict mapping skill name to SkillNode.
    """
    nodes: dict[str, SkillNode] = {}
    skills_path = Path(skills_dir)

    if not skills_path.is_dir():
        return nodes

    for skill_md in sorted(skills_path.rglob("SKILL.md")):
        # Skill name is the parent directory name
        skill_name = skill_md.parent.name
        if not skill_name or skill_name == skills_path.name:
            continue

        nodes[skill_name] = SkillNode(
            name=skill_name,
            path=str(skill_md),
        )

    return nodes


def _matches(pattern: re.Pattern[str], text: str) -> set[str]:
    """Return all group(1) captures from pattern matches over text."""
    return {m.group(1) for m in pattern.finditer(text)}


def _known_backtick_refs(content: str, known_skills: set[str] | None) -> set[str]:
    """Backtick `name` refs kept only when matching a known skill."""
    if not known_skills:
        return set()
    return {
        m.group(1)
        for m in BACKTICK_SKILL_PATTERN.finditer(content)
        if m.group(1) in known_skills
    }


def extract_references(
    skill_content: str, known_skills: set[str] | None = None
) -> list[str]:
    """Find mentions of other skills in SKILL.md content.

    Sources: /command, @skills/<name>, ../<name>/SKILL.md links, backticks
    (when known), and YAML frontmatter `see_also` entries.

    Fenced code blocks and inline code spans are stripped before matching,
    so CSS at-rules, npm scoped packages, and table-cell paths never match.
    """
    fence_stripped = _strip_fenced_blocks(skill_content)
    prose = _strip_code_spans(fence_stripped)
    refs: set[str] = set()
    refs |= _matches(COMMAND_REF_PATTERN, prose)
    refs |= _matches(SKILL_AT_PATTERN, prose)
    refs |= _matches(LINK_PATTERN, prose)
    refs |= _known_backtick_refs(fence_stripped, known_skills)
    refs |= set(_extract_see_also_items(skill_content))
    return sorted(refs)


def build_graph(skills: dict[str, SkillNode]) -> SkillGraph:
    """Construct adjacency list from cross-references.

    Reads each skill's SKILL.md, extracts references, and builds edges.
    Only references to known skills are included.

    Args:
        skills: Dict of skill name to SkillNode (from scan_skills).

    Returns:
        Populated SkillGraph.
    """
    known = set(skills.keys())
    graph = SkillGraph(nodes=dict(skills))
    seen_edges: set[tuple[str, str]] = set()

    for name, node in skills.items():
        try:
            content = Path(node.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        refs = extract_references(content, known_skills=known)

        # O(1): bounded by per-skill ref count, not graph size
        for ref in refs:
            if ref == name:
                continue  # Skip self-references
            if ref not in known:
                continue

            node.references.append(ref)
            graph.nodes[ref].referenced_by.append(name)

            edge = (name, ref)
            if edge not in seen_edges:
                seen_edges.add(edge)
                graph.edges.append(edge)

    return graph


def build_graph_from_data(
    nodes: dict[str, SkillNode],
    edges: list[tuple[str, str]],
) -> SkillGraph:
    """Build a graph from pre-computed nodes and edges (for testing).

    Args:
        nodes: Dict of skill name to SkillNode.
        edges: List of (source, target) tuples.

    Returns:
        Populated SkillGraph.
    """
    graph = SkillGraph(nodes=dict(nodes))
    seen: set[tuple[str, str]] = set()

    for src, dst in edges:
        if src not in graph.nodes or dst not in graph.nodes:
            continue
        if src == dst:
            continue
        if (src, dst) in seen:
            continue

        seen.add((src, dst))
        graph.edges.append((src, dst))

        if dst not in graph.nodes[src].references:
            graph.nodes[src].references.append(dst)
        if src not in graph.nodes[dst].referenced_by:
            graph.nodes[dst].referenced_by.append(src)

    return graph


def _adjacency_undirected(graph: SkillGraph) -> dict[str, set[str]]:
    """Build undirected adjacency dict from graph edges."""
    adj: dict[str, set[str]] = {name: set() for name in graph.nodes}
    for src, dst in graph.edges:
        adj[src].add(dst)
        adj[dst].add(src)
    return adj


def find_clusters(graph: SkillGraph) -> list[SkillCluster]:
    """Group connected skills using BFS. Returns clusters sorted by size desc."""
    adj = _adjacency_undirected(graph)
    visited: set[str] = set()
    clusters: list[SkillCluster] = []

    for start in sorted(graph.nodes):
        if start in visited:
            continue
        component = _bfs_component(start, adj, visited)
        clusters.append(_make_cluster(component, adj))

    clusters.sort(key=lambda c: (-len(c.members), c.name))
    graph.clusters = clusters
    return clusters


def _bfs_component(start: str, adj: dict[str, set[str]], visited: set[str]) -> list[str]:
    """BFS from start; return all reachable nodes; mutates visited."""
    component: list[str] = []
    queue: deque[str] = deque([start])
    visited.add(start)
    # O(1): BFS visits each edge once total — O(V+E), not O(n^2)
    while queue:
        current = queue.popleft()
        component.append(current)
        # O(1): BFS visits each edge once total — O(V+E), not O(n^2)
        for neighbor in sorted(adj[current]):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    return component


def _make_cluster(component: list[str], adj: dict[str, set[str]]) -> SkillCluster:
    """Build a SkillCluster with hub = highest-degree member."""
    hub = max(component, key=lambda n: len(adj[n]))
    cluster_name = hub if len(component) > 1 else component[0]
    return SkillCluster(name=cluster_name, members=sorted(component), hub=hub)


def find_hubs(graph: SkillGraph, min_degree: int = 3) -> list[tuple[str, int]]:
    """Skills with many connections (degree >= min_degree).

    Args:
        graph: The skill graph.
        min_degree: Minimum connection count to qualify as hub.

    Returns:
        List of (skill_name, degree) sorted by degree descending.
    """
    adj = _adjacency_undirected(graph)
    hubs = [
        (name, len(neighbors))
        for name, neighbors in adj.items()
        if len(neighbors) >= min_degree
    ]
    hubs.sort(key=lambda x: (-x[1], x[0]))
    return hubs


def find_orphans(graph: SkillGraph) -> list[str]:
    """Skills with no connections.

    Args:
        graph: The skill graph.

    Returns:
        Sorted list of orphan skill names.
    """
    adj = _adjacency_undirected(graph)
    return sorted(name for name, neighbors in adj.items() if not neighbors)


def shortest_path(
    graph: SkillGraph, skill_a: str, skill_b: str
) -> list[str] | None:
    """Find shortest path between two skills using BFS.

    Args:
        graph: The skill graph.
        skill_a: Start skill name.
        skill_b: End skill name.

    Returns:
        List of skill names forming the path, or None if no path exists.
    """
    if skill_a not in graph.nodes or skill_b not in graph.nodes:
        return None

    if skill_a == skill_b:
        return [skill_a]

    adj = _adjacency_undirected(graph)
    visited: set[str] = {skill_a}
    queue: deque[list[str]] = deque([[skill_a]])

    while queue:
        path = queue.popleft()
        current = path[-1]

        # O(1): BFS visits each edge once total — O(V+E), not O(n^2)
        for neighbor in sorted(adj[current]):
            if neighbor == skill_b:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])

    return None


# ---------------------------------------------------------------------------
# ASCII rendering
# ---------------------------------------------------------------------------


def render_graph(graph: SkillGraph, max_nodes: int = 50) -> str:
    """ASCII visualization of the graph (adjacency list style).

    Args:
        graph: The skill graph.
        max_nodes: Maximum nodes to display.

    Returns:
        Multi-line string with ASCII graph.
    """
    if not graph.nodes:
        return "(empty graph)"

    adj = _adjacency_undirected(graph)
    lines: list[str] = []
    shown = 0

    # Sort by degree descending for most-connected-first display
    sorted_nodes = sorted(
        graph.nodes.keys(),
        key=lambda n: (-len(adj[n]), n),
    )

    for name in sorted_nodes:
        if shown >= max_nodes:
            remaining = len(sorted_nodes) - shown
            lines.append(f"  ... and {remaining} more")
            break

        neighbors = sorted(adj[name])
        if not neighbors:
            lines.append(name)
        elif len(neighbors) == 1:
            lines.append(f"{name} ──── {neighbors[0]}")
        else:
            # Tree-style with connectors
            lines.append(f"{name} ──┬── {neighbors[0]}")
            # O(1): bounded by per-node neighbour count, not graph size
            for neighbor in neighbors[1:-1]:
                padding = " " * len(name)
                lines.append(f"{padding}   ├── {neighbor}")
            padding = " " * len(name)
            lines.append(f"{padding}   └── {neighbors[-1]}")

        shown += 1

    return "\n".join(lines)


def render_cluster(cluster: SkillCluster) -> str:
    """ASCII visualization of a single cluster.

    Args:
        cluster: The skill cluster.

    Returns:
        Multi-line string with cluster visualization.
    """
    if not cluster.members:
        return "(empty cluster)"

    if len(cluster.members) == 1:
        return f"[{cluster.members[0]}]"

    lines: list[str] = [f"Cluster: {cluster.name} ({len(cluster.members)} skills)"]
    if cluster.hub:
        lines.append(f"Hub: {cluster.hub}")
    lines.append("")

    # Star topology around hub
    hub = cluster.hub or cluster.members[0]
    others = [m for m in cluster.members if m != hub]

    if not others:
        lines.append(f"  [{hub}]")
    elif len(others) == 1:
        lines.append(f"  {hub} ──── {others[0]}")
    else:
        lines.append(f"  {hub} ──┬── {others[0]}")
        for other in others[1:-1]:
            padding = " " * len(hub)
            lines.append(f"  {padding}   ├── {other}")
        padding = " " * len(hub)
        lines.append(f"  {padding}   └── {others[-1]}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def compute_stats(graph: SkillGraph) -> dict:
    """Compute summary statistics for the graph.

    Returns dict with: total_skills, total_edges, clusters, hubs,
    orphans, avg_degree, max_degree, max_degree_skill.
    """
    adj = _adjacency_undirected(graph)
    degrees = {name: len(neighbors) for name, neighbors in adj.items()}
    total = len(graph.nodes)

    if not total:
        return {
            "total_skills": 0,
            "total_edges": 0,
            "clusters": 0,
            "hubs": 0,
            "orphans": 0,
            "avg_degree": 0.0,
            "max_degree": 0,
            "max_degree_skill": None,
        }

    clusters = find_clusters(graph) if not graph.clusters else graph.clusters
    hub_list = find_hubs(graph)
    orphan_list = find_orphans(graph)
    max_deg_skill = max(degrees, key=degrees.get)  # type: ignore[arg-type]

    return {
        "total_skills": total,
        "total_edges": len(graph.edges),
        "clusters": len(clusters),
        "hubs": len(hub_list),
        "orphans": len(orphan_list),
        "avg_degree": sum(degrees.values()) / total,
        "max_degree": degrees[max_deg_skill],
        "max_degree_skill": max_deg_skill,
    }


# ---------------------------------------------------------------------------
# Validation (broken refs + cycles)
# ---------------------------------------------------------------------------


def detect_cycles(graph: SkillGraph) -> list[list[str]]:
    """Detect circular dependency chains via DFS from every node.

    Returns a list of cycle paths. Each path is a list ending with the
    repeated node (e.g. ['a', 'b', 'c', 'a']). Cycles are deduplicated by
    the frozenset of their member nodes — any rotation of a given cycle
    appears once in the result.

    Args:
        graph: The skill graph (built via build_graph).
    """
    adj: dict[str, list[str]] = {
        name: list(set(node.references)) for name, node in graph.nodes.items()
    }
    cycles: list[list[str]] = []
    seen_keys: set[frozenset[str]] = set()

    for start in sorted(graph.nodes):
        _walk_for_cycles(start, adj, cycles, seen_keys)

    return cycles


def _walk_for_cycles(
    start: str,
    adj: dict[str, list[str]],
    cycles: list[list[str]],
    seen_keys: set[frozenset[str]],
) -> None:
    """Iterative DFS from one start node, recording any back-edge cycles."""
    stack: list[tuple[str, tuple[str, ...], frozenset[str]]] = [
        (start, (start,), frozenset({start}))
    ]
    while stack:
        current, path, in_path = stack.pop()
        _process_neighbours(current, path, in_path, adj, stack, cycles, seen_keys)


def _process_neighbours(
    current: str,
    path: tuple[str, ...],
    in_path: frozenset[str],
    adj: dict[str, list[str]],
    stack: list[tuple[str, tuple[str, ...], frozenset[str]]],
    cycles: list[list[str]],
    seen_keys: set[frozenset[str]],
) -> None:
    """Push DFS frames for unvisited neighbours; record cycles for back-edges."""
    for neighbour in adj.get(current, []):
        _handle_neighbour(neighbour, path, in_path, stack, cycles, seen_keys)


def _handle_neighbour(
    neighbour: str,
    path: tuple[str, ...],
    in_path: frozenset[str],
    stack: list[tuple[str, tuple[str, ...], frozenset[str]]],
    cycles: list[list[str]],
    seen_keys: set[frozenset[str]],
) -> None:
    """Either record a back-edge cycle or push a forward DFS frame."""
    if neighbour in in_path:
        _maybe_record_cycle(path, neighbour, cycles, seen_keys)
    else:
        stack.append((neighbour, path + (neighbour,), in_path | {neighbour}))


def _maybe_record_cycle(
    path: tuple[str, ...],
    end_node: str,
    cycles: list[list[str]],
    seen_keys: set[frozenset[str]],
) -> None:
    """Add the cycle path[idx:] + [end_node] if its node-set is unseen."""
    idx = path.index(end_node)
    key = frozenset(path[idx:])
    if key not in seen_keys:
        seen_keys.add(key)
        cycles.append(list(path[idx:]) + [end_node])


def extract_explicit_refs(skill_content: str) -> set[str]:
    """Extract only structured/explicit skill citations for validation.

    Used by validate_skill_graph. Includes:
    - @skills/<name> (explicit path form)
    - [text](../<name>/SKILL.md) (explicit link form)
    - YAML see_also: <name> | see_also: [- name1, - name2] (frontmatter)

    Excludes:
    - /command (slash commands often have no backing skill dir — alias layer)
    - `name` backticks (too noisy; many backticks are not skill refs)

    Fenced code blocks are stripped first so CSS at-rules and shell
    snippets cannot pollute the result.
    """
    prose = _strip_code_spans(_strip_fenced_blocks(skill_content))
    refs: set[str] = set()

    for match in SKILL_AT_PATTERN.finditer(prose):
        refs.add(match.group(1))
    for match in LINK_PATTERN.finditer(prose):
        refs.add(match.group(1))

    refs.update(_extract_see_also_items(skill_content))

    return refs


def validate_skill_graph(
    skills_dir: str,
) -> tuple[dict[str, list[str]], list[list[str]]]:
    """Validate the skill graph: report broken references and cycles.

    A reference is "broken" when a SKILL.md cites a skill via an explicit
    structured form (@skills/<name>, link, or YAML see_also) but no
    corresponding directory exists in the skills tree.

    Slash commands (/save, /recall) and backtick references are excluded
    from broken-ref detection because they often resolve via aliases or
    are documentation rather than dependencies.

    Returns:
        (broken_refs, cycles)
        - broken_refs: dict of skill name -> sorted list of missing targets
        - cycles: list of cycle paths from detect_cycles
    """
    nodes = scan_skills(skills_dir)
    known = set(nodes.keys())
    broken: dict[str, list[str]] = {}

    for name, node in nodes.items():
        try:
            content = Path(node.path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        refs = extract_explicit_refs(content)
        missing = sorted(refs - known - {name})
        if missing:
            broken[name] = missing

        # #2801: a dangling ``redirect_to:`` (target skill dir absent) is a
        # broken reference too — fold it into `broken` so `_cmd_validate`
        # exits 1 on it (previously invisible: extract_explicit_refs never
        # parsed redirect_to → G3 rubber-stamped redirect stubs).
        redirect = _extract_redirect_to(content)
        if redirect and redirect not in known and redirect != name:
            broken[name] = sorted(
                set(broken.get(name, []) + [f"redirect_to:{redirect}"])
            )

        # #2801 (spec I3 / §4.4.3 kill-switch): a redirect stub that still
        # declares its own non-empty `triggers:` is an ORPHANED TRIGGER —
        # the trigger was not re-homed to the dispatcher, so invoking it
        # dead-ends on a stub. Correctly-stubbed skills carry `triggers: []`.
        if _is_redirect_stub(content) and _has_nonempty_triggers(content):
            broken[name] = sorted(
                set(broken.get(name, []) + ["orphaned-trigger"])
            )

    # #2801 (spec G3 §3.3): fold double-homed skills (one NAME resolved
    # from >1 SKILL.md dir — scan_skills' dict silently overwrites the
    # collision) into `broken` so `_cmd_validate` exits 1 on them.
    for _n, _c in _detect_double_homed(skills_dir).items():
        broken[_n] = sorted(set(broken.get(_n, []) + [f"double-home:{_c}"]))

    graph = build_graph(nodes)
    cycles = detect_cycles(graph)

    return broken, cycles


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="skill_graph",
        description="Map dependencies between GRIP skills",
    )
    parser.add_argument(
        "--skills-dir",
        default=None,
        help="Path to skills directory (default: auto-detect from GRIP root)",
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("graph", help="Full skill dependency map")
    sub.add_parser("clusters", help="Show skill clusters")
    sub.add_parser("hubs", help="Show hub skills (many connections)")
    sub.add_parser("orphans", help="Show orphan skills (no connections)")
    sub.add_parser("stats", help="Show graph statistics")
    sub.add_parser("validate", help="Detect broken refs + circular dependencies")

    path_parser = sub.add_parser("path", help="Shortest path between two skills")
    path_parser.add_argument("skill1", help="Start skill")
    path_parser.add_argument("skill2", help="End skill")

    return parser


def _resolve_skills_dir(explicit: str | None) -> str:
    """Resolve the skills directory path."""
    if explicit:
        return explicit

    # Try relative to this script's location
    script_dir = Path(__file__).resolve().parent
    grip_root = script_dir.parent
    skills_dir = grip_root / "skills"

    if skills_dir.is_dir():
        return str(skills_dir)

    # Try $HOME/.claude/skills
    home_skills = Path(os.environ.get("HOME", "")) / ".claude" / "skills"
    if home_skills.is_dir():
        return str(home_skills)

    return str(skills_dir)  # Return default even if missing


def _cmd_graph(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    print(render_graph(build_graph(nodes)))
    return 0


def _cmd_clusters(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    for cluster in find_clusters(build_graph(nodes)):
        print(render_cluster(cluster))
        print()
    return 0


def _cmd_hubs(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    hub_list = find_hubs(build_graph(nodes))
    if not hub_list:
        print("No hubs found (no skills with 3+ connections)")
        return 0
    for name, degree in hub_list:
        print(f"  {name}: {degree} connections")
    return 0


def _cmd_orphans(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    orphan_list = find_orphans(build_graph(nodes))
    if not orphan_list:
        print("No orphans (all skills are connected)")
        return 0
    for name in orphan_list:
        print(f"  {name}")
    return 0


def _cmd_path(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    result = shortest_path(build_graph(nodes), args.skill1, args.skill2)
    if result is None:
        print(f"No path between {args.skill1} and {args.skill2}")
        return 1
    print(" -> ".join(result))
    return 0


def _cmd_stats(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    stats = compute_stats(build_graph(nodes))
    print(f"Skills:     {stats['total_skills']}")
    print(f"Edges:      {stats['total_edges']}")
    print(f"Clusters:   {stats['clusters']}")
    print(f"Hubs:       {stats['hubs']}")
    print(f"Orphans:    {stats['orphans']}")
    print(f"Avg degree: {stats['avg_degree']:.1f}")
    if stats["max_degree_skill"]:
        print(f"Max degree: {stats['max_degree']} ({stats['max_degree_skill']})")
    return 0


def _cmd_validate(args: argparse.Namespace, skills_dir: str, nodes: dict[str, SkillNode]) -> int:
    broken, cycles = validate_skill_graph(skills_dir)
    graph = build_graph(nodes)
    edge_count = len(graph.edges)
    print(f"[skill-graph-validator] {len(nodes)} skills, {edge_count} edges")
    _print_cycles(cycles)
    return _print_broken(broken)


def _print_cycles(cycles: list[list[str]]) -> None:
    if not cycles:
        print("  No circular dependencies")
        return
    print(f"  CIRCULAR ({len(cycles)}):")
    for cycle in cycles[:5]:
        print(f"    {' -> '.join(cycle)}")


def _print_broken(broken: dict[str, list[str]]) -> int:
    if not broken:
        print("  No broken references")
        return 0
    print(f"  BROKEN REFS ({len(broken)}):")
    for skill, missing in sorted(broken.items())[:20]:
        print(f"    {skill} -> {missing}")
    if len(broken) > 20:
        print(f"    ... and {len(broken) - 20} more")
    return 1


_CLI_HANDLERS = {
    "graph": _cmd_graph,
    "clusters": _cmd_clusters,
    "hubs": _cmd_hubs,
    "orphans": _cmd_orphans,
    "path": _cmd_path,
    "stats": _cmd_stats,
    "validate": _cmd_validate,
}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point — dispatches to per-command handler."""
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 0
    skills_dir = _resolve_skills_dir(args.skills_dir)
    nodes = scan_skills(skills_dir)
    if not nodes:
        print(f"No skills found in {skills_dir}", file=sys.stderr)
        return 1
    handler = _CLI_HANDLERS.get(args.command)
    return handler(args, skills_dir, nodes) if handler else 0


if __name__ == "__main__":
    sys.exit(main())
