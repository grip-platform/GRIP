"""PRECOG IDR — HAPPI IDR envelope construction, signing, and chain I/O.

Wave C (2026-06-04): real HMAC-SHA256 / Ed25519 signing wired via
lib.audit_chain primitives. _sign_placeholder retained for reading legacy
records; new records always use _sign_real.

Locking: fcntl.flock (POSIX) for concurrent-session safety.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

try:
    from _compat import fcntl  # POSIX fcntl, or the LockFileEx lock on Windows
except ImportError:  # imported/run as lib.X, so lib/ is not itself on sys.path
    from lib._compat import fcntl

def _default_idr_path() -> Path:
    """Resolve the default chain path at CALL time, never at import.

    A module-level constant froze whichever ``CLAUDE_DIR`` the process started
    with, so a later override — a worktree session, a test's
    ``monkeypatch.setenv`` — silently kept writing to the ORIGINAL ledger
    (production, for every runtime caller). Same contract as
    ``lib.idr_head._base_head_path``, which documents the same trap.
    """
    return (
        Path(os.environ.get("CLAUDE_DIR", Path.home() / ".claude"))
        / "state" / "precog" / "idr.jsonl"
    )


def __getattr__(name: str):
    # PEP 562 back-compat: ``_DEFAULT_IDR_PATH`` stays importable for external
    # probes, served LIVE from the current environment. Setting the attribute
    # no longer redirects this module — patch ``_default_idr_path`` instead.
    if name == "_DEFAULT_IDR_PATH":
        return _default_idr_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


HAPPI_VERSION = "1.6"


@dataclass
class PrecogIDR:
    happi: str
    kind: str
    id: str
    predecessor_idr: str | None
    depth: int
    fingerprint: str
    ts: str
    decision: dict[str, Any]
    inputs: dict[str, Any]
    audit: dict[str, Any]
    # Decision anatomy (the decision-anatomy design, round A) — the delegated
    # decision's "mind": why / confidence / uncertainties / assumptions /
    # falsification_criteria, as a validated ``lib.decision_record.Decision``
    # serialised via ``Decision.to_dict()``. OPTIONAL + back-compat: defaults to
    # ``None`` so a deterministic IDR (and every legacy record) is byte-unchanged.
    # Populated ONLY on the delegated/audited path, signed into the IDR with the
    # rest of the body. Excluded from ``content_addr`` when ``None`` so a record
    # without anatomy addresses identically to a pre-anatomy record (see
    # ``_CONTENT_ADDR_EXCLUDE`` / ``content_addr``).
    decision_anatomy: dict[str, Any] | None = None


def _canonical_json(obj: dict) -> str:
    """Deterministic JSON for signing — sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# Volatile, non-content fields excluded from the content address: `id` and `ts`
# are wall-clock + random (build_idr:106), `audit` is the signature derived from
# them. The content coordinate is the SEMANTIC body (kind, predecessor_idr,
# depth, fingerprint, decision, inputs, happi).
_CONTENT_ADDR_EXCLUDE = ("id", "ts", "audit")


def content_addr(envelope: dict) -> str:
    """Content address of an IDR body — sha256 of the canonical body EXCLUDING
    the volatile fields (`id`, `ts`, `audit`).

    Two IDRs with byte-identical semantic content but different `id`+`ts` (and
    therefore different `audit` signatures) share ONE content_addr — the
    addressed coordinate for dedup / merkle linkage in the context memory-chain
    (drafts/happi-context-event-memory-chain-design.md §3, P0). The random `id`
    and wall-clock `ts` remain as non-addressed metadata. This mirrors git's
    tree-hash, which excludes the committer-date so identical trees share a hash.

    Accepts either a raw envelope dict or ``asdict(PrecogIDR)``. Pure (no I/O).
    Returns ``"sha256:<hex>"``.

    Back-compat (Decision Anatomy, Session A): ``decision_anatomy`` is dropped
    from the addressed body ONLY when it is ``None`` (or absent) — so a record
    carrying no anatomy addresses BYTE-IDENTICALLY to a pre-anatomy record, and
    every already-chained content address (P0–P5) is unchanged. When anatomy is
    PRESENT it is load-bearing decision content and IS addressed, so two records
    differing only in their anatomy get distinct addresses.
    """
    addressed = {k: v for k, v in envelope.items() if k not in _CONTENT_ADDR_EXCLUDE}
    if addressed.get("decision_anatomy") is None:
        addressed.pop("decision_anatomy", None)
    return "sha256:" + hashlib.sha256(_canonical_json(addressed).encode()).hexdigest()


