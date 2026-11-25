"""Freeze a signed ledger: a reproducible fingerprint of one era of records.

Why this exists. GRIP's signing key is compromised — 32 bytes, mode 644,
committed to git in plaintext. HMAC is symmetric, so anyone who can read the
repository can forge any record and the signature verifies.

Re-signing the 866 existing records under a fresh key is NOT the remedy. It
would produce records that look trustworthy and are not, and it would destroy
the single property the chain exists to have: that reasoning provably predates
outcomes. Rewriting the past to look better is precisely the HARKing the design
forbids.

The honest construction instead freezes the compromised era exactly as it
stands, fingerprints it, and has a genesis record under a new key commit to
that fingerprint. Records before genesis are then *attested by anchor*; records
after it are *signed*. Two honest categories, rather than one uniform green
tick that is false for 866 of them.

This module computes that fingerprint. It writes nothing and signs nothing —
freezing is a measurement, and a measurement that mutates its subject is not
one.

WHAT THE MERKLE ROOT DOES NOT BIND. `idr_forest.forest_merkle_root` is RFC6962
over each node's `content_addr`, and `content_addr` hashes the semantic body
while EXCLUDING the volatile fields `id`, `ts` and `audit`. So the root moves
when a decision changes and does NOT move when a record is merely renamed or
re-dated. That is right for linkage stability and wrong as a sole anchor: a
holder of the compromised secret could reissue the same decision under a new id
and timestamp without moving it. A genesis record therefore commits to BOTH
`merkle_root` and `sha256_records`, the latter being over the ledger bytes,
where identity and time do appear. Pinned by
`test_merkle_root_does_NOT_bind_id_or_timestamp`.

THE ORPHAN RULE. A record whose predecessor names something absent from the
ledger cannot join the forest, so it is excluded from the Merkle root and
REPORTED BY ID. It is never silently dropped: this module exists because
`_envelope_to_precog` silently dropped 852 of 866 records and the auditor's
tool reported VERIFIED over the 1.6% that survived.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from lib.idr_external_read import _envelope_to_precog, _load_envelopes
from lib.idr_forest import IdrForestError
from lib.idr_forest import add_idr, empty_forest, find_unanchored, forest_merkle_root

ADMISSIBLE_ANCHOR_PREFIXES = ("ci", "human", "council", "hypo")


@dataclass(frozen=True)
class LedgerFreeze:
    """One ledger's frozen fingerprint. Every field is independently checkable."""

    path: str
    records: int
    size_bytes: int
    sha256_file: str
    sha256_records: str
    merkle_root: str
    forest_nodes: int
    anchors: tuple[str, ...]
    anchored: int
    unanchored: int
    orphans: tuple[tuple[str, str], ...]
    duplicates: tuple[str, ...]
    true_roots: int
    fragment_roots: int

    @property
    def complete(self) -> bool:
        """True iff every record in the file reached the forest."""
        return (
            not self.orphans
            and not self.duplicates
            and self.forest_nodes == self.records
        )


def _digests(raw: bytes) -> tuple[str, str, int]:
    """(sha256 of the whole file, sha256 of its non-blank records, record count).

    The record digest is taken over the non-blank lines joined by newline, so it
    is stable against a trailing newline and against the file growing afterwards
    — an append-only ledger is still being written while it is being frozen.
    """
    lines = [l for l in raw.decode("utf-8", "replace").splitlines() if l.strip()]
    joined = "\n".join(lines).encode("utf-8")
    return (
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(joined).hexdigest(),
        len(lines),
    )


def _exogenous_anchors(idrs, known_ids: set[str]) -> tuple[str, ...]:
    """Predecessors that name an admissible exogenous anchor outside the ledger."""
    out = {
        i.predecessor_idr
        for i in idrs
        if i.predecessor_idr
        and i.predecessor_idr not in known_ids
        and i.predecessor_idr.split(":")[0] in ADMISSIBLE_ANCHOR_PREFIXES
    }
    return tuple(sorted(out))


