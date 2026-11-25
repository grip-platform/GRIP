#!/usr/bin/env python3
"""Seal a harness-ledger decision into the IDR forest and the memory chain.

Two keep-up loops record "harnessed" and "deferred" decisions in a plain
JSON ledger: ``lib/cc_feature_scanner.py`` for Claude Code releases
(``data/cc-releases/harness-state.json``) and ``lib/rea_auto_harness.py``
for the REA tool server (``data/rea-releases/harness-state.json``). A
ledger line on its own is a claim: an overwrite or a hand edit is invisible
afterwards, and the decision is tied to nothing that produced it.
``rules/cryptographic-causation.md`` asks for every production decision to
be recorded as a signed IDR leaf plus a memory-context record, always. This
module is the one place both ledgers call after their write (GRIP#7969; the
pattern reached N=2, so one helper serves both).

What a seal carries. The leaf's decision names the ledger, the ledger key,
the version, the feature title, the decision (``harnessed`` or ``deferred``),
the operator's ``how`` or ``reason`` after the redaction shield, and the
sha256 of the canonical JSON of the ledger record, so a reader with the
ledger can check the line against the leaf. The memory-context record cites
the leaf by content address (``records_idr``), the cross-reference every
other GRIP writer uses. Both are read back from disk and checked before the
call returns. A seal that cannot be read back raises ``HarnessSealError``
after the ledger write, so a decision is never silently left unsealed.

Public text. The decision-chain site renders the IDR forest, so the
``how`` or ``reason``, which is operator prose, passes through the shield's
public profile before it is signed. A feature title and the ledger key are
upstream changelog text and are carried as written. When the shield cannot
run (no denylist on this machine) or reports residue, the prose is withheld
from the leaf and the leaf says so; the ledger keeps the original.

Custody. ``build_idr`` signs with the strongest reachable seal and falls
back to a labelled demo seal while announcing the downgrade. This module
adds no weaker path. Run it under the GRIP venv (``bin/grip-py``): the
``__main__`` block re-executes into a capable interpreter first
(``rules/never-system-python-for-grip.md``).

Back-fill. Entries written before this module existed have no belief state
to pair with, so they get no leaf of their own. ``snapshot_ledger`` writes
one leaf for the ledger as found, hashing the whole file, and ``audit_ledger``
treats entries dated on or before that snapshot as covered by it.

Usage:
    bin/grip-py lib/harness_ledger_seal.py audit <cc|rea>      # keys with no leaf; exit 1 if any
    bin/grip-py lib/harness_ledger_seal.py snapshot <cc|rea>   # one leaf for the ledger as found
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# The sibling modules come from THIS checkout, never from the shared tree: the
# same guard lib/rea_auto_harness.py (repo root) and lib/cc_feature_scanner.py
# (lib/) carry, so this module imports the same way whether it is loaded as
# lib.harness_ledger_seal or by its bare name.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "lib")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from lib.context_head import _session_path  # noqa: E402

KIND_DECISION = "harness-ledger-decision"
KIND_SNAPSHOT = "harness-ledger-snapshot"

#: The two ledgers, by the short name their loops use, relative to CLAUDE_DIR.
LEDGERS = {
    "cc": Path("data") / "cc-releases" / "harness-state.json",
    "rea": Path("data") / "rea-releases" / "harness-state.json",
}

_SHIELD_CACHE: dict[str, Callable[[str], str]] = {}


class HarnessSealError(RuntimeError):
    """The leaf or the record was written but could not be read back intact."""


# -- small pure helpers -------------------------------------------------------

def canonical_record_hash(record: dict) -> str:
    """``sha256:<hex>`` of the ledger record's canonical JSON (sorted keys)."""
    body = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))


def _default_context_path() -> Path:
    """The session's context chain, resolved at CALL time from CLAUDE_DIR.

    ``lib.context_chain`` freezes its default path at import, so a test or a
    worktree that sets ``CLAUDE_DIR`` after import would still write the
    original ledger. Resolving here keeps the seal inside the live tree.
    """
    return _session_path(_claude_dir() / "state" / "precog" / "context.jsonl")


def _default_head_pointer() -> Path:
    return _session_path(_claude_dir() / "state" / "precog" / "context-HEAD.txt")


def ledger_path(ledger: str) -> Path:
    """Where the named ledger lives under the live CLAUDE_DIR."""
    _check_ledger(ledger)
    return _claude_dir() / LEDGERS[ledger]


def _check_ledger(ledger: str) -> None:
    if ledger not in LEDGERS:
        raise ValueError(f"unknown ledger {ledger!r}; one of {sorted(LEDGERS)}")


