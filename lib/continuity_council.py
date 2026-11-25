"""Congruent Continuity — the settlement worker's L2 EXOGENOUS COUNCIL leg.

The deterministic floor (``lib/continuity_settle.py``) settles only ``probe:``
falsifiers and leaves every other commitment "left-for-council". Until this
module existed nothing consumed that worklist: the chain's own first refuted
commitment had to be settled INCONGRUENT by hand
(``docs/congruent-continuity-buildlog-2026-08-30.md``). This is the council.

Design (``CONGRUENCE.md`` "L2 — the exogenous council"):

- **Exogenous.** The verdict is produced by non-Anthropic Pool-B models and
  the record's ``verifier`` is the non-Anthropic model id that adjudicated —
  never the agent that made the commitment (exogenous-anchor-law).
- **Disagreement is the signal.** ``aggregate`` consumes the tally of sitting
  votes — a strict majority reaching ``AGREEMENT_FLOOR`` — so no single model's
  opinion (the self-report class relocated) and no lone dissenter can force a
  verdict. Agreement is reported in ``[0, 1]``, the same contract as
  ``lib/hal/moe.py::HALCouncil._compute_agreement``.
- **A council under quorum has ratified nothing.** Fewer than ``QUORUM``
  sitting votes, an exception from the council function, or no votes at all
  leave the commitment UNSETTLED for the next pass (``NO_QUORUM`` is not a
  verdict — the same rule GRIP applies to an AGORA tribunal). A quorate
  council that splits or abstains settles UNVERIFIABLE with its votes as
  evidence: that IS an exogenous outcome. Nothing here ever defaults to
  CONGRUENT.
- **Floor precedence.** A ``probe:`` falsifier is never sent to a model; the
  council only sees what a machine cannot settle.
- **Injectable.** ``settle_council(council_fn=...)`` takes any callable
  ``brief -> CouncilOutcome``; tests pass a stub and make no network call.
  ``sovereign_council_fn`` is the live adapter over ``lib/sovereign_council.py``
  (fan-out to the speakers, a different non-Anthropic judge, a sealed record).

Axis routing: a falsifier prefixed ``intent:`` is judged on the intent axis
(does the trajectory still serve the operator's goal); every other prose
falsifier is judged on reality. ``goal_ref`` is context passed to the council,
not an axis switch — the falsifier states what is being tested.

This is not a safety gate: settlement feeds audit records and WARN-only
consumers (locked decision D3), which is the class an LLM-graded outcome is
admissible for — a wrong verdict here is inefficient, not unsafe.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from lib.continuity_chain import VERDICTS, commit_addr_of, settle, unsettled
from lib.continuity_settle import classify_falsifier

__all__ = [
    "INTENT_PREFIX",
    "QUORUM",
    "AGREEMENT_FLOOR",
    "Vote",
    "CouncilOutcome",
    "CouncilFn",
    "axis_for",
    "brief_for",
    "aggregate",
    "settle_council",
    "sovereign_council_fn",
]

INTENT_PREFIX = "intent:"
#: Fewer sitting votes than this and the council has ratified nothing.
QUORUM = 2
#: Share of sitting votes the winning verdict must reach. Above one half, so a
#: 2-2 split or a 1-1 split is an abstention, never a verdict.
AGREEMENT_FLOOR = 0.6


@dataclass(frozen=True)
class Vote:
    speaker: str      # the non-Anthropic model id that sat
    verdict: str      # CONGRUENT | INCONGRUENT | UNVERIFIABLE (anything else does not sit)
    reason: str = ""


@dataclass
class CouncilOutcome:
    votes: list[Vote] = field(default_factory=list)
    judge: str = ""                 # non-Anthropic model id that adjudicated -> the verdict's verifier
    record_addr: str | None = None  # sealed council record (sha256) when one was made


CouncilFn = Callable[[dict], CouncilOutcome]


def axis_for(decision: dict) -> str:
    """'intent' for an ``intent:``-prefixed falsifier, else 'reality'."""
    falsifier = str(decision.get("falsifier", "")).strip()
    return "intent" if falsifier.startswith(INTENT_PREFIX) else "reality"


def brief_for(node) -> dict:
    """What the council is told: the commitment's own fields plus its axis."""
    d = node.decision
    return {
        "commit_addr": commit_addr_of(node),
        "claim": d.get("claim"),
        "falsifier": d.get("falsifier"),
        "deps": list(d.get("deps", [])),
        "severity": d.get("severity"),
        "goal_ref": d.get("goal_ref"),
        "axis": axis_for(d),
    }


