"""
Auto-Skill Generator for GRIP.

Mines GRIP knowledge substrate (CLAUDE.md evolution, MEMORY.md, KONO,
metrics, learning insights) to detect recurring patterns and auto-generate
skill proposals. Part of the insights-driven learning pipeline.

Usage:
    python3 lib/auto_skill_generator.py detect     # Detect patterns from usage data
    python3 lib/auto_skill_generator.py generate    # Generate skill proposals
    python3 lib/auto_skill_generator.py issues      # Create GitHub issues for proposals
    python3 lib/auto_skill_generator.py report      # Full report (detect + analyze)

Issue #120: Auto-generate skills from detected patterns using GRIP knowledge substrate
Version: 1.0.0
"""

import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from insights_issue_budget import create_issue

logger = logging.getLogger(__name__)

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
SKILLS_DIR = CLAUDE_DIR / "skills"
LEARNING_DIR = CLAUDE_DIR / "learning"
METRICS_PATH = LEARNING_DIR / "metrics.jsonl"
MEMORY_PATH = CLAUDE_DIR / "projects"
PROPOSALS_PATH = LEARNING_DIR / "skill-proposals.jsonl"

# YSH (You Should Have) threshold: a pattern must recur 2+ times before
# proposing skill generation. Lowered from 3 by V>> on 2026-10-03 (see
# rules/knowledge-maturation-functor.md). The lower count is safe here
# because a proposal is not a skill: it is written to skill-proposals.jsonl
# and, within the shared three-per-24-hour budget, opened as an issue for review,
# which is the promotion gate's first implementation with a human in the loop.
# Override via GRIP_YSH_THRESHOLD env var for testing.
YSH_THRESHOLD = int(os.environ.get("GRIP_YSH_THRESHOLD", "2"))