def _public_redactor() -> Callable[[str], str]:
    """The shield's public profile as a text-to-text callable, built once."""
    if "redactor" not in _SHIELD_CACHE:
        from lib.redaction_shield import PROFILE_PUBLIC, Shield, load_denylist

        shield = Shield(load_denylist(), PROFILE_PUBLIC)
        _SHIELD_CACHE["redactor"] = lambda text: shield.safe(text).text
    return _SHIELD_CACHE["redactor"]


def _public(text: str, redactor: Callable[[str], str] | None = None) -> tuple:
    """``(public text, None)``, or ``(None, why)`` when the text is withheld.

    Any failure of the shield withholds the prose rather than passing it on:
    a leaf with less text is still a true record, a leaf that leaks is not.
    """
    if not text:
        return "", None
    try:
        fn = redactor or _public_redactor()
        return fn(text), None
    except Exception as exc:  # noqa: BLE001 -- withhold on any shield failure, never leak
        return None, f"withheld: {type(exc).__name__}"


def _read_back(node_id: str, path: Path):
    """The stored line for ``node_id``, newest first, as a PrecogIDR, or None."""
    from lib.precog.idr import PrecogIDR

    if not path.exists():
        return None
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if node_id not in line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if raw.get("id") == node_id:
            fields = PrecogIDR.__dataclass_fields__
            return PrecogIDR(**{k: raw[k] for k in fields if k in raw})
    return None


# -- the two writes, each read back before it counts -------------------------

def _append_leaf(kind: str, fingerprint: str, decision: dict, inputs: dict,
                 idr_path: Path | None) -> tuple:
    """Sign and append one leaf, then read it back and verify its signature."""
    from lib.idr_forest import Verdict, _verify_node
    from lib.precog import idr as _idr

    leaf = _idr.build_idr(prompt="", fingerprint=fingerprint, decision=decision,
                          predecessor_idr=None, depth=0, kind=kind, inputs=inputs)
    _idr.append_idr(leaf, path=idr_path)
    addr = _idr.content_addr(asdict(leaf))
    target = idr_path or _idr._default_idr_path()
    stored = _read_back(leaf.id, target)
    if stored is None or _idr.content_addr(asdict(stored)) != addr:
        raise HarnessSealError(f"IDR leaf {leaf.id} did not read back from {target}")
    if _verify_node(stored) == Verdict.BROKEN:
        raise HarnessSealError(f"IDR leaf {leaf.id} read back with a broken signature")
    return leaf, addr, _idr.audit_custody_tier(leaf.audit)


def _append_record(summary: str, title: str, addr: str, context_path: Path | None,
                   head_pointer: Path | None):
    """Write the memory-context record citing ``addr``, then read it back."""
    from lib import context_chain as _cc

    cpath = context_path or _default_context_path()
    pointer = head_pointer or _default_head_pointer()
    node = _cc.checkpoint(next_step=None, summary=summary[:600], title=title,
                          tier="feedback", records_idr=addr, path=cpath,
                          head_pointer=pointer)
    stored = _read_back(node.id, cpath)
    if stored is None or stored.decision.get("records_idr") != addr:
        raise HarnessSealError(f"memory-context record {node.id} did not read back citing {addr}")
    return node


# -- public API ---------------------------------------------------------------

def _decision_body(ledger: str, key: str, record: dict,
                   redactor: Callable[[str], str] | None) -> dict:
    deferred = record.get("status") == "deferred"
    prose_src = record.get("reason") if deferred else record.get("how")
    prose, withheld = _public(str(prose_src or ""), redactor)
    return {
        "ledger": ledger,
        "key": key,
        "version": record.get("version"),
        "title": record.get("title"),
        "decision": "deferred" if deferred else "harnessed",
        "prose": prose,
        "prose_withheld": withheld,
        "record_sha256": canonical_record_hash(record),
        "date": record.get("date"),
    }


def seal_harness_decision(ledger: str, key: str, record: dict, *,
                          idr_path: Path | None = None,
                          context_path: Path | None = None,
                          head_pointer: Path | None = None,
                          redactor: Callable[[str], str] | None = None) -> dict:
    """Sign one ledger decision into the IDR forest and the memory chain.

    Called by both ledgers right after their own write. Returns the leaf id,
    its content address, the custody tier the signer reached, the memory
    node id, the record hash, and whether the prose was withheld. Raises
    ``HarnessSealError`` if either write cannot be read back intact.
    """
    _check_ledger(ledger)
    decision = _decision_body(ledger, key, record, redactor)
    key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    leaf, addr, custody = _append_leaf(
        KIND_DECISION, f"harness-ledger:{ledger}:{key_digest}", decision,
        {"ledger": ledger, "key": key}, idr_path,
    )
    summary = (f"harness ledger {ledger}: {decision['decision']} "
               f"{decision['version']} {decision['title']}")
    node = _append_record(summary, "harness ledger decision", addr, context_path, head_pointer)
    return {
        "ledger": ledger,
        "key": key,
        "decision": decision["decision"],
        "idr_id": leaf.id,
        "idr_addr": addr,
        "custody": custody,
        "memory_head": node.id,
        "record_sha256": decision["record_sha256"],
        "prose_withheld": decision["prose_withheld"],
    }


