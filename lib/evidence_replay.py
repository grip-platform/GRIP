"""Evidence-ledger replay — dogfood domain #5, routed through lib/dogfood_kernel.

The domain: an append-only evidence-event ledger (JSONL of ``observe`` and
``assume`` events) driving :class:`lib.evidence_state.EvidenceStore` (merged
ab25f5d0, PR #5019). Live writers may append events unconditionally, so a
ledger accumulates observations that were no-ops when applied (re-recording
evidence the store already held). The idempotent fold is **replay
normalisation**: project the event list onto its EFFECTIVE subsequence — the
events that actually climbed the store's cumulative knowledge lattice.

Why this is a projection (f∘f = f, the Banach shape the kernel enforces):
``k_join`` is monotone and idempotent, so an observation that produced no
cumulative climb contributes nothing to any later state — removing it leaves
every retained event exactly as effective as before. One application lands on
the fixed point; the second application finds nothing to remove.

Registered in :data:`lib.dogfood_kernel.KNOWN_DOMAINS`; the three soundness
invariants (fixed-point ↔ zero residual, idempotence by double application,
non-negative residual) are proven through :func:`lib.dogfood_kernel.generic_proof`
over :class:`EvidenceLedgerDomain` — per the kernel's contract, a new domain
implements only the hooks and inherits the proof machinery.

Replay is READ-ONLY by construction: the rebuilt store carries no
``leaf_writer``, so replaying a ledger can never re-seal IDR leaves — seals
belong to the LIVE observation that first recorded the evidence, never to a
rebuild (the same recording-time discipline the gate's ledger section states).

First consumer: the private personal-tax runner's hand-rolled
``load_store``/``_append_event`` pair (its data never enters this repo; the
kernel is domain-agnostic — schema names are injected by the caller).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from lib.evidence_state import EvidenceState, EvidenceStore  # noqa: E402


class ReplayError(ValueError):
    """A ledger line or event that cannot be replayed as recorded.

    Raised LOUDLY on the first defect — an evidence ledger is an audit
    artefact, and silently skipping or reordering knowledge would corrupt
    exactly the record the store exists to keep.
    """


_STATES = {s.name: s for s in EvidenceState}
_KINDS = ("observe", "assume")


def _fail(where: str, why: str) -> "ReplayError":
    return ReplayError(f"ledger {where}: {why}")


def _validate_observe(where: str, ev: dict) -> None:
    for key in ("var", "state", "ref"):
        if not isinstance(ev.get(key), str) or not ev.get(key):
            raise _fail(where, f"observe event needs non-empty string {key!r}")
    if ev["state"] not in _STATES:
        raise _fail(where, f"unknown EvidenceState {ev['state']!r} "
                           f"(valid: {sorted(_STATES)})")


def _validate_assume(where: str, ev: dict) -> None:
    for key in ("claim", "marker"):
        if not isinstance(ev.get(key), str) or not ev.get(key):
            raise _fail(where, f"assume event needs non-empty string {key!r}")
    assumed = ev.get("assumed")
    if not isinstance(assumed, dict) or not assumed:
        raise _fail(where, "assume event needs a non-empty 'assumed' mapping")
    for var, state in assumed.items():
        if not isinstance(var, str) or not var:
            raise _fail(where, "assumed variable names must be non-empty "
                               f"strings (got {var!r}) — symmetric with the "
                               "observe path's var check")
        if state not in _STATES:
            raise _fail(where, f"assumed[{var!r}] has unknown state {state!r}")


def validate_event(pos: int, ev: object, *, unit: str = "event") -> dict:
    """Strictly validate one parsed event; return it unchanged.

    Unknown kinds, missing fields and unknown states all raise
    :class:`ReplayError` — never skipped. The error names its position with
    an explicit UNIT so the pointer is unambiguous (AGORA #5027 round 2
    concern 1): the codec path reports ``ledger line N`` (1-based file
    line, blank lines counted), the fold/replay paths report
    ``ledger event N`` (0-based position in the event sequence).
    """
    where = f"{unit} {pos}"
    if not isinstance(ev, dict):
        raise _fail(where, f"not a JSON object (got {type(ev).__name__})")
    kind = ev.get("kind")
    if kind not in _KINDS:
        raise _fail(where, f"unknown kind {kind!r} (valid: {list(_KINDS)})")
    if kind == "observe":
        _validate_observe(where, ev)
    else:
        _validate_assume(where, ev)
    return ev


def _parse_ts(lineno: int, ts: object) -> datetime:
    """Parse an event timestamp as an INSTANT, loudly.

    AGORA #5027 concern 1: a lexical string comparison both false-positives
    and false-negatives on mixed-offset ISO timestamps, and a non-string
    ``ts`` bypassed the guard entirely. Here any present ``ts`` must be an
    ISO-8601 string ``datetime.fromisoformat`` accepts; anything else —
    epoch integers, malformed strings — raises rather than silently
    skipping the very guarantee the guard exists to give.
    """
    if not isinstance(ts, str):
        raise ReplayError(
            f"ledger line {lineno}: ts must be an ISO-8601 string, "
            f"got {type(ts).__name__} ({ts!r})")
    try:
        return datetime.fromisoformat(ts)
    except ValueError as exc:
        raise ReplayError(
            f"ledger line {lineno}: unparseable timestamp {ts!r} ({exc})") from exc


def load_events(text: str) -> list[dict]:
    """Parse a JSONL ledger into validated events, preserving order.

    Blank lines are skipped. A malformed line, an invalid event, an
    unparseable timestamp, or a timestamp whose INSTANT moves backward
    raises :class:`ReplayError` — a reordered ledger must fail loudly,
    never silently rearrange knowledge. Instants are compared as parsed
    datetimes (offset-aware), so mixed UTC offsets order correctly; mixing
    aware and naive timestamps in one ledger is itself refused (their
    order is undefined).
    """
    events: list[dict] = []
    last_instant: datetime | None = None
    last_raw: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"ledger line {lineno}: invalid JSON ({exc})") from exc
        ev = validate_event(lineno, obj, unit="line")
        if "ts" in ev:
            instant = _parse_ts(lineno, ev["ts"])
            if last_instant is not None:
                try:
                    backwards = instant < last_instant
                except TypeError as exc:
                    raise ReplayError(
                        f"ledger line {lineno}: timestamp {ev['ts']!r} mixes "
                        f"offset-aware and offset-naive with {last_raw!r} — "
                        "their order is undefined; refusing to replay") from exc
                if backwards:
                    raise ReplayError(
                        f"ledger line {lineno}: timestamp {ev['ts']!r} precedes "
                        f"{last_raw!r} — the ledger is reordered or spliced; "
                        "refusing to replay")
            last_instant, last_raw = instant, ev["ts"]
        events.append(ev)
    return events


def _apply(store: EvidenceStore, index: int, ev: dict) -> bool:
    """Apply one validated event; return True iff it was EFFECTIVE.

    An observe event is effective iff the store's cumulative layer climbed
    (a :class:`FlipRecord` was produced). Any :class:`EvidenceState` name is
    a legal observed state — verified against the store (AGORA #5027 round 2
    concern 4): observing CONFLICT imports an already-recorded dispute
    (effective the first time, redundant on repeat — the absorbent top of
    the knowledge lattice); observing LATENT is ALWAYS a no-op (``k_join``
    identity) and is therefore always stripped by :func:`normalise`. An
    assume event is always effective — it records a caller's explicit act,
    and two identical acts are two acts. Unknown variables surface as :class:`ReplayError` carrying
    the event index (the store's finite-universe refusal, contextualised).
    """
    try:
        if ev["kind"] == "observe":
            return store.observe(ev["var"], _STATES[ev["state"]], ev["ref"]) is not None
        assumed = {k: _STATES[v] for k, v in ev["assumed"].items()}
        store.record_assumption(ev["claim"], assumed, ev["marker"])
        return True
    except ReplayError:
        raise
    except ValueError as exc:
        raise _fail(f"event {index}", str(exc)) from exc


def replay(events: Sequence[dict], schema: Iterable[str]) -> EvidenceStore:
    """Rebuild an :class:`EvidenceStore` from *events* — READ-ONLY.

    The store is constructed WITHOUT a leaf_writer: a replay must never
    re-seal IDR leaves (seals record when evidence was FIRST recorded, and a
    rebuild is not a recording). Callers wanting live sealing attach a
    writer to a fresh store for NEW observations, never to a replay.
    """
    store = EvidenceStore(schema)
    for index, ev in enumerate(events):
        _apply(store, index, validate_event(index, ev))
    return store


def normalise(events: Sequence[dict], schema: Iterable[str]) -> list[dict]:
    """THE FOLD — project *events* onto their effective subsequence.

    Replays through a fresh read-only store, keeping exactly the events that
    changed it. Removing an ineffective observation cannot change any later
    event's effect (``k_join`` idempotence), so this is a projection:
    ``normalise(normalise(e)) == normalise(e)``.
    """
    store = EvidenceStore(schema)
    kept: list[dict] = []
    for index, ev in enumerate(events):
        if _apply(store, index, validate_event(index, ev)):
            kept.append(ev)
    return kept


def residual_redundant(events: Sequence[dict], schema: Iterable[str]) -> int:
    """Divergence metric: how many events the fold would remove (>= 0)."""
    return len(events) - len(normalise(events, schema))


def is_normalised(events: Sequence[dict], schema: Iterable[str]) -> bool:
    """True iff *events* is at the fold's fixed point (zero redundancy)."""
    return normalise(events, schema) == list(events)


def is_idempotent(events: Sequence[dict], schema: Iterable[str]) -> bool:
    """Kernel Invariant 2 by double application — never asserted.

    Present as a module-level function per the :data:`KNOWN_DOMAINS`
    registry convention shared by every dogfood domain.
    """
    once = normalise(events, schema)
    return normalise(once, schema) == once


@dataclass(frozen=True)
class EvidenceLedgerDomain:
    """:class:`lib.dogfood_kernel.IdempotentDomain` over an event ledger.

    ``schema`` is the finite variable universe (injected — the kernel knows
    nothing about any caller's domain). ``fold`` and ``residual`` are pure:
    each call replays through a fresh in-memory store and never touches the
    input, disk, or any IDR chain.
    """

    schema: tuple[str, ...]

    def fold(self, x: list[dict]) -> list[dict]:
        return normalise(x, self.schema)

    def residual(self, x: list[dict]) -> int:
        return residual_redundant(x, self.schema)

    @property
    def zero_residual(self) -> int:
        return 0


def ledger_proof(events: Sequence[dict], schema: Iterable[str]):
    """Banach witness for a ledger via the generic kernel.

    Thin by design: the proof machinery (fixed-point check, double-application
    idempotence, metric agreement, rationale) lives in
    :func:`lib.dogfood_kernel.generic_proof` — this domain supplies only its
    hooks, which is the entire point of the shim.
    """
    from lib.dogfood_kernel import generic_proof
    return generic_proof(EvidenceLedgerDomain(tuple(schema)), list(events))


__all__ = [
    "ReplayError",
    "validate_event",
    "load_events",
    "replay",
    "normalise",
    "residual_redundant",
    "is_normalised",
    "is_idempotent",
    "EvidenceLedgerDomain",
    "ledger_proof",
]
