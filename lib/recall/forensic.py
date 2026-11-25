"""RED chain — signed forensic witness records of proven-incorrect assertions.

Append-only ``<store_dir>/forensic.jsonl``. A node is sealed (signed +
appended) ONLY after the libel guard re-runs the contradiction fresh and it
survives; a failed falsifier returns the node UNSEALED and appends nothing.

Signing key: the SAME mechanism as ``lib/precog/idr.py`` -> ``lib.audit_chain.
_ensure_signing_key`` — read-or-create 32 urandom bytes at
``<claude_dir>/state/audit/audit-signing-key`` (mode 0600). The derivation is
REPLICATED here rather than imported because importing ``lib.audit_chain``
executes module-level side effects (``lib.state_manager`` instantiation +
a ``cryptography`` import), which the stdlib-portable recall substrate must
not depend on. Same file, same bytes, same HMAC-SHA256.

Raw identity is NEVER stored — only ``forensic_uuid`` (HMAC over the identity
tuple, ``schema.forensic_uuid``) plus the field-presence ``identity_mask``.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

try:
    from lib.claude_dir import get_claude_dir
except ImportError:  # dual-root lib/ resolution
    from claude_dir import get_claude_dir  # type: ignore

import hmac as _hmac
import hashlib

from lib.recall import index as _index
from lib.recall import preassert as _preassert
from lib.recall.schema import (
    CAUGHT_OUTPUT_GATE,
    ForensicNode,
    PUBLISH_DIM_KEYS,
    SEVERITY_PRE_OUTPUT,
    VERDICT_DENY,
    canonical_json,
    content_hash,
    forensic_uuid,
)


def _default_key_path() -> Path:
    return get_claude_dir() / "state" / "audit" / "audit-signing-key"


def signing_key(key_path=None) -> bytes:
    """Load or generate the HMAC signing key (replicates audit_chain)."""
    path = Path(key_path) if key_path else _default_key_path()
    if path.exists():
        return path.read_bytes()
    key = os.urandom(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(str(path), 0o600)
    return key


def _chain_path(store) -> Path:
    return _index.resolve_store(store) / "forensic.jsonl"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_nodes(store) -> list[dict]:
    path = _chain_path(store)
    if not path.exists():
        return []
    nodes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            nodes.append(json.loads(line))
        except json.JSONDecodeError:
            nodes.append({"_unparseable": line})
    return nodes


def build_node(claim: str, identity: dict | None, *, evidence=(), claim_source="assistant_output",
               severity=SEVERITY_PRE_OUTPUT, where_caught=CAUGHT_OUTPUT_GATE,
               belief_head: str = "", toolchain_trace=(), falsifier: str = "",
               publish_dims: dict | None = None,
               store=None, key_path=None) -> ForensicNode:
    """Draft (unsealed) node. Identity is HMAC'd immediately; raw values are
    never retained on the node — only the uuid + presence mask.

    ``publish_dims`` is filtered fail-closed to PUBLISH_DIM_KEYS: any other
    key (session, user, ip, …) is silently DROPPED here so machine-local
    identity can never ride into the plaintext aggregation dims by caller
    mistake (council seal 970973d5)."""
    ident = dict(identity or {})
    key = signing_key(key_path)
    mask = tuple(sorted(k for k, v in ident.items() if v not in (None, "")))
    safe_dims = {k: str(v) for k, v in (publish_dims or {}).items()
                 if k in PUBLISH_DIM_KEYS and v not in (None, "")}
    fresh = _index.store_freshness(store)
    return ForensicNode(
        publish_dims=safe_dims,
        forensic_uuid=forensic_uuid(ident, key),
        ts=_now(),
        claim_verbatim=claim,
        claim_source=claim_source,
        evidence=tuple(evidence),
        belief_head=belief_head,
        toolchain_trace=tuple(toolchain_trace),
        verifier={"index_head": fresh.index_head, "event_count": fresh.event_count},
        severity=severity,
        where_caught=where_caught,
        falsifier=falsifier or "the contradicting evidence no longer verifies on disk",
        falsifier_survived=False,
        identity_mask=mask,
    )


def _falsifier_check(node: ForensicNode, store) -> tuple[bool, str, tuple]:
    """The libel guard: re-run preassert fresh; the accusation stands only if
    the moat STILL contradicts the claim with evidence that verifies now."""
    fresh = _preassert.preassert(node.claim_verbatim, store=store)
    if fresh.verdict != VERDICT_DENY or not fresh.evidence:
        return False, f"fresh re-check returned {fresh.verdict}; accusation not sealed", ()
    return True, "fresh preassert re-check DENIED the claim with verifying evidence", tuple(
        {"kind": e.get("type", "record"), "path": e.get("path", ""),
         "hash": e.get("blob_hash", ""), "idr_id": e.get("idr_id")}
        for e in fresh.evidence
    )


def emit(node: ForensicNode, store=None, key_path=None) -> ForensicNode:
    """Falsifier-check first; seal (sign + append) only on survival."""
    survived, detail, evidence = _falsifier_check(node, store)
    if not survived:
        return dataclasses.replace(node, falsifier_survived=False, falsifier_detail=detail)
    nodes = _read_nodes(store)
    prev_hash = content_hash(_strip_sig(nodes[-1])) if nodes else ""
    sealed = dataclasses.replace(
        node, falsifier_survived=True, falsifier_detail=detail,
        evidence=evidence or node.evidence, prev_hash=prev_hash,
    )
    key = signing_key(key_path)
    sig = _hmac.new(key, canonical_json(sealed.signable()).encode("utf-8"),
                    hashlib.sha256).hexdigest()
    sealed = dataclasses.replace(sealed, sig=sig)
    path = _chain_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(canonical_json(sealed.to_dict()) + "\n")
    return sealed


def _strip_sig(node_dict: dict) -> dict:
    d = dict(node_dict)
    d.pop("sig", None)
    return d


def _check_node(i: int, node: dict, prev: dict | None, key: bytes) -> list[dict]:
    breaks = []
    if "_unparseable" in node:
        return [{"index": i, "reason": "unparseable line"}]
    expected_prev = content_hash(_strip_sig(prev)) if prev else ""
    if node.get("prev_hash", "") != expected_prev:
        breaks.append({"index": i, "reason": "prev_hash linkage broken"})
    expected_sig = _hmac.new(key, canonical_json(_strip_sig(node)).encode("utf-8"),
                             hashlib.sha256).hexdigest()
    if node.get("sig", "") != expected_sig:
        breaks.append({"index": i, "reason": "HMAC signature mismatch"})
    return breaks


def verify_chain(store=None, key_path=None) -> dict:
    """Walk the RED chain: recompute every HMAC + prev_hash link."""
    nodes = _read_nodes(store)
    key = signing_key(key_path)
    breaks: list[dict] = []
    prev: dict | None = None
    for i, node in enumerate(nodes):
        breaks.extend(_check_node(i, node, prev, key))
        prev = node if "_unparseable" not in node else prev
    return {"ok": not breaks, "count": len(nodes), "breaks": breaks}
