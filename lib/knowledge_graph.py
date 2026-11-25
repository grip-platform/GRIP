"""Developer knowledge graph.

Maps expertise, file ownership, and collaboration patterns from git history.
Generates visual graphs showing who knows what and who works with whom.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from subprocess_helpers import run_git as _run_git


# === Data Models ===

@dataclass
class Developer:
    """A developer in the knowledge graph."""
    name: str
    email: str = ""
    commits: int = 0
    files_touched: int = 0
    primary_areas: list[str] = field(default_factory=list)
    expertise_score: float = 0.0  # 0-1


@dataclass
class FileNode:
    """A file in the knowledge graph."""
    path: str
    owners: list[str] = field(default_factory=list)
    change_frequency: int = 0
    last_modified: str = ""
    area: str = ""


@dataclass
class CollaborationEdge:
    """A collaboration link between two developers."""
    dev_a: str
    dev_b: str
    shared_files: int = 0
    co_commits: int = 0
    strength: float = 0.0  # 0-1


@dataclass
class ExpertiseEdge:
    """An expertise link between developer and area."""
    developer: str
    area: str
    depth: float = 0.0  # 0-1
    commits_in_area: int = 0


@dataclass
class KnowledgeGraph:
    """Complete knowledge graph of a repository."""
    developers: list[Developer] = field(default_factory=list)
    files: list[FileNode] = field(default_factory=list)
    collaborations: list[CollaborationEdge] = field(default_factory=list)
    expertise: list[ExpertiseEdge] = field(default_factory=list)
    bus_factor: int = 0
    knowledge_silos: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


# === Git Extraction ===

def extract_file_authors(days: int = 90) -> dict[str, list[str]]:
    """Map each file to its authors from git log."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    log = _run_git(
        f'git log --since={since} --pretty=format:%an --name-only'
    )
    if not log:
        return {}

    file_authors: dict[str, list[str]] = defaultdict(list)
    current_author = None

    for line in log.split("\n"):
        line = line.strip()
        if not line:
            current_author = None
            continue
        if current_author is None:
            current_author = line
        else:
            file_authors[line].append(current_author)

    return dict(file_authors)


