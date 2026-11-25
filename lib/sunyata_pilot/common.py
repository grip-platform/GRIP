"""Shared contract for the Sunyata EvidenceState pilot (paper §7–§8).

Single source of truth for every lane: scenario schema, gold schema,
agent interface, and the pre-declared H1 coverage floor. Lanes import
this; the main thread owns it. No lane may edit this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

# Variable input states
PRESENT = "present"
MISSING = "missing"
CONFLICTING = "conflicting"

# Gold dispositions per variable
GOLD_RESOLVABLE = "resolvable"
GOLD_MUST_SUSPEND = "must-suspend"

# Agent actions
ACT_ASSERT = "assert"
ACT_SUSPEND = "suspend"
ACT_FILE_UNDER_ASSUMPTION = "file_under_assumption"

# Pre-declared H1 bound (paper §8): fraction of variables an agent must
# resolve (non-suspend) for its fabrication count to be comparable.
COVERAGE_FLOOR = 0.60

# Materiality threshold for G1's suspension policy: a variable is material
# to the outcome when its evidence state is unresolved AND it is marked
# material in the scenario.
MATERIAL_DEFAULT = True


@dataclass(frozen=True)
class VariableInput:
    """What the agent sees for one variable in one session.

    evidence: self-contained fact strings (e.g. "travel_deduction:
    receipt R-77 states 12,000 ZAR"). Empty for MISSING; two or more
    disagreeing strings for CONFLICTING.
    """
    var: str
    state: str  # PRESENT | MISSING | CONFLICTING
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PriorObservation:
    """A variable observation made in an earlier session, since EVICTED
    (state layer cycled; the ledger should still remember it)."""
    var: str
    state: str  # PRESENT | MISSING | CONFLICTING (as it was observed)
    evidence: tuple[str, ...] = ()
    observed_at: str = "session-1"


@dataclass(frozen=True)
class Gold:
    """The correct disposition for one variable."""
    disposition: str  # GOLD_RESOLVABLE | GOLD_MUST_SUSPEND
    value: Optional[float] = None  # definitive value when resolvable


@dataclass(frozen=True)
class Scenario:
    id: str
    variables: tuple[str, ...]
    inputs: dict[str, VariableInput]
    materiality: dict[str, bool]
    gold: dict[str, Gold]
    prior: tuple[PriorObservation, ...] = ()
    difficulty: float = 0.0  # fraction of variables whose gold is MUST_SUSPEND


@dataclass(frozen=True)
class Disposition:
    var: str
    action: str  # ACT_ASSERT | ACT_SUSPEND | ACT_FILE_UNDER_ASSUMPTION
    value: Optional[float] = None
    assumptions: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()  # evidence strings the decision rests on


@dataclass(frozen=True)
class AgentRun:
    agent: str
    scenario_id: str
    dispositions: tuple[Disposition, ...]
    events: tuple[str, ...] = ()  # audit artefacts handed to H2 auditors


class Agent(Protocol):
    name: str

    def run(self, scenario: Scenario) -> AgentRun: ...
