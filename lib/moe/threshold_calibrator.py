"""
MoE Threshold Calibrator
=========================

Adaptive re-calibration of prompt routing thresholds from task outcomes.
Uses Bayesian-inspired update: shift keyword weights toward levels that
produce successful outcomes, away from levels that produce failures.

Records (route_chosen, task_result, cost) and periodically recalibrates
the routing thresholds in prompt_router.py.

Usage:
    from lib.moe.threshold_calibrator import ThresholdCalibrator

    cal = ThresholdCalibrator()
    cal.record_outcome("fork", success=True, cost=0.02)
    cal.record_outcome("cluster", success=False, cost=0.15)
    cal.calibrate()  # Updates routing thresholds
"""

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
DEFAULT_OUTCOMES_PATH = CLAUDE_DIR / "state" / "moe" / "routing-outcomes.jsonl"
DEFAULT_THRESHOLDS_PATH = CLAUDE_DIR / "state" / "moe" / "routing-thresholds.json"

# Calibration parameters
LEARNING_RATE = 0.1  # How much each outcome shifts the threshold
MIN_OUTCOMES = 5     # Minimum outcomes before calibrating
MAX_SHIFT = 0.15     # Maximum per-level threshold shift per calibration


@dataclass
class RoutingOutcome:
    """Record of a routing decision and its outcome."""
    route_chosen: str  # power level: thread, fork, cluster, pipeline, mesh
    success: bool
    cost: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "route": self.route_chosen,
            "success": self.success,
            "cost": self.cost,
            "ts": self.timestamp,
        }


class ThresholdCalibrator:
    """Adaptive routing threshold calibrator.

    Records routing outcomes and periodically recalibrates thresholds.
    Successful outcomes at a given level reinforce that level's threshold.
    Failed outcomes push toward higher levels (more resources needed).
    """

    def __init__(self, outcomes_path: Optional[Path] = None,
                 thresholds_path: Optional[Path] = None):
        self.outcomes_path = outcomes_path or DEFAULT_OUTCOMES_PATH
        self.thresholds_path = thresholds_path or DEFAULT_THRESHOLDS_PATH
        self._thresholds = self._load_thresholds()

    def _load_thresholds(self) -> dict:
        """Load current routing thresholds."""
        if not self.thresholds_path.exists():
            return self._default_thresholds()
        try:
            return json.loads(self.thresholds_path.read_text())
        except (json.JSONDecodeError, OSError):
            return self._default_thresholds()

    def _default_thresholds(self) -> dict:
        """Default confidence thresholds per level."""
        return {
            "thread": 0.5,
            "fork": 0.6,
            "cluster": 0.7,
            "pipeline": 0.8,
            "mesh": 0.9,
        }

    def _save_thresholds(self):
        """Persist thresholds to disk."""
        self.thresholds_path.parent.mkdir(parents=True, exist_ok=True)
        self.thresholds_path.write_text(json.dumps(self._thresholds, indent=2))

    def record_outcome(self, route: str, success: bool, cost: float = 0.0):
        """Record a routing outcome for later calibration."""
        outcome = RoutingOutcome(route_chosen=route, success=success, cost=cost)
        self.outcomes_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.outcomes_path, "a") as f:
            f.write(json.dumps(outcome.to_dict()) + "\n")

    def load_outcomes(self) -> list[RoutingOutcome]:
        """Load all recorded outcomes."""
        if not self.outcomes_path.exists():
            return []
        outcomes = []
        for line in self.outcomes_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                outcomes.append(RoutingOutcome(
                    route_chosen=d["route"],
                    success=d["success"],
                    cost=d.get("cost", 0.0),
                    timestamp=d.get("ts", 0.0),
                ))
            except (json.JSONDecodeError, KeyError):
                continue
        return outcomes

    def calibrate(self) -> dict:
        """Recalibrate thresholds from recorded outcomes.

        For each level, compute success rate. If success rate is high,
        the threshold can be lowered (more tasks routed there).
        If success rate is low, threshold should be raised (fewer tasks).

        Returns the updated thresholds dict.
        """
        outcomes = self.load_outcomes()
        if len(outcomes) < MIN_OUTCOMES:
            return self._thresholds

        # Group outcomes by route
        by_route: dict[str, list[RoutingOutcome]] = {}
        for o in outcomes:
            by_route.setdefault(o.route_chosen, []).append(o)

        # Update thresholds
        for route, route_outcomes in by_route.items():
            if route not in self._thresholds:
                continue
            total = len(route_outcomes)
            successes = sum(1 for o in route_outcomes if o.success)
            success_rate = successes / total

            current = self._thresholds[route]
            # High success rate -> lower threshold (encourage this route)
            # Low success rate -> raise threshold (discourage this route)
            if success_rate > 0.8:
                shift = -LEARNING_RATE * (success_rate - 0.8)
            elif success_rate < 0.5:
                shift = LEARNING_RATE * (0.5 - success_rate)
            else:
                shift = 0.0

            # Clamp shift
            shift = max(-MAX_SHIFT, min(MAX_SHIFT, shift))
            new_threshold = max(0.1, min(0.95, current + shift))
            self._thresholds[route] = round(new_threshold, 3)

        self._save_thresholds()
        return self._thresholds

    @property
    def thresholds(self) -> dict:
        """Current thresholds (read-only copy)."""
        return dict(self._thresholds)

    def reset(self):
        """Reset thresholds to defaults and clear outcomes."""
        self._thresholds = self._default_thresholds()
        self._save_thresholds()
        if self.outcomes_path.exists():
            self.outcomes_path.unlink()
