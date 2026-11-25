"""Congruent Continuity — the reasoning-trajectory chain (fourth moat leg).

Records the causal path BETWEEN belief and decision as signed, pre-registered
falsifiable commitments, and their exogenous settlements:

- ``kind="continuity-commit"`` — a commitment written BEFORE its intended
  action executes: {claim, falsifier, deps, severity, intended_action?,
  goal_ref?}. The append-before-act ordering, signed into the HMAC chain, is
  the anti-HARKing property: reasoning provably cannot be retro-fitted to the
  outcome.
- ``kind="continuity-verdict"`` — the settlement of one commitment:
  {commit_addr, verdict CONGRUENT|INCONGRUENT|UNVERIFIABLE, axis
  reality|intent, evidence, verifier, action_taken}. Written pre-hoc (the
  PRECOG watcher) or post-hoc (the settlement worker).

Reuses ``lib.precog.idr`` signing + ``lib.idr_forest`` verification wholesale —
zero new cryptography. Structure mirrors ``lib.context_chain`` (same
predecessor threading, same session namespacing, own chain file + HEAD).

Genesis: ``docs/congruent-continuity-genesis.md`` (HITL-locked 2026-08-30).
The genesis anchor is ``human:`` because the four shaping decisions were
literal operator input (V>> via the /hitl Route-D matrix, 2026-08-30).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from lib.audit_chain import Verdict
from lib.context_head import _session_path, read_head, write_head
from lib.idr_forest import build_chain_forest, verify_chain_integrity
from lib.precog.idr import (
    PrecogIDR,
    append_idr,
    build_idr,
    content_addr,
    read_idr_chain,
)

__all__ = [
    "COMMIT_KIND",
    "VERDICT_KIND",
    "VERDICTS",
    "AXES",
    "SEVERITIES",
    "ACTIONS_TAKEN",
    "chain_path",
    "head_path",
    "commit",
    "settle",
    "commit_addr_of",
    "read_continuity_chain",
    "verify_continuity_chain",
    "unsettled",
    "settlement_of",
    "belief_binding",
    "verify_belief_binding",
    "BELIEF_BOUND",
    "BELIEF_UNBOUND",
    "BELIEF_BROKEN",
    "contamination",
    "contamination_roots",
    "suspect",
]

#: Belief-binding states. Three, kept distinguishable on purpose: "this
#: commitment names no belief head" and "it names one that no longer matches" are
#: different facts about a record, and collapsing them reintroduces the
#: silent-absence problem the binding exists to remove.
BELIEF_BOUND = "bound"
BELIEF_UNBOUND = "unbound"
BELIEF_BROKEN = "broken"

COMMIT_KIND = "continuity-commit"
VERDICT_KIND = "continuity-verdict"

VERDICTS = ("CONGRUENT", "INCONGRUENT", "UNVERIFIABLE")
AXES = ("reality", "intent")
SEVERITIES = ("reversible", "irreversible")
ACTIONS_TAKEN = ("none", "steer", "halt-ask", "block")

# The genesis commitment's predecessor — an admissible exogenous anchor
# (human: prefix) so the chain roots into a Forest per exogenous-anchor-law.
_GENESIS_ANCHOR = "human:congruent-continuity-hitl-2026-08-30"

_STATE = Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude")) / "state" / "precog"
_DEFAULT_CHAIN_PATH = _STATE / "continuity.jsonl"
_DEFAULT_HEAD_PATH = _STATE / "continuity-HEAD.txt"


def chain_path(path: Path | None = None) -> Path:
    """Resolve the chain file (session-namespaced, same rule as context chain)."""
    return path or _session_path(_DEFAULT_CHAIN_PATH)


def head_path(path: Path | None = None) -> Path:
    """Resolve the HEAD pointer file (session-namespaced)."""
    return path or _session_path(_DEFAULT_HEAD_PATH)


def commit_addr_of(idr: PrecogIDR) -> str:
    """Content address of a continuity node — the settlement/dep coordinate."""
    return content_addr(asdict(idr))


def _require(value: str, allowed: tuple[str, ...], label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label} must be one of {allowed}, got {value!r}")


def _thread(cpath: Path, head_pointer: Path | None) -> tuple[str, int]:
    """Predecessor + depth threading — same shape as ``append_context``."""
    prev = read_head(head_pointer)
    if prev is None:
        return _GENESIS_ANCHOR, 0
    return prev, len(read_idr_chain(predecessor_id=prev, path=cpath))


def _append(
    decision: dict[str, Any],
    *,
    kind: str,
    fingerprint: str,
    path: Path | None,
    head_pointer: Path | None,
) -> PrecogIDR:
    cpath = chain_path(path)
    hpath = head_path(head_pointer)
    predecessor, depth = _thread(cpath, hpath)
    idr = build_idr(
        prompt="",
        fingerprint=fingerprint,
        decision=decision,
        predecessor_idr=predecessor,
        depth=depth,
        kind=kind,
        inputs={},
    )
    append_idr(idr, path=cpath)
    write_head(idr.id, hpath)
    return idr


def commit(
    claim: str,
    falsifier: str,
    *,
    deps: tuple[str, ...] | list[str] = (),
    intended_action: dict[str, Any] | None = None,
    severity: str = "reversible",
    goal_ref: str | None = None,
    path: Path | None = None,
    head_pointer: Path | None = None,
    bind_belief: bool = True,
    belief_chain_path: Path | None = None,
    belief_head_pointer: Path | None = None,
) -> PrecogIDR:
    """Pre-register one falsifiable commitment. CALL BEFORE THE ACTION RUNS —
    the signed append-before-act ordering is the entire point (temporal
    precedence; anti-HARKing). Optional fields are omitted when absent so an
    unadorned commitment's ``content_addr`` stays minimal and stable.

    ``bind_belief`` binds the commitment to the belief chain's head, closing the
    half of injection 2 that is closable: the two chains were parallel and
    unlinked, so each verified on its own while nothing proved that entry N of one
    corresponded to entry M of the other. See ``belief_binding`` for what this
    does NOT close — a tool result that never reached the belief chain is
    invisible to any digest of it.

    The absent case stays absent. With no belief chain at all there is nothing to
    bind and NO key is written, so the minimal ``content_addr`` above is
    unchanged. A belief chain that exists and cannot be READ records an explicit
    ``unbound`` status with a reason instead, because "there was nothing to bind"
    and "there was something and I could not reach it" are different facts and
    collapsing them is the silent-absence defect this binding exists to remove.
    """
    if not claim.strip() or not falsifier.strip():
        raise ValueError("claim and falsifier must be non-empty")
    _require(severity, SEVERITIES, "severity")
    decision: dict[str, Any] = {
        "claim": claim,
        "falsifier": falsifier,
        "deps": [str(d) for d in deps],
        "severity": severity,
    }
    if intended_action is not None:
        decision["intended_action"] = intended_action
    if goal_ref is not None:
        decision["goal_ref"] = goal_ref
    if bind_belief:
        belief = _belief_for_commit(belief_chain_path, belief_head_pointer)
        if belief is not None:
            decision["belief"] = belief
    fp = hashlib.sha256(
        f"{claim}\x00{falsifier}\x00{goal_ref or ''}".encode()
    ).hexdigest()
    return _append(
        decision, kind=COMMIT_KIND, fingerprint=fp, path=path, head_pointer=head_pointer
    )


def settle(
    commit_addr: str,
    verdict: str,
    *,
    axis: str,
    evidence: dict[str, Any] | str,
    verifier: str,
    action_taken: str = "none",
    path: Path | None = None,
    head_pointer: Path | None = None,
) -> PrecogIDR:
    """Settle one commitment with an exogenous verdict. Fail-closed on write:
    a verdict for a commitment absent from this chain raises — settlements
    must never dangle. (Reads stay fail-open; only authoring is strict.)
    """
    _require(verdict, VERDICTS, "verdict")
    _require(axis, AXES, "axis")
    _require(action_taken, ACTIONS_TAKEN, "action_taken")
    if not str(verifier).strip():
        raise ValueError("verifier must be non-empty")
    if isinstance(evidence, str) and not evidence.strip():
        raise ValueError("evidence must be non-empty")
    cpath = chain_path(path)
    known = {
        commit_addr_of(node)
        for node in read_idr_chain(path=cpath)
        if node.kind == COMMIT_KIND
    }
    if commit_addr not in known:
        raise ValueError(f"unknown commit_addr {commit_addr!r} — verdicts must not dangle")
    decision = {
        "commit_addr": commit_addr,
        "verdict": verdict,
        "axis": axis,
        "evidence": evidence,
        "verifier": verifier,
        "action_taken": action_taken,
    }
    fp = hashlib.sha256(f"{commit_addr}\x00{verdict}".encode()).hexdigest()
    return _append(
        decision, kind=VERDICT_KIND, fingerprint=fp, path=path, head_pointer=head_pointer
    )


def read_continuity_chain(
    head: str | None = None, path: Path | None = None
) -> list[PrecogIDR]:
    """All nodes in file order, or the backward walk from ``head``."""
    return read_idr_chain(predecessor_id=head, path=chain_path(path))


def verify_continuity_chain(path: Path | None = None) -> Verdict:
    """Tri-state integrity verdict over the whole chain (forest HMAC verify).
    An empty chain is vacuously VERIFIED (mirrors the context chain)."""
    chain = read_idr_chain(path=chain_path(path))
    if not chain:
        return Verdict.VERIFIED
    forest = build_chain_forest(chain, genesis_anchor=_GENESIS_ANCHOR)
    return verify_chain_integrity(forest)


def _commit_pairs(chain: list[PrecogIDR]) -> list[tuple[str, PrecogIDR]]:
    """(address, node) for the commitments in an ALREADY-READ chain."""
    return [(commit_addr_of(node), node) for node in chain if node.kind == COMMIT_KIND]


def _commits(path: Path | None) -> list[tuple[str, PrecogIDR]]:
    return _commit_pairs(read_idr_chain(path=chain_path(path)))


def unsettled(path: Path | None = None) -> list[PrecogIDR]:
    """Commitments with no settlement yet — the settlement worker's worklist."""
    chain = read_idr_chain(path=chain_path(path))
    settled = {
        node.decision.get("commit_addr")
        for node in chain
        if node.kind == VERDICT_KIND
    }
    return [
        node
        for node in chain
        if node.kind == COMMIT_KIND and content_addr(asdict(node)) not in settled
    ]


