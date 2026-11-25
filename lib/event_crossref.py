#!/usr/bin/env python3
"""
event_crossref.py — Cross-reference query across GRIP's multi-view storage.

Enables "show me all views of event X" queries across:
    - genome-outcomes.jsonl (run_id, genome_id, scores, genes)
    - hypotheses.jsonl (id, pr_number, claim, status, effect_size)
    - evo-convergence.jsonl (timestamp, calibrated, ff_rate, cube scores)
    - insights.jsonl (id, text, learning_score, tags)

Links events via shared keys: run_id, pr_number, timestamp proximity.

Usage:
    python3 lib/event_crossref.py query --run-id 762b4fa9
    python3 lib/event_crossref.py query --pr 1377
    python3 lib/event_crossref.py query --since 2026-03-20
    python3 lib/event_crossref.py stats
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# Multi-view storage locations
GENOME_OUTCOMES = CLAUDE_DIR / "cache" / "genome-outcomes.jsonl"
HYPOTHESES = CLAUDE_DIR / "learning" / "hypotheses.jsonl"
EVO_CONVERGENCE = CLAUDE_DIR / "learning" / "evo-convergence.jsonl"
INSIGHTS = CLAUDE_DIR / "learning" / "insights.jsonl"


def _load_jsonl(path: Path, max_lines: int = 5000) -> List[Dict[str, Any]]:
    """Load a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    entries = []
    try:
        for line in path.read_text().strip().splitlines()[-max_lines:]:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        pass
    return entries


def query_by_run_id(run_id: str) -> Dict[str, Any]:
    """Find all views of an event by run_id."""
    result = {"run_id": run_id, "views": {}}

    # genome-outcomes: direct match on run_id
    for entry in _load_jsonl(GENOME_OUTCOMES):
        if entry.get("run_id", "") == run_id:
            result["views"]["genome_outcome"] = {
                "run_id": entry.get("run_id"),
                "genome_id": entry.get("genome_id"),
                "score_before": entry.get("score_before"),
                "score_after": entry.get("score_after"),
                "improvement": entry.get("improvement"),
                "depth_reached": entry.get("depth_reached"),
                "cube_scores": entry.get("cube_scores"),
                "expressed_genes": len(entry.get("expressed_genes", [])),
                "invoked_genes": len(entry.get("invoked_genes", [])),
                "timestamp": entry.get("timestamp"),
            }
            break

    return result


def query_by_pr(pr_number: int) -> Dict[str, Any]:
    """Find all views of an event by PR number."""
    result = {"pr_number": pr_number, "views": {}}

    # hypotheses: direct match on pr_number
    hypotheses = []
    for entry in _load_jsonl(HYPOTHESES):
        if entry.get("pr_number") == pr_number:
            hypotheses.append({
                "id": entry.get("id"),
                "claim": entry.get("claim"),
                "metric": entry.get("metric"),
                "prediction": entry.get("prediction"),
                "status": entry.get("status"),
                "effect_size": entry.get("effect_size"),
                "baseline": entry.get("baseline"),
                "actual_value": entry.get("actual_value"),
            })
    if hypotheses:
        result["views"]["hypotheses"] = hypotheses

    # insights: search for PR reference in text
    insights = []
    pr_str = f"#{pr_number}"
    for entry in _load_jsonl(INSIGHTS):
        text = entry.get("text", "")
        if pr_str in text or str(pr_number) in entry.get("context", ""):
            insights.append({
                "id": entry.get("id"),
                "text": entry.get("text", "")[:200],
                "learning_score": entry.get("learning_score"),
                "tags": entry.get("tags"),
            })
    if insights:
        result["views"]["insights"] = insights

    return result


def query_by_timewindow(since: str, until: Optional[str] = None) -> Dict[str, Any]:
    """Find all events in a time window."""
    result = {"since": since, "until": until, "views": {}}

    # Parse timestamps
    since_dt = datetime.fromisoformat(since)
    until_dt = datetime.fromisoformat(until) if until else datetime.now(timezone.utc)

    def _in_window(ts_str: str) -> bool:
        try:
            ts = datetime.fromisoformat(ts_str)
            return since_dt <= ts <= until_dt
        except (ValueError, TypeError):
            return False

    # genome-outcomes in window
    outcomes = []
    for entry in _load_jsonl(GENOME_OUTCOMES):
        if _in_window(entry.get("timestamp", "")):
            outcomes.append({
                "run_id": entry.get("run_id"),
                "improvement": entry.get("improvement"),
                "score_after": entry.get("score_after"),
            })
    if outcomes:
        result["views"]["genome_outcomes"] = outcomes

    # evo-convergence in window
    evo_entries = []
    for entry in _load_jsonl(EVO_CONVERGENCE):
        if _in_window(entry.get("timestamp", "")):
            evo_entries.append({
                "calibrated": entry.get("calibrated"),
                "ff_rate": entry.get("ff_rate"),
                "confidence": entry.get("confidence"),
                "cube": entry.get("cube"),
            })
    if evo_entries:
        result["views"]["evo_convergence"] = evo_entries

    return result


def get_stats() -> Dict[str, Any]:
    """Get statistics about the multi-view storage."""
    return {
        "genome_outcomes": len(_load_jsonl(GENOME_OUTCOMES)),
        "hypotheses": len(_load_jsonl(HYPOTHESES)),
        "evo_convergence": len(_load_jsonl(EVO_CONVERGENCE)),
        "insights": len(_load_jsonl(INSIGHTS)),
        "files": {
            "genome_outcomes": str(GENOME_OUTCOMES),
            "hypotheses": str(HYPOTHESES),
            "evo_convergence": str(EVO_CONVERGENCE),
            "insights": str(INSIGHTS),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP Event Cross-Reference")
    sub = parser.add_subparsers(dest="command")

    q = sub.add_parser("query", help="Query across multi-view storage")
    q.add_argument("--run-id", help="Query by run_id")
    q.add_argument("--pr", type=int, help="Query by PR number")
    q.add_argument("--since", help="Query by time window start (ISO)")
    q.add_argument("--until", help="Query by time window end (ISO)")

    sub.add_parser("stats", help="Show storage statistics")

    args = parser.parse_args()

    if args.command == "query":
        if args.run_id:
            result = query_by_run_id(args.run_id)
        elif args.pr:
            result = query_by_pr(args.pr)
        elif args.since:
            result = query_by_timewindow(args.since, args.until)
        else:
            parser.error("Provide --run-id, --pr, or --since")
            return
        print(json.dumps(result, indent=2, default=str))

    elif args.command == "stats":
        print(json.dumps(get_stats(), indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
