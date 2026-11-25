#!/usr/bin/env python3
"""RSI source mining — Ralph reviews, GitHub issues, MEMORY.md TODOs."""

import json
import re
import subprocess
import sys

from . import config
from .models import Candidate


def mine_ralph_reviews() -> list[dict]:
    """Extract patterns from Ralph review history.

    Parses .rsi-last-collect.json for recurring feedback themes.
    Returns list of {pattern, count, severity, example} dicts.
    """
    if not config.RALPH_COLLECT.exists():
        return []

    try:
        data = json.loads(config.RALPH_COLLECT.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    # Extract review comments from collected data
    reviews = data if isinstance(data, list) else data.get("reviews", [])
    if not reviews:
        return []

    # Pattern categories to detect
    pattern_counts: dict[str, dict] = {}
    pattern_keywords = {
        "missing_tests": r"(?i)test\s*coverage|missing\s*test|no\s*test|add\s*test",
        "type_hints": r"(?i)type\s*hint|typing|type\s*annotation|return\s*type",
        "error_handling": r"(?i)error\s*handling|exception|try[\s/]catch|bare\s*except",
        "documentation": r"(?i)docstring|documentation|jsdoc|comment",
        "code_style": r"(?i)naming|style|formatting|lint|pep\s*8",
        "security": r"(?i)security|injection|sanitiz|vulnerab|xss|csrf",
        "performance": r"(?i)performance|optimi[sz]|slow|O\(n|cache|lazy",
        "architecture": r"(?i)architect|refactor|single\s*responsibility|coupling|cohesion",
    }

    for review in reviews:
        body = ""
        if isinstance(review, dict):
            body = review.get("body", "") or review.get("comment", "") or ""
        elif isinstance(review, str):
            body = review

        for pattern_name, regex in pattern_keywords.items():
            if re.search(regex, body):
                if pattern_name not in pattern_counts:
                    pattern_counts[pattern_name] = {
                        "pattern": pattern_name,
                        "count": 0,
                        "severity": "medium",
                        "examples": [],
                    }
                pattern_counts[pattern_name]["count"] += 1
                # Keep up to 3 examples
                if len(pattern_counts[pattern_name]["examples"]) < 3:
                    snippet = body[:200].strip()
                    pattern_counts[pattern_name]["examples"].append(snippet)

    # Sort by frequency
    results = sorted(pattern_counts.values(), key=lambda x: x["count"], reverse=True)

    # Mark severity based on count
    for r in results:
        if r["count"] >= 5:
            r["severity"] = "high"
        elif r["count"] >= 3:
            r["severity"] = "medium"
        else:
            r["severity"] = "low"

    return results


def mine_github_issues() -> list[Candidate]:
    """Scan GitHub issues labeled sprint-loop for candidates.

    Issues with the 'overhaul' label are treated as epics and
    automatically decomposed into subtasks via epic_decomposer.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--label", "sprint-loop", "--state", "open",
             "--json", "number,title,labels,body", "--limit", "20"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return []

        issues = json.loads(result.stdout)
        candidates = []
        for issue in issues:
            # Estimate effort from labels
            labels = [l.get("name", "") for l in issue.get("labels", [])]
            effort = 3  # default medium
            if "bug" in labels:
                effort = 2
            if "enhancement" in labels:
                effort = 3
            if "improvement" in labels:
                effort = 2

            impact = 3
            if "critical" in " ".join(labels).lower():
                impact = 5
            if "high" in " ".join(labels).lower():
                impact = 4

            # Detect overhaul epics and decompose into subtasks
            if "overhaul" in labels:
                sys.path.insert(0, str(config.CLAUDE_DIR / "lib"))
                from epic_decomposer import decompose_epic, cache_decomposition
                subtask_dicts = decompose_epic(
                    issue_number=issue["number"],
                    title=issue["title"],
                    body=issue.get("body", ""),
                    labels=labels,
                )
                cache_decomposition(issue["number"], subtask_dicts)
                for sd in subtask_dicts:
                    candidates.append(Candidate(**sd))
                continue

            candidates.append(Candidate(
                id=f"issue-{issue['number']}",
                title=issue["title"],
                source="issue",
                impact=impact,
                urgency=3,
                effort=effort,
                issue_number=issue["number"],
                labels=labels,
            ))
        return candidates
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def mine_memory_todos() -> list[Candidate]:
    """Extract PENDING items from MEMORY.md TODO tracker."""
    if not config.MEMORY_FILE.exists():
        return []

    try:
        content = config.MEMORY_FILE.read_text()
    except OSError:
        return []

    candidates = []
    # Match TODO table rows: | # | Item | Origin | Status | Notes |
    pattern = re.compile(
        r"\|\s*(\d+)\s*\|\s*([^|]+)\|\s*[^|]+\|\s*(PENDING|BLOCKED)\s*\|\s*([^|]*)\|"
    )

    for match in pattern.finditer(content):
        item_num = int(match.group(1))
        title = match.group(2).strip()
        status = match.group(3).strip()
        notes = match.group(4).strip()

        # Skip BLOCKED items
        if status == "BLOCKED":
            continue

        # Estimate effort from keywords in notes
        effort = 3
        if any(w in notes.lower() for w in ["small", "quick", "trivial", "s "]):
            effort = 1
        elif any(w in notes.lower() for w in ["large", "complex", "l ", "epic"]):
            effort = 5

        candidates.append(Candidate(
            id=f"memory-{item_num}",
            title=title,
            source="memory",
            impact=3,
            urgency=2,
            effort=effort,
            memory_item=item_num,
        ))

    return candidates
