"""
Team Collaboration Heatmap Generator for GRIP.

Visualizes how team members collaborate through code by analyzing git history.
Detects knowledge silos, collaboration hotspots, temporal overlaps, and
generates actionable recommendations.

Usage:
    python3 lib/collaboration_heatmap.py --days 30 --format text
    python3 lib/collaboration_heatmap.py --format concept_map
    python3 lib/collaboration_heatmap.py silos  # Just knowledge silos

Version: 1.0.0
"""

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------


@dataclass
class CollaborationEdge:
    """A collaboration link between two authors."""
    author_a: str
    author_b: str
    shared_files: list = field(default_factory=list)
    strength: float = 0.0  # 0-1.0
    collaboration_type: str = "co-edit"  # co-edit, sequential, review


@dataclass
class KnowledgeSilo:
    """A file/directory owned exclusively by one person (bus factor = 1)."""
    path: str
    sole_owner: str
    last_touched: str = ""
    risk_level: str = "low"  # low, medium, high, critical
    commit_count: int = 0


@dataclass
class CollaborationHotspot:
    """A file frequently edited by multiple authors."""
    path: str
    authors: list = field(default_factory=list)
    edit_count: int = 0
    author_count: int = 0


@dataclass
class TemporalOverlap:
    """Work hour overlap between two authors."""
    author_a: str
    author_b: str
    overlap_hours: list = field(default_factory=list)
    overlap_ratio: float = 0.0  # 0-1.0


@dataclass
class CollaborationReport:
    """Full collaboration analysis result."""
    edges: list = field(default_factory=list)
    silos: list = field(default_factory=list)
    hotspots: list = field(default_factory=list)
    temporal_overlaps: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    analyzed_days: int = 30
    total_commits: int = 0
    total_authors: int = 0


# ---------------------------------------------------------------------------
# Git Interaction
# ---------------------------------------------------------------------------


