"""Sign the clear decision and seal its outcome as ``hal/infinite`` records.

ADR-0016 Step 7, W1: each clear the cycle performs is a decision GRIP took on
the operator's behalf, so it is recorded as a signed leaf on GRIP's decision
chain, through the existing IDR signing (``lib/precog/idr.py``), like every
other decision record (``rules/cryptographic-causation.md``).

What a leaf may hold is an ALLOWLIST of record fields, never a denylist. A
cycle record carries only numbers, outcomes, threshold settings and sha256
digests of identifiers; nothing in it is transcript text, prompt text or a raw
session, thread or bridge id. The decision chain is published, so a field that
is not on the list below cannot reach it, however a later change grows the
record.

The demo seal is refused. ``GRIP_IDR_REQUIRE_DUAL=1`` is set for the length of
the signing, so on an interpreter that cannot dual-sign (Ed25519 + ML-DSA-65)
the leaf is not written at all, rather than written with the shared-key seal
whose own docstring calls it forgeable (``rules/never-system-python-for-grip.md``).
The preclear decision refuses the clear on signing failure. The later outcome
seal remains evidence and does not gate the cycle.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Mapping, Optional

_LIB = Path(__file__).resolve().parent
_REPO = _LIB.parent
for _path in (str(_REPO), str(_LIB)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

LEAF_KIND = "hal/infinite"
LEAF_EVENT = "w1-cycle"
DECISION_EVENT = "w1-clear-decision"
REQUIRE_DUAL_ENV = "GRIP_IDR_REQUIRE_DUAL"
#: The custody tier a gating record must carry (``lib/precog/idr.py`` CUSTODY_PRODUCT).
PRODUCT_CUSTODY = "dual-public-key"

#: The ONLY record fields a leaf carries. See the module docstring.
LEAF_FIELDS = (
    "v", "ts", "cycle_id", "outcome", "actuator", "session", "next_session",
    "thresholds", "idle_s", "dry_run", "verdict", "continuity", "reload",
    "remote_control", "checkpoint", "w4", "decision",
)
PRECLEAR_FIELDS = (
    "v", "ts", "cycle_id", "actuator", "session", "thresholds", "idle_s",
    "dry_run", "verdict", "continuity", "checkpoint", "w4",
)


def leaf_decision(record: Mapping) -> dict:
    """The allowlisted part of a cycle record, as the leaf's ``decision``."""
    decision = {key: record[key] for key in LEAF_FIELDS if key in record}
    decision["event"] = LEAF_EVENT
    return decision


def clear_decision(record: Mapping) -> dict:
    """Only facts known before the clear, marked as a decision to act."""
    decision = {key: record[key] for key in PRECLEAR_FIELDS if key in record}
    decision["event"] = DECISION_EVENT
    return decision


