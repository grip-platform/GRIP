"""G0 — the UNGATED failure-mode agent (Lane B, Sunyata EvidenceState pilot).

G0 is a deterministic model of an LLM agent that silently defaults. For
every variable it behaves exactly the way the EvidenceState gate exists to
prevent:

- PRESENT evidence     -> assert the parsed value from the first fact;
- MISSING evidence     -> assert 0.0 (a fabricated default, no suspension);
- CONFLICTING evidence -> assert the value from the first fact (it silently
  picks one side and never flags the dispute).

G0 NEVER suspends and never files under assumption. It wraps no
EvidenceState machinery by design — the only artefact it leaves is a
verbose raw-transcript event log (one prose line per decision, written the
way a human would see the chat transcript), which is exactly the surface
H2's auditors inspect when they ask "what would an ungated agent have told
the client?". Prior observations are ignored: G0 has no memory of evicted
evidence and would not cite it if it had.
"""

from __future__ import annotations

from sunyata_pilot.agents import parse_evidence_value
from sunyata_pilot.common import (
    ACT_ASSERT,
    CONFLICTING,
    MISSING,
    PRESENT,
    AgentRun,
    Disposition,
    Scenario,
    VariableInput,
)


class G0Agent:
    """The ungated agent: asserts always, fabricates on gaps."""

    name = "G0"

    def run(self, scenario: Scenario) -> AgentRun:
        dispositions: list[Disposition] = []
        events: list[str] = []
        events.append(
            f"G0 transcript — scenario {scenario.id}: "
            f"{len(scenario.variables)} variables to value.")
        for var in scenario.variables:
            inp = scenario.inputs.get(var) or VariableInput(var, MISSING)
            if inp.state == PRESENT:
                value = parse_evidence_value(inp.evidence[0] if inp.evidence else "")
                fact = inp.evidence[0] if inp.evidence else ""
                dispositions.append(
                    Disposition(var, ACT_ASSERT, value, provenance=inp.evidence))
                events.append(
                    f"{var}: I see {fact!r} on file — asserting {value}.")
            elif inp.state == CONFLICTING:
                value = parse_evidence_value(inp.evidence[0] if inp.evidence else "")
                first = inp.evidence[0] if inp.evidence else ""
                others = " and ".join(repr(f) for f in inp.evidence[1:])
                dispositions.append(
                    Disposition(var, ACT_ASSERT, value, provenance=inp.evidence))
                events.append(
                    f"{var}: the file says {first!r} but also {others} — "
                    f"disagreement; I'll go with the first. Asserting {value}.")
            else:  # MISSING
                dispositions.append(Disposition(var, ACT_ASSERT, 0.0))
                events.append(
                    f"{var}: nothing on file for this — defaulting to 0.0.")
        return AgentRun(
            agent=self.name,
            scenario_id=scenario.id,
            dispositions=tuple(dispositions),
            events=tuple(events),
        )
