"""
PR Impact Analyzer -- blast radius and risk analysis for pull requests.

Analyzes file changes, detects impact zones, calculates risk scores,
and suggests reviewers based on git blame data.

Usage:
    python3 lib/pr_impact_analyzer.py --base main
    python3 lib/pr_impact_analyzer.py --base main --format json
    python3 lib/pr_impact_analyzer.py --base main --format slack
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional

from subprocess_helpers import run_git as _run_git


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileChange:
    """A single file changed in the PR."""
    path: str
    additions: int
    deletions: int
    change_type: str  # 'added' | 'modified' | 'deleted' | 'renamed'


@dataclass
class ImpactZone:
    """A categorized impact zone grouping related files."""
    name: str
    files: list
    risk_level: str  # 'low' | 'medium' | 'high' | 'critical'
    reason: str


@dataclass
class PRImpact:
    """Complete PR impact analysis result."""
    title: str
    files_changed: list  # list[FileChange]
    total_additions: int
    total_deletions: int
    impact_zones: list  # list[ImpactZone]
    risk_score: float
    risk_label: str
    recommendations: list  # list[str]
    reviewers_suggested: list  # list[str]


# ---------------------------------------------------------------------------
# Zone detection patterns
# ---------------------------------------------------------------------------

ZONE_PATTERNS = {
    "config": {
        "patterns": [
            r"package\.json$", r"package-lock\.json$", r"tsconfig.*\.json$",
            r"\.env", r"\.ya?ml$", r"\.toml$", r"pyproject\.toml$",
            r"setup\.cfg$", r"setup\.py$", r"requirements.*\.txt$",
            r"Pipfile", r"\.eslintrc", r"\.prettierrc", r"babel\.config",
            r"webpack\.config", r"vite\.config", r"next\.config",
        ],
        "risk_level": "high",
        "reason": "Configuration changes affect build, runtime, and dependency resolution",
    },
    "ci_cd": {
        "patterns": [
            r"\.github/", r"Dockerfile", r"docker-compose",
            r"Jenkinsfile", r"\.gitlab-ci", r"\.circleci/",
            r"deploy", r"Procfile", r"vercel\.json$", r"railway\.json$",
            r"\.buildkite/",
        ],
        "risk_level": "critical",
        "reason": "CI/CD changes can break build pipelines and deployment",
    },
    "api": {
        "patterns": [
            r"routes?[/\\]", r"endpoint", r"api[/\\]", r"controllers?[/\\]",
            r"schema.*\.(graphql|gql|json|ya?ml)$", r"openapi",
            r"swagger", r"\.proto$",
        ],
        "risk_level": "high",
        "reason": "API changes may break client contracts and integrations",
    },
    "database": {
        "patterns": [
            r"migrations?[/\\]", r"models?[/\\].*\.(py|ts|js|rb)$",
            r"schema\.(sql|prisma|py)$", r"alembic[/\\]", r"knexfile",
            r"\.sql$", r"seeds?[/\\]",
        ],
        "risk_level": "critical",
        "reason": "Database changes risk data loss and require careful migration planning",
    },
    "tests": {
        "patterns": [
            r"tests?[/\\]", r"__tests__[/\\]", r"spec[/\\]",
            r"test_.*\.py$", r".*_test\.(go|rs)$", r".*\.test\.(ts|js|tsx|jsx)$",
            r".*\.spec\.(ts|js|tsx|jsx)$", r"conftest\.py$",
            r"fixtures?[/\\]", r"pytest\.ini$", r"jest\.config",
        ],
        "risk_level": "low",
        "reason": "Test changes improve coverage and reliability",
    },
    "docs": {
        "patterns": [
            r"\.md$", r"\.rst$", r"\.txt$", r"docs?[/\\]", r"wiki[/\\]",
            r"CHANGELOG", r"LICENSE", r"CONTRIBUTING", r"README",
        ],
        "risk_level": "low",
        "reason": "Documentation changes have minimal runtime impact",
    },
    "core": {
        "patterns": [
            r"lib[/\\]", r"src[/\\]", r"app[/\\]", r"pkg[/\\]",
            r"internal[/\\]", r"core[/\\]", r"utils?[/\\]", r"helpers?[/\\]",
        ],
        "risk_level": "medium",
        "reason": "Core source changes affect application behavior",
    },
}

# Zone priority for classification (first match wins for ambiguous files)
ZONE_PRIORITY = ["ci_cd", "database", "api", "config", "tests", "docs", "core"]

RISK_WEIGHTS = {
    "critical": 25,
    "high": 15,
    "medium": 8,
    "low": 3,
}


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_file_changes(base: str = "main") -> list:
    """Parse git diff --stat to extract file changes."""
    # Get diff stat for additions/deletions
    stat_output = _run_git(f"git diff --numstat {base}...HEAD")
    # Get list of changed files with status
    name_status = _run_git(f"git diff --name-status {base}...HEAD")

    # Parse name-status into a lookup
    status_map = {}
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status_code = parts[0][0]  # First char: A, M, D, R
            filepath = parts[-1]  # Last element (handles renames)
            status_map[filepath] = status_code

    changes = []
    for line in stat_output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue

        adds_str, dels_str, filepath = parts[0], parts[1], parts[2]

        # Binary files show as '-'
        additions = int(adds_str) if adds_str != "-" else 0
        deletions = int(dels_str) if dels_str != "-" else 0

        status_code = status_map.get(filepath, "M")
        change_type_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
        change_type = change_type_map.get(status_code, "modified")

        changes.append(FileChange(
            path=filepath,
            additions=additions,
            deletions=deletions,
            change_type=change_type,
        ))

    return changes


def detect_impact_zones(changes: list) -> list:
    """Categorize file changes into impact zones."""
    zone_files = {name: [] for name in ZONE_PATTERNS}

    for change in changes:
        classified = False
        for zone_name in ZONE_PRIORITY:
            zone_def = ZONE_PATTERNS[zone_name]
            for pattern in zone_def["patterns"]:
                if re.search(pattern, change.path, re.IGNORECASE):
                    zone_files[zone_name].append(change.path)
                    classified = True
                    break
            if classified:
                break

        # Unclassified files go to core if they look like source
        if not classified and re.search(r"\.(py|ts|js|tsx|jsx|go|rs|java|rb|php|cs)$", change.path):
            zone_files["core"].append(change.path)

    zones = []
    for zone_name in ZONE_PRIORITY:
        files = zone_files[zone_name]
        if files:
            zone_def = ZONE_PATTERNS[zone_name]
            zones.append(ImpactZone(
                name=zone_name,
                files=files,
                risk_level=zone_def["risk_level"],
                reason=zone_def["reason"],
            ))

    return zones


def calculate_risk_score(changes: list, zones: list) -> tuple:
    """Calculate risk score (0-100) and label."""
    if not changes:
        return (0.0, "trivial")

    score = 0.0

    # Factor 1: File count (0-20 points)
    file_count = len(changes)
    score += min(file_count * 2, 20)

    # Factor 2: Change volume (0-20 points)
    total_lines = sum(c.additions + c.deletions for c in changes)
    if total_lines > 1000:
        score += 20
    elif total_lines > 500:
        score += 15
    elif total_lines > 200:
        score += 10
    elif total_lines > 50:
        score += 5

    # Factor 3: Zone criticality (0-40 points)
    zone_score = 0
    for zone in zones:
        weight = RISK_WEIGHTS.get(zone.risk_level, 0)
        zone_score += weight * min(len(zone.files), 5)
    score += min(zone_score, 40)

    # Factor 4: Test coverage ratio (0-20 points, inverted -- less tests = more risk)
    test_files = sum(1 for c in changes if _is_test_file(c.path))
    non_test_files = len(changes) - test_files
    if non_test_files > 0 and test_files == 0:
        score += 20  # No tests at all for source changes
    elif non_test_files > 0:
        ratio = test_files / non_test_files
        if ratio < 0.25:
            score += 15
        elif ratio < 0.5:
            score += 10
        elif ratio < 1.0:
            score += 5
        # ratio >= 1.0 means good coverage, no penalty

    score = min(score, 100.0)

    # Label
    if score < 20:
        label = "trivial"
    elif score < 40:
        label = "safe"
    elif score < 60:
        label = "moderate"
    elif score < 80:
        label = "risky"
    else:
        label = "dangerous"

    return (round(score, 1), label)


def suggest_reviewers(changes: list) -> list:
    """Suggest reviewers based on git blame for changed files."""
    author_counts = {}

    for change in changes[:10]:  # Limit to 10 files for performance
        if change.change_type == "deleted":
            continue
        blame_output = _run_git(f"git log --format='%aN' -5 -- '{change.path}'")
        for author in blame_output.splitlines():
            author = author.strip().strip("'")
            if author:
                author_counts[author] = author_counts.get(author, 0) + 1

    # Sort by frequency, return top 3
    sorted_authors = sorted(author_counts.items(), key=lambda x: x[1], reverse=True)
    return [author for author, _ in sorted_authors[:3]]


def generate_recommendations(impact: PRImpact) -> list:
    """Generate actionable review recommendations."""
    recs = []

    # Risk-based recommendations
    if impact.risk_score >= 80:
        recs.append("CRITICAL: This PR has dangerous risk level. Require 2+ approvals before merge.")
    elif impact.risk_score >= 60:
        recs.append("HIGH RISK: Schedule a dedicated review session for this PR.")

    # Zone-specific recommendations
    zone_names = {z.name for z in impact.impact_zones}

    if "database" in zone_names:
        recs.append("Database changes detected: verify migration rollback plan exists.")
    if "ci_cd" in zone_names:
        recs.append("CI/CD changes detected: test pipeline in staging before merge.")
    if "api" in zone_names:
        recs.append("API changes detected: check for backward compatibility and update API docs.")
    if "config" in zone_names:
        recs.append("Config changes detected: verify environment-specific values are correct.")

    # Coverage recommendation
    test_files = sum(1 for c in impact.files_changed if _is_test_file(c.path))
    non_test = len(impact.files_changed) - test_files
    if non_test > 0 and test_files == 0:
        recs.append("No test changes found: add tests covering the modified code paths.")
    elif non_test > 3 and test_files < non_test * 0.5:
        recs.append("Test coverage appears low relative to source changes.")

    # Size recommendation
    if impact.total_additions + impact.total_deletions > 500:
        recs.append("Large PR: consider splitting into smaller, focused PRs for easier review.")

    if not recs:
        recs.append("Low-risk change. Standard review process is sufficient.")

    return recs


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

def analyze_pr_impact(base: str = "main") -> PRImpact:
    """Run full PR impact analysis."""
    title_output = _run_git("git log --format='%s' -1 HEAD")
    title = title_output.strip().strip("'")

    changes = analyze_file_changes(base)
    zones = detect_impact_zones(changes)
    score, label = calculate_risk_score(changes, zones)

    total_adds = sum(c.additions for c in changes)
    total_dels = sum(c.deletions for c in changes)

    reviewers = suggest_reviewers(changes)

    impact = PRImpact(
        title=title,
        files_changed=changes,
        total_additions=total_adds,
        total_deletions=total_dels,
        impact_zones=zones,
        risk_score=score,
        risk_label=label,
        recommendations=[],
        reviewers_suggested=reviewers,
    )

    impact.recommendations = generate_recommendations(impact)
    return impact


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

RISK_INDICATORS = {
    "trivial": "[ ]",
    "safe": "[*]",
    "moderate": "[!]",
    "risky": "[!!]",
    "dangerous": "[!!!]",
}

ZONE_RISK_ICONS = {
    "low": "o",
    "medium": "!",
    "high": "!!",
    "critical": "!!!",
}


def format_impact_text(impact: PRImpact) -> str:
    """Format impact analysis for terminal output."""
    indicator = RISK_INDICATORS.get(impact.risk_label, "[?]")
    lines = [
        f"PR Impact Analysis: {impact.title}",
        f"{'=' * 60}",
        f"Risk: {indicator} {impact.risk_score}/100 ({impact.risk_label})",
        f"Files changed: {len(impact.files_changed)} (+{impact.total_additions} -{impact.total_deletions})",
        "",
    ]

    if impact.impact_zones:
        lines.append("Impact Zones:")
        for zone in impact.impact_zones:
            icon = ZONE_RISK_ICONS.get(zone.risk_level, "?")
            lines.append(f"  [{icon}] {zone.name} ({zone.risk_level}): {len(zone.files)} file(s)")
            lines.append(f"      {zone.reason}")
            for f in zone.files[:5]:
                lines.append(f"      - {f}")
            if len(zone.files) > 5:
                lines.append(f"      ... and {len(zone.files) - 5} more")
        lines.append("")

    if impact.recommendations:
        lines.append("Recommendations:")
        for rec in impact.recommendations:
            lines.append(f"  - {rec}")
        lines.append("")

    if impact.reviewers_suggested:
        lines.append(f"Suggested reviewers: {', '.join(impact.reviewers_suggested)}")

    return "\n".join(lines)


def format_impact_slack(impact: PRImpact) -> str:
    """Format impact analysis for Slack mrkdwn."""
    risk_emoji = {
        "trivial": ":white_check_mark:",
        "safe": ":large_green_circle:",
        "moderate": ":warning:",
        "risky": ":red_circle:",
        "dangerous": ":rotating_light:",
    }
    emoji = risk_emoji.get(impact.risk_label, ":question:")

    lines = [
        f"*PR Impact Analysis*: {impact.title}",
        f"{emoji} Risk: *{impact.risk_score}/100* ({impact.risk_label})",
        f"Files changed: {len(impact.files_changed)} (+{impact.total_additions} -{impact.total_deletions})",
        "",
    ]

    if impact.impact_zones:
        lines.append("*Impact Zones:*")
        for zone in impact.impact_zones:
            lines.append(f"  *{zone.name}* ({zone.risk_level}): {len(zone.files)} file(s) - _{zone.reason}_")
        lines.append("")

    if impact.recommendations:
        lines.append("*Recommendations:*")
        for rec in impact.recommendations:
            lines.append(f"  - {rec}")
        lines.append("")

    if impact.reviewers_suggested:
        lines.append(f"*Suggested reviewers:* {', '.join(impact.reviewers_suggested)}")

    return "\n".join(lines)


def format_impact_json(impact: PRImpact) -> str:
    """Format impact analysis as JSON."""
    data = {
        "title": impact.title,
        "files_changed": [asdict(f) for f in impact.files_changed],
        "total_additions": impact.total_additions,
        "total_deletions": impact.total_deletions,
        "impact_zones": [asdict(z) for z in impact.impact_zones],
        "risk_score": impact.risk_score,
        "risk_label": impact.risk_label,
        "recommendations": impact.recommendations,
        "reviewers_suggested": impact.reviewers_suggested,
    }
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_test_file(path: str) -> bool:
    """Check if a file path is a test file."""
    return bool(re.search(
        r"(tests?[/\\]|__tests__[/\\]|spec[/\\]|test_.*\.py$|"
        r".*_test\.(go|rs)$|.*\.test\.(ts|js|tsx|jsx)$|"
        r".*\.spec\.(ts|js|tsx|jsx)$|conftest\.py$)",
        path, re.IGNORECASE,
    ))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="PR Impact Analyzer")
    parser.add_argument("--base", default="main", help="Base branch to compare against")
    parser.add_argument(
        "--format", choices=["text", "json", "slack"], default="text",
        help="Output format",
    )
    args = parser.parse_args()

    impact = analyze_pr_impact(base=args.base)

    if args.format == "json":
        print(format_impact_json(impact))
    elif args.format == "slack":
        print(format_impact_slack(impact))
    else:
        print(format_impact_text(impact))


if __name__ == "__main__":
    main()
