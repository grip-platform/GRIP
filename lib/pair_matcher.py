"""Pair Matcher: Analyze git history to suggest optimal pair programming partnerships.

Builds developer expertise profiles from git log output and uses multiple matching
algorithms (complementary, knowledge transfer, shared context, mentoring) to
suggest high-value pairings.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DeveloperProfile:
    """Expertise profile derived from git history."""

    name: str
    email: str
    expertise_areas: dict[str, float] = field(default_factory=dict)
    languages: dict[str, float] = field(default_factory=dict)
    active_directories: set[str] = field(default_factory=set)
    commit_count: int = 0
    last_active: Optional[datetime] = None
    files_touched: set[str] = field(default_factory=set)

    def seniority_score(self) -> float:
        """Rough seniority estimate: breadth of expertise * commit volume."""
        if not self.expertise_areas:
            return 0.0
        breadth = len(self.expertise_areas)
        depth = sum(self.expertise_areas.values()) / breadth if breadth else 0
        volume = math.log(self.commit_count + 1)
        return breadth * depth * volume


@dataclass
class PairingReason:
    """One reason two developers should pair."""

    reason_type: str  # complementary | knowledge_transfer | shared_context | mentoring
    description: str
    score: float


@dataclass
class PairSuggestion:
    """A suggested pairing between two developers."""

    dev1: str
    dev2: str
    reasons: list[PairingReason] = field(default_factory=list)
    overall_score: float = 0.0
    suggested_task_area: str = ""


@dataclass
class PairingReport:
    """Full report of pair suggestions for a team."""

    suggestions: list[PairSuggestion] = field(default_factory=list)
    team_size: int = 0
    analysis_period_days: int = 90
    knowledge_gaps: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Extension -> language mapping
# ---------------------------------------------------------------------------

EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zsh": "Shell",
    ".c": "C",
    ".cpp": "C++",
    ".h": "C",
    ".hpp": "C++",
    ".cs": "C#",
    ".swift": "Swift",
    ".md": "Markdown",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".json": "JSON",
    ".toml": "TOML",
    ".sql": "SQL",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".lua": "Lua",
    ".r": "R",
    ".R": "R",
    ".pl": "Perl",
    ".php": "PHP",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".erl": "Erlang",
    ".hs": "Haskell",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
}


# ---------------------------------------------------------------------------
# Git log parsing
# ---------------------------------------------------------------------------

_COMMIT_SEPARATOR = "---COMMIT---"
_FIELD_SEPARATOR = "---FIELD---"

GIT_LOG_FORMAT = f"{_FIELD_SEPARATOR}%H{_FIELD_SEPARATOR}%an{_FIELD_SEPARATOR}%ae{_FIELD_SEPARATOR}%aI{_FIELD_SEPARATOR}"


def _parse_commit_block(block: str) -> Optional[dict]:
    """Parse a single commit block into structured data."""
    parts = block.split(_FIELD_SEPARATOR)
    # Expected: ['', hash, name, email, date, '\nfile1\nfile2\n...']
    if len(parts) < 5:
        return None
    commit_hash = parts[1].strip()
    name = parts[2].strip()
    email = parts[3].strip()
    date_str = parts[4].strip()
    files_section = parts[5] if len(parts) > 5 else ""
    files = [f.strip() for f in files_section.strip().split("\n") if f.strip()]

    if not name or not commit_hash:
        return None

    try:
        commit_date = datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        commit_date = None

    return {
        "hash": commit_hash,
        "name": name,
        "email": email,
        "date": commit_date,
        "files": files,
    }


def _extract_area(filepath: str) -> str:
    """Extract the top-level expertise area from a file path.

    Examples:
        lib/copilot/workflow_runner.py -> lib/copilot
        tests/test_foo.py -> tests
        src/components/Button.tsx -> src/components
    """
    parts = filepath.split("/")
    if len(parts) >= 3:
        return "/".join(parts[:2])
    elif len(parts) == 2:
        return parts[0]
    return "root"


def _extract_language(filepath: str) -> Optional[str]:
    """Map a file path to a programming language."""
    _, ext = os.path.splitext(filepath)
    return EXTENSION_LANGUAGE_MAP.get(ext)


def _recency_weight(commit_date: Optional[datetime], cutoff: datetime) -> float:
    """Weight commits more heavily if recent. Returns 0.5-1.0."""
    if commit_date is None:
        return 0.5
    # Make cutoff timezone-aware if commit_date is
    if commit_date.tzinfo is not None and cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    elif commit_date.tzinfo is None and cutoff.tzinfo is not None:
        cutoff = cutoff.replace(tzinfo=None)
    # Days since cutoff start (0 = oldest, max = newest)
    now = datetime.now(timezone.utc) if commit_date.tzinfo else datetime.now()
    total_span = max((now - cutoff).total_seconds(), 1)
    elapsed = max((commit_date - cutoff).total_seconds(), 0)
    ratio = elapsed / total_span  # 0 = old, 1 = new
    return 0.5 + 0.5 * ratio


def parse_git_log_for_profiles(
    git_log_text: str, days: int = 90
) -> list[DeveloperProfile]:
    """Parse raw git log text into developer profiles.

    Args:
        git_log_text: Output from git log with GIT_LOG_FORMAT and --name-only.
        days: Analysis window in days.

    Returns:
        List of DeveloperProfile sorted by commit count descending.
    """
    if not git_log_text.strip():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    profiles_map: dict[str, DeveloperProfile] = {}
    commits = git_log_text.split(_COMMIT_SEPARATOR)

    for block in commits:
        parsed = _parse_commit_block(block)
        if parsed is None:
            continue

        key = parsed["email"] or parsed["name"]
        if key not in profiles_map:
            profiles_map[key] = DeveloperProfile(
                name=parsed["name"], email=parsed["email"]
            )
        profile = profiles_map[key]
        profile.commit_count += 1
        weight = _recency_weight(parsed["date"], cutoff)

        if parsed["date"] is not None:
            if profile.last_active is None or parsed["date"] > profile.last_active:
                profile.last_active = parsed["date"]

        for filepath in parsed["files"]:
            profile.files_touched.add(filepath)
            area = _extract_area(filepath)
            profile.expertise_areas[area] = (
                profile.expertise_areas.get(area, 0) + weight
            )
            lang = _extract_language(filepath)
            if lang:
                profile.languages[lang] = profile.languages.get(lang, 0) + weight

            directory = "/".join(filepath.split("/")[:-1]) if "/" in filepath else "root"
            profile.active_directories.add(directory)

    result = sorted(profiles_map.values(), key=lambda p: p.commit_count, reverse=True)
    return result


# ---------------------------------------------------------------------------
# Matching algorithms
# ---------------------------------------------------------------------------

def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity coefficient between two sets."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _cosine_similarity(dict_a: dict[str, float], dict_b: dict[str, float]) -> float:
    """Cosine similarity between two score dictionaries."""
    all_keys = set(dict_a) | set(dict_b)
    if not all_keys:
        return 0.0
    dot = sum(dict_a.get(k, 0) * dict_b.get(k, 0) for k in all_keys)
    mag_a = math.sqrt(sum(v * v for v in dict_a.values())) or 1e-9
    mag_b = math.sqrt(sum(v * v for v in dict_b.values())) or 1e-9
    return dot / (mag_a * mag_b)


def complementary_match(dev1: DeveloperProfile, dev2: DeveloperProfile) -> float:
    """Score how complementary two developers' expertise is.

    High score when developers have different but adjacent expertise areas.
    Adjacent = they share some directories/languages but focus on different areas.
    """
    areas1 = set(dev1.expertise_areas)
    areas2 = set(dev2.expertise_areas)

    if not areas1 or not areas2:
        return 0.0

    # Unique areas each brings
    unique_to_1 = areas1 - areas2
    unique_to_2 = areas2 - areas1
    shared = areas1 & areas2

    if not unique_to_1 and not unique_to_2:
        return 0.0  # Identical expertise, not complementary

    # Complementarity: ratio of unique areas, boosted by some shared context
    total = len(areas1 | areas2)
    unique_ratio = (len(unique_to_1) + len(unique_to_2)) / total
    adjacency_bonus = min(len(shared) / total, 0.3)  # Cap at 0.3

    # Language overlap adds adjacency
    lang_overlap = _jaccard_similarity(set(dev1.languages), set(dev2.languages))
    lang_bonus = lang_overlap * 0.2

    score = unique_ratio * 0.7 + adjacency_bonus + lang_bonus
    return min(score, 1.0)


def knowledge_transfer_match(dev1: DeveloperProfile, dev2: DeveloperProfile) -> float:
    """Score knowledge transfer potential between two developers.

    High score when one developer is expert in an area the other is weak in,
    but the weaker developer has touched adjacent areas (receptive to learning).
    """
    areas1 = set(dev1.expertise_areas)
    areas2 = set(dev2.expertise_areas)

    if not areas1 or not areas2:
        return 0.0

    # Find areas where one is strong and the other is absent
    only_in_1 = areas1 - areas2
    only_in_2 = areas2 - areas1
    transferable = len(only_in_1) + len(only_in_2)

    if transferable == 0:
        return 0.0

    total = len(areas1 | areas2)
    transfer_ratio = transferable / total

    # Receptivity: do they share some language/directory context?
    dir_overlap = _jaccard_similarity(dev1.active_directories, dev2.active_directories)
    lang_overlap = _jaccard_similarity(set(dev1.languages), set(dev2.languages))
    receptivity = (dir_overlap + lang_overlap) / 2

    score = transfer_ratio * 0.6 + receptivity * 0.4
    return min(score, 1.0)


def shared_context_match(dev1: DeveloperProfile, dev2: DeveloperProfile) -> float:
    """Score shared context between two developers.

    High score when developers work on related files and modules, making
    pairing productive for code review, debugging, and feature work.
    """
    file_overlap = _jaccard_similarity(dev1.files_touched, dev2.files_touched)
    dir_overlap = _jaccard_similarity(dev1.active_directories, dev2.active_directories)
    area_similarity = _cosine_similarity(dev1.expertise_areas, dev2.expertise_areas)

    score = file_overlap * 0.3 + dir_overlap * 0.3 + area_similarity * 0.4
    return min(score, 1.0)


def mentoring_match(dev1: DeveloperProfile, dev2: DeveloperProfile) -> float:
    """Score mentoring potential between two developers.

    High when one developer has broad deep expertise (senior) and the other
    has narrow shallow expertise (junior). The pair benefits from guided work.
    """
    s1 = dev1.seniority_score()
    s2 = dev2.seniority_score()

    if s1 == 0 or s2 == 0:
        return 0.0

    max_s = max(s1, s2)
    min_s = min(s1, s2)

    # Ratio of seniority gap
    gap_ratio = (max_s - min_s) / max_s

    # Too large a gap (>0.95) means almost no shared context for mentoring
    if gap_ratio > 0.95:
        return gap_ratio * 0.3  # Still some value, but diminished

    # Sweet spot: 0.3-0.8 gap
    if 0.3 <= gap_ratio <= 0.8:
        base = 0.8
    elif gap_ratio < 0.3:
        base = gap_ratio  # Too similar, low mentoring value
    else:
        base = 0.6  # Large gap, moderate value

    # Bonus if senior covers areas junior is in (can actually mentor)
    senior = dev1 if s1 >= s2 else dev2
    junior = dev2 if s1 >= s2 else dev1
    overlap = set(senior.expertise_areas) & set(junior.expertise_areas)
    if junior.expertise_areas:
        coverage = len(overlap) / len(junior.expertise_areas)
    else:
        coverage = 0.0

    score = base * 0.7 + coverage * 0.3
    return min(score, 1.0)


def _find_best_task_area(dev1: DeveloperProfile, dev2: DeveloperProfile) -> str:
    """Identify the best area for the pair to work on together."""
    # Shared areas weighted by combined expertise
    shared_areas: dict[str, float] = {}
    for area in set(dev1.expertise_areas) | set(dev2.expertise_areas):
        s1 = dev1.expertise_areas.get(area, 0)
        s2 = dev2.expertise_areas.get(area, 0)
        # Prefer areas where both have some expertise but one has more
        if s1 > 0 and s2 > 0:
            shared_areas[area] = s1 + s2
        elif s1 > 0 or s2 > 0:
            # Transfer area: weigh it less but still valuable
            shared_areas[area] = (s1 + s2) * 0.5

    if not shared_areas:
        return "general"

    return max(shared_areas, key=shared_areas.get)


# Weights for combining match scores
MATCH_WEIGHTS = {
    "complementary": 0.30,
    "knowledge_transfer": 0.25,
    "shared_context": 0.25,
    "mentoring": 0.20,
}


def calculate_pair_score(
    dev1: DeveloperProfile, dev2: DeveloperProfile
) -> PairSuggestion:
    """Calculate the overall pairing score between two developers.

    Combines all four matching algorithms with configurable weights.
    """
    reasons: list[PairingReason] = []

    comp = complementary_match(dev1, dev2)
    if comp > 0.1:
        reasons.append(
            PairingReason(
                reason_type="complementary",
                description=f"Complementary expertise (score {comp:.2f})",
                score=comp,
            )
        )

    kt = knowledge_transfer_match(dev1, dev2)
    if kt > 0.1:
        reasons.append(
            PairingReason(
                reason_type="knowledge_transfer",
                description=f"Knowledge transfer opportunity (score {kt:.2f})",
                score=kt,
            )
        )

    sc = shared_context_match(dev1, dev2)
    if sc > 0.1:
        reasons.append(
            PairingReason(
                reason_type="shared_context",
                description=f"Shared codebase context (score {sc:.2f})",
                score=sc,
            )
        )

    ment = mentoring_match(dev1, dev2)
    if ment > 0.1:
        reasons.append(
            PairingReason(
                reason_type="mentoring",
                description=f"Mentoring potential (score {ment:.2f})",
                score=ment,
            )
        )

    overall = (
        comp * MATCH_WEIGHTS["complementary"]
        + kt * MATCH_WEIGHTS["knowledge_transfer"]
        + sc * MATCH_WEIGHTS["shared_context"]
        + ment * MATCH_WEIGHTS["mentoring"]
    )

    task_area = _find_best_task_area(dev1, dev2)

    return PairSuggestion(
        dev1=dev1.name,
        dev2=dev2.name,
        reasons=reasons,
        overall_score=round(overall, 4),
        suggested_task_area=task_area,
    )


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def find_optimal_pairs(profiles: list[DeveloperProfile]) -> list[PairSuggestion]:
    """Find optimal pair suggestions for all combinations of developers.

    Returns suggestions sorted by overall_score descending.
    """
    if len(profiles) < 2:
        return []

    suggestions: list[PairSuggestion] = []
    for i in range(len(profiles)):
        for j in range(i + 1, len(profiles)):
            suggestion = calculate_pair_score(profiles[i], profiles[j])
            if suggestion.overall_score > 0:
                suggestions.append(suggestion)

    suggestions.sort(key=lambda s: s.overall_score, reverse=True)
    return suggestions


def detect_knowledge_gaps(profiles: list[DeveloperProfile]) -> list[dict]:
    """Find areas where only a single developer has expertise (bus factor = 1).

    Returns list of dicts with 'area', 'sole_expert', and 'score'.
    """
    area_experts: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for profile in profiles:
        for area, score in profile.expertise_areas.items():
            area_experts[area].append((profile.name, score))

    gaps: list[dict] = []
    for area, experts in area_experts.items():
        if len(experts) == 1:
            name, score = experts[0]
            gaps.append({"area": area, "sole_expert": name, "score": score})

    gaps.sort(key=lambda g: g["score"], reverse=True)
    return gaps


def suggest_rotation(
    profiles: list[DeveloperProfile],
    current_pairs: list[tuple[str, str]],
) -> list[PairSuggestion]:
    """Suggest new pairings, excluding current pairs.

    Useful for pair rotation schedules.
    """
    current_set = {frozenset(p) for p in current_pairs}
    all_suggestions = find_optimal_pairs(profiles)
    rotated = [
        s for s in all_suggestions if frozenset((s.dev1, s.dev2)) not in current_set
    ]
    return rotated


def build_report(
    profiles: list[DeveloperProfile], days: int = 90, top: int = 10
) -> PairingReport:
    """Build a complete pairing report."""
    suggestions = find_optimal_pairs(profiles)
    gaps = detect_knowledge_gaps(profiles)
    return PairingReport(
        suggestions=suggestions[:top],
        team_size=len(profiles),
        analysis_period_days=days,
        knowledge_gaps=gaps,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_text(report: PairingReport) -> str:
    """Format report as plain text."""
    lines: list[str] = []
    lines.append("PAIR PROGRAMMING SUGGESTIONS")
    lines.append(f"Team size: {report.team_size} | Analysis period: {report.analysis_period_days} days")
    lines.append("=" * 60)

    if not report.suggestions:
        lines.append("No pair suggestions available (need at least 2 developers).")
        return "\n".join(lines)

    for i, s in enumerate(report.suggestions, 1):
        lines.append(f"\n#{i}  {s.dev1} + {s.dev2}  (score: {s.overall_score:.2f})")
        lines.append(f"    Suggested area: {s.suggested_task_area}")
        for r in s.reasons:
            lines.append(f"    - [{r.reason_type}] {r.description}")

    if report.knowledge_gaps:
        lines.append("\n" + "=" * 60)
        lines.append("KNOWLEDGE GAPS (bus factor = 1)")
        for gap in report.knowledge_gaps:
            lines.append(f"  {gap['area']} -- sole expert: {gap['sole_expert']} (score: {gap['score']:.1f})")

    return "\n".join(lines)


def format_slack(report: PairingReport) -> str:
    """Format report as Slack mrkdwn (*bold*, not **bold**)."""
    lines: list[str] = []
    lines.append("*Pair Programming Suggestions*")
    lines.append(f"Team: {report.team_size} devs | Period: {report.analysis_period_days} days")

    if not report.suggestions:
        lines.append("_No suggestions available._")
        return "\n".join(lines)

    for i, s in enumerate(report.suggestions, 1):
        lines.append(f"\n*#{i} {s.dev1} + {s.dev2}* (score: {s.overall_score:.2f})")
        lines.append(f"  Area: `{s.suggested_task_area}`")
        for r in s.reasons:
            lines.append(f"  - _{r.reason_type}_: {r.description}")

    if report.knowledge_gaps:
        lines.append("\n*Knowledge Gaps* (bus factor = 1)")
        for gap in report.knowledge_gaps:
            lines.append(f"  :warning: `{gap['area']}` -- sole expert: {gap['sole_expert']}")

    return "\n".join(lines)


def _make_serializable(obj):
    """Convert dataclass/set/datetime to JSON-serializable form."""
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _make_serializable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_serializable(v) for v in obj]
    return obj


def format_json(report: PairingReport) -> str:
    """Format report as JSON."""
    data = _make_serializable(report)
    return json.dumps(data, indent=2)


def generate_mcp_data_table(report: PairingReport) -> dict:
    """Generate a table of pair suggestions with scores for MCP consumption."""
    headers = ["Rank", "Developer 1", "Developer 2", "Score", "Task Area", "Top Reason"]
    rows: list[list] = []
    for i, s in enumerate(report.suggestions, 1):
        top_reason = s.reasons[0].reason_type if s.reasons else "N/A"
        rows.append([i, s.dev1, s.dev2, s.overall_score, s.suggested_task_area, top_reason])

    return {
        "type": "data_table",
        "title": "Pair Programming Suggestions",
        "headers": headers,
        "rows": rows,
        "metadata": {
            "team_size": report.team_size,
            "analysis_period_days": report.analysis_period_days,
            "knowledge_gap_count": len(report.knowledge_gaps),
        },
    }


def generate_mcp_concept_map(report: PairingReport) -> dict:
    """Generate a concept map of developer nodes connected by pairing strength."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for s in report.suggestions:
        if s.dev1 not in nodes:
            nodes[s.dev1] = {"id": s.dev1, "label": s.dev1, "type": "developer"}
        if s.dev2 not in nodes:
            nodes[s.dev2] = {"id": s.dev2, "label": s.dev2, "type": "developer"}
        edges.append(
            {
                "source": s.dev1,
                "target": s.dev2,
                "weight": s.overall_score,
                "label": s.suggested_task_area,
                "reasons": [r.reason_type for r in s.reasons],
            }
        )

    return {
        "type": "concept_map",
        "title": "Developer Pairing Network",
        "nodes": list(nodes.values()),
        "edges": edges,
        "metadata": {
            "team_size": report.team_size,
            "edge_count": len(edges),
        },
    }