def _sign_placeholder(envelope: dict) -> dict:
    """sha256 over the canonical envelope (audit field excluded).

    Retained for reading legacy records only. New records use _sign_real.
    scheme='sha256-placeholder' is NOT tamper-evident — an attacker with
    filesystem write access can recompute a matching hash.
    """
    signing_body = {k: v for k, v in envelope.items() if k != "audit"}
    digest = hashlib.sha256(_canonical_json(signing_body).encode()).hexdigest()
    return {
        "scheme": "sha256-placeholder",
        "note": "Legacy placeholder — not tamper-evident. See _sign_real.",
        "key_fingerprint": None,
        "signature": digest,
        "chain_hash": digest,
        "sequence": envelope.get("depth", 0) + 1,
    }


# --------------------------------------------------------------------------
# Custody tiers.
#
# The IDR was designed with two: a DEMO seal (HMAC-SHA256, one shared key) and
# a PRODUCT seal (Ed25519 + ML-DSA-65, verifiable offline by anyone holding no
# secret). Both were built. Every one of the first 719 records took the demo
# seal anyway, because reaching the product path requires passing a tenant_id
# and no caller passed one.
#
# The old code did warn -- but only on `tenant_id` supplied AND unprovisioned,
# and it stayed deliberately silent when no tenant was requested at all. That
# silent branch was 100% of records. The warning was pointed at the case that
# never happened.
#
# So: EVERY fall-through to the demo seal is now announced, and every record
# carries its own tier. A downgrade must never again be inferable only from the
# absence of something.
CUSTODY_DEMO = "demo-shared-key"
CUSTODY_PRODUCT = "dual-public-key"

DOWNGRADE_NO_TENANT = "no-tenant-requested"
DOWNGRADE_UNPROVISIONED = "tenant-unprovisioned"
# Keys are on disk and this interpreter cannot read them: `cryptography` is
# absent, or too old to carry ML-DSA-65. Distinct from UNPROVISIONED because
# the fix is completely different -- install a package, do not generate keys --
# and because before this reason existed the case did not downgrade at all: it
# raised ModuleNotFoundError out of build_idr and took the caller down with it.
DOWNGRADE_NO_CRYPTO = "crypto-backend-unavailable"

# Refuse the demo seal outright. For CI, and for any deployment where a
# shared-key record is not acceptable output.
REQUIRE_DUAL_ENV = "GRIP_IDR_REQUIRE_DUAL"

# The demo seal for WANT OF A CRYPTO BACKEND is refused by default since
# 2026-10-18 (the one-folder product plan, item A3; V>>'s decision 11). The other
# two reasons still downgrade with a warning: no tenant is this repository's
# ordinary state (257 of the last 2,000 records on the shared chain), and an
# unprovisioned tenant is a configuration gap; a missing backend means a real seal
# was available one interpreter away and a forgeable record would be written in
# its place. Measured before the switch: 28 of 1,562 records, the last on
# 2026-10-01. Setting this variable to 1 restores the loud fallback for one run.
ALLOW_DEMO_WITHOUT_CRYPTO_ENV = "GRIP_IDR_ALLOW_DEMO_WITHOUT_CRYPTO"

# How to explain each way the product seal can be out of reach. A table rather
# than a chain of ifs, so adding a fourth reason cannot leave it silently
# described as one of the other three.
_DOWNGRADE_DETAIL = {
    DOWNGRADE_NO_TENANT: (
        "no tenant_id was passed and none is configured, so the product signing "
        "path was never reached. Fix in two steps -- provision the keys "
        "(provision_tenant_pq_keys, i.e. python -m lib.aide.key_provisioning "
        "provision-pq <tenant>), THEN name that tenant in $GRIP_IDR_TENANT or "
        "config/grip.json idr.tenant_id so every call site picks it up"
    ),
    DOWNGRADE_UNPROVISIONED: (
        "tenant_id={tenant!r} is configured but has no dual keypair on disk. "
        "Fix: provision_tenant_pq_keys({tenant!r}), i.e. python -m "
        "lib.aide.key_provisioning provision-pq {tenant}"
    ),
    DOWNGRADE_NO_CRYPTO: (
        "tenant_id={tenant!r} HAS a dual keypair on disk, but this interpreter "
        "cannot read it -- the `cryptography` package is missing, or too old to "
        "carry ML-DSA-65. Fix: run under the GRIP venv, or install "
        "'cryptography>=48' (47.0.0 added ML-DSA only for AWS-LC and "
        "BoringSSL builds; 48.0.0 is the first release where it works on "
        "the OpenSSL wheels pip installs)"
    ),
}