def belief_binding(
    chain_path_: "Path | None" = None, head_pointer: "Path | None" = None
) -> "dict | None":
    """The belief chain's head, CONTENT-addressed, or None when there is none.

    WHY THIS EXISTS. Injection 2 of the 2026-09-17 adversarial round measured a
    real commitment leaf and found its `inputs` empty and no context keys at all:
    a commitment records its claim, its falsifier and its declared dependencies,
    and carries no digest of the context that produced it. So a leaf written after
    a tool result was dropped was BYTE-IDENTICAL to one written with nothing
    dropped. Belief lives in a separate chain, so the two were parallel rather
    than linked -- each verifiable on its own, with nothing proving that entry N
    of one corresponds to entry M of the other.

    It records the head's CONTENT address, not merely its id. The id
    (`precog-<ts>-<rand>`) is a timestamp plus randomness and the HEAD file is a
    mutable pointer, so binding the id alone would be satisfied by editing that
    pointer. `content_addr` binds to what the node SAYS.

    WHAT THIS DOES NOT CLOSE, stated plainly because the remedy is narrower than
    "omission becomes detectable" suggests. It detects an omission FROM THE BELIEF
    CHAIN: if the dropped tool result was recorded there and then the commitment
    was written, the head differs and the binding no longer matches. It detects
    NOTHING about a result that never reached the belief chain at all -- that is
    invisible to any digest of a chain it is not in. One of the two cases, not
    both.
    """
    from lib.context_head import read_latest  # noqa: PLC0415 -- deferred, avoids a cycle

    node = read_latest(chain_path_, head_pointer)
    if node is None:
        return None
    return {
        "status": BELIEF_BOUND,
        "head_id": str(node.id),
        "head_addr": content_addr(asdict(node)),
        "head_depth": int(getattr(node, "depth", 0) or 0),
    }