# ---------------------------------------------------------------------------
# Git integration
# ---------------------------------------------------------------------------

def get_git_log(days: int = 90, repo_path: str = ".") -> str:
    """Run git log and return raw output."""
    since_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    cmd = [
        "git", "-C", repo_path, "log",
        f"--since={since_date}",
        f"--pretty=format:{_COMMIT_SEPARATOR}{GIT_LOG_FORMAT}",
        "--name-only",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="pair_matcher",
        description="Analyze git history to suggest optimal pair programming partnerships.",
    )
    sub = parser.add_subparsers(dest="command")

    suggest_p = sub.add_parser("suggest", help="Suggest optimal pairs")
    suggest_p.add_argument("--days", type=int, default=90, help="Analysis period in days")
    suggest_p.add_argument("--top", type=int, default=10, help="Number of top suggestions")
    suggest_p.add_argument(
        "--format",
        choices=["text", "slack", "json", "table", "map"],
        default="text",
        help="Output format",
    )
    suggest_p.add_argument("--repo", default=".", help="Repository path")

    profiles_p = sub.add_parser("profiles", help="Show developer profiles")
    profiles_p.add_argument("--days", type=int, default=90, help="Analysis period in days")
    profiles_p.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )
    profiles_p.add_argument("--repo", default=".", help="Repository path")

    gaps_p = sub.add_parser("gaps", help="Detect knowledge gaps")
    gaps_p.add_argument("--days", type=int, default=90, help="Analysis period in days")
    gaps_p.add_argument("--repo", default=".", help="Repository path")

    return parser