def extract_author_stats(days: int = 90) -> dict[str, dict]:
    """Get commit stats per author."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    log = _run_git(
        f'git log --since={since} --pretty=format:%an|%ae'
    )
    if not log:
        return {}

    stats: dict[str, dict] = {}
    for line in log.split("\n"):
        if "|" not in line:
            continue
        parts = line.strip().split("|", 1)
        name = parts[0]
        email = parts[1] if len(parts) > 1 else ""
        if name not in stats:
            stats[name] = {"email": email, "commits": 0}
        stats[name]["commits"] += 1

    return stats


# === Graph Building ===

def _classify_area(path: str) -> str:
    """Classify a file path into an area."""
    parts = path.split("/")
    if not parts:
        return "root"

    first = parts[0]
    if first in ("lib", "src"):
        if len(parts) > 1:
            return f"{first}/{parts[1]}"
        return first
    if first in ("tests", "test", "__tests__"):
        return "tests"
    if first in ("docs", "wiki"):
        return "docs"
    if first in (".github", "scripts"):
        return "ci/scripts"
    if first in ("hooks", "agents", "skills", "modes"):
        return first
    if first in ("data", "config"):
        return "data"
    if first in ("apps", "packages"):
        if len(parts) > 1:
            return f"{first}/{parts[1]}"
        return first

    # File extension based
    if "." in path:
        ext = path.rsplit(".", 1)[-1]
        if ext in ("md", "rst", "txt"):
            return "docs"
        if ext in ("yml", "yaml", "json", "toml"):
            return "config"

    return first


def build_knowledge_graph(
    file_authors: dict[str, list[str]],
    author_stats: dict[str, dict],
    days: int = 90,
) -> KnowledgeGraph:
    """Build knowledge graph from extracted data."""
    graph = KnowledgeGraph()

    # Build developer nodes
    all_devs = set()
    dev_areas: dict[str, Counter] = defaultdict(Counter)
    dev_files: dict[str, set] = defaultdict(set)

    for filepath, authors in file_authors.items():
        area = _classify_area(filepath)
        for author in authors:
            all_devs.add(author)
            dev_areas[author][area] += 1
            dev_files[author].add(filepath)

    # Calculate max commits for normalization
    max_commits = max(
        (s.get("commits", 0) for s in author_stats.values()), default=1
    )
    max_commits = max(1, max_commits)

    for name in all_devs:
        stats = author_stats.get(name, {})
        top_areas = [a for a, _ in dev_areas[name].most_common(3)]
        graph.developers.append(Developer(
            name=name,
            email=stats.get("email", ""),
            commits=stats.get("commits", 0),
            files_touched=len(dev_files[name]),
            primary_areas=top_areas,
            expertise_score=min(1.0, stats.get("commits", 0) / max_commits),
        ))

    # Build file nodes
    file_owner_map: dict[str, Counter] = {}
    for filepath, authors in file_authors.items():
        counter = Counter(authors)
        file_owner_map[filepath] = counter
        owners = [a for a, _ in counter.most_common(2)]
        graph.files.append(FileNode(
            path=filepath,
            owners=owners,
            change_frequency=len(authors),
            area=_classify_area(filepath),
        ))

    # Build collaboration edges
    dev_pair_files: dict[tuple[str, str], set] = defaultdict(set)
    for filepath, authors in file_authors.items():
        unique_authors = list(set(authors))
        for i, a in enumerate(unique_authors):
            for b in unique_authors[i + 1:]:
                pair = tuple(sorted([a, b]))
                dev_pair_files[pair].add(filepath)

    for (a, b), shared in dev_pair_files.items():
        max_shared = max(len(dev_files[a]), len(dev_files[b]), 1)
        graph.collaborations.append(CollaborationEdge(
            dev_a=a,
            dev_b=b,
            shared_files=len(shared),
            strength=min(1.0, len(shared) / max_shared),
        ))

    # Build expertise edges
    for name in all_devs:
        total = sum(dev_areas[name].values())
        if total == 0:
            continue
        for area, count in dev_areas[name].most_common(5):
            graph.expertise.append(ExpertiseEdge(
                developer=name,
                area=area,
                depth=min(1.0, count / total),
                commits_in_area=count,
            ))

    # Calculate bus factor
    graph.bus_factor = _calculate_bus_factor(file_authors)

    # Detect knowledge silos
    graph.knowledge_silos = _detect_silos(file_authors)

    # Generate recommendations
    graph.recommendations = _generate_recommendations(graph)

    return graph


def _calculate_bus_factor(file_authors: dict[str, list[str]]) -> int:
    """Calculate bus factor (minimum devs whose departure loses knowledge)."""
    if not file_authors:
        return 0

    # Count files owned primarily by each developer
    primary_owners: Counter = Counter()
    for filepath, authors in file_authors.items():
        if authors:
            primary = Counter(authors).most_common(1)[0][0]
            primary_owners[primary] += 1

    total_files = len(file_authors)
    if total_files == 0:
        return 0

    # Sort developers by file ownership (most files first)
    sorted_devs = primary_owners.most_common()

    # Count how many devs cover 50% of files
    covered = 0
    bus_factor = 0
    threshold = total_files * 0.5
    for _dev, count in sorted_devs:
        covered += count
        bus_factor += 1
        if covered >= threshold:
            break

    return bus_factor


def _detect_silos(file_authors: dict[str, list[str]]) -> list[str]:
    """Detect knowledge silos (areas owned by only one person)."""
    area_authors: dict[str, set] = defaultdict(set)
    for filepath, authors in file_authors.items():
        area = _classify_area(filepath)
        area_authors[area].update(set(authors))

    silos = []
    for area, authors in area_authors.items():
        if len(authors) == 1:
            author = list(authors)[0]
            silos.append(f"{area} (only {author})")

    return sorted(silos)


def _generate_recommendations(graph: KnowledgeGraph) -> list[str]:
    """Generate actionable recommendations from knowledge graph."""
    recs = []

    if graph.bus_factor <= 1:
        recs.append("Critical: Bus factor is 1 — cross-train team members on core areas")

    if graph.knowledge_silos:
        silo_count = len(graph.knowledge_silos)
        recs.append(f"Found {silo_count} knowledge silo(s) — schedule pairing sessions for knowledge transfer")

    # Check for isolated developers
    collab_devs = set()
    for c in graph.collaborations:
        collab_devs.add(c.dev_a)
        collab_devs.add(c.dev_b)
    all_devs = {d.name for d in graph.developers}
    isolated = all_devs - collab_devs
    if isolated:
        recs.append(f"Isolated developer(s): {', '.join(isolated)} — encourage collaboration")

    # Check for review coverage
    if len(graph.developers) > 1:
        avg_collab = (
            len(graph.collaborations) / (len(graph.developers) * (len(graph.developers) - 1) / 2)
            if len(graph.developers) > 1 else 0
        )
        if avg_collab < 0.3:
            recs.append("Low collaboration density — implement code review rotation")

    if not recs:
        recs.append("Knowledge distribution looks healthy")

    return recs


# === Formatters ===

def format_graph_text(graph: KnowledgeGraph) -> str:
    """Format knowledge graph as terminal text."""
    lines = []
    lines.append("Developer Knowledge Graph")
    lines.append("=" * 50)
    lines.append(f"Bus Factor: {graph.bus_factor}")
    lines.append(f"Developers: {len(graph.developers)}")
    lines.append(f"Files Tracked: {len(graph.files)}")

    if graph.developers:
        lines.append("")
        lines.append("Team Expertise:")
        for dev in sorted(graph.developers, key=lambda d: d.commits, reverse=True):
            bar = "#" * int(dev.expertise_score * 20)
            areas = ", ".join(dev.primary_areas[:3]) if dev.primary_areas else "various"
            lines.append(f"  {dev.name}: [{bar:<20}] {dev.commits} commits ({areas})")

    if graph.knowledge_silos:
        lines.append("")
        lines.append("Knowledge Silos (single-owner areas):")
        for silo in graph.knowledge_silos:
            lines.append(f"  ! {silo}")

    if graph.collaborations:
        lines.append("")
        lines.append("Collaboration Map:")
        for collab in sorted(graph.collaborations, key=lambda c: c.strength, reverse=True)[:10]:
            strength_bar = "#" * int(collab.strength * 10)
            lines.append(f"  {collab.dev_a} <-> {collab.dev_b}: [{strength_bar:<10}] {collab.shared_files} shared files")

    if graph.recommendations:
        lines.append("")
        lines.append("Recommendations:")
        for i, rec in enumerate(graph.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    return "\n".join(lines)


def format_graph_slack(graph: KnowledgeGraph) -> str:
    """Format knowledge graph as Slack mrkdwn."""
    lines = []
    lines.append("*Developer Knowledge Graph*")
    lines.append(f"Bus Factor: *{graph.bus_factor}* | Developers: {len(graph.developers)} | Files: {len(graph.files)}")

    if graph.developers:
        lines.append("")
        lines.append("_Team Expertise:_")
        for dev in sorted(graph.developers, key=lambda d: d.commits, reverse=True)[:5]:
            areas = ", ".join(dev.primary_areas[:2]) if dev.primary_areas else "various"
            lines.append(f"  *{dev.name}*: {dev.commits} commits ({areas})")

    if graph.knowledge_silos:
        lines.append("")
        lines.append("_Knowledge Silos:_")
        for silo in graph.knowledge_silos[:5]:
            lines.append(f"  - {silo}")

    if graph.recommendations:
        lines.append("")
        lines.append("_Recommendations:_")
        for rec in graph.recommendations:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def format_graph_json(graph: KnowledgeGraph) -> str:
    """Format knowledge graph as JSON."""
    data = {
        "bus_factor": graph.bus_factor,
        "developers": [
            {
                "name": d.name,
                "commits": d.commits,
                "files_touched": d.files_touched,
                "primary_areas": d.primary_areas,
                "expertise_score": d.expertise_score,
            }
            for d in graph.developers
        ],
        "collaborations": [
            {
                "dev_a": c.dev_a,
                "dev_b": c.dev_b,
                "shared_files": c.shared_files,
                "strength": c.strength,
            }
            for c in graph.collaborations
        ],
        "knowledge_silos": graph.knowledge_silos,
        "recommendations": graph.recommendations,
    }
    return json.dumps(data, indent=2)


def format_graph_concept_map(graph: KnowledgeGraph) -> dict:
    """Format knowledge graph as data for GRIP concept map MCP app."""
    nodes = []
    edges = []

    # Add developer nodes
    for dev in graph.developers:
        nodes.append({
            "id": f"dev_{dev.name.replace(' ', '_')}",
            "label": dev.name,
            "type": "developer",
        })

    # Add area nodes
    areas = {e.area for e in graph.expertise}
    for area in areas:
        nodes.append({
            "id": f"area_{area.replace('/', '_')}",
            "label": area,
            "type": "area",
        })

    # Add expertise edges
    for exp in graph.expertise:
        if exp.depth > 0.1:  # Filter noise
            edges.append({
                "from": f"dev_{exp.developer.replace(' ', '_')}",
                "to": f"area_{exp.area.replace('/', '_')}",
                "label": f"{exp.commits_in_area} commits",
                "style": "solid" if exp.depth > 0.3 else "dashed",
            })

    # Add collaboration edges
    for collab in graph.collaborations:
        if collab.strength > 0.1:
            edges.append({
                "from": f"dev_{collab.dev_a.replace(' ', '_')}",
                "to": f"dev_{collab.dev_b.replace(' ', '_')}",
                "label": f"{collab.shared_files} files",
                "style": "dotted",
            })

    return {
        "title": "Developer Knowledge Graph",
        "nodes": nodes,
        "edges": edges,
    }


# === CLI ===

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Developer Knowledge Graph")
    parser.add_argument("--days", type=int, default=90, help="Days of history to analyze")
    parser.add_argument("--format", choices=["text", "slack", "json", "concept-map"], default="text")
    args = parser.parse_args()

    file_authors = extract_file_authors(args.days)
    author_stats = extract_author_stats(args.days)
    graph = build_knowledge_graph(file_authors, author_stats, args.days)

    if args.format == "slack":
        print(format_graph_slack(graph))
    elif args.format == "json":
        print(format_graph_json(graph))
    elif args.format == "concept-map":
        print(json.dumps(format_graph_concept_map(graph), indent=2))
    else:
        print(format_graph_text(graph))


if __name__ == "__main__":
    main()