def _load_metrics(limit: int = 500) -> list[dict]:
    """Load recent metrics events."""
    if not METRICS_PATH.exists():
        return []
    events = []
    try:
        with open(METRICS_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.debug("Skipping malformed metrics line: %s", line[:80])
                        continue
    except FileNotFoundError:
        logger.info("Metrics file not found at %s", METRICS_PATH)
        return []
    except Exception as e:
        logger.warning("Failed to load metrics: %s", e)
        return []
    return events[-limit:]


def _load_memory_todos() -> list[dict]:
    """Load TODO items from all MEMORY.md files."""
    todos = []
    # Main memory
    main_memory = CLAUDE_DIR / "projects"
    for memory_file in main_memory.rglob("MEMORY.md"):
        try:
            content = memory_file.read_text()
            # Extract table rows with | # | Item | Origin | Status | Notes |
            for match in re.finditer(
                r'\|\s*(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]*)\s*\|',
                content
            ):
                todos.append({
                    "number": match.group(1).strip(),
                    "item": match.group(2).strip(),
                    "origin": match.group(3).strip(),
                    "status": match.group(4).strip(),
                    "notes": match.group(5).strip(),
                    "source": str(memory_file),
                })
        except Exception as e:
            logger.warning("Failed to parse MEMORY file %s: %s", memory_file, e)
            continue
    return todos


def _get_existing_skills() -> set[str]:
    """Get names of all existing skills."""
    if not SKILLS_DIR.exists():
        return set()
    return {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}


def _detect_skill_gaps(metrics: list[dict], existing_skills: set[str]) -> list[dict]:
    """Find skills invoked in metrics but not existing on disk."""
    patterns = []
    skill_refs = Counter()
    for event in metrics:
        if event.get("event") == "skill_invoked":
            skill = event.get("skill", "")
            if skill:
                skill_refs[skill] += 1

    for skill, count in skill_refs.items():
        if count >= YSH_THRESHOLD and skill not in existing_skills:
            patterns.append({
                "pattern": f"skill '{skill}' invoked {count}x but doesn't exist",
                "frequency": count,
                "source": "metrics",
                "proposed_skill": skill,
                "reason": f"Referenced {count} times in metrics but no skill directory found",
            })
    return patterns


def _detect_learned_insights(todos: list[dict], existing_skills: set[str]) -> list[dict]:
    """Find LEARNED TODO items not covered by existing skills."""
    patterns = []
    learned_items = [t for t in todos if "LEARNED" in t.get("status", "")]
    for item in learned_items:
        item_text = item["item"].lower()
        covered = False
        for skill_name in existing_skills:
            if any(word in skill_name for word in item_text.split()[:3]):
                covered = True
                break

        if not covered:
            proposed_name = re.sub(r'[^a-z0-9-]', '-', item_text[:40]).strip('-')
            proposed_name = re.sub(r'-+', '-', proposed_name)
            patterns.append({
                "pattern": f"LEARNED insight: {item['item'][:80]}",
                "frequency": 1,
                "source": f"MEMORY TODO #{item['number']}",
                "proposed_skill": proposed_name,
                "reason": f"Learned from {item['origin']}: {item['notes'][:100]}",
            })
    return patterns


def _detect_fix_patterns(metrics: list[dict]) -> list[dict]:
    """Find recurring error-fix patterns suggesting missing automation."""
    patterns = []
    fix_events = [e for e in metrics if e.get("event") in ("error_fixed", "fix_applied")]
    fix_types = Counter(e.get("fix_type", e.get("category", "unknown")) for e in fix_events)
    for fix_type, count in fix_types.items():
        if count >= YSH_THRESHOLD:
            patterns.append({
                "pattern": f"Fix type '{fix_type}' applied {count}x",
                "frequency": count,
                "source": "metrics",
                "proposed_skill": f"auto-fix-{fix_type}",
                "reason": f"Recurring fix pattern suggests automation opportunity",
            })
    return patterns


def _detect_mode_transitions(metrics: list[dict]) -> list[dict]:
    """Find frequent mode transition pairs suggesting composite modes."""
    patterns = []
    mode_events = [e for e in metrics if e.get("event") == "mode_activated"]
    if len(mode_events) >= 4:
        mode_pairs = []
        for i in range(len(mode_events) - 1):
            pair = (mode_events[i].get("mode", ""), mode_events[i + 1].get("mode", ""))
            if pair[0] and pair[1] and pair[0] != pair[1]:
                mode_pairs.append(f"{pair[0]}+{pair[1]}")
        pair_counts = Counter(mode_pairs)
        for pair, count in pair_counts.items():
            if count >= YSH_THRESHOLD:
                patterns.append({
                    "pattern": f"Mode transition '{pair}' happens {count}x",
                    "frequency": count,
                    "source": "metrics",
                    "proposed_skill": f"composite-{pair.replace('+', '-')}",
                    "reason": f"Frequent mode switching suggests a composite mode would help",
                })
    return patterns


def _detect_kono_patterns() -> list[dict]:
    """Query KONO database for high-recall memories worth formalizing."""
    patterns = []
    try:
        import sqlite3
        db_path = CLAUDE_DIR / "embeddings.db"
        if db_path.exists():
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute(
                "SELECT content, access_count FROM embeddings "
                "WHERE ((access_count >= ? AND type NOT LIKE 'concept_%') "
                "OR type IN ('ltm', 'insight', 'permanent')) "
                "AND COALESCE(type, '') != 'session_summary' "
                "AND LOWER(LTRIM(COALESCE(content, ''))) NOT LIKE '[session_summary]%' "
                "ORDER BY access_count DESC, id DESC LIMIT 20",
                (YSH_THRESHOLD,)
            )
            for content, recall_count in cursor.fetchall():
                patterns.append({
                    "pattern": f"KONO memory '{(content or '')[:60]}' recalled {recall_count}x",
                    "frequency": max(recall_count, 1),
                    "source": "KONO",
                    "proposed_skill": None,
                    "reason": "Frequently recalled memory suggests formalization as rule or skill",
                })
            conn.close()
        else:
            logger.info("KONO database not found at %s, skipping memory analysis", db_path)
    except ImportError:
        logger.info("sqlite3 not available, skipping KONO analysis")
    except Exception as e:
        logger.warning("KONO analysis failed: %s", e)
    return patterns


def detect_patterns() -> list[dict]:
    """Detect recurring patterns from usage data that could become skills.

    Analyzes:
    1. Repeated manual workflows (same sequence of tool calls)
    2. Frequently referenced but non-existent skills in metrics
    3. TODO items that recur across sessions (status: LEARNED)
    4. Error patterns that keep appearing
    5. KONO memories with high recall frequency

    Returns:
        List of {pattern, frequency, source, proposed_skill, reason}
    """
    existing_skills = _get_existing_skills()
    metrics = _load_metrics()
    todos = _load_memory_todos()

    patterns = []
    patterns.extend(_detect_skill_gaps(metrics, existing_skills))
    patterns.extend(_detect_learned_insights(todos, existing_skills))
    patterns.extend(_detect_fix_patterns(metrics))
    patterns.extend(_detect_mode_transitions(metrics))
    patterns.extend(_detect_kono_patterns())
    patterns.extend(_detect_routing_patterns())

    patterns.sort(key=lambda p: -p["frequency"])
    return patterns


def _detect_routing_patterns() -> list[dict]:
    """Detect routing patterns from BAR metrics for self-improvement feedback.

    YSH patterns, each firing at YSH_THRESHOLD occurrences (2 since 2026-10-03):
    - Frequent budget capping (same level capped repeatedly) -> suggest calibration
    - Low-confidence routing (confidence < 0.4, repeatedly) -> suggest signal expansion
    - Wrong outcomes (repeated wrong_approach for same tier) -> suggest heuristic update
    """
    routing_patterns = []
    routing_metrics = CLAUDE_DIR / "data" / "routing-metrics.jsonl"

    if not routing_metrics.exists():
        return routing_patterns

    decisions = []
    outcomes = []
    try:
        with open(routing_metrics) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "outcome":
                    outcomes.append(entry)
                else:
                    decisions.append(entry)
    except Exception:
        return routing_patterns

    # Pattern: frequent budget capping
    capped_levels = Counter(
        d.get("power_level", "") for d in decisions if d.get("budget_capped")
    )
    for level, count in capped_levels.items():
        if count >= YSH_THRESHOLD:
            routing_patterns.append({
                "pattern": f"BAR budget-capped '{level}' {count}x",
                "frequency": count,
                "source": "routing-metrics",
                "proposed_skill": None,
                "reason": f"Budget metabolism capping {level} frequently. Consider calibrating thresholds.",
            })

    # Pattern: low-confidence routing
    low_conf = [d for d in decisions if d.get("confidence", 1.0) < 0.4]
    if len(low_conf) >= YSH_THRESHOLD:
        routing_patterns.append({
            "pattern": f"BAR low-confidence routing {len(low_conf)}x",
            "frequency": len(low_conf),
            "source": "routing-metrics",
            "proposed_skill": None,
            "reason": "Many prompts classified with low confidence. Expand heuristic signals.",
        })

    # Pattern: wrong outcomes
    wrong = Counter(
        o.get("actual_level_needed", "unknown") for o in outcomes
        if o.get("outcome") == "wrong_approach"
    )
    for level, count in wrong.items():
        if count >= YSH_THRESHOLD:
            routing_patterns.append({
                "pattern": f"BAR wrong_approach -> {level} {count}x",
                "frequency": count,
                "source": "routing-metrics",
                "proposed_skill": None,
                "reason": f"Routing to wrong level (should have been {level}). Update heuristic weights.",
            })

    return routing_patterns


def generate_proposals(patterns: list[dict] = None) -> list[dict]:
    """Generate skill proposals from detected patterns.

    Args:
        patterns: Pre-detected patterns (or detects fresh if None).

    Returns:
        List of skill proposals with name, description, keywords, and template.
    """
    if patterns is None:
        patterns = detect_patterns()

    existing_skills = _get_existing_skills()
    proposals = []

    for pattern in patterns:
        proposed = pattern.get("proposed_skill")
        if not proposed or proposed in existing_skills:
            continue

        # Build proposal
        proposal = {
            "name": proposed,
            "description": pattern["reason"],
            "keywords": _extract_keywords(pattern["pattern"]),
            "source_pattern": pattern["pattern"],
            "frequency": pattern["frequency"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "proposed",
        }
        proposals.append(proposal)

    # Save proposals
    if proposals:
        PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROPOSALS_PATH, "a") as f:
            for p in proposals:
                f.write(json.dumps(p, default=str) + "\n")

    return proposals


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from pattern text."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "but", "and", "or", "not", "for", "from", "with", "that",
        "this", "has", "have", "does", "doesn", "don", "its", "only",
    }
    words = re.findall(r'[a-z]+', text.lower())
    return [w for w in words if len(w) > 3 and w not in stop_words][:5]