_announced: set[tuple[str, str | None]] = set()


def _downgrade_detail(reason: str, tenant_id: str | None) -> str:
    """The operator-facing explanation for one downgrade reason."""
    return _DOWNGRADE_DETAIL.get(reason, "reason={reason!r}").format(
        tenant=tenant_id, reason=reason
    )


def _announce_downgrade(reason: str, tenant_id: str | None) -> None:
    """Make a demo-seal signature observable -- or refuse it outright.

    Once per process per (reason, tenant), not once per record: 719 identical
    warnings is a wall of noise, and a warning nobody reads is the same as no
    warning. The durable per-record signal is the ``custody`` field on the
    record itself.

    Keyed on the tenant as well as the reason so that one misconfigured tenant
    cannot mask the next -- two different tenants failing for the same reason
    are two different faults and each needs reporting.

    Under ``GRIP_IDR_REQUIRE_DUAL=1`` this RAISES instead of warning, and it is
    the only place that decision is taken. Every route to the demo seal passes
    through here, so the flag cannot be honoured on one path and missed on
    another. Since 2026-10-18 it also raises, by default, for
    ``DOWNGRADE_NO_CRYPTO`` alone: see ``ALLOW_DEMO_WITHOUT_CRYPTO_ENV``.
    """
    detail = _downgrade_detail(reason, tenant_id)
    if os.environ.get(REQUIRE_DUAL_ENV) == "1":
        raise RuntimeError(
            f"{REQUIRE_DUAL_ENV}=1 refuses the demo seal: this record would be "
            f"signed HMAC-SHA256 ({reason}) -- {detail}."
        )
    if reason == DOWNGRADE_NO_CRYPTO and os.environ.get(ALLOW_DEMO_WITHOUT_CRYPTO_ENV) != "1":
        raise RuntimeError(
            f"refusing the demo seal: this record would be signed HMAC-SHA256 "
            f"({reason}) -- {detail}. A shared-key record written for want of a "
            f"crypto backend is forgeable while a real seal is one interpreter away, "
            f"so it is refused by default; set {ALLOW_DEMO_WITHOUT_CRYPTO_ENV}=1 to "
            f"write it anyway, announced once."
        )

    seen = (reason, tenant_id)
    if seen in _announced:
        return
    _announced.add(seen)

    import logging

    logging.getLogger(__name__).warning(
        "IDR custody DOWNGRADE (%s): %s. Records are being signed with the "
        "shared-key DEMO seal (HMAC-SHA256), which anyone holding the key can "
        "forge -- they are NOT independently verifiable. Set %s=1 to refuse "
        "the demo seal entirely.",
        reason, detail, REQUIRE_DUAL_ENV,
    )


def audit_custody_tier(audit: object) -> str:
    """The custody tier of any audit block, including pre-marker records.

    A record written before the marker existed has no ``custody`` field. That
    absence is not unknown-ness: its scheme says exactly what sealed it, so it
    is classified, never guessed.
    """
    if not isinstance(audit, dict):
        return "unknown"
    marked = audit.get("custody")
    if isinstance(marked, str) and marked:
        return marked
    scheme = audit.get("scheme")
    if scheme == "ed25519+ml-dsa-65":
        return CUSTODY_PRODUCT
    if scheme == "hmac-sha256":
        return CUSTODY_DEMO
    return "unknown"


