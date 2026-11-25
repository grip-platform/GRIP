"""Copilot precog pillar — predictive scheduling and pre-emptive alerts.

PrecogEngine reads RoutineLearner + PredictionRegistry state to forecast
upcoming friction/schedule conflicts. Every prediction carries a falsifier
and confidence so the engine can grade itself.

Falsifier: if predict() returns no results when routines exist with
confidence >= 0.5 and horizon_hours >= 24, the cadence-matching logic
is broken.

CLI:
    python3 -m lib.aide.precog [--hours 24]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from lib.aide.adaptive_engine import PredictionRegistry, RoutineLearner


@dataclass
class PrecogResult:
    """A single forwarded prediction from PrecogEngine."""
    pid: str
    claim: str
    falsifier: str
    confidence: float
    check_at_utc: str
    source: str  # "routine" | "upcoming"


class PrecogEngine:
    """Forecast upcoming friction and schedule events from absorbed data.

    Reads RoutineLearner cadences and PredictionRegistry.upcoming() to
    surface what needs attention in the next ``horizon_hours``. Each
    new routine forecast is registered as a falsifiable prediction.
    """

    def __init__(
        self,
        routines: Optional[RoutineLearner] = None,
        registry: Optional[PredictionRegistry] = None,
    ) -> None:
        self._routines = routines or RoutineLearner()
        self._registry = registry or PredictionRegistry()

    def predict(
        self,
        now: Optional[datetime] = None,
        horizon_hours: int = 24,
    ) -> List[PrecogResult]:
        """Return falsifiable predictions for the next ``horizon_hours``.

        Two sources:
        1. Routines whose next occurrence falls within the horizon.
        2. Already-registered upcoming predictions not yet graded.
        """
        now = now or datetime.now(timezone.utc)
        return (
            self._from_routines(now, horizon_hours)
            + self._from_upcoming(now, horizon_hours)
        )

    # ------------------------------------------------------------------

    def _from_routines(self, now: datetime, horizon_hours: int) -> List[PrecogResult]:
        """Convert due routines into registered predictions."""
        out: List[PrecogResult] = []
        horizon_end = now + timedelta(hours=horizon_hours)
        for routine in self._routines.all():
            if routine.confidence < 0.5:
                continue
            nxt = _next_occurrence(routine, now)
            if nxt is None or nxt > horizon_end:
                continue
            # Idempotent registration: re-running predict() must not spam the
            # registry with a fresh prediction for the same routine+time. Skip
            # if an ungraded prediction for this routine already exists.
            already = [p for p in self._registry.upcoming(within_hours=horizon_hours)
                       if p.outcome is None and p.claim.startswith(f"Routine '{routine.name}'")]
            if already:
                pred = already[0]
            else:
                pred = self._registry.register(
                    claim=f"Routine '{routine.name}' ({routine.cadence}) due at {nxt.strftime('%H:%M UTC')}",
                    falsifier=f"'{routine.name}' does not occur at the expected hour — cadence broken or cancelled",
                    confidence=routine.confidence,
                    ttl_hours=max(1, int((nxt - now).total_seconds() / 3600) + 1),
                )
            out.append(PrecogResult(
                pid=pred.pid, claim=pred.claim, falsifier=pred.falsifier,
                confidence=pred.confidence, check_at_utc=pred.check_at_utc,
                source="routine",
            ))
        return out

    def _from_upcoming(self, now: datetime, horizon_hours: int) -> List[PrecogResult]:
        """Surface already-registered predictions due within horizon."""
        return [
            PrecogResult(
                pid=p.pid, claim=p.claim, falsifier=p.falsifier,
                confidence=p.confidence, check_at_utc=p.check_at_utc,
                source="upcoming",
            )
            for p in self._registry.upcoming(within_hours=horizon_hours)
        ]


# --------------------------------------------------------------------------
# Module-level helper (pure, testable without engine instance)
# --------------------------------------------------------------------------

def _next_occurrence(routine, now: datetime) -> Optional[datetime]:
    """Compute the soonest future UTC time this routine fires.

    Uses naive UTC+2 shift to match local hour — same assumption as
    RoutineLearner.due_now().  Returns None for unrecognised cadence.
    """
    local = now + timedelta(hours=2)  # naive SAST
    candidate = local.replace(
        hour=routine.hour_local, minute=0, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += timedelta(days=1)

    if routine.cadence == "daily":
        return candidate - timedelta(hours=2)

    if routine.cadence == "weekday":
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return candidate - timedelta(hours=2)

    if routine.cadence == "weekly":
        if routine.weekday is None:
            return None
        days_ahead = (routine.weekday - candidate.weekday()) % 7 or 7
        candidate += timedelta(days=days_ahead)
        return candidate - timedelta(hours=2)

    return None  # unrecognised cadence


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Copilot precog — forecast upcoming events")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args(argv)

    results = PrecogEngine().predict(horizon_hours=args.hours)
    if not results:
        print(f"No predictions in the next {args.hours}h.")
        return 0
    print(f"Predictions for the next {args.hours}h:")
    for r in results:
        print(f"  [{r.pid}] ({r.confidence:.0%}) {r.claim}")
        print(f"        falsifier: {r.falsifier}")
        print(f"        check_at:  {r.check_at_utc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