def fingerprint(decision: Mapping) -> str:
    """sha256 of the decision's canonical JSON: the leaf's content coordinate."""
    canonical = json.dumps(dict(decision), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextlib.contextmanager
def _demo_seal_refused() -> Iterator[None]:
    """Refuse the shared-key seal for the duration, then restore the environment."""
    before = os.environ.get(REQUIRE_DUAL_ENV)
    os.environ[REQUIRE_DUAL_ENV] = "1"
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(REQUIRE_DUAL_ENV, None)
        else:
            os.environ[REQUIRE_DUAL_ENV] = before


def _build_leaf(idr: object, decision: Mapping, record: Mapping,
                predecessor: Optional[str] = None):
    """One signed ``hal/infinite`` leaf for ``decision``, the demo seal refused.

    ``predecessor`` is the leaf this one follows from, when there is one: an
    outcome names the decision it carried out. A decision itself has none. The
    watcher is not part of any session's decision chain, so linking it to
    whatever record happened to be the chain's head would assert a causal edge
    that does not exist (``lib/idr_head.py`` says why that is worse than none).
    """
    with _demo_seal_refused():
        return idr.build_idr(
            prompt="", fingerprint=fingerprint(decision), decision=decision,
            predecessor_idr=predecessor, depth=1 if predecessor else 0, kind=LEAF_KIND,
            inputs={"record": str(record.get("v", ""))},
        )


def seal(record: Mapping, *, idr_path: Optional[Path] = None, idr: object = None) -> dict:
    """Append the cycle's outcome leaf to the decision chain. Never raises.

    Returns ``{ok, idr_id, content_addr, custody}`` on success and
    ``{ok: False, reason}`` otherwise. ``idr_path`` redirects the chain;
    ``None`` is GRIP's own chain under ``$CLAUDE_DIR``. ``idr`` is the IDR
    module to sign with; ``None`` means GRIP's own ``lib/precog/idr.py``. A
    test passes a stand-in, so it never imports the whole precog package. When
    the record carries a signed decision, the outcome leaf is its child.
    """
    try:
        if idr is None:
            import lib.precog.idr as idr
        decided = (record.get("decision") or {}).get("idr_id")
        leaf = _build_leaf(idr, leaf_decision(record), record, decided)
        idr.append_idr(leaf, path=idr_path)
    except Exception as exc:  # noqa: BLE001 - the outcome seal is evidence, never a gate
        return {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return {"ok": True, "idr_id": leaf.id, **_leaf_facts(idr, leaf)}


def _load_context():
    """GRIP's belief-chain module, loaded on first use.

    Its own function so that a test replaces THIS name. Swapping the entry in
    ``sys.modules`` is not enough: once any other test has imported the real
    module, ``import lib.context_chain`` finds it as an attribute of the ``lib``
    package and never looks at ``sys.modules`` again.
    """
    import lib.context_chain as context
    return context


def _record_belief(record: Mapping, address: str, context: object,
                   path: Optional[Path], head_pointer: Optional[Path]) -> dict:
    """Pair the decision leaf with a belief-chain node that cites it.

    GRIP's contract is that a decision and what was believed when it was taken
    are recorded together. The beliefs are the facts in the leaf itself; this
    node puts them on the belief chain by reference (``records_idr``), the way
    ``lib/prove_it_provenance.py`` pairs its own leaves.
    """
    try:
        if context is None:
            context = _load_context()
        node = context.checkpoint(
            next_step=None, title="w1 clear decision", tier="feedback",
            summary=f"w1 clear decision: cycle {record.get('cycle_id', '')}"[:600],
            records_idr=address, path=path, head_pointer=head_pointer)
    except Exception as exc:  # noqa: BLE001 - an unpaired decision does not gate a clear open
        return {"ok": False, "reason": f"context record not written: {type(exc).__name__}"}
    return {"ok": True, "memory_head": node.id}


def sign_clear(record: Mapping, *, idr_path: Optional[Path] = None, idr: object = None,
               context: object = None, context_path: Optional[Path] = None,
               head_pointer: Optional[Path] = None) -> dict:
    """Sign and record the decision to clear; any failure refuses the clear.

    Stricter than :func:`seal` on purpose: this record gates an action, so it
    must carry the product custody tier and a content address BEFORE it is
    appended, and it must then be paired with a belief-chain node. Anything
    less is a refusal. A leaf that was written is always named in the result
    (``idr_id``, ``content_addr``), even when the pairing failed, so the caller
    can tie the outcome it records to that leaf. The address is computed from
    the leaf in memory, before the append.
    """
    try:
        if idr is None:
            import lib.precog.idr as idr
        leaf = _build_leaf(idr, clear_decision(record), record)
        custody = idr.audit_custody_tier(leaf.audit)
        if custody != PRODUCT_CUSTODY:
            return {"ok": False, "reason": "dual signature unavailable"}
        address = idr.content_addr(asdict(leaf))
        if not address:
            return {"ok": False, "reason": "content address unavailable"}
        idr.append_idr(leaf, path=idr_path)
    except Exception as exc:  # noqa: BLE001 - no signed, written leaf means no clear
        return {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}
    written = {"idr_id": leaf.id, "content_addr": address, "custody": custody}
    return {**written, **_record_belief(record, address, context, context_path, head_pointer)}


def _leaf_facts(idr: object, leaf: object) -> dict:
    """The appended leaf's content address and custody tier, best-effort.

    The leaf is already on the chain when this runs, so a failure here must not
    turn a written seal into a reported failure; it is reported as unknown.
    """
    try:
        return {"content_addr": idr.content_addr(asdict(leaf)),
                "custody": idr.audit_custody_tier(leaf.audit)}
    except Exception as exc:  # noqa: BLE001
        return {"content_addr": None, "custody": None, "facts_error": type(exc).__name__}
