"""H3 harness — the lens must earn its name (paper §8).

Lane E of the Sunyata EvidenceState pilot fleet. Constraint attribution:
each declared design constraint is measured as a candidate and must show
a benchmark delta that is absent from the baselines and not implied by
Kleene + option types + a conventional audit log.

Candidate (i) — the CONFLICT corner (B2 vs B1, paper §7/§8):
    On matched scenarios containing CONFLICTING variables, compare B1
    (no conflict state: asserts the first fact) with B2 (conflict state:
    suspends / discloses, never asserts). The candidate survives iff the
    delta exists (B1 asserts while B2 suspends, and B1's fabrication rate
    on gold-must-suspend conflict vars strictly exceeds B2's) AND the
    delta is gold-correct (B2 never fabricates on those vars).

Candidate (ii) — the state-cycles / ledger-monotone split (G1 vs B1):
    On matched multi-session scenarios (``scenario.prior`` non-empty),
    compare G1's flip-ledger with B1's conventional log. G1 cites the
    evicted prior observation (a FILE_UNDER_ASSUMPTION disposition whose
    events or provenance carry the prior evidence); B1 shows no eviction
    trace (plain suspend, no prior mention). The candidate survives iff
    the disposition or its provenance differs between G1 and B1 on at
    least one prior scenario.

Fabrication follows the h1 lane's definition: ACT_ASSERT *with a value*
on a variable whose gold disposition is GOLD_MUST_SUSPEND. A disposition
missing for a variable counts as ACT_SUSPEND (an absent decision defers).

Input shape: ``results`` is a dict of agent name -> sequence of
``(AgentRun, Scenario)`` pairs, e.g.
``{"B1": [...], "B2": [...], "G1": [...]}`` — the same pairing the h1
lane consumes. Runs are paired across agents by ``scenario.id``; the
last run per id wins.

Verdict shape:

    {
      "verdict": "CONFIRMED" | "FALSIFIED",
      "reasons": [str],          # empty when CONFIRMED
      "candidates": {
        "conflict_corner": {survives, measurable, action_difference,
                            fabrication, fabrication_rate, gold_correct,
                            detail},
        "ledger_monotone": {survives, measurable, deltas, detail},
      },
      # FALSIFIED only — the paper's committed loser line + rename note:
      "committed_line": "the components were sufficient, the Buddhism was pedagogy",
      "rename_note": "the successor version renames accordingly",
    }
"""

from __future__ import annotations

from typing import Sequence

from sunyata_pilot.common import (
    ACT_ASSERT,
    ACT_FILE_UNDER_ASSUMPTION,
    ACT_SUSPEND,
    CONFLICTING,
    GOLD_MUST_SUSPEND,
    AgentRun,
    PriorObservation,
    Scenario,
)

VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_FALSIFIED = "FALSIFIED"

LOSER_LINE = "the components were sufficient, the Buddhism was pedagogy"
RENAME_NOTE = "the successor version renames accordingly"


def _by_scenario(results: Sequence[tuple[AgentRun, Scenario]]):
    """Map scenario_id -> (run, scenario); the last run per id wins."""
    index: dict[str, tuple[AgentRun, Scenario]] = {}
    for run, scenario in results:
        index[scenario.id] = (run, scenario)
    return index


def _disposition(run: AgentRun, var: str):
    """The run's disposition for ``var``, or None (counts as suspended)."""
    for disp in run.dispositions:
        if disp.var == var:
            return disp
    return None


def _fabricates(run: AgentRun, var: str, scenario: Scenario) -> bool:
    """h1-consistent fabrication: assert-with-value on a must-suspend var."""
    if scenario.gold[var].disposition != GOLD_MUST_SUSPEND:
        return False
    disp = _disposition(run, var)
    return disp is not None and disp.action == ACT_ASSERT and disp.value is not None