def snapshot_ledger(ledger: str, path: Path | None = None, *,
                    idr_path: Path | None = None,
                    context_path: Path | None = None,
                    head_pointer: Path | None = None) -> dict:
    """One leaf for the ledger file as found: its hash, size and entry counts.

    The honest back-fill shape: entries written before sealing existed get
    no fabricated decision each, they get one leaf that pins the bytes they
    were in on the day the snapshot was taken.
    """
    target = Path(path) if path is not None else ledger_path(ledger)
    data = target.read_bytes()
    entries = json.loads(data.decode("utf-8")).get("harnessed", {})
    deferred = sum(1 for e in entries.values() if e.get("status") == "deferred")
    taken_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    decision = {
        "ledger": ledger,
        "snapshot_of": target.name,
        "file_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "entries": len(entries),
        "deferred": deferred,
        "taken_at": taken_at,
        "note": "ledger as found; entries dated on or before this had no leaf of their own",
    }
    leaf, addr, custody = _append_leaf(
        KIND_SNAPSHOT, f"harness-ledger-snapshot:{ledger}", decision,
        {"ledger": ledger}, idr_path,
    )
    summary = f"harness ledger {ledger}: snapshot of {len(entries)} entries ({decision['file_sha256'][:23]})"
    node = _append_record(summary, "harness ledger snapshot", addr, context_path, head_pointer)
    return {"ledger": ledger, "idr_id": leaf.id, "idr_addr": addr, "custody": custody,
            "memory_head": node.id, "entries": len(entries),
            "file_sha256": decision["file_sha256"], "taken_at": taken_at}


def audit_ledger(ledger: str, path: Path | None = None, *,
                 idr_path: Path | None = None) -> dict:
    """Which ledger keys have no leaf and are not covered by a later snapshot.

    This is the falsification check GRIP#7969 names: after sealing, every
    ``mark`` or ``defer`` must leave a key that appears in the IDR ledger.
    """
    from lib.precog import idr as _idr

    target = Path(path) if path is not None else ledger_path(ledger)
    entries = json.loads(target.read_text(encoding="utf-8")).get("harnessed", {})
    sealed: set[str] = set()
    snapshot_dates: list[str] = []
    for leaf in _idr.read_idr_chain(path=idr_path):
        body = leaf.decision if isinstance(leaf.decision, dict) else {}
        if body.get("ledger") != ledger:
            continue
        if leaf.kind == KIND_DECISION and body.get("key"):
            sealed.add(body["key"])
        elif leaf.kind == KIND_SNAPSHOT and body.get("taken_at"):
            snapshot_dates.append(body["taken_at"][:10])
    last_snapshot = max(snapshot_dates) if snapshot_dates else None
    unsealed = sorted(
        key for key, entry in entries.items()
        if key not in sealed
        and (last_snapshot is None or str(entry.get("date", "")) > last_snapshot)
    )
    return {
        "ledger": ledger,
        "entries": len(entries),
        "sealed": len(sealed & set(entries)),
        "snapshots": len(snapshot_dates),
        "last_snapshot": last_snapshot,
        "unsealed": unsealed,
    }


def main(argv: list[str]) -> int:
    """CLI: ``audit <cc|rea>`` (exit 1 when keys are unsealed) or ``snapshot <cc|rea>``."""
    usage = "Usage: harness_ledger_seal.py <audit|snapshot> <cc|rea>"
    if len(argv) < 3 or argv[1] not in ("audit", "snapshot") or argv[2] not in LEDGERS:
        print(usage, file=sys.stderr)
        return 2
    if argv[1] == "snapshot":
        print(json.dumps(snapshot_ledger(argv[2]), indent=2))
        return 0
    report: dict[str, Any] = audit_ledger(argv[2])
    print(json.dumps(report, indent=2))
    return 1 if report["unsealed"] else 0


if __name__ == "__main__":
    # Imported as top-level `pq_reexec` off THIS checkout (lib/ is on sys.path
    # above), so a system interpreter re-executes into one that can dual-sign
    # before any record is written.
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(Path(__file__))
    sys.exit(main(sys.argv))
