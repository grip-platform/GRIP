#!/usr/bin/env python3
"""
insight_promoter.py — Auto-promotion pipeline for approved insights to KONO.

Closes the open learning loop: detect -> store -> approve -> PROMOTE to KONO.

Reads approved insights from learning/approved/, promotes them to KONO
substrate as typed memories with appropriate dimension routing, and marks
them as promoted to prevent re-processing.

Memory type mapping (MacCubeFACE-inspired typed memory):
    - high_novelty + generalisable -> 'emergent' (dimension 10)
    - teaching_moment             -> 'procedural' (dimension 4)
    - generalisation              -> 'factual' (dimension 1)
    - default                     -> 'episodic' (dimension 7)

Usage:
    python3 lib/insight_promoter.py                # Promote all pending
    python3 lib/insight_promoter.py --dry-run      # Show what would promote
    python3 lib/insight_promoter.py --json          # JSON output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
LIB_DIR = CLAUDE_DIR / "lib"
APPROVED_DIR = CLAUDE_DIR / "learning" / "approved"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# Deterministic floor: /evo friction telemetry is a MEASUREMENT, not a skill.
# It must never enter the skill -> KONO promotion path. This is the
# promotion chokepoint; the matching creation gate lives in
# learning_detector.detect_learning_event. Shared predicate (DRY single
# source of truth) — fail-closed: refuse-to-promote is the safe verdict.
from telemetry_shape import is_evo_telemetry  # noqa: E402  (re-exported)


def classify_memory_type(insight: Dict[str, Any]) -> str:
    """
    Classify an approved insight into a KONO memory type.

    Uses the insight's trigger flags to determine the appropriate
    memory dimension for storage.
    """
    triggers = insight.get("triggers", {})
    evaluation = insight.get("evaluation", {})

    # Emergent: high novelty + generalisable (cross-project patterns)
    if triggers.get("high_novelty") and evaluation.get("is_generalisable"):
        return "emergent"

    # Procedural: teaching moments (how-to knowledge)
    if triggers.get("teaching_moment"):
        return "procedural"

    # Factual: generalisations (declarative knowledge)
    if triggers.get("generalisation"):
        return "factual"

    # Default: episodic (session-specific)
    return "episodic"


def load_approved_insights() -> List[Dict[str, Any]]:
    """Load all approved insights that haven't been promoted yet."""
    if not APPROVED_DIR.exists():
        return []

    insights = []
    for path in sorted(APPROVED_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            # Skip already promoted
            if data.get("promoted_to_kono"):
                continue
            data["_path"] = str(path)
            insights.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return insights


def promote_insight(insight: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    """
    Promote a single approved insight to KONO substrate.

    Returns:
        Dict with promotion result.
    """
    memory_type = classify_memory_type(insight)
    content = insight.get("full_content") or insight.get("description", "")
    skill_name = insight.get("skill_name", "unknown")

    if not content:
        return {"ok": False, "error": "empty content", "skill_name": skill_name}

    # Deterministic floor: never promote /evo friction telemetry to KONO.
    # It is a measurement, not a generalisable lesson. Refusing is safe and
    # fail-closed — the entry stays in learning/approved/ for the quarantine
    # step to route away, but it does NOT get a KONO embedding.
    if is_evo_telemetry(content):
        return {
            "ok": False,
            "error": "evo telemetry is not a skill — refused KONO promotion",
            "skill_name": skill_name,
            "telemetry": True,
        }

    # Build rich content with metadata
    rich_content = f"[Insight: {skill_name}] {content}"

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "skill_name": skill_name,
            "memory_type": memory_type,
            "content_preview": rich_content[:100],
        }

    return _store_and_mark(insight, rich_content, memory_type, skill_name)


def _store_and_mark(
    insight: Dict[str, Any],
    rich_content: str,
    memory_type: str,
    skill_name: str,
) -> Dict[str, Any]:
    """Store one insight in KONO and stamp the approved file as promoted."""
    try:
        from kono_substrate import MemorySubstrate
        substrate = MemorySubstrate()

        embedding_id = substrate.store_session_memory(
            content=rich_content,
            memory_type=memory_type,
        )

        if embedding_id is None:
            return {"ok": False, "error": "store returned None", "skill_name": skill_name}

        path = Path(insight["_path"])
        insight_data = json.loads(path.read_text())
        insight_data["promoted_to_kono"] = True
        insight_data["kono_embedding_id"] = embedding_id
        insight_data["kono_memory_type"] = memory_type
        insight_data["promoted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        path.write_text(json.dumps(insight_data, indent=2))

        return {
            "ok": True,
            "skill_name": skill_name,
            "memory_type": memory_type,
            "embedding_id": embedding_id,
        }

    except ImportError:
        return {"ok": False, "error": "kono_substrate not available", "skill_name": skill_name}
    except Exception as e:
        return {"ok": False, "error": str(e), "skill_name": skill_name}


def promote_all(dry_run: bool = False) -> Dict[str, Any]:
    """Promote all pending approved insights to KONO."""
    start = time.time()
    insights = load_approved_insights()

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "dry_run": dry_run,
        "total_approved": len(list(APPROVED_DIR.glob("*.json"))) if APPROVED_DIR.exists() else 0,
        "pending_promotion": len(insights),
        "promoted": [],
        "failed": [],
        "elapsed_s": 0.0,
    }

    for insight in insights:
        result = promote_insight(insight, dry_run=dry_run)
        if result.get("ok"):
            results["promoted"].append(result)
        else:
            results["failed"].append(result)

    results["elapsed_s"] = round(time.time() - start, 2)
    results["ok"] = len(results["failed"]) == 0

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP Insight Auto-Promoter")
    parser.add_argument("--dry-run", action="store_true", help="Show what would promote")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    results = promote_all(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"{prefix}Insight Promoter: {len(results['promoted'])} promoted, "
              f"{len(results['failed'])} failed "
              f"({results['pending_promotion']} pending of {results['total_approved']} approved)")
        for p in results["promoted"]:
            print(f"  + {p['skill_name']} -> {p['memory_type']}")
        for f in results["failed"]:
            print(f"  x {f['skill_name']}: {f.get('error', 'unknown')}")


if __name__ == "__main__":
    main()