def _insert_until_settled(forest, idrs):
    """Place every record whose ancestry is intact. Returns
    (forest, severed, duplicate_ids).

    `add_idr` requires a record's predecessor to exist, and a JSONL ledger is in
    write order, not topological order. Repeating to a fixed point places every
    record whose ancestry is intact, whatever the file order.

    TWO failures are separated, because conflating them produces a false report.
    A record whose id is ALREADY placed is a DUPLICATE — a repeat, not a severed
    record. An earlier version caught both with a bare `except Exception` and
    reported a duplicate as an orphan wanting predecessor `''`, which is simply
    untrue: it is not severed, and it has no missing parent. Only a genuinely
    absent predecessor makes a record severed.

    `IdrForestError` is caught specifically rather than `Exception`, so a future
    change in the forest's failure modes surfaces as a crash instead of silently
    reclassifying records as severed.
    """
    pending = list(idrs)
    duplicates: list[str] = []
    while True:
        deferred = []
        for idr in pending:
            if idr.id in forest.nodes:
                duplicates.append(idr.id)
                continue
            try:
                forest = add_idr(forest, idr)
            except IdrForestError:
                deferred.append(idr)
        if len(deferred) == len(pending):
            return forest, deferred, duplicates
        pending = deferred


def _chain_shape(idrs, known_ids: set[str]) -> tuple[int, int]:
    """(records that start a chain legitimately, records that start one by breakage).

    A TRUE root has no predecessor at all — the normal shape for 852 of GRIP's
    866 IDRs. A FRAGMENT root names a predecessor that is absent from this
    ledger, so the chain it belongs to is severed above it.

    The distinction is the whole reason this is reported separately from the
    Merkle root. GRIP's context ledger holds 20,272 records of which 20,220 link
    correctly to a parent — counting them all as "orphans" because the forest
    could not be rooted would be true of the forest and deeply misleading about
    the data. It is 52 fragments, not 20,272 loose records.
    """
    true_roots = sum(1 for i in idrs if not i.predecessor_idr)
    fragment_roots = sum(
        1 for i in idrs
        if i.predecessor_idr and i.predecessor_idr not in known_ids
    )
    return true_roots, fragment_roots


def freeze_ledger(path: str | Path) -> LedgerFreeze:
    """Fingerprint one ledger. Pure: reads the file, mutates nothing."""
    path = Path(path)
    raw = path.read_bytes()
    sha_file, sha_records, records = _digests(raw)

    idrs = [i for i in (_envelope_to_precog(e) for e in _load_envelopes(path)) if i]
    known = {i.id for i in idrs}
    anchors = _exogenous_anchors(idrs, known)
    true_roots, fragment_roots = _chain_shape(idrs, known)

    forest = empty_forest(anchors or ("ci:freeze-no-exogenous-anchor",))
    forest, orphaned, duplicates = _insert_until_settled(forest, idrs)
    unanchored = len(find_unanchored(forest))

    return LedgerFreeze(
        path=str(path),
        records=records,
        size_bytes=len(raw),
        sha256_file=sha_file,
        sha256_records=sha_records,
        merkle_root=forest_merkle_root(forest),
        forest_nodes=len(forest.nodes),
        anchors=anchors,
        anchored=len(forest.nodes) - unanchored,
        unanchored=unanchored,
        orphans=tuple(
            sorted((i.id, i.predecessor_idr or "") for i in orphaned)
        ),
        duplicates=tuple(sorted(duplicates)),
        true_roots=true_roots,
        fragment_roots=fragment_roots,
    )


def freeze_to_json(freeze: LedgerFreeze) -> str:
    """Canonical JSON for a freeze — the bytes a genesis record commits to."""
    return json.dumps(asdict(freeze), sort_keys=True, separators=(",", ":"))


def freeze_commitment(freeze: LedgerFreeze) -> str:
    """One hex digest over the WHOLE freeze report — counts, shape and all.

    SCHEMA-DEPENDENT BY CONSTRUCTION, and a genesis record must NOT carry this
    value alone. It digests `LedgerFreeze` as it is defined today, so adding a
    field moves it even though not one byte of the ledger changed — observed
    while building this module, when adding `duplicates` moved the live
    ledger's commitment from 9447949f to 67449568.

    That is the right behaviour for a report checksum (did this analysis change?)
    and the wrong anchor for a permanent record (did the DATA change?). A genesis
    record commits to `merkle_root`, which is RFC6962 over the records' own
    content-addresses and moves only when a record does, and to `sha256_records`,
    which is over the ledger bytes. Both are independent of anything defined here.
    """
    return hashlib.sha256(freeze_to_json(freeze).encode("utf-8")).hexdigest()
