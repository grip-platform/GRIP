#!/usr/bin/env python3
"""
tip_injector.py -- Proactive Tip Injection (TIM Phase 3).

Queries KONO/insights for relevant tips and formats them as [GUIDELINE]
blocks for injection into agent prompts before task execution.

arXiv:2603.10600 -- Tier -1 in GRIP-First Retrieval.

Key design decisions:
- Complexity gate: only inject for tasks with estimated complexity > 1
  (paper evidence: simple tasks degrade with tip injection, -5.6pp)
- Similarity threshold: 0.6 (paper's optimal for cosine retrieval)
- Max 5 tips (paper's top-k setting)
- Tips formatted as scannable [GUIDELINE] blocks with priority + category
"""

from __future__ import annotations

import json, os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
INSIGHTS_FILE = CLAUDE_DIR / "learning" / "insights.jsonl"
SIMILARITY_THRESHOLD = 0.6
MAX_TIPS = 5

# Complexity keywords that suggest a task needs tip injection.
# Simple queries (single-file reads, status checks) don't benefit.
COMPLEXITY_SIGNALS = {
    "refactor", "migrate", "fix", "debug", "implement", "integrate",
    "deploy", "security", "performance", "optimize", "architecture",
    "multi", "cross", "concurrent", "race", "distributed", "pipeline",
}


@dataclass
class Tip:
    """A retrievable tip from the learning pipeline."""
    tip_type: str  # strategy | recovery | optimization
    text: str
    learning_score: float
    source: str
    tags: list
    attribution: Optional[dict] = None

    def to_guideline(self) -> str:
        """Format as a [GUIDELINE] block for prompt injection."""
        priority = "HIGH" if self.learning_score >= 0.8 else "MEDIUM"
        category = self.tip_type.upper()
        lines = [f"[PRIORITY: {priority}] {category} Tip:"]
        lines.append(f"  {self.text}")
        if self.attribution and self.attribution.get("root_cause"):
            lines.append(f"  Root cause: {self.attribution['root_cause'][:100]}")
        return "\n".join(lines)


def estimate_complexity(task_description: str) -> int:
    """Estimate task complexity from description keywords.

    Returns 0 (simple), 1 (moderate), or 2 (complex).
    Complexity gate: tips only injected when result > 0.
    """
    words = set(task_description.lower().split())
    matches = words & COMPLEXITY_SIGNALS
    if len(matches) >= 3:
        return 2
    if len(matches) >= 1:
        return 1
    # Length heuristic: long descriptions usually mean complex tasks
    if len(task_description) > 200:
        return 1
    return 0


def load_tips(tip_type_filter: Optional[str] = None) -> list:
    """Load tips from insights.jsonl, optionally filtered by tip_type."""
    if not INSIGHTS_FILE.exists():
        return []
    tips = []
    for line in INSIGHTS_FILE.read_text(errors="replace").strip().split("\n"):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        # Only include records with tip_type (TIM-enhanced insights)
        tt = record.get("tip_type")
        if not tt:
            continue
        if tip_type_filter and tt != tip_type_filter:
            continue
        tips.append(Tip(
            tip_type=tt,
            text=record.get("text", ""),
            learning_score=record.get("learning_score", 0.5),
            source=record.get("source", "unknown"),
            tags=record.get("tags", []),
            attribution=record.get("attribution"),
        ))
    return tips


def retrieve_tips(task_description: str, max_tips: int = MAX_TIPS) -> list:
    """Retrieve relevant tips for a task, respecting complexity gate.

    Returns empty list if task is too simple for tip injection.
    Uses keyword matching as baseline; semantic search via KONO is future work.
    """
    complexity = estimate_complexity(task_description)
    if complexity == 0:
        return []

    tips = load_tips()
    if not tips:
        return []

    # Score tips by tag overlap with task description
    task_words = set(task_description.lower().split())
    scored = []
    for tip in tips:
        tag_words = set()
        for tag in tip.tags:
            tag_words.update(tag.lower().replace(":", " ").split())
        overlap = len(task_words & tag_words)
        # Boost by learning score
        score = overlap * 0.3 + tip.learning_score * 0.7
        scored.append((score, tip))

    # Sort by score descending, take top-k
    scored.sort(key=lambda x: -x[0])
    return [tip for _, tip in scored[:max_tips]]


def format_guidelines(tips: list) -> str:
    """Format tips as a GUIDELINES section for prompt injection."""
    if not tips:
        return ""
    lines = ["", "## Guidelines (from learned experience)", ""]
    for tip in tips:
        lines.append(tip.to_guideline())
        lines.append("")
    return "\n".join(lines)


def inject_tips(task_description: str) -> str:
    """Main entry point: retrieve and format tips for a task.

    Returns empty string if no tips are relevant or task is too simple.
    """
    tips = retrieve_tips(task_description)
    return format_guidelines(tips)


if __name__ == "__main__":
    import sys
    desc = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "refactor the authentication module"
    print(f"Task: {desc}")
    print(f"Complexity: {estimate_complexity(desc)}")
    result = inject_tips(desc)
    if result:
        print(result)
    else:
        print("No tips to inject (task too simple or no matching tips).")
