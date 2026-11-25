#!/usr/bin/env python3
"""
embodiment_feedback.py — GRIP-First retrieval feedback loop.

Analyses embodiment-tracker.jsonl to tune GRIP-First retrieval thresholds.
Closes the feedback loop: track hits/misses -> analyse patterns -> adjust floors.

Reads: cache/embodiment-tracker.jsonl (recorded by grip-first-retrieval-hook.py)
Writes: cache/embodiment-tuning.json (threshold adjustments)

Threshold tuning logic:
    - High hit rate (>80%): thresholds may be too loose, tighten slightly
    - Low hit rate (<40%): thresholds may be too tight, loosen slightly
    - Sweet spot (40-80%): no adjustment needed
    - Minimum 20 observations required before any adjustment

Usage:
    python3 lib/embodiment_feedback.py analyse    # Analyse current data
    python3 lib/embodiment_feedback.py tune        # Propose threshold adjustments
    python3 lib/embodiment_feedback.py stats       # Summary statistics
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
TRACKER_FILE = CLAUDE_DIR / "cache" / "embodiment-tracker.jsonl"
TUNING_FILE = CLAUDE_DIR / "cache" / "embodiment-tuning.json"

# Minimum observations before adjusting thresholds
MIN_OBSERVATIONS = 20

# Current default floors (from grip_first_common.py)
DEFAULT_SIMILARITY_FLOOR = 0.12
DEFAULT_HIGH_SIMILARITY_FLOOR = 0.25

# Adjustment step size (conservative — 0.02 per iteration)
ADJUSTMENT_STEP = 0.02

# Bounds to prevent over-tuning
MIN_FLOOR = 0.05
MAX_FLOOR = 0.40


def load_tracker() -> List[Dict[str, Any]]:
    """Load embodiment tracker entries."""
    if not TRACKER_FILE.exists():
        return []
    entries = []
    for line in TRACKER_FILE.read_text().strip().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def analyse(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyse embodiment patterns."""
    if not entries:
        return {"ok": True, "total": 0, "message": "No data yet"}

    outcomes = Counter()
    for entry in entries:
        outcome = entry.get("outcome", entry.get("result", "unknown"))
        outcomes[outcome] += 1

    total = len(entries)
    hits = outcomes.get("hit", 0) + outcomes.get("found", 0) + outcomes.get("kono_hit", 0)
    misses = outcomes.get("miss", 0) + outcomes.get("not_found", 0) + outcomes.get("kono_miss", 0)
    hit_rate = hits / total if total > 0 else 0

    return {
        "ok": True,
        "total": total,
        "hits": hits,
        "misses": misses,
        "hit_rate": round(hit_rate, 3),
        "outcomes": dict(outcomes),
        "sufficient_data": total >= MIN_OBSERVATIONS,
    }


def propose_tuning(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Propose threshold adjustments based on embodiment data."""
    # Load existing tuning
    current_low = DEFAULT_SIMILARITY_FLOOR
    current_high = DEFAULT_HIGH_SIMILARITY_FLOOR
    if TUNING_FILE.exists():
        try:
            data = json.loads(TUNING_FILE.read_text())
            current_low = data.get("similarity_floor", DEFAULT_SIMILARITY_FLOOR)
            current_high = data.get("high_similarity_floor", DEFAULT_HIGH_SIMILARITY_FLOOR)
        except (json.JSONDecodeError, OSError):
            pass

    if not analysis.get("sufficient_data"):
        return {
            "ok": True,
            "action": "none",
            "reason": f"Insufficient data ({analysis.get('total', 0)}/{MIN_OBSERVATIONS})",
            "current": {"similarity_floor": current_low, "high_similarity_floor": current_high},
        }

    hit_rate = analysis.get("hit_rate", 0)

    if hit_rate > 0.80:
        # Too many hits — thresholds might be too loose
        new_low = min(current_low + ADJUSTMENT_STEP, MAX_FLOOR)
        new_high = min(current_high + ADJUSTMENT_STEP, MAX_FLOOR)
        action = "tighten"
        reason = f"Hit rate {hit_rate:.1%} > 80% — tightening floors to reduce false positives"
    elif hit_rate < 0.40:
        # Too few hits — thresholds might be too tight
        new_low = max(current_low - ADJUSTMENT_STEP, MIN_FLOOR)
        new_high = max(current_high - ADJUSTMENT_STEP, MIN_FLOOR)
        action = "loosen"
        reason = f"Hit rate {hit_rate:.1%} < 40% — loosening floors to improve recall"
    else:
        # Sweet spot
        new_low = current_low
        new_high = current_high
        action = "none"
        reason = f"Hit rate {hit_rate:.1%} in target range (40-80%)"

    proposal = {
        "ok": True,
        "action": action,
        "reason": reason,
        "current": {"similarity_floor": current_low, "high_similarity_floor": current_high},
        "proposed": {"similarity_floor": new_low, "high_similarity_floor": new_high},
        "observations": analysis.get("total", 0),
        "hit_rate": hit_rate,
    }

    # Save tuning state
    TUNING_FILE.parent.mkdir(parents=True, exist_ok=True)
    TUNING_FILE.write_text(json.dumps({
        "similarity_floor": new_low,
        "high_similarity_floor": new_high,
        "last_analysis": analysis,
        "last_action": action,
    }, indent=2))

    return proposal


def main() -> None:
    parser = argparse.ArgumentParser(description="GRIP Embodiment Feedback Loop")
    parser.add_argument("command", choices=["analyse", "tune", "stats"])

    args = parser.parse_args()
    entries = load_tracker()

    if args.command == "analyse" or args.command == "stats":
        result = analyse(entries)
        print(json.dumps(result, indent=2))

    elif args.command == "tune":
        analysis = analyse(entries)
        proposal = propose_tuning(analysis)
        print(json.dumps(proposal, indent=2))


if __name__ == "__main__":
    main()
