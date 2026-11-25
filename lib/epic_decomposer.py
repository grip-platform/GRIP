#!/usr/bin/env python3
"""
Epic Decomposer
================

Automatically breaks down GitHub issues labeled 'overhaul' into
smaller subtasks that Broly agents can execute independently.

Epics are detected by the 'overhaul' label. The decomposer:
1. Parses the epic body for subtask markers (checkboxes, phases, headers)
2. Creates Candidate objects for each subtask
3. Links subtasks to parent epic via parent_epic_id
4. Estimates effort per subtask from content analysis
5. Returns a flat list for the RSI queue

Integration: Called by rsi_orchestrator.mine_github_issues() when
an issue has the 'overhaul' label.

Version: 1.0.0
"""

import json
import os
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR_OVERRIDE", str(Path.home() / ".claude")))
DECOMPOSITION_CACHE = CLAUDE_DIR / "cache" / "epic-decompositions.json"

# Patterns for extracting subtasks from issue body
CHECKBOX_RE = re.compile(r"^[-*]\s*\[[ x]\]\s*(.+)$", re.MULTILINE)
PHASE_HEADER_RE = re.compile(r"^###\s+Phase\s+\d+[:\s]*(.+)$", re.MULTILINE)
NUMBERED_RE = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)

# Effort estimation keywords
EFFORT_LOW = {"seed", "wire", "populate", "config", "test", "fix", "update", "add"}
EFFORT_MED = {"implement", "create", "build", "extend", "integrate", "write"}
EFFORT_HIGH = {"design", "architect", "overhaul", "refactor", "migrate", "rewrite"}


def estimate_subtask_effort(text: str) -> int:
    """Estimate effort 1-5 from subtask description keywords."""
    lower = text.lower()
    if any(w in lower for w in EFFORT_HIGH):
        return 4
    if any(w in lower for w in EFFORT_MED):
        return 3
    if any(w in lower for w in EFFORT_LOW):
        return 2
    return 3  # default medium


def estimate_subtask_impact(text: str) -> int:
    """Estimate impact 1-5 from subtask description."""
    lower = text.lower()
    if any(w in lower for w in {"council", "multi-provider", "routing", "architecture"}):
        return 5
    if any(w in lower for w in {"test", "benchmark", "cache", "integration"}):
        return 4
    if any(w in lower for w in {"config", "seed", "populate", "threshold"}):
        return 3
    return 3


def extract_subtasks(body: str) -> list[dict]:
    """Extract subtasks from an epic issue body.

    Parses three patterns in priority order:
    1. Checkbox items (- [ ] task)
    2. Phase headers (### Phase N: description)
    3. Numbered lists (1. task)

    Returns list of dicts with 'title', 'effort', 'impact', 'phase'.
    """
    subtasks = []
    seen_titles = set()

    # Phase tracking - group checkboxes under their phase
    current_phase = None
    lines = body.split("\n")

    for line in lines:
        # Detect phase headers
        phase_match = PHASE_HEADER_RE.match(line)
        if phase_match:
            current_phase = phase_match.group(1).strip()
            continue

        # Detect checkboxes (most common in epic bodies)
        checkbox_match = CHECKBOX_RE.match(line.strip())
        if checkbox_match:
            title = checkbox_match.group(1).strip()
            # Skip already-checked items
            if "[x]" in line.lower():
                continue
            if title not in seen_titles:
                seen_titles.add(title)
                subtasks.append({
                    "title": title,
                    "effort": estimate_subtask_effort(title),
                    "impact": estimate_subtask_impact(title),
                    "phase": current_phase,
                })

    # If no checkboxes found, try numbered lists
    if not subtasks:
        for match in NUMBERED_RE.finditer(body):
            title = match.group(1).strip()
            if title not in seen_titles:
                seen_titles.add(title)
                subtasks.append({
                    "title": title,
                    "effort": estimate_subtask_effort(title),
                    "impact": estimate_subtask_impact(title),
                    "phase": None,
                })

    return subtasks