def _belief_for_commit(
    chain_path_: "Path | None", head_pointer: "Path | None"
) -> "dict | None":
    """Fail-open, never silent. Mirrors `lib/rfc3161_anchor.py`'s three statuses.

    A commitment must never be lost because the belief chain is unreadable --
    refusing to record a decision because a sibling file is corrupt would be worse
    than recording it unbound. So a failure is caught AND named: an explicit
    `unbound` status with a reason, which a reader can tell apart from a
    commitment that simply had no belief chain to bind to.

    Genuine absence returns None and the caller omits the key entirely, which
    keeps an unadorned commitment's `content_addr` exactly as minimal and stable
    as `commit`'s docstring has always promised.
    """
    try:
        return belief_binding(chain_path_, head_pointer)
    except Exception as exc:  # noqa: BLE001 -- any failure is unavailability
        return {"status": BELIEF_UNBOUND,
                "reason": f"{type(exc).__name__}: {exc}"}


def verify_belief_binding(
    node: PrecogIDR, chain_path_: "Path | None" = None
) -> tuple[str, str]:
    """Re-check one commitment's belief binding. Returns (status, detail).

    `BELIEF_UNBOUND` is NOT a failure: most commitments predate this binding and
    every one of them is legitimately unbound. `BELIEF_BROKEN` is the finding --
    the commitment names a belief head whose content no longer hashes to what was
    recorded, which is the omission or rewrite the binding exists to surface.
    """
    from lib.context_chain import read_context_chain  # noqa: PLC0415

    belief = node.decision.get("belief") if isinstance(node.decision, dict) else None
    if not isinstance(belief, dict) or belief.get("status") != BELIEF_BOUND:
        reason = (belief or {}).get("reason", "") if isinstance(belief, dict) else ""
        return BELIEF_UNBOUND, reason or "no belief binding recorded"

    want_id, want_addr = belief.get("head_id", ""), belief.get("head_addr", "")
    if not want_id or not want_addr:
        return BELIEF_BROKEN, "binding is present but incomplete"
    try:
        chain = read_context_chain(head=want_id, path=chain_path_)
    except Exception as exc:  # noqa: BLE001
        return BELIEF_UNBOUND, f"belief chain unreadable: {type(exc).__name__}: {exc}"
    match = [n for n in chain if str(n.id) == want_id]
    if not match:
        return BELIEF_BROKEN, f"belief head {want_id} is not in the chain"
    got = content_addr(asdict(match[-1]))
    if got != want_addr:
        return BELIEF_BROKEN, (
            f"belief head {want_id} content changed "
            f"(recorded {want_addr[:16]}, now {got[:16]})"
        )
    return BELIEF_BOUND, want_addr