def _default_git(args: list) -> str:
    """Run git command and return stdout."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


# ---------------------------------------------------------------------------
# Core Analysis Functions
# ---------------------------------------------------------------------------


def parse_git_log_for_collaboration(days: int = 30, _git: Optional[Callable] = None) -> list:
    """Parse git log into structured commit dicts.

    Args:
        days: How many days of history to analyze.
        _git: Injectable git runner for testing. Signature: _git(args) -> str.

    Returns:
        List of dicts with keys: hash, author, date, hour, files.
    """
    git = _git or _default_git
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    # Use --name-only with a delimiter to parse commits
    raw = git([
        "log", f"--since={since}", "--format=COMMIT_SEP%n%H%n%an%n%aI",
        "--name-only", "--no-merges",
    ])

    if not raw:
        return []

    commits = []
    blocks = raw.split("COMMIT_SEP\n")

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.split("\n")
        if len(lines) < 3:
            continue

        commit_hash = lines[0].strip()
        author = lines[1].strip()
        date_str = lines[2].strip()

        # Parse hour from ISO date
        hour = 12  # default
        try:
            dt = datetime.fromisoformat(date_str)
            hour = dt.hour
        except (ValueError, TypeError):
            pass

        # Files are remaining non-empty lines
        files = [f.strip() for f in lines[3:] if f.strip()]

        if commit_hash and author:
            commits.append({
                "hash": commit_hash,
                "author": author,
                "date": date_str,
                "hour": hour,
                "files": files,
            })

    return commits


def build_file_ownership(commits: list) -> dict:
    """Map files to their owners based on commit frequency.

    Args:
        commits: List of commit dicts from parse_git_log_for_collaboration.

    Returns:
        Dict mapping file path -> {author: commit_count, ...}.
    """
    ownership = defaultdict(lambda: Counter())

    for commit in commits:
        author = commit.get("author", "")
        for f in commit.get("files", []):
            ownership[f][author] += 1

    return dict(ownership)


def detect_collaboration_edges(commits: list) -> list:
    """Find collaboration patterns between authors.

    Two authors collaborate when they both edit the same file.
    Strength is based on how many files they share relative to their total.

    Args:
        commits: List of commit dicts.

    Returns:
        List of CollaborationEdge instances.
    """
    # Map: file -> set of authors
    file_authors = defaultdict(set)
    for commit in commits:
        author = commit.get("author", "")
        for f in commit.get("files", []):
            file_authors[f].add(author)

    # Build pair -> shared files
    pair_files = defaultdict(set)
    for f, authors in file_authors.items():
        authors_list = sorted(authors)
        for i in range(len(authors_list)):
            for j in range(i + 1, len(authors_list)):
                pair = (authors_list[i], authors_list[j])
                pair_files[pair].add(f)

    if not pair_files:
        return []

    # Calculate strength: shared files / max possible
    max_shared = max(len(files) for files in pair_files.values()) if pair_files else 1

    edges = []
    for (a, b), shared in sorted(pair_files.items(), key=lambda x: len(x[1]), reverse=True):
        strength = len(shared) / max_shared if max_shared > 0 else 0.0
        edges.append(CollaborationEdge(
            author_a=a,
            author_b=b,
            shared_files=sorted(shared),
            strength=round(strength, 3),
            collaboration_type="co-edit",
        ))

    return edges


def detect_knowledge_silos(ownership: dict, min_commits: int = 3) -> list:
    """Find files where only one person has committed (bus factor = 1).

    Args:
        ownership: Dict from build_file_ownership.
        min_commits: Minimum commits to consider a file non-trivial.

    Returns:
        List of KnowledgeSilo instances sorted by risk level.
    """
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    silos = []

    for path, authors in ownership.items():
        # Only one author
        if len(authors) == 1:
            author = list(authors.keys())[0]
            count = authors[author]

            if count < min_commits:
                continue

            # Risk based on commit count and file type
            risk = "low"
            if count >= 20:
                risk = "critical"
            elif count >= 10:
                risk = "high"
            elif count >= 5:
                risk = "medium"

            # Boost risk for important file types
            important_extensions = {".py", ".sh", ".ts", ".tsx", ".js", ".jsx"}
            ext = Path(path).suffix
            if ext in important_extensions and risk == "low" and count >= 3:
                risk = "medium"

            silos.append(KnowledgeSilo(
                path=path,
                sole_owner=author,
                commit_count=count,
                risk_level=risk,
            ))

    silos.sort(key=lambda s: risk_order.get(s.risk_level, 3))
    return silos


def detect_hotspots(commits: list, min_authors: int = 2) -> list:
    """Find files edited by multiple authors (collaboration hotspots).

    Args:
        commits: List of commit dicts.
        min_authors: Minimum unique authors to qualify as hotspot.

    Returns:
        List of CollaborationHotspot instances sorted by author count descending.
    """
    file_data = defaultdict(lambda: {"authors": set(), "edits": 0})

    for commit in commits:
        author = commit.get("author", "")
        for f in commit.get("files", []):
            file_data[f]["authors"].add(author)
            file_data[f]["edits"] += 1

    hotspots = []
    for path, data in file_data.items():
        if len(data["authors"]) >= min_authors:
            hotspots.append(CollaborationHotspot(
                path=path,
                authors=sorted(data["authors"]),
                edit_count=data["edits"],
                author_count=len(data["authors"]),
            ))

    hotspots.sort(key=lambda h: (h.author_count, h.edit_count), reverse=True)
    return hotspots


def calculate_temporal_overlap(commits: list) -> list:
    """Find work hour overlaps between authors.

    Groups commits by hour-of-day per author, then calculates overlap ratio.

    Args:
        commits: List of commit dicts with 'hour' field.

    Returns:
        List of TemporalOverlap instances.
    """
    # Build hour histograms per author
    author_hours = defaultdict(lambda: Counter())
    for commit in commits:
        author = commit.get("author", "")
        hour = commit.get("hour", 12)
        author_hours[author][hour] += 1

    if len(author_hours) < 2:
        return []

    # For each pair, find overlapping hours
    authors = sorted(author_hours.keys())
    overlaps = []

    for i in range(len(authors)):
        for j in range(i + 1, len(authors)):
            a, b = authors[i], authors[j]
            hours_a = set(author_hours[a].keys())
            hours_b = set(author_hours[b].keys())

            shared_hours = sorted(hours_a & hours_b)
            all_hours = hours_a | hours_b

            ratio = len(shared_hours) / len(all_hours) if all_hours else 0.0

            overlaps.append(TemporalOverlap(
                author_a=a,
                author_b=b,
                overlap_hours=shared_hours,
                overlap_ratio=round(ratio, 3),
            ))

    overlaps.sort(key=lambda o: o.overlap_ratio, reverse=True)
    return overlaps


def generate_recommendations(report: CollaborationReport) -> list:
    """Generate actionable recommendations from a collaboration report.

    Args:
        report: A CollaborationReport instance.

    Returns:
        List of recommendation strings.
    """
    recs = []

    # Knowledge silo warnings
    critical_silos = [s for s in report.silos if s.risk_level == "critical"]
    high_silos = [s for s in report.silos if s.risk_level == "high"]

    if critical_silos:
        owners = set(s.sole_owner for s in critical_silos)
        recs.append(
            f"CRITICAL: {len(critical_silos)} files have bus factor = 1 with 20+ commits. "
            f"Owners: {', '.join(sorted(owners))}. Schedule pair programming or knowledge transfer sessions."
        )

    if high_silos:
        recs.append(
            f"HIGH: {len(high_silos)} files have bus factor = 1 with 10+ commits. "
            "Consider cross-training or code review rotation."
        )

    # Low collaboration
    if report.edges:
        weak_edges = [e for e in report.edges if e.strength < 0.2]
        if len(weak_edges) > len(report.edges) * 0.7:
            recs.append(
                "Most collaboration links are weak (< 0.2 strength). "
                "Team members work in isolated areas. Consider shared ownership practices."
            )

    # Temporal gaps
    if report.temporal_overlaps:
        no_overlap = [o for o in report.temporal_overlaps if o.overlap_ratio == 0.0]
        if no_overlap:
            pairs = [f"{o.author_a} + {o.author_b}" for o in no_overlap[:3]]
            recs.append(
                f"No work hour overlap detected for: {', '.join(pairs)}. "
                "Schedule overlapping collaboration windows."
            )

    # Hotspot congestion
    congested = [h for h in report.hotspots if h.author_count >= 3 and h.edit_count >= 10]
    if congested:
        recs.append(
            f"{len(congested)} files have 3+ authors with 10+ edits. "
            "Consider CODEOWNERS assignment or modular decomposition."
        )

    # Positive signals
    if not recs:
        if report.total_authors >= 2 and report.edges:
            recs.append(
                "Collaboration patterns look healthy. "
                "Team members share knowledge across files."
            )
        elif report.total_authors < 2:
            recs.append(
                "Only 1 author detected. Collaboration metrics require multiple contributors."
            )

    return recs


def analyze_collaboration(days: int = 30, _git: Optional[Callable] = None) -> CollaborationReport:
    """Full collaboration analysis pipeline.

    Args:
        days: Number of days of git history to analyze.
        _git: Injectable git runner for testing.

    Returns:
        Complete CollaborationReport.
    """
    commits = parse_git_log_for_collaboration(days=days, _git=_git)

    if not commits:
        return CollaborationReport(analyzed_days=days)

    ownership = build_file_ownership(commits)
    edges = detect_collaboration_edges(commits)
    silos = detect_knowledge_silos(ownership)
    hotspots = detect_hotspots(commits)
    temporal = calculate_temporal_overlap(commits)

    authors = set(c["author"] for c in commits)

    report = CollaborationReport(
        edges=edges,
        silos=silos,
        hotspots=hotspots,
        temporal_overlaps=temporal,
        analyzed_days=days,
        total_commits=len(commits),
        total_authors=len(authors),
    )

    report.recommendations = generate_recommendations(report)
    return report


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _build_heatmap_grid(edges: list, max_width: int = 60) -> str:
    """Build an ASCII heatmap grid showing collaboration strength."""
    if not edges:
        return "  (no collaboration edges)\n"

    # Collect unique authors
    authors = set()
    for e in edges:
        authors.add(e.author_a)
        authors.add(e.author_b)
    authors = sorted(authors)

    if len(authors) < 2:
        return "  (need 2+ authors for heatmap)\n"

    # Build strength matrix
    strength = {}
    for e in edges:
        key = (e.author_a, e.author_b)
        key_r = (e.author_b, e.author_a)
        strength[key] = e.strength
        strength[key_r] = e.strength

    # Truncate names for display
    max_name = 10
    short = {a: a[:max_name] for a in authors}

    # Header
    lines = []
    header = " " * (max_name + 2)
    for a in authors:
        header += f"{short[a]:>{max_name}} "
    lines.append(header)

    # Rows
    blocks = " .:-=+*#@"
    for a in authors:
        row = f"{short[a]:>{max_name}}  "
        for b in authors:
            if a == b:
                row += f"{'X':>{max_name}} "
            else:
                s = strength.get((a, b), 0.0)
                idx = min(int(s * (len(blocks) - 1)), len(blocks) - 1)
                char = blocks[idx]
                label = f"{char}{s:.1f}"
                row += f"{label:>{max_name}} "
        lines.append(row)

    return "\n".join(lines) + "\n"


def format_text(report: CollaborationReport) -> str:
    """Format report as terminal-friendly text with ASCII heatmap.

    Args:
        report: CollaborationReport instance.

    Returns:
        Multi-line string for terminal display.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  TEAM COLLABORATION HEATMAP")
    lines.append(f"  {report.analyzed_days} days | {report.total_commits} commits | {report.total_authors} authors")
    lines.append("=" * 60)

    # Heatmap grid
    lines.append("\n--- Collaboration Strength Grid ---\n")
    lines.append(_build_heatmap_grid(report.edges))

    # Top edges
    if report.edges:
        lines.append("\n--- Top Collaborations ---\n")
        for e in report.edges[:5]:
            lines.append(
                f"  {e.author_a} <-> {e.author_b}: "
                f"strength={e.strength:.2f}, {len(e.shared_files)} shared files"
            )

    # Hotspots
    if report.hotspots:
        lines.append("\n--- Hotspots (multi-author files) ---\n")
        for h in report.hotspots[:10]:
            lines.append(
                f"  {h.path}: {h.author_count} authors, {h.edit_count} edits"
            )

    # Knowledge silos
    if report.silos:
        lines.append("\n--- Knowledge Silos (bus factor = 1) ---\n")
        for s in report.silos[:10]:
            lines.append(
                f"  [{s.risk_level.upper():>8}] {s.path} -> {s.sole_owner} ({s.commit_count} commits)"
            )

    # Temporal overlap
    if report.temporal_overlaps:
        lines.append("\n--- Work Hour Overlap ---\n")
        for t in report.temporal_overlaps[:5]:
            hours_str = ", ".join(f"{h}:00" for h in t.overlap_hours[:6])
            if len(t.overlap_hours) > 6:
                hours_str += f" (+{len(t.overlap_hours) - 6} more)"
            lines.append(
                f"  {t.author_a} + {t.author_b}: "
                f"{t.overlap_ratio:.0%} overlap [{hours_str}]"
            )

    # Recommendations
    if report.recommendations:
        lines.append("\n--- Recommendations ---\n")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")

    lines.append("")
    return "\n".join(lines)


