"""Intel primitive — Protocol + dataclasses (the syscall layer).

PR1 of 5. Defines the 5-function contract every IntelAdapter implements,
plus the 6 immutable dataclasses that flow between them.

Council revisions (drafts/intel-primitive-2026-05-25/COUNCIL.md):
- R1: ``EnrichedObservation.confidence`` defaults to ``0.0`` (not NaN) +
      ``reason: str`` field. ``0.0`` ≠ unknown; ``0.0`` means L1 blocked
      the observation. Reason field carries the plain-English explanation.
- R2: L1-fail path STILL emits an IDR (``outcome=L1_BLOCKED``) via
      ``precog.idr.build_idr`` — NOT silently dropped pre-IDR. The PLAN.md
      §6 mermaid contradiction is resolved by making L1 outcomes
      IDR'd just like L2 outcomes. Adapters MUST attach the IDR id.
- R3: ``discover(scope)`` returns ``tuple[Seed, ...]`` (NOT ``Iterator``)
      for IDR-replay determinism — same scope, same byte-for-byte tuple.
- R5: This file ships ONLY the Protocol + dataclasses (atomic split).
      Discord/LinkedIn/WhatsApp adapters ship in PRs 2/3/4.
- R6: H-INTEL-PROT-1 pre-registered: 3 adapter implementations stay
      ≥30% pairwise distinct at T+30d (devil's leaky-abstraction
      mitigation).

R0 discipline: each dataclass is ``frozen=True`` — state flows through
return values, never instance mutation (KISS, testable, replayable).
The adapter is a coordinator; I/O is shimmed clients passed via DI.

See ``rules/syscall-doctrine.md`` (everything is a shim),
``rules/llm-augmentation-pattern.md`` (L1 floor + L2 recall),
``rules/untrusted-content-capability-lock.md`` (spawn surface lock).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# --- Scope (operator intent) ------------------------------------------------


@dataclass(frozen=True)
class IntelScope:
    """What the operator asked for. Bounded by mode + power overlays.

    Modes/powers compose orthogonally as flat fields (KISS — no nested
    config trees). ``capability_lock`` is the allowlist passed to
    ``spawn_with_lock`` when an adapter spawns a subprocess.
    """

    platform: str                                    # "discord" | "linkedin" | "whatsapp" | ...
    mode: str = "default"                            # "strat-ops" | "founder" | "sneak" | ...
    power: tuple[str, ...] = ()                      # ("stealth",) | ("sneak", "absorb") | ()
    seed_hints: tuple[str, ...] = ()                 # channel IDs / JIDs / URLs / queries
    time_window_hours: int = 24
    max_observations: int = 50                       # hard cap per scan
    rate_limit_per_min: int = 30                     # platform budget per scope
    confidence_floor: float = 0.5                    # IDR / relay confidence floor
    capability_lock: tuple[str, ...] = ("Read", "Grep", "Glob")
    consent_gate: bool = True                        # respect ToS + opt-in


# --- Seed (discovered entry point) ------------------------------------------


@dataclass(frozen=True)
class Seed:
    """A discovered entry point (channel / feed / group / URL).

    Platform-native ``seed_id`` (e.g. Discord channel ID, WhatsApp group
    JID, LinkedIn profile URL). ``cursor`` is the pagination cursor or
    ``None`` for first-page scans.
    """

    platform: str
    seed_id: str
    seed_type: str                                   # "channel" | "feed" | "group" | "url" | "thread"
    label: str = ""                                  # human-readable
    cursor: str | None = None
    refs: tuple[str, ...] = ()                       # URL / API endpoint / DM path


# --- Observation (raw scan result) ------------------------------------------


@dataclass(frozen=True)
class Observation:
    """A single raw observation pulled from a seed.

    Mirrors ``lib/copilot/absorb/base.AbsorbRecord`` shape so an absorber
    can become an adapter with a thin shim (DRY). The relevance verdict
    is NOT applied here — that happens in ``enrich()``.
    """

    obs_id: str                                      # f"{platform}:{seed_id}:{native_id}"
    seed: Seed
    raw_payload: dict[str, Any]                      # platform-native shape
    captured_utc: str
    occurred_utc: str                                # when the underlying event happened
    text: str
    timestamp: int                                   # epoch seconds
    actors: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()


# --- EnrichedObservation (post L1+L2) ---------------------------------------


@dataclass(frozen=True)
class EnrichedObservation:
    """Observation + L1 floor verdict + L2 KONO semantic + IDR fingerprint.

    R1 baked in: ``confidence`` defaults to ``0.0`` (NOT NaN). ``0.0``
    means L1 blocked the observation — it does NOT mean "unknown".
    ``reason`` carries the plain-English explanation; readers MUST
    inspect it to interpret ``confidence == 0.0``.

    R2 baked in: ``idr_id`` is mandatory (defaults to empty string for
    structural typing but adapters MUST attach a real IDR — including
    on L1-fail paths via ``build_idr(decision={"outcome": "L1_BLOCKED",
    ...})``). Silent pre-IDR drop is the failure this dataclass
    structurally prevents.
    """

    observation: Observation
    l1_pass: bool                                    # deterministic regex/rule floor result
    l1_reason: str                                   # why kept/dropped (R1: required)
    # Post-PR1 AGORA F4: contract for ``semantic_vector`` — when present, it
    # is a 768-dim float tuple (Nomic Embed v1.5, GRIP's canonical KONO
    # embedding dimension; see ``lib/guild_semantic_analyzer.py``). ``None``
    # iff L2 did not run (e.g. L1 blocked). Length validation is the L2
    # caller's responsibility — frozen-dataclass ``__post_init__`` validation
    # is deferred (would require importing KONO config; Phase 2 work).
    semantic_vector: tuple[float, ...] | None = None # KONO 768-dim if L2 ran
    l2_score: float = 0.0                            # KONO/HAL semantic 0..1 (0.0 if L1 fail)
    l2_reason: str = ""                              # plain-English L2 explanation
    confidence: float = 0.0                          # R1: 0.0 default, NOT NaN
    reason: str = ""                                 # R1: composite plain-English reason
    idr_id: str = ""                                 # R2: IDR emitted even on L1 fail


# --- RelayReceipt (outbound delivery) ---------------------------------------


@dataclass(frozen=True)
class RelayReceipt:
    """Outbound delivery confirmation for one EnrichedObservation.

    ``ack_ids`` are relay-dispatcher acks (one per ``relayed_to`` channel).
    Powers like ``sneak`` / ``absorb-knowledge`` skip relay — adapters
    return a receipt with ``delivered=False, reason="sneak-mode"``.
    """

    obs_id: str
    relayed_to: tuple[str, ...]                      # channel IDs delivered to
    relayed_at: int                                  # epoch
    delivered: bool
    ack_ids: tuple[str, ...] = ()
    relay_idr_id: str = ""
    reason: str = ""                                 # error / suppression reason


# --- Feedback (operator signal back into the loop) --------------------------


@dataclass(frozen=True)
class Feedback:
    """Operator's response to a relayed observation (closes the learn loop).

    ``operator_signal`` vocabulary is constrained — adapters MUST reject
    unknown values. See ``OPERATOR_SIGNALS`` for the enumerated set.
    """

    enriched: EnrichedObservation
    operator_signal: str                             # see OPERATOR_SIGNALS
    note: str = ""
    captured_utc: str = ""


OPERATOR_SIGNALS: tuple[str, ...] = (
    "useful",
    "noise",
    "false-positive",
    "missed",
)


# --- IntelAdapter (the 5-function contract) ---------------------------------


@runtime_checkable
class IntelAdapter(Protocol):
    """Every platform adapter implements these 5 contract functions.

    R3 baked in: ``discover`` returns ``tuple[Seed, ...]`` (deterministic,
    IDR-replayable) — NOT ``Iterator``. Same scope → same byte-for-byte
    tuple across runs.

    Each method is pure of side-effects on the adapter instance — state
    flows through return values, never instance attributes. I/O happens
    through shimmed clients passed via DI (KONO recall, channel transport).
    """

    # Post-PR1 AGORA F2: ``name`` was a bare attribute and
    # ``runtime_checkable`` Protocol only inspects METHODS — so
    # ``isinstance(obj, IntelAdapter)`` could return True for an adapter
    # missing the attribute, and later ``adapter.name`` would raise
    # ``AttributeError`` deep in the relay path. Promoted to a method so
    # ``isinstance`` actually enforces the contract.

    def name(self) -> str:
        """Platform identifier: 'discord' | 'linkedin' | 'whatsapp' | ..."""
        ...

    def discover(self, scope: IntelScope) -> tuple[Seed, ...]:
        """Find entry points for a scope. Deterministic ordering required."""
        ...

    def scan(self, seed: Seed) -> tuple[Observation, ...]:
        """Pull raw observations from one seed. Bounded by scope caps upstream."""
        ...

    def enrich(self, obs: Observation) -> EnrichedObservation:
        """Apply L1 floor + L2 KONO semantic. MUST emit an IDR even on L1 fail (R2)."""
        ...

    def relay(self, enriched: EnrichedObservation) -> RelayReceipt:
        """Deliver to operator channel. Sneak/absorb powers skip — return delivered=False."""
        ...

    def learn(self, feedback: Feedback) -> None:
        """Update L1 rules / L2 weights from operator signal. Side-effect via DI'd store."""
        ...


__all__ = (
    "EnrichedObservation",
    "Feedback",
    "IntelAdapter",
    "IntelScope",
    "OPERATOR_SIGNALS",
    "Observation",
    "RelayReceipt",
    "Seed",
)
