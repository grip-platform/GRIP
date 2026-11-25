"""
AdaptiveLearning - behavioral preference learning for copilot.

Three signal types:
1. Explicit corrections: User says "too detailed" -> immediate update (high confidence)
2. Behavioral observation: Engagement patterns -> gradual evidence accumulation
3. Decision calibration: Predicted vs actual outcomes -> confidence adjustment

Uses YSH threshold (3x occurrences) to promote observations to permanent preferences.
Confidence decay: 0.95^days (matches KONO fitness formula).
"""

import json
import logging
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


COPILOT_DIR = Path(os.environ.get("COPILOT_DIR", str(Path.home() / ".claude" / "data" / "aide")))
PREFERENCES_FILE = COPILOT_DIR / "preferences.json"

# YSH threshold: observations reaching 3 occurrences promote to permanent
YSH_THRESHOLD = 3
# Confidence decay per day (matches KONO)
CONFIDENCE_DECAY = 0.95


class Preference:
    """A learned preference with confidence tracking."""

    def __init__(
        self,
        id: str,
        category: str,
        key: str,
        value: Any,
        confidence: float = 0.5,
        evidence_count: int = 1,
        first_observed: str = "",
        last_confirmed: str = "",
        source: str = "observation",
    ):
        self.id = id
        self.category = category
        self.key = key
        self.value = value
        self.confidence = confidence
        self.evidence_count = evidence_count
        self.first_observed = first_observed or datetime.now(timezone.utc).isoformat()
        self.last_confirmed = last_confirmed or self.first_observed
        self.source = source

    @classmethod
    def from_dict(cls, data: dict) -> "Preference":
        return cls(
            id=data.get("id", ""),
            category=data.get("category", ""),
            key=data.get("key", ""),
            value=data.get("value"),
            confidence=data.get("confidence", 0.5),
            evidence_count=data.get("evidence_count", 1),
            first_observed=data.get("first_observed", ""),
            last_confirmed=data.get("last_confirmed", ""),
            source=data.get("source", "observation"),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "confidence": round(self.confidence, 4),
            "evidence_count": self.evidence_count,
            "first_observed": self.first_observed,
            "last_confirmed": self.last_confirmed,
            "source": self.source,
        }

    @property
    def is_permanent(self) -> bool:
        """YSH threshold: 3+ observations = permanent."""
        return self.evidence_count >= YSH_THRESHOLD

    def reinforce(self, confidence_boost: float = 0.1):
        """Add evidence supporting this preference."""
        self.evidence_count += 1
        self.confidence = min(1.0, self.confidence + confidence_boost)
        self.last_confirmed = datetime.now(timezone.utc).isoformat()

    def decay(self, days: float):
        """Apply time-based confidence decay."""
        if self.is_permanent:
            return  # Permanent preferences don't decay
        self.confidence *= CONFIDENCE_DECAY ** days
        self.confidence = max(0.01, self.confidence)


class Correction:
    """A recorded user correction to a preference."""

    def __init__(
        self,
        preference_id: str,
        old_value: Any,
        new_value: Any,
        user_feedback: str = "",
        timestamp: str = "",
    ):
        self.preference_id = preference_id
        self.old_value = old_value
        self.new_value = new_value
        self.user_feedback = user_feedback
        self.timestamp = timestamp or datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "preference_id": self.preference_id,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "user_feedback": self.user_feedback,
        }