def format_slack(report: CollaborationReport) -> str:
    """Format report as Slack mrkdwn.

    Uses Slack conventions: *bold* (single asterisk), `code`, no **bold**.

    Args:
        report: CollaborationReport instance.

    Returns:
        Slack mrkdwn formatted string.
    """
    lines = []
    lines.append(
        f"*Team Collaboration Heatmap* | "
        f"{report.analyzed_days}d | {report.total_commits} commits | {report.total_authors} authors"
    )

    # Top collaborations
    if report.edges:
        lines.append("\n*Top Collaborations*")
        for e in report.edges[:5]:
            bar = _strength_bar(e.strength)
            lines.append(f"  {e.author_a} + {e.author_b}: {bar} ({len(e.shared_files)} files)")

    # Knowledge silos
    critical = [s for s in report.silos if s.risk_level in ("critical", "high")]
    if critical:
        lines.append(f"\n*Knowledge Silos* ({len(critical)} high/critical)")
        for s in critical[:5]:
            emoji_map = {"critical": "!!!", "high": "!"}
            marker = emoji_map.get(s.risk_level, "")
            lines.append(f"  {marker} `{s.path}` -> {s.sole_owner} ({s.commit_count} commits)")

    # Hotspots
    if report.hotspots:
        lines.append(f"\n*Hotspots* ({len(report.hotspots)} files)")
        for h in report.hotspots[:5]:
            lines.append(f"  `{h.path}`: {h.author_count} authors, {h.edit_count} edits")

    # Recommendations
    if report.recommendations:
        lines.append("\n*Recommendations*")
        for rec in report.recommendations:
            lines.append(f"  - {rec}")

    return "\n".join(lines)