def aggregate(votes: list[Vote]) -> tuple[str, float, dict]:
    """Consume the disagreement structure of the sitting votes.

    Returns ``(verdict, agreement, detail)``. ``verdict`` is a chain verdict or
    ``"NO_QUORUM"`` (not settleable); ``agreement`` is the winning share of the
    sitting votes in ``[0, 1]``; ``detail`` carries the tally and seat count.
    """
    sitting = [v for v in votes if v.verdict in VERDICTS]
    detail = {"sitting": len(sitting), "tally": dict(Counter(v.verdict for v in sitting))}
    if len(sitting) < QUORUM:
        return "NO_QUORUM", 0.0, detail
    top, n = Counter(v.verdict for v in sitting).most_common(1)[0]
    agreement = n / len(sitting)
    if top == "UNVERIFIABLE" or agreement < AGREEMENT_FLOOR:
        return "UNVERIFIABLE", agreement, detail
    return top, agreement, detail


def _convene(council_fn: CouncilFn, brief: dict) -> tuple[CouncilOutcome | None, str | None]:
    """Call the council; an exception is unavailability, never a verdict."""
    try:
        outcome = council_fn(brief)
    except Exception as exc:  # noqa: BLE001 — any failure is 'unavailable'
        return None, f"council unavailable: {type(exc).__name__}: {exc}"[:240]
    if outcome is None or not outcome.votes:
        return None, "council returned no votes"
    if not str(outcome.judge).strip():
        return None, "council named no judge — an unnamed verifier is not exogenous evidence"
    return outcome, None


def _settle_one(node, council_fn: CouncilFn, *, path, head_pointer) -> dict:
    addr = commit_addr_of(node)
    brief = brief_for(node)
    outcome, why = _convene(council_fn, brief)
    if outcome is None:
        return {"commit_addr": addr, "action": "council-unavailable", "reason": why}
    verdict, agreement, detail = aggregate(outcome.votes)
    if verdict == "NO_QUORUM":
        return {"commit_addr": addr, "action": "no-quorum", "sitting": detail["sitting"],
                "quorum": QUORUM}
    evidence = {"votes": [asdict(v) for v in outcome.votes], "agreement": agreement,
                "quorum": QUORUM, **detail}
    if outcome.record_addr:
        evidence["record_addr"] = outcome.record_addr
    settle(addr, verdict, axis=brief["axis"], evidence=evidence, verifier=outcome.judge,
           action_taken="none", path=path, head_pointer=head_pointer)
    return {"commit_addr": addr, "action": "settled", "verdict": verdict, "axis": brief["axis"],
            "verifier": outcome.judge, "evidence": evidence}


