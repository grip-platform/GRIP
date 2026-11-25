"""Sunyata benchmark corpus loader (Lane A).

Loads the synthetic SARS-flavoured scenario fixtures from
`scenarios/*.json` and converts them into the frozen `common` dataclasses.
Deterministic (files sorted by name), offline, no network.

Difficulty is COMPUTED here, never read from a fixture: the fraction of a
scenario's variables whose gold disposition is GOLD_MUST_SUSPEND.

API:
    load_scenarios() -> tuple[Scenario, ...]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sunyata_pilot.common import (
    CONFLICTING,
    GOLD_MUST_SUSPEND,
    GOLD_RESOLVABLE,
    MISSING,
    PRESENT,
    Gold,
    PriorObservation,
    Scenario,
    VariableInput,
)

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

_VALID_STATES = {PRESENT, MISSING, CONFLICTING}
_VALID_DISPOSITIONS = {GOLD_MUST_SUSPEND, GOLD_RESOLVABLE}


def _parse_variable_input(raw: dict[str, Any], var: str) -> VariableInput:
    state = raw.get("state")
    if state not in _VALID_STATES:
        raise ValueError(f"fixture {var}: unknown input state {state!r}")
    evidence = tuple(raw.get("evidence") or ())
    return VariableInput(var=var, state=state, evidence=evidence)


def _parse_gold(raw: dict[str, Any], var: str) -> Gold:
    disposition = raw.get("disposition")
    if disposition not in _VALID_DISPOSITIONS:
        raise ValueError(f"fixture {var}: unknown gold disposition {disposition!r}")
    value = raw.get("value")
    if value is not None and disposition != GOLD_RESOLVABLE:
        raise ValueError(f"fixture {var}: non-resolvable gold carries a value")
    if disposition == GOLD_RESOLVABLE and value is None:
        raise ValueError(f"fixture {var}: resolvable gold missing its value")
    return Gold(disposition=disposition, value=float(value) if value is not None else None)


def _parse_prior(raw: dict[str, Any]) -> PriorObservation:
    return PriorObservation(
        var=raw["var"],
        state=raw.get("state", PRESENT),
        evidence=tuple(raw.get("evidence") or ()),
        observed_at=raw.get("observed_at", "session-1"),
    )


def _parse_scenario(raw: dict[str, Any]) -> Scenario:
    sid = raw["id"]
    variables = tuple(raw["variables"])
    inputs = {var: _parse_variable_input(raw["inputs"][var], var) for var in variables}
    materiality = {var: bool(raw["materiality"][var]) for var in variables}
    gold = {var: _parse_gold(raw["gold"][var], var) for var in variables}
    prior = tuple(_parse_prior(p) for p in raw.get("prior") or ())
    difficulty = sum(1 for var in variables if gold[var].disposition == GOLD_MUST_SUSPEND) / len(variables)
    return Scenario(
        id=sid,
        variables=variables,
        inputs=inputs,
        materiality=materiality,
        gold=gold,
        prior=prior,
        difficulty=difficulty,
    )


def load_scenarios() -> tuple[Scenario, ...]:
    """Load every `scenarios/*.json` fixture, sorted by file name.

    Deterministic: same files in, same tuple out. No network access.
    Raises ValueError on a malformed fixture rather than silently
    producing a broken Scenario.
    """
    scenarios: list[Scenario] = []
    for path in sorted(SCENARIOS_DIR.glob("*.json")):
        raw = json.loads(path.read_text())
        scenarios.append(_parse_scenario(raw))
    return tuple(scenarios)