class AdaptiveLearning:
    """
    Adaptive learning engine for company copilot.

    Tracks preferences, applies corrections, manages confidence
    decay, and promotes observations via YSH threshold.
    """

    def __init__(self, prefs_path: Optional[Path] = None):
        self.prefs_path = prefs_path or PREFERENCES_FILE
        self.preferences: Dict[str, Preference] = {}
        self.corrections: List[Correction] = []
        self._load()

    def _load(self):
        """Load preferences from JSON file.

        On parse error, logs a warning and starts with empty defaults
        rather than silently losing data or crashing.
        """
        if not self.prefs_path.exists():
            return

        try:
            with open(self.prefs_path) as f:
                raw = f.read()
        except OSError as e:
            logger.warning("Cannot read preferences file %s: %s", self.prefs_path, e)
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(
                "Corrupted preferences file %s (size=%d bytes): %s. "
                "Starting with empty defaults.",
                self.prefs_path, len(raw), e,
            )
            return

        if not isinstance(data, dict):
            logger.error(
                "Preferences file %s has unexpected type %s, expected dict. "
                "Starting with empty defaults.",
                self.prefs_path, type(data).__name__,
            )
            return

        for p in data.get("preferences", []):
            try:
                pref = Preference.from_dict(p)
                self.preferences[pref.id] = pref
            except (KeyError, TypeError, AttributeError) as e:
                logger.warning("Skipping malformed preference entry: %s", e)

        for c in data.get("corrections", []):
            try:
                self.corrections.append(Correction(
                    preference_id=c.get("preference_id", ""),
                    old_value=c.get("old_value"),
                    new_value=c.get("new_value"),
                    user_feedback=c.get("user_feedback", ""),
                    timestamp=c.get("timestamp", ""),
                ))
            except (KeyError, TypeError, AttributeError) as e:
                logger.warning("Skipping malformed correction entry: %s", e)

    def _save(self):
        """Atomically save preferences to JSON file."""
        self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "preferences": [p.to_dict() for p in self.preferences.values()],
            "corrections": [c.to_dict() for c in self.corrections[-50:]],  # Keep last 50
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        import tempfile
        fd, tmp = tempfile.mkstemp(
            dir=str(self.prefs_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
                f.write("\n")
            os.replace(tmp, str(self.prefs_path))
        except Exception:
            os.unlink(tmp)
            raise

    def observe(self, category: str, observation: Dict[str, Any]) -> Optional[str]:
        """
        Record a behavioral observation.

        If the observation matches an existing preference, reinforce it.
        Otherwise create a new preference with low confidence.

        Args:
            category: Observation category (e.g., "briefing", "digest", "communication")
            observation: Key-value observation data

        Returns:
            Preference ID if created/updated
        """
        key = observation.get("key", "")
        value = observation.get("value")

        if not key:
            return None

        pref_id = f"{category}:{key}"

        if pref_id in self.preferences:
            existing = self.preferences[pref_id]
            if existing.value == value:
                existing.reinforce()
            else:
                # Value changed - this might be a correction
                existing.value = value
                existing.last_confirmed = datetime.now(timezone.utc).isoformat()
        else:
            self.preferences[pref_id] = Preference(
                id=pref_id,
                category=category,
                key=key,
                value=value,
                confidence=0.3,
                source="observation",
            )

        self._save()
        return pref_id

    def correct(
        self,
        preference_id: str,
        new_value: Any,
        feedback: str = "",
    ) -> bool:
        """
        Record an explicit user correction.

        Immediately updates the preference with high confidence.

        Args:
            preference_id: ID of preference to correct
            new_value: Corrected value
            feedback: User's feedback text

        Returns:
            True if correction applied
        """
        if preference_id not in self.preferences:
            # Create from correction with high confidence
            parts = preference_id.split(":", 1)
            category = parts[0] if len(parts) > 1 else "general"
            key = parts[1] if len(parts) > 1 else preference_id

            self.preferences[preference_id] = Preference(
                id=preference_id,
                category=category,
                key=key,
                value=new_value,
                confidence=0.9,
                evidence_count=YSH_THRESHOLD,  # Corrections are immediately permanent
                source="correction",
            )
        else:
            pref = self.preferences[preference_id]
            old_value = pref.value

            self.corrections.append(Correction(
                preference_id=preference_id,
                old_value=old_value,
                new_value=new_value,
                user_feedback=feedback,
            ))

            pref.value = new_value
            pref.confidence = 0.9
            pref.evidence_count = max(pref.evidence_count, YSH_THRESHOLD)
            pref.last_confirmed = datetime.now(timezone.utc).isoformat()
            pref.source = "correction"

        self._save()
        return True

    def get_preference(self, preference_id: str) -> Optional[Preference]:
        """Get a specific preference."""
        return self.preferences.get(preference_id)

    def get_preferences_by_category(self, category: str) -> List[Preference]:
        """Get all preferences in a category."""
        return [
            p for p in self.preferences.values()
            if p.category == category
        ]

    def get_active_preferences(self, min_confidence: float = 0.3) -> List[Preference]:
        """Get all preferences above confidence threshold."""
        return [
            p for p in self.preferences.values()
            if p.confidence >= min_confidence
        ]

    def get_permanent_preferences(self) -> List[Preference]:
        """Get all preferences that have reached YSH threshold."""
        return [p for p in self.preferences.values() if p.is_permanent]

    def run_decay(self) -> int:
        """
        Apply confidence decay to all non-permanent preferences.

        Returns:
            Number of preferences that fell below threshold
        """
        now = datetime.now(timezone.utc)
        dropped = 0

        for pref in self.preferences.values():
            if pref.is_permanent:
                continue

            try:
                last = datetime.fromisoformat(pref.last_confirmed)
                days = (now - last).total_seconds() / 86400
                old_confidence = pref.confidence
                pref.decay(days)

                if old_confidence >= 0.3 and pref.confidence < 0.3:
                    dropped += 1
            except (ValueError, TypeError) as e:
                logger.debug("Skipping decay for %s: %s", pref.id, e)
                continue

        # Always save - confidence values were mutated in-place, must persist
        # to prevent double-decay if run_decay is called again in same session
        self._save()

        return dropped

    def consolidate(self) -> Dict[str, int]:
        """
        Weekly consolidation pass.

        - Promote confirmed preferences (3+ evidence)
        - Decay unconfirmed preferences
        - Remove very low confidence preferences

        Returns:
            Summary counts
        """
        promoted = 0
        decayed = 0
        removed = 0

        to_remove = []

        for pref_id, pref in self.preferences.items():
            if pref.evidence_count >= YSH_THRESHOLD and pref.source != "correction":
                pref.source = "promoted"
                promoted += 1
            elif pref.confidence < 0.05:
                to_remove.append(pref_id)
                removed += 1
            elif not pref.is_permanent:
                decayed += 1

        for pref_id in to_remove:
            del self.preferences[pref_id]

        self._save()

        return {
            "promoted": promoted,
            "decayed": decayed,
            "removed": removed,
            "total": len(self.preferences),
            "permanent": len(self.get_permanent_preferences()),
        }

    def calibrate_confidence(
        self,
        category: str,
        predicted: Any,
        actual: Any,
    ) -> float:
        """
        Calibrate confidence based on prediction accuracy.

        When the copilot predicts an outcome and the actual outcome
        differs, reduce confidence in that category.

        Returns:
            Adjustment factor applied
        """
        adjustment = 1.0

        if predicted == actual:
            adjustment = 1.05  # Small boost for correct predictions
        else:
            adjustment = 0.9  # Reduce for incorrect

        # Apply to all preferences in category
        for pref in self.get_preferences_by_category(category):
            pref.confidence = min(1.0, pref.confidence * adjustment)

        self._save()
        return adjustment

    def summary(self) -> Dict[str, Any]:
        """Get learning engine summary."""
        categories = {}
        for pref in self.preferences.values():
            if pref.category not in categories:
                categories[pref.category] = {"count": 0, "permanent": 0}
            categories[pref.category]["count"] += 1
            if pref.is_permanent:
                categories[pref.category]["permanent"] += 1

        return {
            "total_preferences": len(self.preferences),
            "permanent": len(self.get_permanent_preferences()),
            "active": len(self.get_active_preferences()),
            "corrections": len(self.corrections),
            "categories": categories,
        }