def settle_council(
    *,
    council_fn: CouncilFn,
    path: Path | None = None,
    head_pointer: Path | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Settle every council-classified unsettled commitment. One report row per
    commitment considered: ``settled`` / ``dry-run`` / ``left-for-floor`` /
    ``council-unavailable`` / ``no-quorum``. ``dry_run`` convenes nothing."""
    rows: list[dict] = []
    work = unsettled(path=path)
    if limit is not None:
        work = work[:limit]
    for node in work:
        addr = commit_addr_of(node)
        if classify_falsifier(str(node.decision.get("falsifier", ""))) == "probe":
            rows.append({"commit_addr": addr, "action": "left-for-floor"})
            continue
        if dry_run:
            brief = brief_for(node)
            rows.append({"commit_addr": addr, "action": "dry-run", "axis": brief["axis"], "brief": brief})
            continue
        rows.append(_settle_one(node, council_fn, path=path, head_pointer=head_pointer))
    return rows


# --- the live adapter over lib.sovereign_council --------------------------------

_VERDICT_WORD = re.compile(r"\b(CONGRUENT|INCONGRUENT|UNVERIFIABLE)\b")


def _prompt_for(brief: dict) -> str:
    axis_q = ("does the trajectory described still serve the operator's stated goal"
              if brief["axis"] == "intent"
              else "is the premise true against canonical reality")
    return (
        "You are one seat on an exogenous council settling a pre-registered commitment "
        "made by an AI agent BEFORE it acted. Judge the commitment, not the agent.\n\n"
        f"CLAIM (the premise acted on): {brief['claim']}\n"
        f"FALSIFIER (what would refute it): {brief['falsifier']}\n"
        f"AXIS: {brief['axis']} — {axis_q}?\n"
        f"SEVERITY of the intended action: {brief['severity']}\n"
        f"GOAL REFERENCE: {brief.get('goal_ref') or 'none recorded'}\n"
        f"DEPENDS ON: {', '.join(brief.get('deps') or []) or 'nothing recorded'}\n\n"
        "Reply ONLY with compact JSON: {\"verdict\": \"CONGRUENT\" | \"INCONGRUENT\" | "
        "\"UNVERIFIABLE\", \"reason\": \"<one sentence>\"}. CONGRUENT means the claim "
        "holds and the falsifier did not fire; INCONGRUENT means the falsifier fires; "
        "UNVERIFIABLE means you cannot tell from what is given — abstaining is a "
        "first-class answer, never guess."
    )


def _vote_from(result: dict) -> Vote | None:
    """Map one speaker result to a Vote; a dead speaker did not sit."""
    speaker = str(result.get("model") or result.get("name") or "").strip()
    if not result.get("ok") or not speaker:
        return None
    text = str(result.get("text") or "")
    if result.get("truncated"):
        return Vote(speaker, "UNVERIFIABLE", "answer cut off mid-sentence")
    try:
        parsed = json.loads(text.strip().strip("`").removeprefix("json").strip())
        verdict, reason = str(parsed.get("verdict", "")).upper(), str(parsed.get("reason", ""))
    except (json.JSONDecodeError, AttributeError, TypeError):
        m = _VERDICT_WORD.search(text.upper())
        verdict, reason = (m.group(1) if m else "UNVERIFIABLE"), text.strip()[:200]
    if verdict not in VERDICTS:
        verdict, reason = "UNVERIFIABLE", f"unparseable answer: {text.strip()[:120]}"
    return Vote(speaker, verdict, reason)


def sovereign_council_fn(brief: dict, *, speakers: list[str] | None = None,
                         judge_name: str | None = None) -> CouncilOutcome:
    """Convene ``lib/sovereign_council.py`` on one brief: fan-out to the
    speakers, a DIFFERENT non-Anthropic judge names itself as the verifier, and
    the sealed record's sha becomes the evidence's ``record_addr``. Imported
    lazily so tests and the floor never touch provider setup."""
    import lib.sovereign_council as sc

    prompt = _prompt_for(brief)
    seats = [s for s in list(speakers or sc.DEFAULT_SPEAKERS) if "anthropic" not in s and "claude" not in s]
    results, _wall = sc.fan_out(prompt, seats)
    verdict = sc.judge(prompt, results, judge_name or sc.DEFAULT_JUDGE)
    try:
        _record, sha = sc.seal(prompt, results, verdict)
    except ValueError:  # a malformed result cannot be sealed; the votes still stand, unsealed
        sha = None
    votes = [v for v in (_vote_from(r) for r in results) if v is not None]
    return CouncilOutcome(votes=votes, judge=str(verdict.get("judge_model") or ""), record_addr=sha)


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="continuity_council")
    p.add_argument("--chain", help="chain file override (tests)")
    p.add_argument("--head", help="HEAD pointer override (tests)")
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true", help="list what would be convened; call no model")
    ns = p.parse_args(argv)
    rows = settle_council(
        council_fn=sovereign_council_fn,
        path=Path(ns.chain) if ns.chain else None,
        head_pointer=Path(ns.head) if ns.head else None,
        limit=ns.limit,
        dry_run=ns.dry_run,
    )
    for row in rows:
        print(json.dumps(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