def settlement_of(commit_addr: str, path: Path | None = None) -> PrecogIDR | None:
    """The latest verdict recorded for one commitment, else None."""
    latest = None
    for node in read_idr_chain(path=chain_path(path)):
        if node.kind == VERDICT_KIND and node.decision.get("commit_addr") == commit_addr:
            latest = node
    return latest


def _dependents_index(pairs: list[tuple[str, PrecogIDR]]) -> dict[str, frozenset[str]]:
    """Reverse dependency edges: dep address -> commitments that list it in ``deps``."""
    index: dict[str, frozenset[str]] = {}
    for addr, node in pairs:
        deps = node.decision.get("deps", [])
        index.update({dep: index.get(dep, frozenset()) | {addr} for dep in deps})
    return index


def _closure(root: str, index: dict[str, frozenset[str]]) -> set[str]:
    """Transitive dependents of ``root`` over the reverse index (O(V + E))."""
    tainted: set[str] = set()
    frontier: set[str] = {root}
    while frontier:  # each address enters the frontier at most once
        nxt = frozenset().union(*map(index.get, frontier, [frozenset()] * len(frontier)))
        frontier = set(nxt) - tainted - {root}
        tainted |= frontier
    return tainted


def contamination(refuted_addr: str, path: Path | None = None) -> tuple[str, ...]:
    """Dependency-closure of one refuted commitment: every downstream
    commitment whose ``deps`` transitively include it (truth-maintenance /
    speculative-squash shape). Pure read; file order preserved.

    The refuted commitment ITSELF is deliberately not part of the returned
    closure — the return value answers "what else is now suspect?"; a caller
    wanting the full tainted set prepends ``refuted_addr``
    (``tests/test_continuity_chain.py::test_contamination_is_transitive_and_scoped``
    pins this contract)."""
    pairs = _commits(path)
    tainted = _closure(refuted_addr, _dependents_index(pairs))
    return tuple(addr for addr, _node in pairs if addr in tainted)


