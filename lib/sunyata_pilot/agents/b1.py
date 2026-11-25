"""B1 — the novelty kill-test baseline (paper §7, council fold C2).

Kleene three-valued logic + option types + a conventional append-only
audit log: the strongest cheap assembly of prior art. Per variable:

    PRESENT     -> TRUE: assert the parsed value
    MISSING     -> UNKNOWN: suspend (material) / skip-with-note (immaterial)
    CONFLICTING -> no conflict state exists: assert the FIRST fact's value

The conflict branch is B1's retained failure mode: the contradiction is
silently absorbed and survives only as a plain log line noting the facts
seen. No sealing, no structured flip records, no negative provenance.
Prior observations (scenario.prior) are ignored — B1 reads only current
inputs.

run() returns the log lines appended during that run; the log itself is
append-only across runs and is never replaced or truncated.
"""

from __future__ import annotations

import re
from typing import Optional

from sunyata_pilot.common import (
    ACT_ASSERT,
    ACT_SUSPEND,
    MATERIAL_DEFAULT,
    MISSING,
    PRESENT,
    Agent,
    AgentRun,
    Disposition,
    Scenario,
)

_VALUE_VERBS = ("states", "says", "amount", "value", "total", "=")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def parse_evidence_value(evidence: str) -> Optional[float]:
    """Parse the numeric value a fact string asserts, or None.

    Prefer the first number after a value verb ("states 12,000" ->
    12000.0); otherwise take the last number not glued to a letter, which
    skips receipt ids like "R-77". Commas are treated as thousand
    separators.
    """
    lower = evidence.lower()
    for verb in _VALUE_VERBS:
        idx = lower.find(verb)
        if idx >= 0:
            match = _NUMBER_RE.search(evidence, idx + len(verb))
            if match:
                return float(match.group().replace(",", ""))
    candidates: list[str] = []
    for match in _NUMBER_RE.finditer(evidence):
        prev = evidence[match.start() - 1] if match.start() > 0 else ""
        if prev and (prev.isalnum() or prev == "."):
            continue
        candidates.append(match.group())
    if not candidates:
        return None
    return float(candidates[-1].replace(",", ""))


class B1Agent(Agent):
    """Kleene + option types + conventional append-only audit log."""

    name = "B1"

    def __init__(self) -> None:
        self.log: list[str] = []

    def _note(self, line: str) -> None:
        self.log.append(line)

    def run(self, scenario: Scenario) -> AgentRun:
        start = len(self.log)
        self._note(f"run: scenario={scenario.id} agent={self.name}")
        dispositions: list[Disposition] = []
        for var in scenario.variables:
            inp = scenario.inputs[var]
            material = scenario.materiality.get(var, MATERIAL_DEFAULT)
            if inp.state == PRESENT:
                dispositions.append(self._present(var, inp.evidence))
            elif inp.state == MISSING:
                disp = self._missing(var, material)
                if disp is not None:
                    dispositions.append(disp)
            else:
                disp = self._conflict(var, inp.evidence, material)
                if disp is not None:
                    dispositions.append(disp)
        return AgentRun(
            agent=self.name,
            scenario_id=scenario.id,
            dispositions=tuple(dispositions),
            events=tuple(self.log[start:]),
        )

    def _present(self, var: str, evidence: tuple[str, ...]) -> Disposition:
        first = evidence[0] if evidence else ""
        value = parse_evidence_value(first)
        self._note(f"assert: {var} = {value}")
        return Disposition(
            var=var,
            action=ACT_ASSERT,
            value=value,
            provenance=(first,) if first else (),
        )

    def _missing(self, var: str, material: bool) -> Optional[Disposition]:
        if material:
            self._note(f"suspend: {var} (missing, material)")
            return Disposition(var=var, action=ACT_SUSPEND)
        self._note(f"skip: {var} (missing, immaterial; no value asserted)")
        return None

    def _conflict(
        self, var: str, evidence: tuple[str, ...], material: bool
    ) -> Optional[Disposition]:
        if not evidence:
            return self._missing(var, material)
        first = evidence[0]
        value = parse_evidence_value(first)
        self._note(
            f"assert: {var} = {value} "
            f"(conflict: {len(evidence)} facts seen; asserting first)"
        )
        return Disposition(
            var=var,
            action=ACT_ASSERT,
            value=value,
            provenance=(first,),
        )