def decompose_epic(issue_number: int, title: str, body: str,
                   labels: Optional[list] = None) -> list[dict]:
    """Decompose an epic issue into Candidate-compatible subtask dicts.

    Args:
        issue_number: GitHub issue number
        title: Epic title
        body: Epic body text
        labels: GitHub labels list

    Returns:
        List of dicts ready for Candidate(**subtask) construction.
        Each subtask has parent_epic_id linking back to the epic.
    """
    subtasks = extract_subtasks(body)

    if not subtasks:
        # No parseable subtasks - return the epic itself as a single large candidate
        return [{
            "id": f"issue-{issue_number}",
            "title": title,
            "source": "issue",
            "impact": 5,
            "urgency": 3,
            "effort": 5,
            "estimated_cost_usd": 5.0,
            "issue_number": issue_number,
            "is_epic": True,
            "labels": labels or [],
            "status": "pending",
        }]

    candidates = []
    for i, sub in enumerate(subtasks, 1):
        phase_prefix = f"[{sub['phase']}] " if sub.get("phase") else ""
        candidates.append({
            "id": f"epic-{issue_number}-sub-{i}",
            "title": f"{phase_prefix}{sub['title']}",
            "source": "issue",
            "impact": sub["impact"],
            "urgency": 3,
            "effort": sub["effort"],
            "estimated_cost_usd": max(0.50, sub["effort"] * 0.70),
            "issue_number": issue_number,
            "is_epic": False,
            "labels": labels or [],
            "parent_epic_id": f"issue-{issue_number}",
            "status": "pending",
        })

    return candidates


def decompose_from_github(issue_number: int) -> list[dict]:
    """Fetch an issue from GitHub and decompose it.

    Convenience function that handles the gh CLI call.
    """
    try:
        result = subprocess.run(
            ["gh", "issue", "view", str(issue_number),
             "--json", "number,title,body,labels"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return []

        issue = json.loads(result.stdout)
        labels = [l.get("name", "") for l in issue.get("labels", [])]
        return decompose_epic(
            issue_number=issue["number"],
            title=issue["title"],
            body=issue.get("body", ""),
            labels=labels,
        )
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def cache_decomposition(issue_number: int, subtasks: list[dict]):
    """Cache decomposition results to avoid re-parsing."""
    DECOMPOSITION_CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if DECOMPOSITION_CACHE.exists():
        try:
            cache = json.loads(DECOMPOSITION_CACHE.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}

    cache[str(issue_number)] = {
        "subtasks": subtasks,
        "count": len(subtasks),
    }
    DECOMPOSITION_CACHE.write_text(json.dumps(cache, indent=2))


def get_cached_decomposition(issue_number: int) -> Optional[list[dict]]:
    """Retrieve cached decomposition if available."""
    if not DECOMPOSITION_CACHE.exists():
        return None
    try:
        cache = json.loads(DECOMPOSITION_CACHE.read_text())
        entry = cache.get(str(issue_number))
        if entry:
            return entry.get("subtasks")
    except (json.JSONDecodeError, OSError):
        pass
    return None


def get_epic_progress(issue_number: int, candidates: list) -> dict:
    """Check completion progress of an epic's subtasks.

    Args:
        issue_number: The epic issue number
        candidates: Full candidate list from queue

    Returns:
        Dict with total, completed, pending, pct_complete
    """
    epic_id = f"issue-{issue_number}"
    children = [c for c in candidates if getattr(c, "parent_epic_id", None) == epic_id]

    if not children:
        return {"total": 0, "completed": 0, "pending": 0, "pct_complete": 0}

    completed = sum(1 for c in children if c.status == "completed")
    total = len(children)
    return {
        "total": total,
        "completed": completed,
        "pending": total - completed,
        "pct_complete": round((completed / total) * 100) if total > 0 else 0,
    }


# CLI interface
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: epic_decomposer.py <command> [args]")
        print("Commands: decompose <issue#>, progress <issue#>, cache-show")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "decompose" and len(sys.argv) >= 3:
        issue_num = int(sys.argv[2])
        subtasks = decompose_from_github(issue_num)
        cache_decomposition(issue_num, subtasks)
        print(json.dumps(subtasks, indent=2))
        print(f"\n{len(subtasks)} subtasks extracted from issue #{issue_num}")

    elif cmd == "cache-show":
        if DECOMPOSITION_CACHE.exists():
            print(DECOMPOSITION_CACHE.read_text())
        else:
            print("No cached decompositions.")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
