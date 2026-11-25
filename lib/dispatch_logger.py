"""Dispatch Logger — append-only JSONL audit of skill/pattern dispatch decisions.

Purpose (issue #2558):
    Surface silent-resolution risk at ~210 skill scale. Every non-exempt
    routing decision logs the chosen pattern alongside the alternatives that
    were considered and why each was rejected. Pure observability — never
    changes which pattern is chosen.

Design:
    - Append-only JSONL (one record per assess() call), atomic via tmp+rename
    - Top-3 alternatives with scores + rejection reasons
    - File rotation when current log exceeds 10MB (date-stamped suffix)
    - Fail-safe: never raises, never blocks routing
    - No DB, no daemon — KISS

Public API:
    log_dispatch(prompt, assessment) -> None
    build_record(prompt, assessment) -> dict     # exposed for tests
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Late import to avoid circular dependency with prompt_routing
# (prompt_routing imports this module; this module type-hints RoutingAssessment)

DEFAULT_LOG_PATH = (
    Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
    / "logs"
    / "skill_dispatch.jsonl"
)
DEFAULT_ROTATE_BYTES = 10 * 1024 * 1024  # 10 MB
DEFAULT_PROMPT_PREFIX_LEN = 256

# Pattern ordering Direct < Focused < Sprint < FullMesh — must mirror prompt_routing.PATTERNS.
_ALL_PATTERNS = ("Direct", "Focused", "Sprint", "FullMesh")


def _hash_prompt(prompt: str, length: int = DEFAULT_PROMPT_PREFIX_LEN) -> str:
    """SHA-256 of the prompt prefix. Deterministic across processes."""
    prefix = (prompt or "")[:length].encode("utf-8", errors="replace")
    return hashlib.sha256(prefix).hexdigest()


def _score_for_pattern(pattern: str, recommended: str, base_score: int) -> float:
    """Synthetic 0-1 affinity score per candidate pattern.

    The router doesn't expose per-pattern probabilities, so we approximate
    affinity from tier-distance to the chosen pattern. Chosen pattern keeps
    its base_score (normalised); alternatives degrade by tier distance.
    """
    if pattern not in _ALL_PATTERNS or recommended not in _ALL_PATTERNS:
        return 0.0
    distance = abs(_ALL_PATTERNS.index(pattern) - _ALL_PATTERNS.index(recommended))
    if distance == 0:
        return round(min(base_score / 15.0, 1.0), 3)
    # Each tier of distance halves the affinity from chosen
    return round(max(0.0, (base_score / 15.0) / (2 ** distance)), 3)


def _rejection_reason(pattern: str, assessment) -> str:
    """Plain-English rejection rationale for an alternative pattern."""
    rec_idx = _ALL_PATTERNS.index(assessment.recommended_pattern)
    cand_idx = _ALL_PATTERNS.index(pattern)
    if cand_idx > rec_idx:
        return f"heavier tier than chosen ({pattern} > {assessment.recommended_pattern}); BAR scored {assessment.power_level}"
    return f"lighter tier than chosen ({pattern} < {assessment.recommended_pattern}); BAR scored {assessment.power_level}"


def _build_alternatives(assessment) -> list[dict]:
    """Top alternative patterns (excluding chosen), with scores + reasons.

    Returns up to 3 entries. Always non-empty when there is more than one
    pattern in the taxonomy; an entry array of length 1 is reserved for the
    rare "only one viable pattern" case (which our 4-pattern taxonomy never
    produces, but the field schema supports it for forward compatibility).
    """
    chosen = assessment.recommended_pattern
    others = [p for p in _ALL_PATTERNS if p != chosen]
    # Order alternatives by descending affinity to chosen (closest first)
    others.sort(
        key=lambda p: _score_for_pattern(p, chosen, assessment.score),
        reverse=True,
    )
    return [
        {
            "pattern": p,
            "score": _score_for_pattern(p, chosen, assessment.score),
            "reason": _rejection_reason(p, assessment),
        }
        for p in others[:3]
    ]


def build_record(prompt: str, assessment) -> dict:
    """Pure function — builds the JSON record without writing it.

    Exposed for tests so they don't need to read JSONL back from disk.
    """
    chosen_score = round(min(assessment.score / 15.0, 1.0), 3)
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt_hash": _hash_prompt(prompt),
        "chosen": {
            "pattern": assessment.recommended_pattern,
            "score": chosen_score,
            "reason": assessment.rationale or "",
        },
        "alternatives": _build_alternatives(assessment),
        "user_implied_pattern": assessment.user_implied_pattern,
        "mismatch_tiers": assessment.mismatch_tiers,
        "power_level": assessment.power_level,
        "confidence": round(float(assessment.confidence), 3),
        "session_id": os.environ.get("CLAUDE_SESSION_ID", "unknown"),
    }


def _rotate_if_needed(path: Path, max_bytes: int = DEFAULT_ROTATE_BYTES) -> None:
    """Rotate the log when it exceeds max_bytes. Never raises."""
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        date_suffix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rotated = path.with_name(f"skill_dispatch.{date_suffix}.jsonl")
        # If the rotated path already exists (multiple rotations same day),
        # append a counter so we never clobber prior history.
        counter = 1
        while rotated.exists():
            rotated = path.with_name(f"skill_dispatch.{date_suffix}.{counter}.jsonl")
            counter += 1
        path.rename(rotated)
    except OSError:
        pass


def _atomic_append(path: Path, line: str) -> None:
    """Append one line under a sidecar FileLock so concurrent multi-writer
    sessions never interleave — each line lands whole, any length.

    Replaces a prior version whose docstring claimed atomicity under concurrent
    load but actually wrote a dead ``mkstemp`` tmp (never used for the append)
    and then did a plain UNLOCKED ``open('a')`` buffered append — interleavable
    across the 5+ concurrent GRIP sessions for lines over PIPE_BUF.

    Best-effort: telemetry must never break the dispatch path, so a lock/IO
    failure falls back to a plain append rather than raising.
    """
    try:
        from file_lock import FileLock
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(path, timeout=5, sidecar=True):
            with open(path, "a", encoding="utf-8") as out:
                out.write(line)
    except Exception:
        # Best-effort fallback: telemetry must never break the dispatch path.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as out:
                out.write(line)
        except OSError:
            pass


def log_dispatch(
    prompt: str,
    assessment,
    log_path: Optional[Path] = None,
) -> None:
    """Append one JSONL record describing this dispatch decision.

    Fail-safe: any exception is swallowed. Never raises, never blocks
    routing. `log_path` override is exposed for tests.
    """
    try:
        if assessment is None or getattr(assessment, "exempt", True):
            return
        path = Path(log_path) if log_path else DEFAULT_LOG_PATH
        _rotate_if_needed(path)
        record = build_record(prompt, assessment)
        _atomic_append(path, json.dumps(record) + "\n")
    except Exception:
        # Pure observability — must never block the agent.
        pass
