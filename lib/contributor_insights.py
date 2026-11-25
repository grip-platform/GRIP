#!/usr/bin/env python3
"""Contributor Insights -- analyze git history for expertise, bus factor, and collaboration.

Surfaces contributor patterns, expertise mapping, bus factor analysis, and
collaboration insights to help teams understand who knows what code.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Contributor:
    name: str
    email: str
    commits: int = 0
    files_touched: int = 0
    first_commit: str = ""
    last_commit: str = ""
    streak_days: int = 0
    top_areas: list[str] = field(default_factory=list)


@dataclass
class ExpertiseArea:
    path_pattern: str
    primary_owner: str = ""
    contributors: list[str] = field(default_factory=list)
    ownership_pct: float = 0.0
    bus_factor: int = 0


@dataclass
class CollaborationEdge:
    author_a: str
    author_b: str
    shared_files: int = 0
    shared_commits: int = 0
    strength: float = 0.0


@dataclass
class ContributorReport:
    contributors: list[Contributor] = field(default_factory=list)
    expertise_areas: list[ExpertiseArea] = field(default_factory=list)
    collaborations: list[CollaborationEdge] = field(default_factory=list)
    bus_factor_score: float = 0.0
    recommendations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

def parse_git_log(log_lines: list[str]) -> list[dict]:
    """Parse git log --format='%H|%an|%ae|%aI|%s' --numstat output.

    Returns a list of commit dicts with keys:
        hash, author, email, date, message, files (list of {additions, deletions, path})
    """
    commits: list[dict] = []
    current: Optional[dict] = None

    for raw_line in log_lines:
        line = raw_line.rstrip("\n")

        # Skip blank lines between commits
        if not line.strip():
            continue

        # Check if this is a header line (5 pipe-separated fields)
        parts = line.split("|", 4)
        if len(parts) == 5 and _looks_like_hash(parts[0].strip()):
            # Save previous commit
            if current is not None:
                commits.append(current)
            current = {
                "hash": parts[0].strip(),
                "author": parts[1].strip(),
                "email": parts[2].strip(),
                "date": parts[3].strip(),
                "message": parts[4].strip(),
                "files": [],
            }
        elif current is not None:
            # numstat line: additions\tdeletions\tpath
            numstat_parts = line.split("\t")
            if len(numstat_parts) >= 3:
                adds = numstat_parts[0].strip()
                dels = numstat_parts[1].strip()
                path = numstat_parts[2].strip()
                if path:
                    current["files"].append({
                        "additions": _safe_int(adds),
                        "deletions": _safe_int(dels),
                        "path": path,
                    })

    # Don't forget the last commit
    if current is not None:
        commits.append(current)

    return commits


def _looks_like_hash(s: str) -> bool:
    """Check if string looks like a git hash (hex chars, 7-40 length)."""
    if len(s) < 7 or len(s) > 40:
        return False
    return all(c in "0123456789abcdef" for c in s)


def _safe_int(s: str) -> int:
    """Convert to int, returning 0 for binary file markers (-)."""
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Contributor analysis
# ---------------------------------------------------------------------------

def analyze_contributors(commits: list[dict]) -> list[Contributor]:
    """Analyze commits to build contributor profiles."""
    authors: dict[str, dict] = {}

    for commit in commits:
        name = commit.get("author", "Unknown")
        email = commit.get("email", "")
        date_str = commit.get("date", "")
        files = commit.get("files", [])

        key = name
        if key not in authors:
            authors[key] = {
                "name": name,
                "email": email,
                "commit_count": 0,
                "files_set": set(),
                "dates": [],
                "areas": Counter(),
            }

        info = authors[key]
        info["commit_count"] += 1

        if date_str:
            info["dates"].append(date_str)

        for f in files:
            path = f.get("path", "")
            if path:
                info["files_set"].add(path)
                area = _extract_area(path)
                if area:
                    info["areas"][area] += 1

    result: list[Contributor] = []
    for key, info in authors.items():
        dates_sorted = sorted(info["dates"])
        first = dates_sorted[0] if dates_sorted else ""
        last = dates_sorted[-1] if dates_sorted else ""

        # Parse dates for streak calculation
        parsed_dates = _parse_date_strings(dates_sorted)
        streak = _calculate_streak(parsed_dates)

        top = [area for area, _ in info["areas"].most_common(5)]

        result.append(Contributor(
            name=info["name"],
            email=info["email"],
            commits=info["commit_count"],
            files_touched=len(info["files_set"]),
            first_commit=first,
            last_commit=last,
            streak_days=streak,
            top_areas=top,
        ))

    # Sort by commits descending
    result.sort(key=lambda c: c.commits, reverse=True)
    return result


def _extract_area(path: str) -> str:
    """Extract the top-level directory from a file path."""
    parts = Path(path).parts
    if len(parts) >= 2:
        return parts[0]
    return ""


def _parse_date_strings(date_strings: list[str]) -> list[date]:
    """Parse ISO date strings to date objects, deduplicating."""
    dates: set[date] = set()
    for ds in date_strings:
        try:
            # Handle ISO format with timezone: 2024-01-15T10:30:00+02:00
            dt = datetime.fromisoformat(ds.replace("Z", "+00:00"))
            dates.add(dt.date())
        except (ValueError, TypeError):
            # Try plain date
            try:
                dates.add(date.fromisoformat(ds[:10]))
            except (ValueError, TypeError):
                continue
    return sorted(dates)


# ---------------------------------------------------------------------------
# Streak calculation
# ---------------------------------------------------------------------------

def _calculate_streak(dates: list[date]) -> int:
    """Calculate the longest consecutive-day commit streak.

    Args:
        dates: Sorted list of unique dates with commits.

    Returns:
        Longest streak in days.
    """
    if not dates:
        return 0
    if len(dates) == 1:
        return 1

    longest = 1
    current = 1

    for i in range(1, len(dates)):
        diff = (dates[i] - dates[i - 1]).days
        if diff == 1:
            current += 1
            longest = max(longest, current)
        elif diff > 1:
            current = 1

    return longest


def calculate_current_streak(dates: list[date], today: Optional[date] = None) -> int:
    """Calculate the current active streak (ending today or yesterday).

    Args:
        dates: Sorted list of unique dates with commits.
        today: Override for testing. Defaults to date.today().

    Returns:
        Current streak in days, or 0 if no recent commits.
    """
    if not dates:
        return 0

    if today is None:
        today = date.today()

    # Must have committed today or yesterday to have a current streak
    last = dates[-1]
    gap = (today - last).days
    if gap > 1:
        return 0

    streak = 1
    for i in range(len(dates) - 2, -1, -1):
        diff = (dates[i + 1] - dates[i]).days
        if diff == 1:
            streak += 1
        else:
            break

    return streak


# ---------------------------------------------------------------------------
# Expertise analysis
# ---------------------------------------------------------------------------

def analyze_expertise(commits: list[dict], min_commits: int = 3) -> list[ExpertiseArea]:
    """Analyze commits to map code ownership by directory.

    Args:
        commits: List of commit dicts from parse_git_log.
        min_commits: Minimum commits to an area for it to be included.

    Returns:
        List of ExpertiseArea sorted by path.
    """
    # area -> {author -> commit_count}
    area_authors: dict[str, Counter] = defaultdict(Counter)

    for commit in commits:
        author = commit.get("author", "Unknown")
        files = commit.get("files", [])
        areas_in_commit: set[str] = set()

        for f in files:
            path = f.get("path", "")
            area = _extract_area(path)
            if area:
                areas_in_commit.add(area)

        for area in areas_in_commit:
            area_authors[area][author] += 1

    result: list[ExpertiseArea] = []
    for area, author_counts in sorted(area_authors.items()):
        total = sum(author_counts.values())
        if total < min_commits:
            continue

        primary = author_counts.most_common(1)[0]
        primary_name = primary[0]
        primary_pct = (primary[1] / total) * 100.0

        contributors = [name for name, _ in author_counts.most_common()]

        # Bus factor: count contributors with >= 20% ownership
        bf = sum(1 for name, count in author_counts.items()
                 if (count / total) >= 0.20)

        result.append(ExpertiseArea(
            path_pattern=area,
            primary_owner=primary_name,
            contributors=contributors,
            ownership_pct=round(primary_pct, 1),
            bus_factor=bf,
        ))

    return result


# ---------------------------------------------------------------------------
# Collaboration analysis
# ---------------------------------------------------------------------------

def analyze_collaboration(commits: list[dict]) -> list[CollaborationEdge]:
    """Analyze commits to find collaboration patterns.

    Two authors collaborate when they both edit the same files.

    Returns:
        List of CollaborationEdge sorted by strength descending.
    """
    # file -> set of authors who touched it
    file_authors: dict[str, set[str]] = defaultdict(set)
    # author -> set of files
    author_files: dict[str, set[str]] = defaultdict(set)

    for commit in commits:
        author = commit.get("author", "Unknown")
        for f in commit.get("files", []):
            path = f.get("path", "")
            if path:
                file_authors[path].add(author)
                author_files[author].add(path)

    # Build pair -> shared files
    pair_shared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path, authors in file_authors.items():
        authors_list = sorted(authors)
        for i in range(len(authors_list)):
            for j in range(i + 1, len(authors_list)):
                pair = (authors_list[i], authors_list[j])
                pair_shared[pair].add(path)

    # Count shared commits (commits where both authors touched at least one common file)
    # A "shared commit" context: count commits per file per author
    file_author_commits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for commit in commits:
        author = commit.get("author", "Unknown")
        for f in commit.get("files", []):
            path = f.get("path", "")
            if path:
                file_author_commits[path][author] += 1

    result: list[CollaborationEdge] = []
    for (a, b), shared_files in pair_shared.items():
        shared_file_count = len(shared_files)

        # Shared commits: sum of min(commits_a, commits_b) per shared file
        shared_commit_count = 0
        for f in shared_files:
            ca = file_author_commits[f].get(a, 0)
            cb = file_author_commits[f].get(b, 0)
            shared_commit_count += min(ca, cb)

        # Strength: based on shared files relative to total files of each author
        total_files_a = len(author_files.get(a, set()))
        total_files_b = len(author_files.get(b, set()))
        if total_files_a == 0 or total_files_b == 0:
            strength = 0.0
        else:
            # Jaccard similarity of file sets
            union = len(author_files[a] | author_files[b])
            intersection = shared_file_count
            strength = intersection / union if union > 0 else 0.0

        strength = round(min(strength, 1.0), 3)

        result.append(CollaborationEdge(
            author_a=a,
            author_b=b,
            shared_files=shared_file_count,
            shared_commits=shared_commit_count,
            strength=strength,
        ))

    result.sort(key=lambda e: e.strength, reverse=True)
    return result


# ---------------------------------------------------------------------------
# Bus factor calculation
# ---------------------------------------------------------------------------

def calculate_bus_factor(expertise_areas: list[ExpertiseArea]) -> float:
    """Calculate overall bus factor score on 0-10 scale.

    For each area, count contributors with >= 20% ownership.
    Score = average contributors per area, capped at 10.
    1 = single point of failure, 10 = well-distributed.
    """
    if not expertise_areas:
        return 0.0

    total_bf = sum(area.bus_factor for area in expertise_areas)
    avg = total_bf / len(expertise_areas)

    # Scale to 0-10: 1 contributor = 1.0, 5+ = 10.0
    if avg <= 1.0:
        score = avg
    else:
        # Linear scale: 1->1, 5->10
        score = 1.0 + (avg - 1.0) * (9.0 / 4.0)

    return round(min(max(score, 0.0), 10.0), 1)


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def _generate_recommendations(
    contributors: list[Contributor],
    expertise_areas: list[ExpertiseArea],
    collaborations: list[CollaborationEdge],
    bus_factor_score: float,
) -> list[str]:
    """Generate actionable recommendations from analysis."""
    recs: list[str] = []

    # Bus factor warnings
    if bus_factor_score < 3.0:
        recs.append(
            "CRITICAL: Bus factor is very low ({:.1f}/10). "
            "Knowledge is concentrated in too few people.".format(bus_factor_score)
        )

    # Single-owner areas
    solo_areas = [a for a in expertise_areas if a.bus_factor <= 1]
    if solo_areas:
        names = ", ".join(a.path_pattern for a in solo_areas[:5])
        recs.append(
            "Single-owner risk: {} {} only one knowledgeable contributor. "
            "Consider pair programming or code reviews.".format(
                names, "has" if len(solo_areas) == 1 else "have"
            )
        )

    # Dominant contributor
    if contributors:
        total_commits = sum(c.commits for c in contributors)
        top = contributors[0]
        if total_commits > 0:
            pct = (top.commits / total_commits) * 100
            if pct > 60:
                recs.append(
                    "{} accounts for {:.0f}% of all commits. "
                    "Consider distributing work more evenly.".format(top.name, pct)
                )

    # Low collaboration
    if not collaborations or all(e.strength < 0.1 for e in collaborations):
        recs.append(
            "Low collaboration detected. Team members work in silos. "
            "Introduce cross-functional code reviews."
        )

    # Inactive contributors (no commits in last 30 days relative to dataset)
    if contributors and len(contributors) > 1:
        active_dates = []
        for c in contributors:
            if c.last_commit:
                try:
                    dt = datetime.fromisoformat(c.last_commit.replace("Z", "+00:00"))
                    active_dates.append(dt)
                except (ValueError, TypeError):
                    pass
        if active_dates:
            latest = max(active_dates)
            threshold = latest - timedelta(days=30)
            inactive = [
                c.name for c in contributors
                if c.last_commit and _parse_single_date(c.last_commit) and
                datetime.fromisoformat(c.last_commit.replace("Z", "+00:00")) < threshold
            ]
            if inactive:
                recs.append(
                    "Inactive contributors (30+ days): {}. "
                    "Check if they need onboarding support.".format(", ".join(inactive[:5]))
                )

    if not recs:
        recs.append("Team knowledge distribution looks healthy. Keep it up.")

    return recs


def _parse_single_date(s: str) -> Optional[date]:
    """Parse a single date string, returning None on failure."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_from_data(commits: list[dict], min_commits: int = 3) -> ContributorReport:
    """Generate a contributor report from pre-parsed commit data.

    Args:
        commits: List of commit dicts with keys: hash, author, email, date, message, files.
        min_commits: Minimum commits per area for expertise analysis.

    Returns:
        ContributorReport with all analysis completed.
    """
    contributors = analyze_contributors(commits)
    expertise_areas = analyze_expertise(commits, min_commits=min_commits)
    collaborations = analyze_collaboration(commits)
    bus_factor_score = calculate_bus_factor(expertise_areas)
    recommendations = _generate_recommendations(
        contributors, expertise_areas, collaborations, bus_factor_score
    )

    return ContributorReport(
        contributors=contributors,
        expertise_areas=expertise_areas,
        collaborations=collaborations,
        bus_factor_score=bus_factor_score,
        recommendations=recommendations,
    )


