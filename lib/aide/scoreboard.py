"""AIDE scoreboard — prediction accuracy from the prediction registry.

Pure, dependency-free module: reads the prediction registry, computes an
accuracy summary, and renders a one-line plain-language trust line for a
Slack digest footer. No network, no LLM, no side effects beyond reading
the registry.

Usage:
    from lib.aide.scoreboard import accuracy_summary, render_trust_line
    summary = accuracy_summary()
    print(render_trust_line(summary))
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))

# sys.path robustness: make lib.aide.* importable from any cwd.
# Insert CLAUDE_DIR if missing — and only insert; never remove the
# caller's cwd entry (that is another module's concern).
if str(CLAUDE_DIR) not in sys.path:
    sys.path.insert(0, str(CLAUDE_DIR))

# Evidence marker appended by the human grader (see slack_oracle.py).
HUMAN_GRADE_MARKER = "human-graded via Slack"


def _load_predictions(registry: Optional[Any]) -> List[Dict[str, Any]]:
    """Return every prediction record; build the default registry if needed.

    ``registry`` may be any object exposing ``all(limit=0) -> list[dict]``.
    ``limit=0`` means no cap (see lib.aide.jsonl_io.read), so the whole
    registry is graded, not just the most recent N.
    """
    if registry is None:
        from lib.aide.adaptive_engine import PredictionRegistry

        registry = PredictionRegistry()
    return registry.all(limit=0)


def accuracy_summary(registry: Optional[Any] = None) -> Dict[str, Any]:
    """Summarize AIDE's prediction track record from the registry.

    Args:
        registry: Optional object exposing ``all(limit=0) -> list[dict]``
            (a real PredictionRegistry or a test fake). Defaults to the
            on-disk registry.

    Returns:
        {
          "graded":       int,           # outcome is not None
          "confirmed":    int,           # outcome == "confirmed"
          "falsified":    int,           # outcome == "falsified"
          "open":         int,           # outcome is None
          "total":        int,           # all predictions seen
          "accuracy":     float | None,  # confirmed / graded (None if graded == 0)
          "human_graded": int,           # graded with "human-graded via Slack" evidence
          "auto_graded":  int,           # graded without that marker
        }
    """
    predictions = _load_predictions(registry)

    graded = confirmed = falsified = open_count = 0
    human_graded = auto_graded = 0

    for rec in predictions:
        outcome = rec.get("outcome")
        if outcome is None:
            open_count += 1
            continue
        graded += 1
        if outcome == "confirmed":
            confirmed += 1
        elif outcome == "falsified":
            falsified += 1
        evidence = rec.get("evidence") or []
        if any(HUMAN_GRADE_MARKER in str(item) for item in evidence):
            human_graded += 1
        else:
            auto_graded += 1

    accuracy = (confirmed / graded) if graded else None

    return {
        "graded": graded,
        "confirmed": confirmed,
        "falsified": falsified,
        "open": open_count,
        "total": len(predictions),
        "accuracy": accuracy,
        "human_graded": human_graded,
        "auto_graded": auto_graded,
    }


def render_trust_line(summary: Dict[str, Any]) -> str:
    """One plain-language line for a Slack digest footer (<= 140 chars).

    Example:
        "AIDE track record: 3 graded — 2 right, 1 wrong (67% accurate). 4 still open."
    """
    graded = summary.get("graded", 0)
    if graded == 0:
        return "AIDE has no graded predictions yet — tap a button to start teaching it."

    confirmed = summary.get("confirmed", 0)
    falsified = summary.get("falsified", 0)
    open_count = summary.get("open", 0)
    accuracy = summary.get("accuracy")
    pct = int(round(accuracy * 100)) if accuracy is not None else 0

    return (
        f"AIDE track record: {graded} graded — {confirmed} right, "
        f"{falsified} wrong ({pct}% accurate). {open_count} still open."
    )


if __name__ == "__main__":
    print(json.dumps(accuracy_summary()))
