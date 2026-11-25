"""Propagation ledger — append-only log of insight propagation attempts.

W6 of plans/auto-insight-rsi-sprint-2026-05-05.md. Cross-cutting collapse
that turns Gaps 1, 2, and 4 into queries against one substrate.

Extends ``learning/propagation_log.jsonl`` with explicit ``session_id`` +
``outcome`` enum + a query layer. Backwards-compatible: legacy multi-target
records (``targets_written`` + ``targets_skipped``) are normalised on read
into per-target ``PropagationAttempt`` records.

Design constraints (per plan W6):
- Atomic append under N>=10 concurrent writers (``fcntl.flock`` semantics).
- Append-only: never rewrites prior entries.
- Read path is lock-free (eventual-consistent vs the most recent write
  in flight; one extra entry on the next read is acceptable).
- Pure stdlib: no third-party dependencies.

API:
- ``append(attempt)``               — atomic single-line write
- ``queue()``                       — entries with outcome not in {SUCCESS, UNPROPAGATABLE}
- ``unacted_in_session(sid)``       — deny-check input for W9 hook
- ``by_target(path)``               — annotation set for W8 reconciler
- ``cross_session_pending()``       — sidecar candidates for W10
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable
try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class PropagationOutcome(str, Enum):
    """Per-target propagation result."""
    SUCCESS = "success"               # canonical file written
    DEDUP_SKIP = "dedup_skip"         # fingerprint already propagated to this target
    UNPROPAGATABLE = "unpropagatable"  # registered as a fixed-point (DFI-FIB cap)
    DEFERRED = "deferred"             # routed to sidecar for cross-session pickup
    BYPASSED = "bypassed"             # GRIP_INSIGHT_PROPAGATION_BYPASS=1 used
    ERROR = "error"                   # exception during propagation


@dataclass(frozen=True)
class PropagationAttempt:
    """Single per-target propagation attempt.

    Keep this struct small and additive. New fields default-back so old
    log entries continue to deserialise.
    """
    fingerprint: str                  # 16-char SHA from insight_value_scorer
    classification: str               # operational_discovery | design_pattern | ...
    target: str                       # canonical file path the insight tried to land in
    outcome: PropagationOutcome
    timestamp: float                  # unix epoch seconds
    session_id: str = "unknown"       # CC session UUID
    depth: int = 0                    # InnerspeakerGo recursion depth at attempt time
    signals: list[str] = field(default_factory=list)
    operator_bypass: bool = False     # was GRIP_INSIGHT_PROPAGATION_BYPASS=1 set?

    def to_jsonable(self) -> dict:
        """Serialisable dict (enum -> str)."""
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _default_log_path() -> Path:
    """Return the canonical ledger path.

    Honours ``GRIP_PROPAGATION_LOG`` env var for tests/overrides; otherwise
    points at the worktree's ``learning/propagation_log.jsonl``.
    """
    override = os.environ.get("GRIP_PROPAGATION_LOG")
    if override:
        return Path(override)
    # Walk up until we find the learning/ directory (handles worktrees).
    here = Path(__file__).resolve()
    for parent in [here.parent.parent, *here.parent.parent.parents]:
        candidate = parent / "learning" / "propagation_log.jsonl"
        if (parent / "learning").is_dir():
            return candidate
    return here.parent.parent / "learning" / "propagation_log.jsonl"


LEDGER_PATH = _default_log_path()


# ---------------------------------------------------------------------------
# Append (atomic, locked)
# ---------------------------------------------------------------------------


def append(attempt: PropagationAttempt, *, path: Path | None = None) -> None:
    """Atomically append a single PropagationAttempt as one JSONL line.

    Holds an exclusive ``fcntl.flock`` for the duration of the write. Other
    processes appending concurrently block until this write completes;
    interleaving is impossible at line granularity.
    """
    target = path or LEDGER_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(attempt.to_jsonable(), ensure_ascii=False) + "\n"
    with target.open("a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Read + normalise (legacy + new format)
# ---------------------------------------------------------------------------


def _parse_timestamp(value) -> float:
    """Accept float, int, or ISO-8601 string."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _normalise(record: dict) -> list[PropagationAttempt]:
    """Convert one log record (legacy or new) into per-target attempts."""
    fp = record.get("fingerprint", "")
    classification = record.get("classification") or record.get("category", "unknown")
    signals = list(record.get("signals", []))
    ts = _parse_timestamp(record.get("timestamp", 0))
    sid = record.get("session_id", "unknown")
    depth = int(record.get("depth", 0))
    bypass = bool(record.get("operator_bypass", False))

    if "target" in record and "outcome" in record:
        # New-format entry — exactly one attempt.
        try:
            outcome = PropagationOutcome(record["outcome"])
        except ValueError:
            outcome = PropagationOutcome.ERROR
        return [PropagationAttempt(
            fingerprint=fp, classification=classification, target=record["target"],
            outcome=outcome, timestamp=ts, session_id=sid, depth=depth,
            signals=signals, operator_bypass=bypass,
        )]

    # Legacy multi-target record — split into per-target attempts.
    attempts: list[PropagationAttempt] = []
    for t in record.get("targets_written", []) or []:
        attempts.append(PropagationAttempt(
            fingerprint=fp, classification=classification, target=str(t),
            outcome=PropagationOutcome.SUCCESS, timestamp=ts, session_id=sid,
            depth=depth, signals=signals, operator_bypass=bypass,
        ))
    for t in record.get("targets_skipped", []) or []:
        attempts.append(PropagationAttempt(
            fingerprint=fp, classification=classification, target=str(t),
            outcome=PropagationOutcome.DEDUP_SKIP, timestamp=ts, session_id=sid,
            depth=depth, signals=signals, operator_bypass=bypass,
        ))
    return attempts