def _format_profiles_text(profiles: list[DeveloperProfile]) -> str:
    """Format profiles as text."""
    lines: list[str] = []
    lines.append("DEVELOPER PROFILES")
    lines.append("=" * 50)
    for p in profiles:
        lines.append(f"\n{p.name} <{p.email}>")
        lines.append(f"  Commits: {p.commit_count}")
        last = p.last_active.strftime("%Y-%m-%d") if p.last_active else "unknown"
        lines.append(f"  Last active: {last}")
        if p.languages:
            top_langs = sorted(p.languages.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append(f"  Languages: {', '.join(l for l, _ in top_langs)}")
        if p.expertise_areas:
            top_areas = sorted(p.expertise_areas.items(), key=lambda x: x[1], reverse=True)[:5]
            lines.append(f"  Top areas: {', '.join(a for a, _ in top_areas)}")
        lines.append(f"  Seniority score: {p.seniority_score():.1f}")
    return "\n".join(lines)


def _format_profiles_json(profiles: list[DeveloperProfile]) -> str:
    """Format profiles as JSON."""
    data = [_make_serializable(p) for p in profiles]
    return json.dumps(data, indent=2)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    repo = getattr(args, "repo", ".")
    days = args.days
    log_text = get_git_log(days=days, repo_path=repo)
    profiles = parse_git_log_for_profiles(log_text, days=days)

    if args.command == "suggest":
        report = build_report(profiles, days=days, top=args.top)
        fmt = args.format
        if fmt == "text":
            print(format_text(report))
        elif fmt == "slack":
            print(format_slack(report))
        elif fmt == "json":
            print(format_json(report))
        elif fmt == "table":
            print(json.dumps(generate_mcp_data_table(report), indent=2))
        elif fmt == "map":
            print(json.dumps(generate_mcp_concept_map(report), indent=2))

    elif args.command == "profiles":
        fmt = args.format
        if fmt == "text":
            print(_format_profiles_text(profiles))
        elif fmt == "json":
            print(_format_profiles_json(profiles))

    elif args.command == "gaps":
        gaps = detect_knowledge_gaps(profiles)
        if not gaps:
            print("No knowledge gaps detected (all areas have multiple contributors).")
        else:
            print("KNOWLEDGE GAPS (bus factor = 1)")
            print("=" * 50)
            for gap in gaps:
                print(f"  {gap['area']} -- sole expert: {gap['sole_expert']} (score: {gap['score']:.1f})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