def _strength_bar(strength: float, width: int = 10) -> str:
    """Render a strength value as an ASCII bar."""
    filled = int(strength * width)
    return "[" + "#" * filled + "." * (width - filled) + "]"


def format_json(report: CollaborationReport) -> str:
    """Format report as full JSON.

    Args:
        report: CollaborationReport instance.

    Returns:
        JSON string.
    """
    data = {
        "analyzed_days": report.analyzed_days,
        "total_commits": report.total_commits,
        "total_authors": report.total_authors,
        "edges": [asdict(e) for e in report.edges],
        "silos": [asdict(s) for s in report.silos],
        "hotspots": [asdict(h) for h in report.hotspots],
        "temporal_overlaps": [asdict(t) for t in report.temporal_overlaps],
        "recommendations": report.recommendations,
    }
    return json.dumps(data, indent=2)


def format_concept_map(report: CollaborationReport) -> dict:
    """Format report as dict for grip_concept_map MCP app.

    Authors are nodes, collaboration strength defines edges.

    Args:
        report: CollaborationReport instance.

    Returns:
        Dict with structure: {title, nodes, edges}.
    """
    # Collect all unique authors
    authors = set()
    for e in report.edges:
        authors.add(e.author_a)
        authors.add(e.author_b)
    # Include silo owners too
    for s in report.silos:
        authors.add(s.sole_owner)

    # Node type based on silo count
    silo_owners = Counter(s.sole_owner for s in report.silos)

    nodes = []
    for author in sorted(authors):
        silo_count = silo_owners.get(author, 0)
        if silo_count >= 5:
            node_type = "risk"
        elif silo_count >= 2:
            node_type = "warning"
        else:
            node_type = "healthy"

        nodes.append({
            "id": author,
            "label": author,
            "type": node_type,
        })

    edges = []
    for e in report.edges:
        style = "solid" if e.strength >= 0.5 else "dashed"
        edges.append({
            "from": e.author_a,
            "to": e.author_b,
            "label": f"{e.strength:.2f} ({len(e.shared_files)} files)",
            "style": style,
        })

    return {
        "title": f"Team Collaboration ({report.analyzed_days}d)",
        "nodes": nodes,
        "edges": edges,
    }