def create_issues_from_proposals(
    proposals: list[dict] = None, dry_run: bool = False, *, runner=None, ledger_path=None
) -> list[dict]:
    """Create GitHub issues for skill proposals that meet threshold.

    Args:
        proposals: Pre-generated proposals (or generates fresh if None).
        dry_run: If True, preview candidates without checking GitHub or the budget.

    Returns:
        Results naming each proposal and its issue_url, skipped reason, or error.
    """
    if proposals is None:
        proposals = generate_proposals()

    results = []
    # Only create issues for high-frequency patterns
    for proposal in proposals[:3]:  # Max 3 issues per run
        if proposal["frequency"] < YSH_THRESHOLD:
            continue

        title = f"feat: Auto-generate skill '{proposal['name']}' from detected pattern"
        body = (
            f"## Pattern Detected\n\n"
            f"{proposal['source_pattern']}\n\n"
            f"## Frequency\n\n"
            f"Seen {proposal['frequency']}x (YSH threshold: {YSH_THRESHOLD})\n\n"
            f"## Proposed Skill\n\n"
            f"- **Name**: `{proposal['name']}`\n"
            f"- **Description**: {proposal['description']}\n"
            f"- **Keywords**: {', '.join(proposal['keywords'])}\n\n"
            f"## Source\n\n"
            f"Auto-detected by `lib/auto_skill_generator.py` (Issue #120)\n\n"
            f"---\n"
            f"*Generated by the insights-driven learning pipeline.*"
        )

        if dry_run:
            results.append({
                "proposal": proposal["name"],
                "dry_run": True,
                "title": title,
                "body_preview": body[:200],
            })
            continue

        result = create_issue(title, body, runner=runner, ledger_path=ledger_path)
        results.append({"proposal": proposal["name"], **result})

    return results