def _custody_gap(tenant_id: str | None) -> str | None:
    """Why the product seal is out of reach for this record — ``None`` if it is not.

    Three questions, asked in order, each with a completely different fix: is a
    tenant named at all, does that tenant have key material, and can THIS
    interpreter read that material. They stay separate because collapsing them
    would throw away the only useful part of the answer.

    The third question is the one that used to be missing. ``tenant_pq_keys_exist``
    is a file-existence check, so on an interpreter without ``cryptography`` it
    returns True and the signing attempt then died with ModuleNotFoundError —
    a crash where a downgrade belonged.
    """
    if not tenant_id:
        return DOWNGRADE_NO_TENANT
    from lib.aide.key_provisioning import pq_backend_available, tenant_pq_keys_exist
    if not tenant_pq_keys_exist(tenant_id):
        return DOWNGRADE_UNPROVISIONED
    if not pq_backend_available():
        return DOWNGRADE_NO_CRYPTO
    return None


def _product_audit(entry_hash: str, tenant_id: str) -> dict:
    """The PRODUCT seal — Ed25519 + ML-DSA-65 over the body digest.

    Carries ``tenant_id`` to LOCATE the verifying keys. That is not a trust
    input: each limb is pinned by its own key fingerprint, so pointing the
    record at a different tenant's keys fails closed rather than verifying.
    """
    from lib.pq_dual_sign import dual_sign
    audit = dual_sign(entry_hash.encode(), tenant_id)
    audit["tenant_id"] = tenant_id
    # The PRODUCT half of "every record carries its own tier". Without this the
    # strong path was the ONLY one whose custody had to be inferred from
    # `scheme` — exactly the state this module exists to end, and the easy half
    # to forget because it is the good outcome.
    audit["custody"] = CUSTODY_PRODUCT
    return audit


def _demo_audit(entry_hash: str, reason: str) -> dict:
    """The DEMO seal — HMAC-SHA256 under one shared key. Forgeable by design.

    Only ``signature`` is verified by ``_verify_node``; ``chain_hash`` and
    ``sequence`` are absent because precog IDRs are independently signed
    (predecessor linkage lives in ``predecessor_idr`` in the body, not in a
    sequential chain hash).

    ``custody`` and ``downgrade`` live in the audit envelope, which
    ``compute_entry_hash`` EXCLUDES from the signed body — so adding them could
    not invalidate the 719 records signed before they existed. That placement is
    also their limit, stated plainly: an attacker holding the shared key can
    strip the marker. It is not an anti-tamper control. It exists so OUR OWN
    drift is visible to every reader of the record, which is the failure that
    actually happened.
    """
    import hashlib as _h
    import hmac as _hmac
    from lib.audit_chain import _ensure_signing_key
    key = _ensure_signing_key()
    return {
        "scheme": "hmac-sha256",
        "custody": CUSTODY_DEMO,
        "downgrade": reason,
        "key_fingerprint": _h.sha256(key).hexdigest()[:8],
        "signature": "hmac-sha256:" + _hmac.new(key, entry_hash.encode(), _h.sha256).hexdigest(),
    }


def _sign_real(envelope: dict, tenant_id: str | None = None) -> dict:
    """Sign an IDR audit block — the product seal when reachable, else the demo one.

    Both tiers sign the SAME ``compute_entry_hash`` body digest, so the signed
    body is unchanged from a legacy record and existing chains and verifiers are
    unaffected. The audit ENVELOPE differs (it now carries ``custody`` and, on a
    downgrade, ``downgrade``), so anything doing byte-level equality or dedup
    over the whole record will see the new keys.

    Takes ``tenant_id`` explicitly and never resolves one itself — resolution is
    ``build_idr``'s job, and keeping it out of here leaves this function's
    contract exactly as it was for direct callers and tests.
    """
    from lib.audit_chain import compute_entry_hash
    body = {k: v for k, v in envelope.items() if k != "audit"}
    entry_hash = compute_entry_hash(body)
    gap = _custody_gap(tenant_id)
    if gap is None:
        return _product_audit(entry_hash, tenant_id)
    _announce_downgrade(gap, tenant_id)
    return _demo_audit(entry_hash, gap)