def generate_insights(
    path: str = ".",
    since: Optional[str] = None,
    until: Optional[str] = None,
    min_commits: int = 3,
) -> ContributorReport:
    """Generate contributor insights from a git repository.

    Args:
        path: Path to the git repository.
        since: Start date (e.g., '2024-01-01').
        until: End date (e.g., '2024-12-31').
        min_commits: Minimum commits per area for expertise analysis.

    Returns:
        ContributorReport with full analysis.
    """
    cmd = [
        "git", "-C", path, "log",
        "--format=%H|%an|%ae|%aI|%s",
        "--numstat",
    ]
    if since:
        cmd.append("--since={}".format(since))
    if until:
        cmd.append("--until={}".format(until))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError("git log failed: {}".format(result.stderr.strip()))

    log_lines = result.stdout.splitlines()
    commits = parse_git_log(log_lines)
    return generate_from_data(commits, min_commits=min_commits)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_insights_text(report: ContributorReport) -> str:
    """Format report as terminal-friendly text with tables."""
    lines: list[str] = []

    lines.append("=" * 70)
    lines.append("CONTRIBUTOR INSIGHTS REPORT")
    lines.append("=" * 70)
    lines.append("")

    # Contributors table
    lines.append("CONTRIBUTORS ({})".format(len(report.contributors)))
    lines.append("-" * 70)
    lines.append("{:<20} {:>7} {:>7} {:>6} {}".format(
        "Name", "Commits", "Files", "Streak", "Top Areas"
    ))
    lines.append("-" * 70)

    for c in report.contributors:
        areas_str = ", ".join(c.top_areas[:3]) if c.top_areas else "-"
        lines.append("{:<20} {:>7} {:>7} {:>5}d {}".format(
            c.name[:20], c.commits, c.files_touched, c.streak_days, areas_str
        ))

    lines.append("")

    # Expertise areas
    lines.append("EXPERTISE AREAS ({})".format(len(report.expertise_areas)))
    lines.append("-" * 70)
    lines.append("{:<20} {:<15} {:>8} {:>4} {}".format(
        "Area", "Owner", "Owner %", "BF", "Contributors"
    ))
    lines.append("-" * 70)

    for e in report.expertise_areas:
        contribs_str = ", ".join(e.contributors[:3])
        lines.append("{:<20} {:<15} {:>7.1f}% {:>4} {}".format(
            e.path_pattern[:20], e.primary_owner[:15],
            e.ownership_pct, e.bus_factor, contribs_str
        ))

    lines.append("")

    # Collaboration
    if report.collaborations:
        lines.append("COLLABORATION ({})".format(len(report.collaborations)))
        lines.append("-" * 70)
        lines.append("{:<15} {:<15} {:>7} {:>7} {:>8}".format(
            "Author A", "Author B", "Files", "Commits", "Strength"
        ))
        lines.append("-" * 70)

        for edge in report.collaborations[:10]:
            lines.append("{:<15} {:<15} {:>7} {:>7} {:>7.3f}".format(
                edge.author_a[:15], edge.author_b[:15],
                edge.shared_files, edge.shared_commits, edge.strength
            ))

        lines.append("")

    # Bus factor
    lines.append("BUS FACTOR SCORE: {:.1f}/10".format(report.bus_factor_score))
    lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 70)
        for i, rec in enumerate(report.recommendations, 1):
            lines.append("{}. {}".format(i, rec))
        lines.append("")

    return "\n".join(lines)