def _iter_attempts(path: Path | None = None) -> Iterable[PropagationAttempt]:
    """Stream all attempts from the ledger, normalised. Returns empty on missing file."""
    target = path or LEDGER_PATH
    if not target.exists():
        return
    with target.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            yield from _normalise(record)


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------


def queue(*, path: Path | None = None) -> list[PropagationAttempt]:
    """Entries pending action. Excludes SUCCESS and UNPROPAGATABLE.

    This is W7 (queue drainer) input.
    """
    terminal = {PropagationOutcome.SUCCESS, PropagationOutcome.UNPROPAGATABLE}
    return [a for a in _iter_attempts(path) if a.outcome not in terminal]


def unacted_in_session(session_id: str, *, path: Path | None = None) -> list[PropagationAttempt]:
    """Per-session unacted attempts. W9 deny-on-ship hook input."""
    terminal = {PropagationOutcome.SUCCESS, PropagationOutcome.UNPROPAGATABLE}
    return [
        a for a in _iter_attempts(path)
        if a.session_id == session_id and a.outcome not in terminal
    ]


def by_target(target_path: Path | str, *, path: Path | None = None) -> list[PropagationAttempt]:
    """All SUCCESS attempts for a given canonical-file target.

    W8 annotation reconciler input — given a canonical file, what insights
    successfully landed in it?
    """
    needle = str(target_path)
    return [
        a for a in _iter_attempts(path)
        if a.target == needle and a.outcome == PropagationOutcome.SUCCESS
    ]


def cross_session_pending(*, path: Path | None = None) -> dict[str, list[PropagationAttempt]]:
    """Pending attempts grouped by target. W10 sidecar reconciler input."""
    out: dict[str, list[PropagationAttempt]] = {}
    for a in queue(path=path):
        out.setdefault(a.target, []).append(a)
    return out


# ---------------------------------------------------------------------------
# Smart write — dedup-aware
# ---------------------------------------------------------------------------


def record_outcome(
    attempt: PropagationAttempt, *, path: Path | None = None
) -> PropagationAttempt:
    """Append with dedup awareness on (fingerprint, target).

    If ``attempt.outcome == SUCCESS`` and a prior SUCCESS already exists
    in the ledger for the same (fingerprint, target) pair, the outcome
    is downgraded to ``DEDUP_SKIP`` BEFORE writing. Returns the actually-
    recorded attempt.

    Non-SUCCESS attempts pass through to ``append`` unchanged.

    Best-effort dedup: under a pathological race, two SUCCESS records for
    the same (fp, target) can still appear if both writers read before
    either writes. The duplicate is detectable at query time.
    """
    if attempt.outcome != PropagationOutcome.SUCCESS:
        append(attempt, path=path)
        return attempt
    existing = by_target(attempt.target, path=path)
    if any(
        a.fingerprint == attempt.fingerprint
        and a.outcome == PropagationOutcome.SUCCESS
        for a in existing
    ):
        from dataclasses import replace
        attempt = replace(attempt, outcome=PropagationOutcome.DEDUP_SKIP)
    append(attempt, path=path)
    return attempt


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def current_session_id() -> str:
    """Return the current Claude session ID, or 'unknown' if not set."""
    return os.environ.get("CLAUDE_SESSION_ID") or "unknown"


def operator_bypass_active() -> bool:
    """Return True iff GRIP_INSIGHT_PROPAGATION_BYPASS=1."""
    return os.environ.get("GRIP_INSIGHT_PROPAGATION_BYPASS") == "1"
