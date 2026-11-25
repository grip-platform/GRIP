#!/usr/bin/env python3
"""
Holistic Backlog Consumer — the critical-reasoning gate atop the backlog reader.

The reader (``lib/holistic_backlog.py::read_top_k``) aggregates + ranks pending work
from six sources. This consumer applies a deterministic reasoning gate to each top
item and emits a :class:`Proposal` (ACT / DEFER / SKIP + a plain-language reason). It
*proposes* — it never auto-executes and never touches the live heartbeat loop.

Proposing-with-a-reason is the complete scope of GH #2810 ("process + propose actions
with reasoning gate"). Wiring the periodic heartbeat trigger (which decides *when* to
call this, and owns the persistent seen-set) is a separate, flag-gated follow-up — a
different concern (SRP), not missing behaviour here.

Idempotent by construction: :func:`propose` is a pure function of ``(items, seen)`` —
running it twice on the same backlog state yields byte-identical verdicts (a Banach
fixed point, proven by :func:`selftest_idempotent`). A ``seen`` set suppresses
re-proposing an item already actioned, so a periodic pulse never spams the same item.

Origin: heartbeat-backlog-consumer (GH #2810). Primary Author: V>>
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from claude_dir import get_claude_dir  # noqa: E402
from holistic_backlog import BacklogItem, read_top_k  # noqa: E402

CLAUDE_DIR = get_claude_dir()
_TELEMETRY = CLAUDE_DIR / "logs" / "backlog-consumer.jsonl"
# Persistent dedup boundary: item ids already ACTed on a prior pulse. Enables
# cross-pulse idempotency so a periodic heartbeat never re-proposes the same item.
_SEEN_PATH = CLAUDE_DIR / "state" / "backlog" / "consumer-seen.json"

# The critical-reasoning gate, made explicit as thresholds (falsifiable knobs).
ACT_PRIORITY_FLOOR = 60      # below this ⇒ DEFER to human triage, never auto-ACT
SKIP_EFFORT_CEILING = 5      # est_effort at max ⇒ too large for one pulse ⇒ DEFER

ACT = "ACT"
DEFER = "DEFER"
SKIP = "SKIP"


@dataclass(frozen=True)
class Proposal:
    """A reasoned verdict on one backlog item. Immutable — a record, not a command."""

    item_id: str
    title: str
    source: str
    verdict: str          # ACT | DEFER | SKIP
    reason: str           # plain-language justification (R0: why this verdict)
    score: float

    def as_row(self) -> dict:
        """Serialisable, deterministic view (rounded score) for telemetry + tests."""
        return {
            "item_id": self.item_id,
            "title": self.title,
            "source": self.source,
            "verdict": self.verdict,
            "reason": self.reason,
            "score": round(self.score, 2),
        }


def _gate(item: BacklogItem, seen: set[str]) -> Proposal:
    """Deterministic critical-reasoning gate for one item. Pure — no I/O, no side effects."""
    if item.id in seen:
        verdict, reason = SKIP, "already actioned within the dedup window"
    elif item.est_effort >= SKIP_EFFORT_CEILING:
        verdict, reason = DEFER, (
            f"effort {item.est_effort} at ceiling {SKIP_EFFORT_CEILING}; "
            "too large for one pulse — needs decomposition"
        )
    elif item.priority < ACT_PRIORITY_FLOOR:
        verdict, reason = DEFER, (
            f"priority {item.priority} below ACT floor {ACT_PRIORITY_FLOOR}; hold for human triage"
        )
    else:
        verdict, reason = ACT, (
            f"priority {item.priority} >= floor {ACT_PRIORITY_FLOOR} "
            f"and effort {item.est_effort} within bounds"
        )
    return Proposal(item.id, item.title, item.source, verdict, reason, item.score)


def propose(items: Iterable[BacklogItem], seen: Optional[set[str]] = None) -> list[Proposal]:
    """Apply the reasoning gate to each item. Pure + idempotent.

    ``propose(items, seen) == propose(items, seen)`` for identical inputs (Banach fixed
    point). No I/O, no auto-execution — returns reasoned proposals only.
    """
    seen_set = seen or set()
    return [_gate(item, seen_set) for item in items]


def consume(
    k: int = 3,
    seen: Optional[set[str]] = None,
    reader: Callable[[int], list[BacklogItem]] = read_top_k,
) -> list[Proposal]:
    """Read the top-k backlog and return reasoned proposals. Never executes.

    ``reader`` is injected (DIP) so tests supply a deterministic backlog without
    touching GitHub or the filesystem.
    """
    return propose(reader(k), seen=seen)


def log_proposals(proposals: Iterable[Proposal], path: Path = _TELEMETRY) -> int:
    """Append each proposal as one JSONL row. Fail-safe: never raises on an I/O error.

    Returns the number of rows written (0 on empty input or any I/O failure) so a caller
    can assert landing without its own try/except. The ``ts`` field is telemetry
    metadata — it does not affect the idempotent verdict in :func:`propose`.
    """
    rows = list(proposals)
    if not rows:
        return 0
    ts = datetime.now(timezone.utc).isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for proposal in rows:
                fh.write(json.dumps({"ts": ts, **proposal.as_row()}, ensure_ascii=False) + "\n")
        return len(rows)
    except OSError:
        return 0


def _load_seen(path: Path = _SEEN_PATH) -> set[str]:
    """Load the persisted seen-set (item ids ACTed on prior pulses).

    Fail-safe: a missing file, unreadable path, or malformed JSON yields an EMPTY set —
    the worst case is re-proposing an item (a duplicate proposal row), never a crash and
    never a silently-suppressed item.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def _save_seen(seen: set[str], path: Path = _SEEN_PATH) -> bool:
    """Persist the seen-set (sorted, deterministic). Fail-safe: returns False on I/O error, never raises."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(seen), ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def drain(
    k: int = 3,
    seen_path: Path = _SEEN_PATH,
    reader: Callable[[int], list[BacklogItem]] = read_top_k,
    log_path: Path = _TELEMETRY,
) -> list[Proposal]:
    """One idempotent pulse: read top-k, propose against the PERSISTED seen-set, record ACTed ids, log.

    Cross-pulse idempotency (the piece single-shot :func:`consume` lacks): an item ACTed on one
    pulse is added to the persisted seen-set, so the NEXT pulse SKIPs it — a periodic heartbeat
    never re-proposes the same item (no spam). Only ACT verdicts are persisted: a DEFER/SKIP item
    is deliberately re-surfaced next pulse until it becomes actionable or is triaged away.

    Never auto-executes: ACT is a *proposal*, recorded once so a human or a downstream trigger acts.
    Pure reasoning stays in :func:`propose`; this adds only the persistent dedup boundary + telemetry.
    """
    seen = _load_seen(seen_path)
    proposals = propose(reader(k), seen=seen)
    newly_acted = {p.item_id for p in proposals if p.verdict == ACT}
    if newly_acted:
        _save_seen(seen | newly_acted, seen_path)
    log_proposals(proposals, path=log_path)
    return proposals


def selftest_idempotent() -> None:
    """Assert :func:`propose` is a Banach fixed point on a fixed synthetic backlog.

    Raises ``AssertionError`` loudly on any drift. No LLM, pure determinism — mirrors
    ``lib/decision_chain_publish.py::selftest_idempotent``.
    """
    sample = [
        BacklogItem(id="a", title="P0 tiny", source="github", priority=100, est_effort=1, complexity_tier="FAST"),
        BacklogItem(id="b", title="huge", source="plan", priority=90, est_effort=SKIP_EFFORT_CEILING, complexity_tier="VERIFY"),
        BacklogItem(id="c", title="low pri", source="memory", priority=40, est_effort=2, complexity_tier="FAST"),
    ]
    first = [p.as_row() for p in propose(sample)]
    second = [p.as_row() for p in propose(sample)]
    assert first == second, f"NON-IDEMPOTENT: {first} != {second}"
    verdicts = {p.item_id: p.verdict for p in propose(sample)}
    expected = {"a": ACT, "b": DEFER, "c": DEFER}
    assert verdicts == expected, f"reasoning-gate drift: {verdicts} != {expected}"


if __name__ == "__main__":
    # `drain` = persistent-seen-set pulse (idempotent across invocations, for cron/heartbeat);
    # bare (default) = one-shot consume with an empty seen-set.
    argv = sys.argv[1:]
    mode_drain = bool(argv) and argv[0] == "drain"
    if mode_drain:
        argv = argv[1:]
    k_arg = int(argv[0]) if argv else 3
    if mode_drain:
        props = drain(k_arg)               # drain() persists the seen-set + logs internally
        wrote = len(props)
    else:
        props = consume(k_arg)
        wrote = log_proposals(props)
    for p in props:
        print(f"[{p.verdict:5s}] {p.source:10s} score={p.score:6.1f}  {p.title}  — {p.reason}")
    tag = "drained" if mode_drain else "logged"
    print(f"\n{len(props)} proposal(s), {wrote} {tag} -> {_TELEMETRY}")