def format_data_table(report: CollaborationReport) -> dict:
    """Format report as dict for grip_data_table MCP app.

    Args:
        report: CollaborationReport instance.

    Returns:
        Dict with structure: {title, columns, rows, searchable, pageSize}.
    """
    columns = [
        {"key": "path", "label": "File Path"},
        {"key": "status", "label": "Status"},
        {"key": "authors", "label": "Authors", "sortable": True},
        {"key": "edits", "label": "Edits", "sortable": True},
        {"key": "owner", "label": "Primary Owner"},
        {"key": "risk", "label": "Risk", "sortable": True},
    ]

    rows = []

    # Hotspots as "shared" rows
    for h in report.hotspots:
        rows.append({
            "path": h.path,
            "status": "hotspot",
            "authors": h.author_count,
            "edits": h.edit_count,
            "owner": ", ".join(h.authors[:3]),
            "risk": "low",
        })

    # Silos as "silo" rows
    for s in report.silos:
        rows.append({
            "path": s.path,
            "status": "silo",
            "authors": 1,
            "edits": s.commit_count,
            "owner": s.sole_owner,
            "risk": s.risk_level,
        })

    # Sort: critical first, then high, etc.
    risk_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows.sort(key=lambda r: risk_order.get(r["risk"], 3))

    return {
        "title": f"File Collaboration Matrix ({report.analyzed_days}d)",
        "columns": columns,
        "rows": rows,
        "searchable": True,
        "pageSize": 25,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Team Collaboration Heatmap Generator"
    )
    parser.add_argument("command", nargs="?", default="analyze",
                        choices=["analyze", "silos", "hotspots", "edges"],
                        help="What to analyze")
    parser.add_argument("--days", type=int, default=30, help="Days of history")
    parser.add_argument(
        "--format",
        choices=["text", "slack", "json", "concept_map", "data_table"],
        default="text",
        help="Output format",
    )

    args = parser.parse_args()

    report = analyze_collaboration(days=args.days)

    formatters = {
        "text": format_text,
        "slack": format_slack,
        "json": format_json,
        "concept_map": lambda r: json.dumps(format_concept_map(r), indent=2),
        "data_table": lambda r: json.dumps(format_data_table(r), indent=2),
    }

    formatter = formatters[args.format]

    if args.command == "silos":
        # Filter report to just silos
        filtered = CollaborationReport(
            silos=report.silos,
            analyzed_days=report.analyzed_days,
            total_commits=report.total_commits,
            total_authors=report.total_authors,
        )
        filtered.recommendations = generate_recommendations(filtered)
        print(formatter(filtered))
    elif args.command == "hotspots":
        filtered = CollaborationReport(
            hotspots=report.hotspots,
            analyzed_days=report.analyzed_days,
            total_commits=report.total_commits,
            total_authors=report.total_authors,
        )
        print(formatter(filtered))
    elif args.command == "edges":
        filtered = CollaborationReport(
            edges=report.edges,
            analyzed_days=report.analyzed_days,
            total_commits=report.total_commits,
            total_authors=report.total_authors,
        )
        print(formatter(filtered))
    else:
        print(formatter(report))


if __name__ == "__main__":
    main()