def format_insights_slack(report: ContributorReport) -> str:
    """Format report as Slack mrkdwn (*bold* not **bold**)."""
    lines: list[str] = []

    lines.append("*Contributor Insights Report*")
    lines.append("")

    # Contributors
    lines.append("*Contributors* ({})".format(len(report.contributors)))
    for c in report.contributors:
        areas = ", ".join(c.top_areas[:3]) if c.top_areas else "general"
        lines.append(
            "- *{}*: {} commits, {} files, {}d streak | _{}_".format(
                c.name, c.commits, c.files_touched, c.streak_days, areas
            )
        )

    lines.append("")

    # Expertise
    lines.append("*Expertise Areas* ({})".format(len(report.expertise_areas)))
    for e in report.expertise_areas:
        lines.append(
            "- `{}` owned by *{}* ({:.0f}%) | Bus factor: {}".format(
                e.path_pattern, e.primary_owner, e.ownership_pct, e.bus_factor
            )
        )

    lines.append("")

    # Collaboration
    if report.collaborations:
        lines.append("*Top Collaborations*")
        for edge in report.collaborations[:5]:
            lines.append(
                "- *{}* + *{}*: {} shared files (strength: {:.2f})".format(
                    edge.author_a, edge.author_b, edge.shared_files, edge.strength
                )
            )
        lines.append("")

    # Bus factor
    emoji = ":white_check_mark:" if report.bus_factor_score >= 5 else ":warning:"
    lines.append(
        "{} *Bus Factor*: {:.1f}/10".format(emoji, report.bus_factor_score)
    )
    lines.append("")

    # Recommendations
    if report.recommendations:
        lines.append("*Recommendations*")
        for rec in report.recommendations:
            lines.append("- {}".format(rec))

    return "\n".join(lines)


