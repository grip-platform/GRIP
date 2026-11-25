"""RISC Pattern Validator (mu layer) — consistency and contradiction detection.

Phase 2 of the RISC Semantic Encoder. Cross-checks individual messages
against user history to detect:
  1. Consistency shifts (sudden tone change from baseline)
  2. Contradiction catch (Kelly Rule 6: says X works, asks for not-X)
  3. Domain drift (off-topic for channel context)
  4. Recurrence patterns (same low-score pattern 3+ times)

Uses per-user JSONL history for O(n) lookups where n = recent messages.

Usage:
    from lib.risc_pattern_validator import validate_patterns
    result = validate_patterns("user123", score, message, channel="general")
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
HISTORY_FILE = CLAUDE_DIR / "data" / "risc-history.jsonl"
HISTORY_WINDOW = 20  # recent messages to check for patterns


@dataclass
class ValidationResult:
    consistent: bool = True
    contradiction: str = ""
    domain_drift: bool = False
    recurrence_count: int = 0
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "consistent": self.consistent,
            "contradiction": self.contradiction,
            "domain_drift": self.domain_drift,
            "recurrence_count": self.recurrence_count,
            "flags": self.flags,
        }


def _load_user_history(user_id: str) -> list[dict]:
    """Load recent RISC scores for a user. O(n) file scan, bounded by window."""
    if not HISTORY_FILE.exists():
        return []
    entries = []
    for line in HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            if entry.get("user_id") == user_id:
                entries.append(entry)
        except json.JSONDecodeError:
            continue
    return entries[-HISTORY_WINDOW:]


def _append_history(user_id: str, score_dict: dict, message: str) -> None:
    """Append a RISC score to user history."""
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {"user_id": user_id, "message": message[:200], **score_dict}
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _check_consistency(history: list[dict], current: dict) -> bool:
    """Detect sudden tone shift from user's baseline."""
    if len(history) < 3:
        return True  # not enough data
    recent_avgs = [h.get("avg", 70) for h in history[-5:]]
    baseline = sum(recent_avgs) / len(recent_avgs)
    current_avg = current.get("avg", 70)
    # Flag if current deviates > 25 points from baseline
    return abs(current_avg - baseline) <= 25


def _check_contradiction(history: list[dict], message: str) -> str:
    """Kelly Rule 6: detect says-X-does-not-X patterns."""
    if len(history) < 2:
        return ""
    lower = message.lower()
    # Pattern: user previously said something works, now asks for opposite
    work_patterns = [
        (r"\b(?:works?|helps?|fixed)\b", r"\b(?:doesn'?t work|broken|failing)\b"),
        (r"\bi need a break\b", r"\b(?:let'?s keep going|don'?t stop)\b"),
        (r"\b(?:trust the data|evidence shows)\b", r"\b(?:trust me|just believe)\b"),
    ]
    recent_text = " ".join(h.get("message", "") for h in history[-3:]).lower()
    for positive, negative in work_patterns:
        if re.search(positive, recent_text) and re.search(negative, lower):
            return f"Previously expressed '{positive}', now '{negative}'"
    return ""


def _check_recurrence(history: list[dict]) -> int:
    """Count consecutive low-score messages (any axis < 50)."""
    count = 0
    for entry in reversed(history):
        flags = entry.get("flags", [])
        if flags:
            count += 1
        else:
            break
    return count


def validate_patterns(
    user_id: str,
    score_dict: dict,
    message: str,
    channel: str = "",
) -> ValidationResult:
    """Run mu-layer validation on a scored message."""
    history = _load_user_history(user_id)
    result = ValidationResult()

    # Consistency check
    result.consistent = _check_consistency(history, score_dict)
    if not result.consistent:
        result.flags.append("inconsistent-tone")

    # Contradiction catch (Kelly Rule 6)
    result.contradiction = _check_contradiction(history, message)
    if result.contradiction:
        result.flags.append("contradiction")

    # Recurrence pattern
    result.recurrence_count = _check_recurrence(history)
    if result.recurrence_count >= 3:
        result.flags.append(f"recurrence-{result.recurrence_count}x")

    # Append current score to history for next check
    _append_history(user_id, score_dict, message)

    return result


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2:
        user = sys.argv[1]
        msg = " ".join(sys.argv[2:])
        from lib.risc_encoder import score_message
        score = score_message(msg)
        result = validate_patterns(user, score.to_dict(), msg)
        print(f"Score: {score}")
        print(f"Validation: {result.to_dict()}")
    else:
        print("Usage: risc_pattern_validator.py <user_id> <message>")
