#!/usr/bin/env python3
"""Aggregate skill health dashboard for GRIP.

Scans the skills/ directory, cross-references usage data from
skill_usage_tracker, and scores each skill across four health dimensions:
usage frequency, dependency connectedness, freshness, and size.

Produces a composite health score with traffic-light classification and
identifies prune candidates.

Zero external dependencies -- stdlib only.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
SKILLS_DIR = CLAUDE_DIR / "skills"

SKIP_ENTRIES = frozenset({
    "_archived", ".git", "__pycache__", "node_modules", "CLAUDE.md",
})

# Weights for composite score
WEIGHT_USAGE = 0.30
WEIGHT_DEPENDENCY = 0.30
WEIGHT_FRESHNESS = 0.20
WEIGHT_SIZE = 0.20

# Traffic light thresholds
GREEN_THRESHOLD = 70
YELLOW_THRESHOLD = 40

# Freshness: days since modification for score decay
FRESHNESS_FULL_DAYS = 30       # <=30 days = 100
FRESHNESS_ZERO_DAYS = 365      # >=365 days = 0

# Size scoring: ideal SKILL.md size range in bytes
SIZE_IDEAL_MIN = 200
SIZE_IDEAL_MAX = 5000
SIZE_PENALTY_MIN = 50     # Below this = 0
SIZE_PENALTY_MAX = 20000  # Above this = 0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class SkillInfo:
    """Metadata about a single skill directory."""

    __slots__ = (
        "name", "has_skill_md", "file_count", "skill_md_size",
        "skill_md_mtime", "total_size",
    )

    def __init__(self, name: str) -> None:
        self.name: str = name
        self.has_skill_md: bool = False
        self.file_count: int = 0
        self.skill_md_size: int = 0
        self.skill_md_mtime: float = 0.0
        self.total_size: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "has_skill_md": self.has_skill_md,
            "file_count": self.file_count,
            "skill_md_size": self.skill_md_size,
            "total_size": self.total_size,
        }


class HealthScore:
    """Health assessment for a single skill."""

    __slots__ = (
        "name", "usage_score", "dependency_score", "freshness_score",
        "size_score", "composite", "status", "has_dependents",
        "is_prune_candidate",
    )

    def __init__(self, name: str) -> None:
        self.name: str = name
        self.usage_score: float = 0.0
        self.dependency_score: float = 0.0
        self.freshness_score: float = 0.0
        self.size_score: float = 0.0
        self.composite: float = 0.0
        self.status: str = "RED"
        self.has_dependents: bool = False
        self.is_prune_candidate: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "usage_score": round(self.usage_score, 1),
            "dependency_score": round(self.dependency_score, 1),
            "freshness_score": round(self.freshness_score, 1),
            "size_score": round(self.size_score, 1),
            "composite": round(self.composite, 1),
            "status": self.status,
            "has_dependents": self.has_dependents,
            "is_prune_candidate": self.is_prune_candidate,
        }


# ---------------------------------------------------------------------------
# Skill scanning
# ---------------------------------------------------------------------------

def scan_skills(skills_dir: Path) -> list[SkillInfo]:
    """Scan the skills directory and return metadata for each skill."""
    results: list[SkillInfo] = []
    if not skills_dir.is_dir():
        return results

    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in SKIP_ENTRIES:
            continue
        if entry.name.startswith("."):
            continue

        info = SkillInfo(entry.name)

        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            info.has_skill_md = True
            try:
                stat = skill_md.stat()
                info.skill_md_size = stat.st_size
                info.skill_md_mtime = stat.st_mtime
            except OSError:
                pass

        # Count files and total size (non-recursive, top-level only)
        try:
            for child in entry.iterdir():
                if child.is_file():
                    info.file_count += 1
                    try:
                        info.total_size += child.stat().st_size
                    except OSError:
                        pass
                elif child.is_dir():
                    # Count directory as one entry but walk for size
                    info.file_count += 1
                    try:
                        for sub in child.rglob("*"):
                            if sub.is_file():
                                try:
                                    info.total_size += sub.stat().st_size
                                except OSError:
                                    pass
                    except OSError:
                        pass
        except OSError:
            pass

        results.append(info)

    return results


# ---------------------------------------------------------------------------
# Usage data loading
# ---------------------------------------------------------------------------

def load_usage_data(
    skills_dir: Path,
) -> dict[str, int]:
    """Attempt to load usage counts from skill_usage_tracker cache.

    Looks for a usage-cache.json in the CLAUDE_DIR/cache/ directory.
    Falls back to empty dict if unavailable.

    Returns {skill_name: invocation_count}.
    """
    # Try cache file first
    cache_file = skills_dir.parent / "cache" / "skill-usage-cache.json"
    if cache_file.is_file():
        try:
            with cache_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                # Handle both flat {name: count} and nested formats
                result: dict[str, int] = {}
                for key, val in data.items():
                    if isinstance(val, int):
                        result[key] = val
                    elif isinstance(val, dict) and "count" in val:
                        result[key] = int(val["count"])
                return result
        except (json.JSONDecodeError, OSError, ValueError):
            pass

    return {}


# ---------------------------------------------------------------------------
# Dependency analysis
# ---------------------------------------------------------------------------

def analyze_dependencies(
    skills: list[SkillInfo],
    skills_dir: Path,
) -> dict[str, set[str]]:
    """Build a dependency map: skill_name -> set of skills that reference it.

    Scans SKILL.md files for references to other skill names.
    A skill is a "dependent" if another skill's SKILL.md mentions it.
    """
    skill_names = {s.name for s in skills}
    # dependents[X] = set of skills that reference X
    dependents: dict[str, set[str]] = defaultdict(set)

    for skill in skills:
        if not skill.has_skill_md:
            continue
        skill_md = skills_dir / skill.name / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        content_lower = content.lower()
        for other_name in skill_names:
            if other_name == skill.name:
                continue
            # Check if the other skill is referenced
            if other_name.lower() in content_lower:
                dependents[other_name].add(skill.name)

    return dict(dependents)


# ---------------------------------------------------------------------------
# Health dimension scorers
# ---------------------------------------------------------------------------

def score_usage(
    invocation_count: int,
    max_count: int,
) -> float:
    """Score usage 0-100.

    Uses logarithmic scaling so a few high-use skills don't squash
    everything else. 0 invocations = 0, max invocations = 100.
    """
    if max_count <= 0 or invocation_count <= 0:
        return 0.0
    # log(1) = 0, log(max+1) = ceiling
    log_val = math.log1p(invocation_count)
    log_max = math.log1p(max_count)
    if log_max == 0:
        return 0.0
    return min(100.0, (log_val / log_max) * 100.0)


def score_dependency(
    dependent_count: int,
    has_skill_md: bool,
) -> float:
    """Score dependency health 0-100.

    - No SKILL.md: base penalty (max 40)
    - 0 dependents: 30 (exists but isolated)
    - 1 dependent: 60
    - 2 dependents: 75
    - 3+ dependents: 85-100 (diminishing returns)
    """
    if not has_skill_md:
        # Orphan with no documentation
        base = 20.0
    else:
        base = 30.0

    if dependent_count <= 0:
        return base

    # Diminishing returns: each additional dependent adds less
    bonus = min(70.0, 30.0 * math.log1p(dependent_count))
    return min(100.0, base + bonus)


def score_freshness(
    mtime: float,
    now: float | None = None,
) -> float:
    """Score freshness 0-100 based on SKILL.md modification time.

    <=FRESHNESS_FULL_DAYS days old = 100
    >=FRESHNESS_ZERO_DAYS days old = 0
    Linear decay between.

    If mtime is 0 (no SKILL.md or unreadable), returns 0.
    """
    if mtime <= 0:
        return 0.0

    if now is None:
        now = time.time()

    age_days = (now - mtime) / 86400.0
    if age_days < 0:
        age_days = 0

    if age_days <= FRESHNESS_FULL_DAYS:
        return 100.0
    if age_days >= FRESHNESS_ZERO_DAYS:
        return 0.0

    # Linear decay
    span = FRESHNESS_ZERO_DAYS - FRESHNESS_FULL_DAYS
    remaining = FRESHNESS_ZERO_DAYS - age_days
    return (remaining / span) * 100.0


def score_size(
    skill_md_size: int,
) -> float:
    """Score size 0-100 based on SKILL.md byte count.

    Ideal range: SIZE_IDEAL_MIN to SIZE_IDEAL_MAX = 100
    Below SIZE_PENALTY_MIN or above SIZE_PENALTY_MAX = 0
    Linear ramps between penalty and ideal boundaries.

    If size is 0 (no SKILL.md), returns 0.
    """
    if skill_md_size <= 0:
        return 0.0

    if SIZE_IDEAL_MIN <= skill_md_size <= SIZE_IDEAL_MAX:
        return 100.0

    if skill_md_size < SIZE_PENALTY_MIN:
        return 0.0
    if skill_md_size > SIZE_PENALTY_MAX:
        return 0.0

    if skill_md_size < SIZE_IDEAL_MIN:
        # Ramp up from SIZE_PENALTY_MIN to SIZE_IDEAL_MIN
        span = SIZE_IDEAL_MIN - SIZE_PENALTY_MIN
        offset = skill_md_size - SIZE_PENALTY_MIN
        return (offset / span) * 100.0

    # skill_md_size > SIZE_IDEAL_MAX
    # Ramp down from SIZE_IDEAL_MAX to SIZE_PENALTY_MAX
    span = SIZE_PENALTY_MAX - SIZE_IDEAL_MAX
    remaining = SIZE_PENALTY_MAX - skill_md_size
    return (remaining / span) * 100.0


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------

def classify_status(score: float) -> str:
    """Traffic light classification."""
    if score >= GREEN_THRESHOLD:
        return "GREEN"
    if score >= YELLOW_THRESHOLD:
        return "YELLOW"
    return "RED"


def compute_composite(
    usage: float,
    dependency: float,
    freshness: float,
    size: float,
    *,
    weights: tuple[float, float, float, float] | None = None,
) -> float:
    """Weighted average of four dimensions."""
    if weights is None:
        w_u, w_d, w_f, w_s = WEIGHT_USAGE, WEIGHT_DEPENDENCY, WEIGHT_FRESHNESS, WEIGHT_SIZE
    else:
        w_u, w_d, w_f, w_s = weights

    total_weight = w_u + w_d + w_f + w_s
    if total_weight == 0:
        return 0.0

    raw = (usage * w_u + dependency * w_d + freshness * w_f + size * w_s)
    return raw / total_weight


def compute_health(
    skills: list[SkillInfo],
    usage_data: dict[str, int],
    dependents: dict[str, set[str]],
    now: float | None = None,
    weights: tuple[float, float, float, float] | None = None,
    skip_usage: bool = False,
) -> list[HealthScore]:
    """Compute health scores for all skills.

    When skip_usage is True, usage dimension is excluded and weights are
    redistributed proportionally among the remaining three dimensions.
    """
    max_usage = max(usage_data.values()) if usage_data else 0

    results: list[HealthScore] = []
    for skill in skills:
        hs = HealthScore(skill.name)

        # Usage
        if skip_usage:
            hs.usage_score = 0.0
        else:
            count = usage_data.get(skill.name, 0)
            hs.usage_score = score_usage(count, max_usage)

        # Dependency
        dep_set = dependents.get(skill.name, set())
        hs.dependency_score = score_dependency(len(dep_set), skill.has_skill_md)
        hs.has_dependents = len(dep_set) > 0

        # Freshness
        hs.freshness_score = score_freshness(skill.skill_md_mtime, now=now)

        # Size
        hs.size_score = score_size(skill.skill_md_size)

        # Composite
        if skip_usage:
            # Redistribute usage weight proportionally
            w_d = WEIGHT_DEPENDENCY
            w_f = WEIGHT_FRESHNESS
            w_s = WEIGHT_SIZE
            if weights is not None:
                _, w_d, w_f, w_s = weights
            hs.composite = compute_composite(
                0, hs.dependency_score, hs.freshness_score, hs.size_score,
                weights=(0, w_d, w_f, w_s),
            )
        else:
            hs.composite = compute_composite(
                hs.usage_score, hs.dependency_score,
                hs.freshness_score, hs.size_score,
                weights=weights,
            )

        hs.status = classify_status(hs.composite)
        hs.is_prune_candidate = (hs.status == "RED" and not hs.has_dependents)

        results.append(hs)

    return results


# ---------------------------------------------------------------------------
# Summary statistics
# ---------------------------------------------------------------------------

def compute_summary(health_scores: list[HealthScore]) -> dict[str, Any]:
    """Compute aggregate summary stats."""
    total = len(health_scores)
    if total == 0:
        return {
            "total_skills": 0,
            "green": 0,
            "yellow": 0,
            "red": 0,
            "average_health": 0.0,
            "prune_candidates": 0,
        }

    green = sum(1 for h in health_scores if h.status == "GREEN")
    yellow = sum(1 for h in health_scores if h.status == "YELLOW")
    red = sum(1 for h in health_scores if h.status == "RED")
    avg = sum(h.composite for h in health_scores) / total
    prune = sum(1 for h in health_scores if h.is_prune_candidate)

    return {
        "total_skills": total,
        "green": green,
        "yellow": yellow,
        "red": red,
        "average_health": round(avg, 1),
        "prune_candidates": prune,
    }


# ---------------------------------------------------------------------------
# Sorting and filtering
# ---------------------------------------------------------------------------

SORT_FIELDS = {
    "name": lambda h: h.name,
    "composite": lambda h: -h.composite,
    "usage": lambda h: -h.usage_score,
    "dependency": lambda h: -h.dependency_score,
    "freshness": lambda h: -h.freshness_score,
    "size": lambda h: -h.size_score,
    "status": lambda h: (0 if h.status == "RED" else 1 if h.status == "YELLOW" else 2, h.name),
}


def sort_scores(
    scores: list[HealthScore],
    field: str = "composite",
) -> list[HealthScore]:
    """Sort health scores by field. Defaults to composite (descending)."""
    key_fn = SORT_FIELDS.get(field)
    if key_fn is None:
        key_fn = SORT_FIELDS["composite"]
    return sorted(scores, key=key_fn)


def filter_scores(
    scores: list[HealthScore],
    status: str | None = None,
) -> list[HealthScore]:
    """Filter health scores by traffic light status."""
    if status is None:
        return scores
    status_upper = status.upper()
    return [h for h in scores if h.status == status_upper]


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(
    health_scores: list[HealthScore],
    summary: dict[str, Any],
    top_n: int | None = None,
) -> str:
    """Format health dashboard as markdown."""
    lines: list[str] = []

    lines.append("# Skill Health Dashboard")
    lines.append("")
    lines.append(f"**Total skills**: {summary['total_skills']} | "
                 f"**GREEN**: {summary['green']} | "
                 f"**YELLOW**: {summary['yellow']} | "
                 f"**RED**: {summary['red']} | "
                 f"**Avg health**: {summary['average_health']}")
    lines.append("")

    items = health_scores
    if top_n is not None:
        items = items[:top_n]

    if items:
        lines.append("| Skill | Usage | Dep | Fresh | Size | Health | Status |")
        lines.append("|-------|------:|----:|------:|-----:|-------:|--------|")
        for h in items:
            lines.append(
                f"| {h.name} | {h.usage_score:.0f} | {h.dependency_score:.0f} "
                f"| {h.freshness_score:.0f} | {h.size_score:.0f} "
                f"| {h.composite:.1f} | {h.status} |"
            )
        lines.append("")

    prune = [h for h in health_scores if h.is_prune_candidate]
    if prune:
        lines.append(f"## Prune Candidates ({len(prune)})")
        lines.append("")
        for h in prune:
            lines.append(f"- {h.name} (health: {h.composite:.1f})")
        lines.append("")

    return "\n".join(lines)


def format_json(
    health_scores: list[HealthScore],
    summary: dict[str, Any],
    top_n: int | None = None,
) -> str:
    """Format health dashboard as JSON."""
    items = health_scores
    if top_n is not None:
        items = items[:top_n]

    data = {
        "summary": summary,
        "skills": [h.to_dict() for h in items],
        "prune_candidates": [
            h.name for h in health_scores if h.is_prune_candidate
        ],
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_dashboard(
    skills_dir: Path,
    *,
    sort_field: str = "composite",
    filter_status: str | None = None,
    top_n: int | None = None,
    json_output: bool = False,
    prune_only: bool = False,
    now: float | None = None,
) -> str:
    """Full pipeline: scan, score, format."""
    skills = scan_skills(skills_dir)
    usage_data = load_usage_data(skills_dir)
    dependents = analyze_dependencies(skills, skills_dir)

    skip_usage = len(usage_data) == 0
    health_scores = compute_health(
        skills, usage_data, dependents, now=now, skip_usage=skip_usage,
    )

    # Filter
    health_scores = filter_scores(health_scores, filter_status)

    # Sort
    health_scores = sort_scores(health_scores, sort_field)

    # Prune-only mode
    if prune_only:
        health_scores = [h for h in health_scores if h.is_prune_candidate]

    summary = compute_summary(health_scores)

    if json_output:
        return format_json(health_scores, summary, top_n=top_n)
    return format_markdown(health_scores, summary, top_n=top_n)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill_health_dashboard",
        description="Aggregate skill health dashboard for GRIP.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output as JSON instead of markdown",
    )
    parser.add_argument(
        "--sort",
        type=str,
        default="composite",
        choices=list(SORT_FIELDS.keys()),
        metavar="FIELD",
        help="Sort by field: name, composite, usage, dependency, freshness, size, status",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        dest="filter_status",
        choices=["GREEN", "YELLOW", "RED", "green", "yellow", "red"],
        metavar="STATUS",
        help="Filter by traffic light status",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Show only the top N skills",
    )
    parser.add_argument(
        "--prune-candidates",
        action="store_true",
        help="Show only prune candidates (RED + no dependents)",
    )
    parser.add_argument(
        "--skills-dir",
        type=str,
        default=None,
        help="Override skills directory (default: ~/.claude/skills)",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    skills_dir = Path(args.skills_dir) if args.skills_dir else SKILLS_DIR

    output = run_dashboard(
        skills_dir,
        sort_field=args.sort,
        filter_status=args.filter_status,
        top_n=args.top,
        json_output=args.json_output,
        prune_only=args.prune_candidates,
    )
    print(output)


if __name__ == "__main__":
    main()