def format_insights_json(report: ContributorReport) -> str:
    """Format report as full JSON."""

    def _serialize(obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        if isinstance(obj, set):
            return sorted(obj)
        return str(obj)

    return json.dumps(asdict(report), indent=2, default=_serialize)


def format_insights_concept_map(report: ContributorReport) -> dict:
    """Format report as a dict for grip_concept_map.

    Contributors as nodes, collaboration as edges.
    Expertise areas as typed nodes connected to their contributors.
    """
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[str] = set()

    # Add contributor nodes
    for c in report.contributors:
        node_id = "contributor-{}".format(c.name.lower().replace(" ", "-"))
        if node_id not in seen_nodes:
            nodes.append({
                "id": node_id,
                "label": "{} ({}c)".format(c.name, c.commits),
                "type": "contributor",
            })
            seen_nodes.add(node_id)

    # Add expertise area nodes
    for e in report.expertise_areas:
        node_id = "area-{}".format(e.path_pattern.lower().replace(" ", "-"))
        if node_id not in seen_nodes:
            nodes.append({
                "id": node_id,
                "label": e.path_pattern,
                "type": "area",
            })
            seen_nodes.add(node_id)

        # Connect primary owner to area
        owner_id = "contributor-{}".format(e.primary_owner.lower().replace(" ", "-"))
        if owner_id in seen_nodes:
            edges.append({
                "from": owner_id,
                "to": node_id,
                "label": "owns ({:.0f}%)".format(e.ownership_pct),
                "style": "solid",
            })

    # Add collaboration edges
    for edge in report.collaborations:
        a_id = "contributor-{}".format(edge.author_a.lower().replace(" ", "-"))
        b_id = "contributor-{}".format(edge.author_b.lower().replace(" ", "-"))
        if a_id in seen_nodes and b_id in seen_nodes and edge.strength > 0.05:
            style = "solid" if edge.strength > 0.3 else "dashed"
            edges.append({
                "from": a_id,
                "to": b_id,
                "label": "{:.0f}% collab".format(edge.strength * 100),
                "style": style,
            })

    return {
        "title": "Contributor Knowledge Map",
        "nodes": nodes,
        "edges": edges,
    }


def format_insights_data_table(report: ContributorReport) -> dict:
    """Format report as a dict for grip_data_table (sortable contributor table)."""
    columns = [
        {"key": "name", "label": "Contributor", "sortable": True},
        {"key": "commits", "label": "Commits", "sortable": True},
        {"key": "files", "label": "Files Touched", "sortable": True},
        {"key": "streak", "label": "Streak (days)", "sortable": True},
        {"key": "top_areas", "label": "Top Areas", "sortable": False},
        {"key": "first_commit", "label": "First Commit", "sortable": True},
        {"key": "last_commit", "label": "Last Commit", "sortable": True},
    ]

    rows: list[dict] = []
    for c in report.contributors:
        rows.append({
            "name": c.name,
            "commits": c.commits,
            "files": c.files_touched,
            "streak": c.streak_days,
            "top_areas": ", ".join(c.top_areas[:3]) if c.top_areas else "-",
            "first_commit": c.first_commit[:10] if c.first_commit else "-",
            "last_commit": c.last_commit[:10] if c.last_commit else "-",
        })

    return {
        "title": "Contributor Insights",
        "columns": columns,
        "rows": rows,
        "searchable": True,
        "pageSize": 20,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Contributor Insights - analyze git history for expertise and collaboration"
    )
    parser.add_argument("path", nargs="?", default=".", help="Path to git repository")
    parser.add_argument("--since", default=None, help="Start date (e.g., 2024-01-01)")
    parser.add_argument("--until", default=None, help="End date (e.g., 2024-12-31)")
    parser.add_argument(
        "--format", dest="fmt", default="text",
        choices=["text", "slack", "json", "concept_map", "data_table"],
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--min-commits", type=int, default=3,
        help="Minimum commits per area for expertise analysis (default: 3)"
    )

    args = parser.parse_args()

    try:
        report = generate_insights(
            path=args.path,
            since=args.since,
            until=args.until,
            min_commits=args.min_commits,
        )
    except RuntimeError as e:
        print("Error: {}".format(e), file=sys.stderr)
        sys.exit(1)

    formatters = {
        "text": format_insights_text,
        "slack": format_insights_slack,
        "json": format_insights_json,
        "concept_map": lambda r: json.dumps(format_insights_concept_map(r), indent=2),
        "data_table": lambda r: json.dumps(format_insights_data_table(r), indent=2),
    }

    output = formatters[args.fmt](report)
    print(output)


if __name__ == "__main__":
    main()