def _candidate_conflict(b1_seq, b2_seq) -> dict:
    """Candidate (i): the CONFLICT corner drives different actions (B2 vs B1)."""
    b1_map, b2_map = _by_scenario(b1_seq), _by_scenario(b2_seq)
    matched = sorted(set(b1_map) & set(b2_map))
    action_difference: list[dict] = []
    fabrication = {"B1": 0, "B2": 0}
    denom = 0
    conflict_scenarios = 0
    for sid in matched:
        _, scenario = b1_map[sid]
        conflict_vars = [
            v for v in scenario.variables
            if scenario.inputs[v].state == CONFLICTING
        ]
        if not conflict_vars:
            continue
        conflict_scenarios += 1
        b1_run, _ = b1_map[sid]
        b2_run, _ = b2_map[sid]
        for var in conflict_vars:
            if scenario.gold[var].disposition != GOLD_MUST_SUSPEND:
                continue
            denom += 1
            b1_disp = _disposition(b1_run, var)
            b2_disp = _disposition(b2_run, var)
            b1_action = b1_disp.action if b1_disp else ACT_SUSPEND
            b2_action = b2_disp.action if b2_disp else ACT_SUSPEND
            if b1_action == ACT_ASSERT and b1_disp is not None \
                    and b1_disp.value is not None:
                fabrication["B1"] += 1
            if b2_action == ACT_ASSERT and b2_disp is not None \
                    and b2_disp.value is not None:
                fabrication["B2"] += 1
            if b1_action == ACT_ASSERT and b2_action == ACT_SUSPEND:
                action_difference.append({"scenario_id": sid, "var": var})
    measurable = conflict_scenarios > 0
    delta = measurable and fabrication["B1"] > fabrication["B2"]
    gold_correct = fabrication["B2"] == 0
    survives = bool(action_difference) and delta and gold_correct
    detail = (
        f"{len(action_difference)} B1-assert/B2-suspend divergences across "
        f"{conflict_scenarios} matched conflict scenario(s); fabrication "
        f"B1={fabrication['B1']} B2={fabrication['B2']} on {denom} "
        f"gold-must-suspend conflict var(s)"
    )
    return {
        "survives": survives,
        "measurable": measurable,
        "action_difference": action_difference,
        "fabrication": dict(fabrication),
        "fabrication_rate": {
            "B1": fabrication["B1"] / denom if denom else None,
            "B2": fabrication["B2"] / denom if denom else None,
        },
        "gold_correct": gold_correct,
        "detail": detail,
    }


def _cites_prior(prior: PriorObservation, run: AgentRun, disp) -> bool:
    """Whether ``run`` cites the evicted prior observation anywhere."""
    texts = list(prior.evidence)
    if disp is not None:
        texts += list(disp.provenance) + list(disp.assumptions)
    for line in run.events:
        for text in texts:
            if text and text in line:
                return True
    return False


def _dispositions_differ(d1, d2) -> bool:
    if d1 is None or d2 is None:
        return d1 is not d2
    return (d1.action, d1.provenance, d1.assumptions) != (
        d2.action,
        d2.provenance,
        d2.assumptions,
    )


def _candidate_ledger(g1_seq, b1_seq) -> dict:
    """Candidate (ii): the ledger-monotone split changes multi-session work."""
    g1_map, b1_map = _by_scenario(g1_seq), _by_scenario(b1_seq)
    matched = sorted(set(g1_map) & set(b1_map))
    deltas: list[dict] = []
    prior_scenarios = 0
    for sid in matched:
        _, scenario = g1_map[sid]
        if not scenario.prior:
            continue
        prior_scenarios += 1
        g1_run, _ = g1_map[sid]
        b1_run, _ = b1_map[sid]
        for prior in scenario.prior:
            g1_disp = _disposition(g1_run, prior.var)
            b1_disp = _disposition(b1_run, prior.var)
            g1_cites = (
                g1_disp is not None
                and g1_disp.action == ACT_FILE_UNDER_ASSUMPTION
                and _cites_prior(prior, g1_run, g1_disp)
            )
            b1_no_trace = (
                (b1_disp is None or b1_disp.action == ACT_SUSPEND)
                and not _cites_prior(prior, b1_run, b1_disp)
            )
            if g1_cites and b1_no_trace and _dispositions_differ(g1_disp, b1_disp):
                deltas.append({"scenario_id": sid, "var": prior.var})
    measurable = prior_scenarios > 0
    survives = bool(deltas)
    detail = (
        f"{len(deltas)} disposition/provenance delta(s) across "
        f"{prior_scenarios} matched prior scenario(s)"
    )
    return {
        "survives": survives,
        "measurable": measurable,
        "deltas": deltas,
        "detail": detail,
    }


def h3_verdict(results: dict) -> dict:
    """Verdict on H3 — the lens must earn its name (paper §8).

    CONFIRMED iff at least one declared candidate survives. FALSIFIED
    otherwise — and the paper's committed loser line plus the rename note
    are then included verbatim.
    """
    cand_i = _candidate_conflict(results.get("B1", ()), results.get("B2", ()))
    cand_ii = _candidate_ledger(results.get("G1", ()), results.get("B1", ()))
    confirmed = cand_i["survives"] or cand_ii["survives"]
    verdict = {
        "verdict": VERDICT_CONFIRMED if confirmed else VERDICT_FALSIFIED,
        "reasons": [] if confirmed else [LOSER_LINE],
        "candidates": {
            "conflict_corner": cand_i,
            "ledger_monotone": cand_ii,
        },
    }
    if not confirmed:
        verdict["committed_line"] = LOSER_LINE
        verdict["rename_note"] = RENAME_NOTE
    return verdict
