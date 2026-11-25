"""G1 — the FULL EvidenceState gate agent (Lane B, Sunyata EvidenceState pilot).

G1 wraps the real ``lib.evidence_state`` machinery and adds NOTHING of its
own to the logic: the two-layer store (working cycles under eviction,
cumulative + append-only ledger climb monotonically), ``decide()``'s
materiality-sensitive suspension policy (council fold C1: suspension is
policy, never FDE entailment), ``file_under_assumption()``'s explicit
"file now, amend later" move, and the finite, versioned
``negative_provenance`` surface (council fold C5). G1 never asserts a value
the evidence does not support.

Disposition policy, calibrated for the corpus-wide ``COVERAGE_FLOOR``:

- PRESENT evidence -> the claim's working state is TRUE -> decide() returns
  DEFINITIVE -> **assert** the parsed value from the evidence basis, with
  the fact strings attached as provenance.
- Missing (working LATENT) on a MATERIAL variable -> decide() returns
  SUSPENDED (recommended REQUEST_EVIDENCE when a source is known, else
  SUSPEND); G1 files the claim **under assumption** — a non-suspend
  resolution that asserts NO value, records the assumption on the store's
  append-only assumption ledger, and cites any retained ledger flips from
  evicted prior observations in the marker.
- CONFLICTING evidence (working CONFLICT, material or not) -> **suspend**:
  decide() recommends ESCALATE and file_under_assumption() refuses a
  CONFLICT claim by contract (a dispute is not a gap to paper over).
- Missing on an IMMATERIAL variable -> **suspend** (never assert a value
  for an unresolved variable).

With this policy the non-suspend fraction of any scenario is bounded below
by its resolvable-plus-assumable fraction, so a corpus whose
must-suspend share respects the pre-declared H1 bound keeps G1 above
``COVERAGE_FLOOR`` (0.60). A scenario whose gold is entirely must-suspend
may still come out all-suspend — the floor is corpus-wide by design.

The events log is the audit surface handed to H2: the replay + observation
events, every decide() verdict, every flip on the append-only ledger, every
assumption record, the per-scenario coverage note, and a
negative-provenance block for every variable that was NOT asserted. Every
run also closes with the four H2 grammar shapes the auditor resolves from
without reading the richer ledger (contract in harness/h2.py's docstring):
an ``index:`` line of ``var@lineno`` pointers, a dense
``negative-provenance: missing=<csv>; conflicting=<csv>`` line, and one
``entry:`` line per resolved variable (assert or file-under-assumption).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from lib.evidence_state import (
    Decision,
    EvidenceState,
    EvidenceStore,
    FlipRecord,
    decide,
    file_under_assumption,
    negative_provenance,
)
from sunyata_pilot.agents import parse_evidence_value_strict
from sunyata_pilot.common import (
    ACT_ASSERT,
    ACT_FILE_UNDER_ASSUMPTION,
    ACT_SUSPEND,
    CONFLICTING,
    COVERAGE_FLOOR,
    MATERIAL_DEFAULT,
    MISSING,
    PRESENT,
    AgentRun,
    Disposition,
    PriorObservation,
    Scenario,
    VariableInput,
)

__all__ = ["G1Agent"]

_OBSERVED = (PRESENT, CONFLICTING)


class G1Agent:
    """The gated agent: asserts only what the evidence supports."""

    name = "G1"

    def run(self, scenario: Scenario) -> AgentRun:
        materiality = self._materiality(scenario)
        priors = {p.var: p for p in scenario.prior}
        store: EvidenceStore = EvidenceStore(schema=scenario.variables)
        events: list[str] = []

        # 1) Replay prior observations FIRST (observe, then evict): the
        #    ledger retains the flips while the working layer cycles back
        #    to LATENT — an auditor can see what was once believed and
        #    when it stopped being operative.
        for prior in scenario.prior:
            recs = self._observe_facts(store, prior.var, prior.state, prior.evidence)
            store.evict(prior.var)
            flips = "; ".join(self._flip_desc(r) for r in recs if r is not None)
            events.append(
                f"replay prior {prior.var} (observed at {prior.observed_at}, "
                f"state {prior.state}): {flips or 'no ledger climb (already held)'}; "
                f"evicted — working back to LATENT, the ledger retains the flips")

        # 2) Observe the current inputs. MISSING is deliberately never
        #    observed: the variable stays LATENT (open-world knowledge),
        #    which is exactly the state decide() must see.
        for var in scenario.variables:
            inp = scenario.inputs.get(var) or VariableInput(var, MISSING)
            if inp.state not in _OBSERVED:
                continue
            for rec in self._observe_facts(store, var, inp.state, inp.evidence):
                if rec is not None:
                    events.append(
                        f"observe {var}: {self._flip_desc(rec)}")

        # 3) Decide per variable: build the coherent base claim from the
        #    store (store.claim — its state IS the working state), apply
        #    decide()'s suspension policy, map the verdict to a
        #    Disposition.
        sources = self._sources(scenario)
        dispositions: list[Disposition] = []
        for var in scenario.variables:
            inp = scenario.inputs.get(var) or VariableInput(var, MISSING)
            claim = store.claim(var)
            decision = decide(claim, store, materiality=materiality, sources=sources)
            disp, event = self._disposition(
                var, inp, claim, decision, materiality, priors, store)
            dispositions.append(disp)
            events.append(event)

        # 4) Audit artefacts for H2: the flip ledger, the assumption
        #    ledger, the coverage note, and negative provenance for every
        #    variable that was NOT asserted (the proof-of-ignorance
        #    surface only matters where no value was committed).
        for record in store.ledger:
            events.append(self._flip_desc(record, prefix="flip"))
        for record in store.assumptions:
            events.append(
                f"assumption[{record.seq}] claim={record.claim} "
                f"assumed={record.assumed} marker={record.marker!r} ts={record.ts}")
        for disp in dispositions:
            if disp.action == ACT_ASSERT:
                continue
            block = negative_provenance(
                store, store.claim(disp.var), materiality)
            events.append(
                f"negative_provenance {disp.var}: "
                f"{json.dumps(block.to_dict(), sort_keys=True)}")
        total = len(dispositions)
        non_suspend = sum(1 for d in dispositions if d.action != ACT_SUSPEND)
        frac = (non_suspend / total) if total else 0.0
        events.append(
            f"coverage this scenario: {non_suspend}/{total} non-suspend "
            f"({frac:.2f}; corpus floor {COVERAGE_FLOOR})")

        # 5) The H2 audit grammar (contract in harness/h2.py's docstring):
        #    one index line of var@lineno pointers into per-variable entry
        #    lines, one dense negative-provenance disclosure, and one
        #    entry line per resolved variable. The practitioner resolves
        #    every disposition from these four shapes without reading the
        #    richer ledger above; line numbers are 0-based positions in
        #    the events tuple recorded at append time.
        index_tokens: list[str] = []
        missing: list[str] = []
        conflicting: list[str] = []
        for var, disp in zip(scenario.variables, dispositions):
            if disp.action == ACT_ASSERT:
                evidence = "; ".join(disp.provenance) or "(no evidence recorded)"
                events.append(f"entry: {var} = {disp.value} | {evidence}")
                index_tokens.append(f"{var}@{len(events) - 1}")
            elif disp.action == ACT_FILE_UNDER_ASSUMPTION:
                note = disp.assumptions[0] if disp.assumptions else ""
                events.append(f"entry: {var} = file_under_assumption | {note}")
                index_tokens.append(f"{var}@{len(events) - 1}")
            elif store.claim(var).state is EvidenceState.CONFLICT:
                conflicting.append(var)
            else:
                missing.append(var)
        events.append(
            "negative-provenance: missing=" + ",".join(missing)
            + "; conflicting=" + ",".join(conflicting))
        events.append("index: " + " ".join(index_tokens))

        return AgentRun(
            agent=self.name,
            scenario_id=scenario.id,
            dispositions=tuple(dispositions),
            events=tuple(events),
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _materiality(scenario: Scenario) -> Callable[[str], bool]:
        """common.MATERIAL_DEFAULT merged with the scenario's materiality
        map: an unmapped variable fails TOWARD material (the conservative
        floor), exactly as lib/evidence_state.default_materiality does."""
        merged = dict.fromkeys(scenario.variables, MATERIAL_DEFAULT)
        merged.update(scenario.materiality)
        return lambda var: merged.get(var, MATERIAL_DEFAULT)

    @staticmethod
    def _sources(scenario: Scenario) -> dict[str, str]:
        """Known evidence sources: current facts first, then evicted prior
        facts (a source whose record was seen once is still a known
        source)."""
        sources: dict[str, str] = {}
        for var in scenario.variables:
            inp = scenario.inputs.get(var)
            if inp is not None and inp.evidence:
                sources[var] = inp.evidence[0]
        for prior in scenario.prior:
            if prior.evidence and prior.var not in sources:
                sources[prior.var] = prior.evidence[0]
        return sources

    @staticmethod
    def _observe_facts(store: EvidenceStore, var: str, state: str,
                       evidence: tuple[str, ...]) -> list[FlipRecord | None]:
        """Observe one variable's input as FDE evidence.

        PRESENT -> one TRUE observation (told-true). CONFLICTING -> the two
        disagreeing fact strings join to CONFLICT: the first is observed
        told-TRUE, the second told-FALSE (Belnap's Both — the join records
        the contradiction instead of discarding either side); a single
        conflicting string is observed directly as CONFLICT. MISSING ->
        nothing observed (stays LATENT).
        """
        recs: list[FlipRecord | None] = []
        if state == PRESENT:
            ref = evidence[0] if evidence else f"{var}: observed present (no detail)"
            recs.append(store.observe(var, EvidenceState.TRUE, ref))
        elif state == CONFLICTING:
            if len(evidence) >= 2:
                recs.append(store.observe(var, EvidenceState.TRUE, evidence[0]))
                recs.append(store.observe(var, EvidenceState.FALSE, evidence[1]))
            else:
                ref = evidence[0] if evidence else f"{var}: conflicting (no detail)"
                recs.append(store.observe(var, EvidenceState.CONFLICT, ref))
        return recs

    @staticmethod
    def _flip_desc(record: FlipRecord, prefix: str = "flip") -> str:
        return (f"{prefix}[{record.seq}] {record.var}: "
                f"{record.prior.name}->{record.new.name} "
                f"ref={record.evidence_ref!r} ts={record.ts}")

    @staticmethod
    def _prior_citation(var: str, store: EvidenceStore) -> str:
        """Cite the ledger flips retained for *var* (evicted prior
        observations): the ledger never un-knows, and the auditor must see
        that a value once held evidence which is no longer operative."""
        flips = [r for r in store.ledger if r.var == var]
        if not flips:
            return ""
        return "; ".join(
            f"seq {r.seq} {r.prior.name}->{r.new.name} ref={r.evidence_ref!r}"
            for r in flips)

    def _disposition(
            self, var: str, inp: VariableInput, claim, decision: Decision,
            materiality: Callable[[str], bool],
            priors: dict[str, PriorObservation],
            store: EvidenceStore) -> tuple[Disposition, str]:
        """Map decide()'s verdict to a common.Disposition.

        Assert ONLY on DEFINITIVE with a TRUE claim (the value is parsed
        from the evidence basis). Anything else asserts nothing: CONFLICT
        (and any non-LATENT residual) suspends — a dispute is not a gap;
        material LATENT files under assumption; immaterial LATENT suspends.
        """
        recommended = (decision.recommended.value
                       if decision.recommended is not None else "None")
        if decision.verdict == "DEFINITIVE" and claim.state is EvidenceState.TRUE:
            value = parse_evidence_value_strict(
                inp.evidence[0] if inp.evidence else "")
            if value is None:
                disp = Disposition(var, ACT_SUSPEND, provenance=inp.evidence)
                return disp, (
                    f"decide {var}: {claim.state.name} -> DEFINITIVE but "
                    f"evidence carries no amount -> suspend: a valueless "
                    f"fact is a gap, not a zero")
            disp = Disposition(var, ACT_ASSERT, value, provenance=inp.evidence)
            return disp, (
                f"decide {var}: {claim.state.name} -> DEFINITIVE -> "
                f"asserting {value}")
        if claim.state is not EvidenceState.LATENT:
            disp = Disposition(var, ACT_SUSPEND)
            return disp, (
                f"decide {var}: {claim.state.name} -> SUSPENDED "
                f"(recommended {recommended}) -> suspend: contradictory "
                f"evidence is a dispute, not a gap")
        if materiality(var):
            cite = self._prior_citation(var, store)
            marker = (f"no current evidence for {var}"
                      + (f"; prior observation: {cite}" if cite else ""))
            file_under_assumption(
                claim, store, {var: EvidenceState.LATENT}, marker,
                materiality=materiality)
            prior = priors.get(var)
            disp = Disposition(
                var, ACT_FILE_UNDER_ASSUMPTION, value=None,
                assumptions=(f"{var} assumed {EvidenceState.LATENT.name} "
                             f"under marker: {marker}",),
                provenance=prior.evidence if prior is not None else ())
            note = f"; prior ledger flip retained: {cite}" if cite else ""
            return disp, (
                f"decide {var}: {claim.state.name} (material) -> SUSPENDED "
                f"(recommended {recommended}) -> filed under assumption "
                f"'{marker}'{note}")
        disp = Disposition(var, ACT_SUSPEND)
        return disp, (
            f"decide {var}: {claim.state.name} (immaterial) -> SUSPENDED "
            f"(recommended {recommended}) -> suspend: never assert an "
            f"unresolved value")