def contamination_roots(path: Path | None = None) -> dict[str, frozenset[str]]:
    """Refuted commit_addr -> its contamination closure, for EVERY INCONGRUENT
    verdict in the chain, computed from ONE chain read and ONE reverse-dependency
    index.

    Callers that want both the tainted set and the root that tainted each member
    (the pre-hoc watcher names the root in its warning) must not derive them
    separately: ``contamination()`` rebuilds the index from a fresh chain read on
    every call, so a per-root loop costs ``O(R x chain)`` in R, the number of
    refutations. This is that loop hoisted above the index build. Same closures,
    same order-independent contract — only the redundant reads are gone.

    Pure read; no memoisation, so a verdict appended after one call is visible to
    the next. Each closure excludes its own root, matching ``contamination()``.
    """
    chain = read_idr_chain(path=chain_path(path))
    refuted = {
        node.decision.get("commit_addr")
        for node in chain
        if node.kind == VERDICT_KIND and node.decision.get("verdict") == "INCONGRUENT"
    }
    if not refuted:
        return {}
    index = _dependents_index(_commit_pairs(chain))
    return {addr: frozenset(_closure(addr, index)) for addr in refuted}


def suspect(path: Path | None = None) -> frozenset[str]:
    """Every commitment that inherited a refuted premise: the union of
    ``contamination()`` over each INCONGRUENT verdict in the chain. Pure read,
    recomputed from the signed records on every call — the mark is about
    provenance, so a downstream commitment stays SUSPECT even if its own
    falsifier later settles CONGRUENT. Consumers: the settlement worker
    (persists the closure per refutation) and the pre-hoc watcher (warns when
    a SUSPECT commitment's intended tool is about to run)."""
    return frozenset().union(*contamination_roots(path=path).values())


# --- CLI ---------------------------------------------------------------------

def _paths(ns) -> dict:
    return {"path": Path(ns.chain) if ns.chain else None,
            "head_pointer": Path(ns.head) if ns.head else None}


def _cli_commit(ns) -> int:
    node = commit(
        ns.claim,
        ns.falsifier,
        deps=tuple(ns.dep or ()),
        intended_action=json.loads(ns.intended_action) if ns.intended_action else None,
        severity=ns.severity,
        goal_ref=ns.goal_ref,
        **_paths(ns),
    )
    print(json.dumps({"id": node.id, "content_addr": commit_addr_of(node)}))
    return 0


def _cli_settle(ns) -> int:
    try:
        evidence: dict | str = json.loads(ns.evidence)
    except (json.JSONDecodeError, TypeError):
        evidence = ns.evidence
    node = settle(
        ns.commit_addr,
        ns.verdict,
        axis=ns.axis,
        evidence=evidence,
        verifier=ns.verifier,
        action_taken=ns.action_taken,
        **_paths(ns),
    )
    print(json.dumps({"id": node.id, "content_addr": commit_addr_of(node)}))
    return 0


def _check_belief_bindings(cpath: "Path | None", belief_path: "Path | None") -> int:
    """Report commitments whose belief binding no longer holds. Returns the count.

    UNBOUND is not reported: most commitments predate the binding and every one of
    them is legitimately unbound. Only BROKEN is a finding.
    """
    broken = [
        (addr, detail)
        for addr, node in _commits(cpath)
        for status, detail in [verify_belief_binding(node, belief_path)]
        if status == BELIEF_BROKEN
    ]
    if broken:
        print(f"belief binding: {len(broken)} commitment(s) BROKEN", file=sys.stderr)
        for addr, detail in broken[:10]:
            print(f"  {addr[:16]}  {detail}", file=sys.stderr)
        if len(broken) > 10:
            print(f"  ... and {len(broken) - 10} more", file=sys.stderr)
    return len(broken)


