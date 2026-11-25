"""External IDR ledger loader — P4 of #2799.

Reads a HAPPI-compatible IDR JSONL file from outside GRIP's own
~/.claude/ boundary (e.g. ~/.hal/, a relay peer, or any HAPPI/1.x
audit trace) into an ``idr_forest.Forest`` for replay + canonicalise
verification.

The loader is schema-bridging: it accepts any JSONL line whose
``v`` field starts with ``happi/1.`` (HAPPI/1.x protocol), maps
it to a ``PrecogIDR``, and adds it to a Forest rooted at an
exogenous anchor. Lines that do not carry an IDR payload (e.g.
heartbeat or diagnostic events) are skipped. A record qualifies on a
string ``id``, ``fingerprint`` and ``ts``, an int ``depth`` and a dict
``decision``. Its ``predecessor_idr`` may be a string OR ``None`` —
``None`` is the NORMAL value for a root record, and 852 of the 866
records in GRIP's own production ledger are roots.

The skipping is SILENT, and that is a known weakness rather than a
feature: a caller cannot tell "this line was not a record" from "this
record was rejected". That silence is what allowed a stricter
predecessor rule to discard 98.4% of the ledger while the auditor's
tool reported a clean verdict over the 1.6% that survived. Counting
and surfacing the skips is tracked separately — this docstring
previously asserted the discarding behaviour as if it were the
contract, which would have justified reverting the fix.

It also carries the ZERO-TRUST verifier an outside auditor needs
(``verify_idr_external`` / ``verify_forest_external``): the in-house
chain verifier resolves its verifying keys from GRIP's own
``state/audit/keys/`` directory, which is worthless to a third party
who does not trust GRIP. These take the auditor's own copy of the
published keys instead and read no GRIP state at all.

Pure (no I/O beyond the caller-provided file path). Stateless.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from lib.audit_chain import Verdict
from lib.idr_forest import Forest, add_idr, empty_forest
from lib.precog.idr import PrecogIDR


def _is_happi_envelope(env: dict) -> bool:
    """True iff ``env`` declares a HAPPI/1.x protocol version.

    Two spellings exist in the wild and BOTH must be accepted:

    * ``v: "happi/1.3"`` — a relay/HAL envelope, prefixed.
    * ``happi: "1.3"``   — a record GRIP wrote itself. ``precog.idr.append_idr``
      serialises the ``PrecogIDR`` dataclass with ``asdict``, so the key is the
      field name ``happi`` and the value is the BARE ``HAPPI_VERSION`` constant
      ("1.3"), with no ``happi/`` prefix.

    Accepting only prefixed ``v`` made this loader silently return an EMPTY
    forest for every record in GRIP's own ``state/precog/idr.jsonl`` — no error,
    no warning, just nothing, which then reads as a clean verify over no records
    at all. Measured before the fix: 1 record written, 0 loaded.

    Still a real check, not a wildcard: the value must name a HAPPI 1.x version
    in one of those two forms. Arbitrary JSON is still skipped.
    """
    prefixed = env.get("v")
    if isinstance(prefixed, str) and prefixed.startswith("happi/1."):
        return True
    bare = env.get("happi")
    return isinstance(bare, str) and (
        bare.startswith("happi/1.") or bare.startswith("1.")
    )


def _iter_envelopes(lines) -> Iterator[dict]:
    """Yield each line that parses as JSON AND declares a HAPPI/1.x version.

    Blank lines, non-JSON, and non-HAPPI JSON are skipped. Shared by the file
    and in-memory loaders so the two can never disagree about what counts.
    """
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            env = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(env, dict) or not _is_happi_envelope(env):
            continue
        yield env


def _load_envelopes(path: Path) -> Iterator[dict]:
    """Yield every JSON line from ``path`` that parses as a HAPPI/1.x envelope."""
    with open(path, encoding="utf-8") as fh:
        yield from _iter_envelopes(fh)


def _envelope_to_precog(env: dict) -> PrecogIDR | None:
    """Map a HAPPI envelope dict to a PrecogIDR.

    Returns ``None`` if the envelope does not carry an IDR payload (a missing
    or non-string ``id``, ``fingerprint`` or ``ts``; a non-int ``depth``; a
    non-dict ``decision``).

    ``predecessor_idr`` is ``str | None`` on the dataclass and ``None`` is the
    NORMAL value for a root record — the first entry of a chain has nothing
    before it. An earlier version of this function required it to be a string,
    which silently dropped every root: 852 of the 866 records in GRIP's own
    production ledger, all at depth 0, every one for that single reason. The
    auditor's tool loaded 1.6% of the ledger and said nothing about the rest.
    """
    id_ = env.get("id")
    predecessor = env.get("predecessor_idr")
    depth = env.get("depth")
    fingerprint = env.get("fingerprint")
    decision = env.get("decision")
    inputs = env.get("inputs", {})
    ts = env.get("ts")
    audit = env.get("audit", {})
    anatomy = env.get("decision_anatomy")

    if not all(isinstance(x, str) for x in (id_, fingerprint, ts)):
        return None
    # A root record legitimately has no predecessor. Only a WRONGLY-TYPED
    # predecessor disqualifies the envelope; absence never does.
    if predecessor is not None and not isinstance(predecessor, str):
        return None
    if not isinstance(depth, int):
        return None
    if not isinstance(decision, dict):
        return None

    return PrecogIDR(
        # Preserve the version string EXACTLY as written. It is part of the
        # signed body, so normalising "1.3" -> "happi/1.3" here would change the
        # hashed bytes and turn every genuine record BROKEN. ``happi`` is
        # preferred because it is the dataclass field the signature covers.
        happi=str(env.get("happi") or env.get("v") or "happi/1.3"),
        kind=str(env.get("kind", "external-idr")),
        id=id_,
        predecessor_idr=predecessor,
        depth=depth,
        fingerprint=fingerprint,
        ts=ts,
        decision=decision,
        inputs=inputs if isinstance(inputs, dict) else {},
        audit=audit if isinstance(audit, dict) else {},
        decision_anatomy=anatomy if isinstance(anatomy, dict) else None,
    )


def load_external_ledger(
    path: str | Path,
    *,
    anchor: str = "ci:external-read-p4",
) -> Forest:
    """Load an external HAPPI IDR JSONL file into a canonical Forest.

    Args:
        path: Path to a JSONL file of HAPPI/1.x envelopes.
        anchor: Exogenous anchor id for the Forest root. Defaults to
            ``"ci:external-read-p4"`` — a CI-traceable anchor that
            passes the ``EXOGENOUS_ANCHOR_PATTERNS`` gate.

    Returns:
        A ``Forest`` containing every IDR from the file that maps
        to a valid ``PrecogIDR``, rooted at ``anchor``.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ``IdrForestError``: If the anchor is inadmissible or a node
            violates forest constraints (duplicate id, missing
            predecessor).

    Any envelope that does not carry an IDR payload (non-HAPPI lines,
    non-IDR events like heartbeats, malformed records) is silently
    skipped.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"external ledger not found: {p}")

    forest = empty_forest((anchor,))
    for env in _load_envelopes(p):
        idr = _envelope_to_precog(env)
        if idr is None:
            continue
        forest = add_idr(forest, idr)
    return forest