def _effective_tenant(tenant_id: str | None) -> str | None:
    """The tenant this record signs as: the caller's if it named one, else the
    installation's configured tenant, else ``None``.

    THE single resolution point. There are ~17 ``build_idr`` call sites across
    GRIP and not one of them passed a tenant, which is exactly why every record
    took the demo seal — the product path was unreachable without an argument
    nobody had a reason to supply. Resolving here means a call site does not
    have to know it exists, and the answer cannot drift between them.

    Returns ``None`` on an unconfigured machine, which is the ordinary state and
    reproduces the previous behaviour byte for byte. Dual custody switches on
    when the operator configures a tenant, never as a side effect of an upgrade.

    Both import spellings are tolerated because this module is reached under
    two: ``lib.precog.idr`` from most callers, and ``precog.idr`` from
    ``lib/learning_proposer.py``, which binds a different module object.
    """
    if tenant_id:
        return tenant_id
    try:
        from lib.idr_tenant import resolve_tenant_id
    except ImportError:  # imported as precog.idr, so lib/ is on sys.path itself
        from idr_tenant import resolve_tenant_id
    return resolve_tenant_id()


def _resolve_anatomy_dict(decision_anatomy: Any) -> dict[str, Any] | None:
    """Validate + canonicalise a delegated decision's anatomy for signing.

    THE Session-A chokepoint: the SMT gate + anatomy/honesty invariants
    (``lib.decision_record``) are applied HERE, once, at the IDR build boundary —
    never per-callsite. Accepts either a ``Decision`` instance or its
    ``to_dict()`` mapping; returns the validated canonical dict, or ``None`` when
    no anatomy is supplied (the deterministic / minimal tier — byte-unchanged).

    ``decision_record`` is imported lazily so this module stays import-light and
    free of an import cycle (decision_record is pure stdlib; idr.py is its
    consumer, not the reverse).
    """
    if decision_anatomy is None:
        return None
    from lib.decision_record import Decision, from_model_dict, validate
    if isinstance(decision_anatomy, Decision):
        return validate(decision_anatomy).to_dict()  # honesty + tier check
    if isinstance(decision_anatomy, dict):
        return from_model_dict(decision_anatomy).to_dict()  # SMT gate + validate
    raise TypeError(
        "decision_anatomy must be a Decision, its to_dict() mapping, or None — "
        f"got {type(decision_anatomy).__name__}"
    )


def build_idr(
    prompt: str,
    fingerprint: str,
    decision: dict[str, Any],
    predecessor_idr: str | None,
    depth: int,
    *,
    kind: str = "precog-decision",
    inputs: dict[str, Any] | None = None,
    tenant_id: str | None = None,
    decision_anatomy: Any = None,
) -> PrecogIDR:
    """Construct a PrecogIDR envelope, signed with the strongest reachable seal.

    tenant_id: when a dual keypair is provisioned for it (key_provisioning.
    provision_tenant_pq_keys), the record is dual-signed Ed25519 + ML-DSA-65
    (scheme ed25519+ml-dsa-65); otherwise HMAC-SHA256 (back-compat). See
    _sign_real.

    When the caller names no tenant — which is every call site in GRIP today —
    the installation's configured tenant is used instead (``$GRIP_IDR_TENANT``
    or ``config/grip.json`` ``idr.tenant_id``, resolved by ``lib.idr_tenant``).
    On a machine where neither is set this resolves to ``None`` and the record
    is byte-identical to one built before this resolution existed, so nothing
    changes until the operator provisions and configures a tenant deliberately.

    decision_anatomy (Decision Anatomy, Session A): OPTIONAL delegated-decision
    "mind" — a ``lib.decision_record.Decision`` or its ``to_dict()`` mapping. When
    supplied it is validated through the SMT gate + anatomy/honesty invariants and
    SIGNED INTO the IDR with the rest of the body (so a tampered anatomy fails
    ``verify_chain_integrity``). When ``None`` (deterministic / minimal tier) the
    envelope, signature, and ``content_addr`` are BYTE-IDENTICAL to a pre-anatomy
    IDR — existing callers are unchanged. This is the single chokepoint; callers
    never re-validate the anatomy themselves.
    """
    from datetime import datetime, timezone
    anatomy = _resolve_anatomy_dict(decision_anatomy)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 4-byte random suffix avoids same-second collisions when build_idr is called
    # multiple times within one second (real production risk for concurrent waves).
    idr_id = f"precog-{int(time.time())}-{secrets.token_hex(4)}"

    envelope = {
        "happi": HAPPI_VERSION,
        "kind": kind,
        "id": idr_id,
        "predecessor_idr": predecessor_idr,
        "depth": depth,
        "fingerprint": fingerprint,
        "ts": ts,
        "decision": decision,
        "inputs": inputs or {},
    }
    # Add to the SIGNED body ONLY when present — keeps the deterministic-tier
    # envelope (and therefore its signature) byte-identical to the legacy shape.
    if anatomy is not None:
        envelope["decision_anatomy"] = anatomy
    audit = _sign_real(envelope, tenant_id=_effective_tenant(tenant_id))
    return PrecogIDR(
        happi=HAPPI_VERSION,
        kind=kind,
        id=idr_id,
        predecessor_idr=predecessor_idr,
        depth=depth,
        fingerprint=fingerprint,
        ts=ts,
        decision=decision,
        inputs=inputs or {},
        audit=audit,
        decision_anatomy=anatomy,
    )


