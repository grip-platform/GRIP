"""Adaptive engine — preferences, routines, predictions.

Three responsibilities:

* :class:`PreferenceLearner` — what V>> rejects vs accepts (explicit signals)
* :class:`RoutineLearner`   — daily / weekly cadence detection (implicit)
* :class:`PredictionRegistry` — every prediction has a falsifier and a check
  timestamp, so the engine can grade itself

Designed as three small state machines so each is testable in isolation.
The legacy :mod:`adaptive` module is kept for backward-compatible imports
during migration; new code should call into this module directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import jsonl_io, paths


# ----- Preference learner -------------------------------------------------

@dataclass
class Preference:
    """A learned preference with a confidence in [0, 1]."""
    key: str
    value: Any
    confidence: float
    evidence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    source: str = "observation"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PreferenceLearner:
    """Track preferences with explicit (high-conf) + observed (low-conf) signals."""

    YSH_THRESHOLD = 3  # promote observation to permanent after 3 confirmations
    DECAY_PER_DAY = 0.95

    def __init__(self) -> None:
        paths.ensure_state_dir()
        self._path = paths.PREFERENCES
        raw = jsonl_io.read_json(self._path, default={})
        self._prefs: Dict[str, Preference] = {
            k: Preference(**v) for k, v in raw.items()
        }

    def reject(self, key: str, value: Any, reason: str = "") -> Preference:
        """Record a rejection — high confidence (explicit signal)."""
        return self._record(key, {"rejected": value, "reason": reason},
                            confidence=0.9, source="rejection")

    def accept(self, key: str, value: Any) -> Preference:
        """Record an acceptance — high confidence (explicit signal)."""
        return self._record(key, value, confidence=0.9, source="acceptance")

    def observe(self, key: str, value: Any) -> Preference:
        """Record an observed pattern — low confidence, accumulates."""
        return self._record(key, value, confidence=0.4, source="observation")

    def _record(self, key: str, value: Any, *, confidence: float,
                source: str) -> Preference:
        now = datetime.now(timezone.utc).isoformat()
        if key in self._prefs:
            existing = self._prefs[key]
            existing.evidence_count += 1
            existing.last_seen = now
            existing.confidence = min(0.99, existing.confidence + confidence * 0.2)
            if existing.evidence_count >= self.YSH_THRESHOLD:
                existing.source = "permanent"
            existing.value = value
        else:
            self._prefs[key] = Preference(
                key=key, value=value, confidence=confidence,
                first_seen=now, last_seen=now, source=source,
            )
        self._save()
        return self._prefs[key]

    def get(self, key: str) -> Optional[Preference]:
        return self._prefs.get(key)

    def all(self) -> List[Preference]:
        return list(self._prefs.values())

    def _save(self) -> None:
        jsonl_io.write_json(self._path,
                            {k: p.to_dict() for k, p in self._prefs.items()})


# ----- Routine learner ----------------------------------------------------

@dataclass
class Routine:
    """A detected daily / weekly cadence (e.g. "9 AM standup", "Friday demo")."""
    name: str
    cadence: str            # "daily" | "weekly" | "weekday"
    hour_local: int         # 0-23 in Africa/Johannesburg
    weekday: Optional[int] = None   # 0=Monday for weekly
    confidence: float = 0.5
    samples: int = 1
    last_seen: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RoutineLearner:
    """Detect cadences from absorbed activity timestamps."""

    def __init__(self) -> None:
        paths.ensure_state_dir()
        self._path = paths.ROUTINES
        raw = jsonl_io.read_json(self._path, default={})
        self._routines: Dict[str, Routine] = {
            k: Routine(**v) for k, v in raw.items()
        }

    def record(self, name: str, *, cadence: str, hour_local: int,
               weekday: Optional[int] = None) -> Routine:
        """Add or reinforce a routine sighting."""
        now = datetime.now(timezone.utc).isoformat()
        key = f"{name}:{cadence}:{hour_local}:{weekday}"
        if key in self._routines:
            r = self._routines[key]
            r.samples += 1
            r.confidence = min(0.99, r.confidence + 0.05)
            r.last_seen = now
        else:
            self._routines[key] = Routine(
                name=name, cadence=cadence, hour_local=hour_local,
                weekday=weekday, confidence=0.4, samples=1, last_seen=now,
            )
        self._save()
        return self._routines[key]

    def all(self) -> List[Routine]:
        return list(self._routines.values())

    def due_now(self, now: Optional[datetime] = None) -> List[Routine]:
        """Return routines whose cadence matches ``now`` (UTC, but matches local hour)."""
        # MVP: use UTC hour; SAST is UTC+2, so caller should pass an
        # aware datetime in Africa/Johannesburg if exact alignment matters.
        now = now or datetime.now(timezone.utc)
        local = now + timedelta(hours=2)  # naive Africa/Johannesburg shift
        out: List[Routine] = []
        for r in self._routines.values():
            if r.confidence < 0.5:
                continue
            if r.hour_local != local.hour:
                continue
            if r.cadence == "weekly" and r.weekday != local.weekday():
                continue
            out.append(r)
        return out

    def _save(self) -> None:
        jsonl_io.write_json(self._path,
                            {k: r.to_dict() for k, r in self._routines.items()})


# ----- Prediction registry ------------------------------------------------

@dataclass
class Prediction:
    """A falsifiable prediction with a check timestamp."""
    pid: str
    claim: str
    falsifier: str
    confidence: float
    created_utc: str
    check_at_utc: str
    outcome: Optional[str] = None       # "confirmed" | "falsified" | None
    evidence: List[str] = field(default_factory=list)


class PredictionRegistry:
    """Append-only prediction log with outcome verification."""

    def __init__(self) -> None:
        paths.ensure_state_dir()
        self._path = paths.PREDICTIONS

    def register(self, *, claim: str, falsifier: str, confidence: float,
                 ttl_hours: int = 24) -> Prediction:
        """Create and persist a new prediction."""
        now = datetime.now(timezone.utc)
        pred = Prediction(
            pid=f"P-{int(now.timestamp())}-{now.microsecond // 1000:03d}",
            claim=claim,
            falsifier=falsifier,
            confidence=max(0.0, min(1.0, confidence)),
            created_utc=now.isoformat(),
            check_at_utc=(now + timedelta(hours=ttl_hours)).isoformat(),
        )
        jsonl_io.append(self._path, asdict(pred))
        return pred

    def upcoming(self, within_hours: int = 24) -> List[Prediction]:
        """Predictions due to be checked in the next ``within_hours``."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=within_hours)
        out: List[Prediction] = []
        for r in jsonl_io.stream(self._path):
            if r.get("outcome") is not None:
                continue
            check = datetime.fromisoformat(r["check_at_utc"])
            if check <= cutoff:
                out.append(Prediction(**r))
        return out

    def all(self, limit: int = 0) -> List[Dict[str, Any]]:
        return jsonl_io.read(self._path, limit=limit, reverse=True)

    def grade(self, pid: str, *, outcome: str,
              evidence: Optional[List[str]] = None) -> bool:
        """Set the outcome on a registered prediction — closes the Popperian loop.

        Completes the self-grading the module docstring promises ("so the engine
        can grade itself"). Append-only + single-writer, so a full
        read-modify-rewrite via ``jsonl_io.write_all`` is safe. Only an
        ungraded (``outcome is None``) prediction is updated, so grading is
        idempotent — a second call with the same pid is a no-op once set.
        Returns True iff a matching ungraded prediction was found.
        """
        if outcome not in ("confirmed", "falsified"):
            raise ValueError("outcome must be 'confirmed' or 'falsified'")
        records = jsonl_io.read(self._path)
        found = False
        for rec in records:
            if rec.get("pid") == pid and rec.get("outcome") is None:
                rec["outcome"] = outcome
                if evidence:
                    rec["evidence"] = list(rec.get("evidence") or []) + list(evidence)
                found = True
        if found:
            jsonl_io.write_all(self._path, records)
        return found