def load_external_ledger_stream(
    lines: list[str],
    *,
    anchor: str = "ci:external-read-p4",
) -> Forest:
    """Load a Forest from an in-memory list of JSON lines.

    Pure helper for hermetic tests — avoids filesystem I/O.
    Signature matches ``load_external_ledger`` otherwise.
    """
    forest = empty_forest((anchor,))
    for env in _iter_envelopes(lines):
        idr = _envelope_to_precog(env)
        if idr is None:
            continue
        forest = add_idr(forest, idr)
    return forest


# ---------------------------------------------------------------------------
# External-auditor verification (the zero-trust half)
# ---------------------------------------------------------------------------
#
# Loading a ledger is only half of what an outsider needs. The in-house verifier
# (``idr_forest.verify_chain_integrity`` -> ``_verify_node`` -> ``dual_verify``)
# resolves its verifying keys from GRIP's OWN ``state/audit/keys/<tenant>/``
# directory, located via the ``tenant_id`` written into the record's audit block.
# That is correct for GRIP checking itself and useless for a third party: an
# auditor who does not trust GRIP cannot accept keys GRIP hands them at verify
# time, because whoever can forge the record can also swap the key beside it.
#
# These functions close that: they take the auditor's OWN copy of the published
# verifying keys and never touch GRIP state. The record, the two keys, and this
# code are the whole trust base.