def append_idr(idr: PrecogIDR, path: Path | None = None) -> None:
    """Atomically append one IDR as a JSONL line.

    Uses fcntl.flock for POSIX concurrent-session safety.
    """
    target = path or _default_idr_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    # The persisted line must match the body that was SIGNED, byte for byte.
    # build_idr adds `decision_anatomy` to the signed envelope ONLY when it is
    # present (see the guard above its _sign_real call), but asdict() always
    # emits the dataclass field — so the stored line carried a key the signer
    # never saw. An auditor reconstructing the body by dropping `audit` alone
    # hashed something the signer never signed and got BROKEN on a genuine
    # record, which looks exactly like a forgery. Dropping the null here makes
    # stored == signed, so verbatim reconstruction simply works.
    #
    # Forward-only, and deliberately so: records written before this carry the
    # null and still need the drop at verify time. Re-signing them to match
    # would manufacture a custody they never had.
    body = asdict(idr)
    if body.get("decision_anatomy") is None:
        body.pop("decision_anatomy", None)
    line = _canonical_json(body) + "\n"
    with open(target, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    _advance_head(idr.id, path)


def _advance_head(node_id: str, path: Path | None) -> None:
    """Point the decision chain's HEAD at the record just written.

    ONLY for the default chain. A custom ``path`` is a SEPARATE ledger —
    ``prove_it_provenance``, ``update_team_provenance`` and the sovereign council
    each write their own — and advancing the shared head from one of those would
    make the next main-chain record claim a predecessor it has no relationship
    to. A false causal edge is worse than the absent one it replaces, so a
    side-ledger moves nothing and manages its own lineage.

    Writing the pointer changes no signed byte of any record; it only supplies
    the predecessor the NEXT ``build_idr`` can link to. Fail-open by contract:
    the record is already durably on disk, so a pointer failure must degrade the
    next record's link, never raise over a write that already succeeded.
    """
    if path is not None:
        return
    try:
        from lib.idr_head import advance
    except ImportError:  # imported as precog.idr, so lib/ is on sys.path itself
        try:
            from idr_head import advance  # type: ignore[no-redef]
        except ImportError:
            return
    advance(node_id)


def read_idr_chain(
    predecessor_id: str | None = None,
    path: Path | None = None,
) -> list[PrecogIDR]:
    """Read the predecessor chain starting from predecessor_id.

    If predecessor_id is None, returns all IDRs in file order.
    Otherwise traverses the chain backwards from the named IDR.
    """
    target = path or _default_idr_path()
    if not target.exists():
        return []

    all_idrs: list[PrecogIDR] = []
    with open(target, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                # ``if k in d`` is the back-compat key: legacy records predate
                # optional fields (e.g. ``decision_anatomy``). Without the guard a
                # missing key raises KeyError → the ``except`` below would SILENTLY
                # DROP the legacy record from the chain. Optional fields fall back
                # to their dataclass defaults instead.
                all_idrs.append(PrecogIDR(**{
                    k: d[k] for k in PrecogIDR.__dataclass_fields__ if k in d
                }))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    if predecessor_id is None:
        return all_idrs

    # Build lookup: id → idr
    by_id = {idr.id: idr for idr in all_idrs}
    chain: list[PrecogIDR] = []
    current_id: str | None = predecessor_id
    visited: set[str] = set()
    while current_id and current_id not in visited:
        idr = by_id.get(current_id)
        if idr is None:
            break
        chain.append(idr)
        visited.add(current_id)
        current_id = idr.predecessor_idr
    chain.reverse()
    return chain