def _cli_verify(ns) -> int:
    """Integrity verdict, plus the two facts integrity alone does not carry.

    Injection 5 of the 2026-09-17 adversarial round registered a commitment and
    never settled it, as after a crash. The chain returned `Verdict.VERIFIED` --
    correctly, because its integrity WAS intact -- and nothing in the output said
    a commitment was hanging. The rule already tracked "settlement coverage
    (unsettled backlog -> 0)" as a metric, so the gap was known and tracked, and
    tracked is not surfaced: a reader of the verdict could not see it.

    So the integrity verdict is printed unchanged (an unsettled commitment is NOT
    an integrity failure and must not be dressed as one), and the counts are
    printed beside it. The exit code stays keyed on integrity alone, deliberately:
    making a hanging commitment non-zero would turn every crash into a broken
    chain and train the reader to ignore the code.
    """
    cpath = _paths(ns)["path"]
    verdict = verify_continuity_chain(path=cpath)
    name = getattr(verdict, "name", str(verdict)).upper()
    print(name)

    pending = unsettled(path=cpath)
    total = len(_commits(cpath))
    if pending:
        print(f"unsettled: {len(pending)} of {total} commitment(s) have no "
              f"settlement -- integrity is unaffected, but these are unadjudicated",
              file=sys.stderr)
        for node in pending[:10]:
            claim = str(node.decision.get("claim", ""))[:72]
            print(f"  {commit_addr_of(node)[:16]}  {claim}", file=sys.stderr)
        if len(pending) > 10:
            print(f"  ... and {len(pending) - 10} more", file=sys.stderr)
    elif total:
        print(f"unsettled: 0 of {total} commitment(s)", file=sys.stderr)

    # The belief check needs the belief chain the commitments were written
    # AGAINST, which is a different file from the commitment chain. Passing
    # nothing means the DEFAULT belief chain, which is right when the commitment
    # chain is also the default and wrong the moment it is not: the binding points
    # into a chain this checker is not reading, so every intact binding reports
    # BROKEN. Reproduced 2026-09-17 before this guard existed -- an explicit
    # --chain with one valid binding printed "belief binding: 1 commitment(s)
    # BROKEN" against a record nobody had touched.
    #
    # A verifier that accuses an untouched record is worse than one that declines,
    # so an unknown pairing DECLINES and says so. Silence would be the same
    # absence-as-success defect in the other direction.
    belief_arg = getattr(ns, "belief_chain", None)
    if belief_arg:
        _check_belief_bindings(cpath, Path(belief_arg))
    elif ns.chain:
        print("belief binding: NOT CHECKED -- --chain was given without "
              "--belief-chain, so the belief chain these commitments were written "
              "against is unknown. Checking the default one would report every "
              "intact binding as BROKEN.", file=sys.stderr)
    else:
        _check_belief_bindings(cpath, None)

    return {"VERIFIED": 0, "DEGRADED": 2}.get(name, 1)


def _cli_unsettled(ns) -> int:
    for node in unsettled(path=_paths(ns)["path"]):
        print(json.dumps({"content_addr": commit_addr_of(node), "claim": node.decision.get("claim")}))
    return 0


def _cli_contamination(ns) -> int:
    for addr in contamination(ns.commit_addr, path=_paths(ns)["path"]):
        print(addr)
    return 0


def _cli_suspect(ns) -> int:
    for addr in sorted(suspect(path=_paths(ns)["path"])):
        print(addr)
    return 0


_CLI = {
    "commit": _cli_commit,
    "settle": _cli_settle,
    "verify": _cli_verify,
    "unsettled": _cli_unsettled,
    "contamination": _cli_contamination,
    "suspect": _cli_suspect,
}


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(prog="continuity_chain")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("commit", help="pre-register a falsifiable commitment")
    c.add_argument("--claim", required=True)
    c.add_argument("--falsifier", required=True)
    c.add_argument("--dep", action="append")
    c.add_argument("--severity", default="reversible", choices=SEVERITIES)
    c.add_argument("--intended-action", dest="intended_action")
    c.add_argument("--goal-ref", dest="goal_ref")

    s = sub.add_parser("settle", help="settle a commitment with a verdict")
    s.add_argument("--commit-addr", dest="commit_addr", required=True)
    s.add_argument("--verdict", required=True, choices=VERDICTS)
    s.add_argument("--axis", required=True, choices=AXES)
    s.add_argument("--evidence", required=True)
    s.add_argument("--verifier", required=True)
    s.add_argument("--action-taken", dest="action_taken", default="none", choices=ACTIONS_TAKEN)

    v = sub.add_parser("verify", help="verify chain integrity")
    # Without this the belief check had no way to be told WHICH belief chain a
    # non-default commitment chain was written against, so it fell back to the
    # default one and reported an intact binding as BROKEN. An unexpressible
    # parameter, not a wrong value.
    v.add_argument("--belief-chain", dest="belief_chain",
                   help="belief chain the commitments were written against "
                        "(required when --chain is given, or the belief check is skipped)")
    sub.add_parser("unsettled", help="list unsettled commitments")
    k = sub.add_parser("contamination", help="dependency closure of a refuted commitment")
    k.add_argument("--commit-addr", dest="commit_addr", required=True)
    sub.add_parser("suspect", help="every commitment built on a refuted premise")

    for sp in sub.choices.values():
        sp.add_argument("--chain", help="chain file override (tests)")
        sp.add_argument("--head", help="HEAD pointer override (tests)")
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    return _CLI[ns.cmd](ns)


if __name__ == "__main__":
    sys.exit(main())