def verify_idr_external(idr: PrecogIDR, ed_pub_raw: bytes, mldsa_pub_raw: bytes):
    """Verify ONE record against externally-supplied raw public keys.

    Args:
        idr: the record to check.
        ed_pub_raw / mldsa_pub_raw: raw public-key bytes the AUDITOR holds, from
            their own copy of the published keys (see
            ``lib.pq_dual_sign.public_key_raw_from_pem`` to get raw bytes out of
            a published PEM file). Never read from GRIP's key directory.

    Returns a ``Verdict``, fail-closed and tri-state:

    * ``VERIFIED`` — the record is dual-scheme and BOTH the Ed25519 and the
      ML-DSA-65 signature verify under the supplied keys, whose fingerprints
      bind the ones the record declares.
    * ``BROKEN`` — dual-scheme but either limb fails: bad signature, tampered
      body, absent limb, or a fingerprint that does not bind (key substitution).
    * ``DEGRADED`` — not checkable by an outsider at all. Either the record is
      not dual-signed (an HMAC record needs a shared secret an auditor must
      never be given, so it is UNVERIFIABLE here — never a pass), or the verify
      itself errored.

    DEGRADED is never a quiet VERIFIED. "I could not check this" and "I checked
    this and it is sound" are different answers and stay different answers.
    """
    from lib.idr_forest import signed_entry_hash
    from lib.pq_dual_sign import dual_verify_external
    try:
        message = signed_entry_hash(idr).encode()
        return dual_verify_external(message, idr.audit, ed_pub_raw, mldsa_pub_raw)
    except Exception:  # noqa: BLE001 — a verifier reports, it never crashes the scan
        return Verdict.DEGRADED


def verify_forest_external(forest: Forest, ed_pub_raw: bytes, mldsa_pub_raw: bytes):
    """Verify every record in ``forest`` against externally-supplied raw keys.

    Folds with the same precedence the in-house verifier uses
    (``idr_forest.verify_chain_integrity``): one ``BROKEN`` makes the whole
    result ``BROKEN``; any ``DEGRADED`` holds the result at ``DEGRADED``; only
    an all-``VERIFIED`` forest returns ``VERIFIED``.

    ONE deliberate difference: an EMPTY forest is ``DEGRADED``, not ``VERIFIED``.
    The in-house verifier is vacuously true on an empty forest, which is fine
    when GRIP checks its own live chain. Here the auditor supplies the file, so
    an empty or entirely-unparseable ledger would otherwise print a clean pass
    over nothing at all — the exact silent-VERIFIED this whole path exists to
    prevent. Verifying nothing is not verification.
    """
    verdicts = [
        verify_idr_external(node.idr, ed_pub_raw, mldsa_pub_raw)
        for node in forest.nodes.values()
    ]
    if not verdicts:
        return Verdict.DEGRADED
    if Verdict.BROKEN in verdicts:
        return Verdict.BROKEN
    if Verdict.DEGRADED in verdicts:
        return Verdict.DEGRADED
    return Verdict.VERIFIED
