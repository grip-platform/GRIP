"""Insight router — maps classified insights to their canonical target files.

Takes a ClassificationResult from insight_classifier and returns:
1. The target file path(s) where the insight should be propagated
2. The section within that file (if applicable)
3. The write mode (append, update_section, create_new)

Per Rule 20 classification matrix:
- operational_discovery → rules/operational-context.md + project memory
- design_pattern        → project memory + canonical skill file(s)
- behavioral_rule       → project memory (type: feedback) + rules/ if universal
- code_change           → TaskCreate or tracked issue
- documentation_gap     → Write doc or file task
- conversation_only     → No routing (auto-memory handles it)

Design constraints:
- Pure function: no I/O, no state.
- Returns paths and instructions, doesn't perform writes.
- Skill file discovery is keyword-based (no filesystem scan).
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional

from insight_classifier import ClassificationResult, InsightCategory


class WriteMode(Enum):
    """How to write to the target file."""
    APPEND = "append"              # Add to end of file
    APPEND_SECTION = "append_section"  # Add to specific section
    CREATE_NEW = "create_new"      # Create new file
    TASK_CREATE = "task_create"    # Create a tracked task instead


@dataclass(frozen=True)
class PropagationTarget:
    """A single target for insight propagation."""
    path: Path
    write_mode: WriteMode
    section: Optional[str] = None  # For APPEND_SECTION mode
    reason: str = ""               # Why this target was chosen


@dataclass
class RoutingResult:
    """Complete routing decision for an insight."""
    category: InsightCategory
    targets: List[PropagationTarget] = field(default_factory=list)
    skip_reason: Optional[str] = None  # If no propagation needed
    skill_hints: List[str] = field(default_factory=list)  # Detected skill names


# GRIP paths
CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
RULES_DIR = CLAUDE_DIR / "rules"
SKILLS_DIR = CLAUDE_DIR / "skills"
OPERATIONAL_CONTEXT = RULES_DIR / "operational-context.md"


# Skill name detection patterns
_SKILL_PATTERNS = [
    (r"/(\w+[-_]?\w+)\s", "slash_command"),  # /skill-name
    (r"skill[s]?\s+['\"`]?(\w+[-_]?\w+)['\"`]?", "skill_mention"),
    (r"(\w+[-_]?\w+)\s+skill", "skill_suffix"),
    (r"bg-dispatch|rsi-optimal|auto-ship|sprint", "known_skill"),
]
_SKILL_COMPILED = [(re.compile(p, re.IGNORECASE), n) for p, n in _SKILL_PATTERNS]


def _extract_skill_hints(content: str) -> List[str]:
    """Extract potential skill names mentioned in the insight."""
    hints = set()
    for pattern, _ in _SKILL_COMPILED:
        for match in pattern.finditer(content):
            # Get the captured group (skill name)
            if match.groups():
                hint = match.group(1).lower().replace("_", "-")
                # Filter out common false positives
                if hint not in ("the", "a", "an", "this", "that", "is", "are"):
                    hints.add(hint)
    return list(hints)


def _resolve_project_memory_dir() -> Path:
    """Get the project memory directory for the current working directory."""
    cwd = Path.cwd().resolve()
    encoded = str(cwd).replace("/", "-").replace(".", "-")
    return CLAUDE_DIR / "projects" / encoded / "memory"


def route(
    classification: ClassificationResult,
    content: str,
) -> RoutingResult:
    """Route a classified insight to its target files.

    Args:
        classification: The ClassificationResult from insight_classifier.
        content: The original insight content (for skill hint extraction).

    Returns:
        RoutingResult with target paths and write instructions.
    """
    category = classification.category
    skill_hints = _extract_skill_hints(content)

    # Conversation-only: no propagation needed
    if category == InsightCategory.CONVERSATION_ONLY:
        return RoutingResult(
            category=category,
            skip_reason="conversation_only — auto-memory captures, no propagation needed",
            skill_hints=skill_hints,
        )

    targets: List[PropagationTarget] = []
    project_memory_dir = _resolve_project_memory_dir()

    # Operational discovery → operational-context.md + project memory
    if category == InsightCategory.OPERATIONAL_DISCOVERY:
        targets.append(PropagationTarget(
            path=OPERATIONAL_CONTEXT,
            write_mode=WriteMode.APPEND,
            reason="operational discovery per Rule 20 / closed-loop memory",
        ))
        targets.append(PropagationTarget(
            path=project_memory_dir / "operational_discoveries.md",
            write_mode=WriteMode.APPEND,
            reason="project-level operational memory",
        ))

    # Design pattern → project memory + canonical skill file
    elif category == InsightCategory.DESIGN_PATTERN:
        targets.append(PropagationTarget(
            path=project_memory_dir / "design_patterns.md",
            write_mode=WriteMode.APPEND,
            reason="design pattern memory per Rule 20",
        ))
        # If skill hints found, target those skill files
        for skill_name in skill_hints[:2]:  # Max 2 skills
            skill_path = SKILLS_DIR / skill_name / "SKILL.md"
            targets.append(PropagationTarget(
                path=skill_path,
                write_mode=WriteMode.APPEND_SECTION,
                section="## Lessons Learned",
                reason=f"canonical skill file for {skill_name}",
            ))

    # Behavioral rule → feedback memory + rules/ if universal
    elif category == InsightCategory.BEHAVIORAL_RULE:
        targets.append(PropagationTarget(
            path=project_memory_dir / "behavioral_rules.md",
            write_mode=WriteMode.APPEND,
            reason="behavioral rule feedback per Rule 20",
        ))
        # Universal rules go to efficiency-rules.md (but need human review)
        # We don't auto-write to rules/ — too dangerous. Create task instead.
        targets.append(PropagationTarget(
            path=CLAUDE_DIR / "backlog" / "rule_candidates.md",
            write_mode=WriteMode.APPEND,
            reason="candidate for promotion to rules/ (needs review)",
        ))

    # Code change → task creation
    elif category == InsightCategory.CODE_CHANGE:
        targets.append(PropagationTarget(
            path=CLAUDE_DIR / "backlog" / "code_tasks.md",
            write_mode=WriteMode.APPEND,
            reason="code change task per Rule 20",
        ))

    # Documentation gap → doc task
    elif category == InsightCategory.DOCUMENTATION_GAP:
        targets.append(PropagationTarget(
            path=CLAUDE_DIR / "backlog" / "doc_tasks.md",
            write_mode=WriteMode.APPEND,
            reason="documentation gap task per Rule 20",
        ))

    return RoutingResult(
        category=category,
        targets=targets,
        skill_hints=skill_hints,
    )


def route_batch(
    classifications: List[ClassificationResult],
    contents: List[str],
) -> List[RoutingResult]:
    """Route multiple classified insights."""
    return [route(c, content) for c, content in zip(classifications, contents)]