def format_report(patterns: list[dict], proposals: list[dict]) -> str:
    """Format detection results as readable report."""
    lines = [
        "## Insights-Driven Learning Report",
        "",
        f"**Patterns detected**: {len(patterns)}",
        f"**Skill proposals**: {len(proposals)}",
        f"**YSH threshold**: {YSH_THRESHOLD}x",
        "",
    ]

    if patterns:
        lines.append("### Detected Patterns")
        lines.append("")
        lines.append("| # | Pattern | Freq | Source | Proposed Skill |")
        lines.append("|---|---------|------|--------|----------------|")
        for i, p in enumerate(patterns[:10], 1):
            skill = p.get("proposed_skill") or "(needs analysis)"
            lines.append(
                f"| {i} | {p['pattern'][:50]} | {p['frequency']} | "
                f"{p['source']} | {skill} |"
            )
        lines.append("")

    if proposals:
        lines.append("### Skill Proposals")
        lines.append("")
        for p in proposals:
            lines.append(f"- **{p['name']}**: {p['description'][:80]}")
            lines.append(f"  Keywords: {', '.join(p['keywords'])}")
        lines.append("")

    if not patterns:
        lines.append("No recurring patterns detected yet. Need more usage data.")
        lines.append("Track events with: `python3 lib/metrics_tracker.py record <event>`")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 lib/auto_skill_generator.py <detect|generate|issues|report> [--dry-run]")
        sys.exit(1)

    cmd = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    # Configure logging for CLI
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if cmd == "detect":
        patterns = detect_patterns()
        print(json.dumps(patterns, indent=2, default=str))

    elif cmd == "generate":
        proposals = generate_proposals()
        if proposals:
            print(f"Generated {len(proposals)} skill proposal(s):")
            for p in proposals:
                print(f"  - {p['name']}: {p['description'][:60]}")
        else:
            print("No new skill proposals (insufficient pattern data or all covered).")

    elif cmd == "issues":
        results = create_issues_from_proposals(dry_run=dry_run)
        for r in results:
            if r.get("dry_run"):
                print(f"[DRY-RUN] Candidate before budget/deduplication for '{r['proposal']}': {r['title']}")
            elif "issue_url" in r:
                print(f"Created issue for '{r['proposal']}': {r['issue_url']}")
            elif "skipped" in r:
                print(f"Skipped for '{r['proposal']}': {r['skipped']}")
            else:
                print(f"Failed for '{r['proposal']}': {r.get('error', 'unknown')}")

    elif cmd == "report":
        patterns = detect_patterns()
        proposals = generate_proposals(patterns)
        print(format_report(patterns, proposals))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
