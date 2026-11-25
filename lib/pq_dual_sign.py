"""Post-quantum dual signature — Ed25519 + ML-DSA-65 (FIPS 204).

Phase-1 key-custody primitive for the cryptographic-causation moat
(``rules/cryptographic-causation.md``). D2 was locked by V>> on 2026-06-26:
every record that leaves the machine or backs an outward claim is **dual-signed**
— classical **Ed25519** (fast, tiny, battle-tested) AND post-quantum
**ML-DSA-65** (survives a quantum adversary) — so a record stays verifiable even
if ONE primitive is later broken.

Deterministic verify ONLY — there is NEVER an LLM on this path
(``rules/llm-augmentation-pattern.md`` crypto category-error: an LLM bolted onto
a signature check would forge the tamper-evidence the moat sells).

Fail-closed dual semantics (the load-bearing invariant — both limbs are an AND,
not an OR):

* **VERIFIED** iff BOTH the Ed25519 AND the ML-DSA-65 signature verify against
  the pinned public keys with matching fingerprints.
* **BROKEN** iff EITHER signature is invalid/malformed, EITHER block is absent,
  or EITHER fingerprint fails to bind (key-substitution attempt).
* **DEGRADED** iff the verifying material is unavailable, or the record is not a
  dual-scheme record — informational, NEVER a silent VERIFIED.

Keys are provisioned per-tenant by ``lib.aide.key_provisioning`` (the Ed25519
and ML-DSA-65 PEM keypairs under ``state/audit/keys/<tenant_id>/``). This module
is the sign/verify half; it owns no key material of its own.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

# Tri-state verdict — reuse the canonical audit_chain Verdict so every verify
# surface in GRIP speaks ONE vocabulary (DRY).
from lib.audit_chain import Verdict

# The on-record scheme tag for a dual-signed audit block.
SCHEME = "ed25519+ml-dsa-65"


def _fingerprint(pub_raw: bytes) -> str:
    """First 8 hex of SHA256(raw pubkey).

    Same convention as ``audit_chain._compute_key_fingerprint`` and
    ``key_provisioning._compute_key_fingerprint`` so a dual record's
    fingerprints interoperate with the Ed25519 single-sig fingerprints already
    on the chain.
    """
    return hashlib.sha256(pub_raw).hexdigest()[:8]


def public_key_raw_from_pem(pem: bytes) -> bytes:
    """Raw public-key bytes from a published PEM verifying key (either limb).

    The verifying keys GRIP publishes are PEM ``SubjectPublicKeyInfo`` files,
    but ``dual_verify_external`` takes RAW key bytes — this is the bridge an
    outside auditor needs to get from the artefact they can fetch to the input
    the verifier takes. Dispatches on the key type because the two limbs expose
    raw bytes differently (Ed25519 via ``public_bytes``, ML-DSA-65 via
    ``public_bytes_raw``).

    Raises ``ValueError`` on malformed PEM or an unsupported key algorithm.
    Raising is deliberate and safe: a bad key file is a broken PRECONDITION,
    not a verification result, and the caller must be told which. It can never
    yield a VERIFIED — the only two outcomes here are usable raw bytes or a
    loud error.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import mldsa
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = serialization.load_pem_public_key(bytes(pem))
    except Exception as exc:
        raise ValueError(f"not a readable PEM public key: {exc}") from exc
    if isinstance(pub, Ed25519PublicKey):
        return pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    if isinstance(pub, mldsa.MLDSA65PublicKey):
        return pub.public_bytes_raw()
    raise ValueError(
        f"unsupported public key algorithm {type(pub).__name__} — the dual scheme "
        f"is Ed25519 + ML-DSA-65"
    )


def _key_dir(tenant_id, base: Optional[Path] = None) -> Path:
    """Resolve the tenant key directory.

    ``base`` is read at CALL time from ``key_provisioning._TENANT_KEY_BASE`` when
    not supplied, so tests that monkeypatch the base path take effect (and so the
    single source of truth for the key root is not duplicated here).
    """
    if base is not None:
        return Path(base) / str(tenant_id)
    from lib.aide import key_provisioning
    return key_provisioning._TENANT_KEY_BASE / str(tenant_id)


def _load_private_keys(tenant_id, base: Optional[Path] = None):
    """Load ``(ed25519_priv, mldsa65_priv)`` or ``None`` if EITHER is missing."""
    d = _key_dir(tenant_id, base)
    ed_path = d / "signing.key"
    mldsa_path = d / "signing-mldsa65.key"
    if not (ed_path.exists() and mldsa_path.exists()):
        return None
    from cryptography.hazmat.primitives import serialization
    ed = serialization.load_pem_private_key(ed_path.read_bytes(), password=None)
    mldsa = serialization.load_pem_private_key(mldsa_path.read_bytes(), password=None)
    return ed, mldsa


def _load_public_keys(tenant_id, base: Optional[Path] = None):
    """Load ``(ed_pub, ed_fp, mldsa_pub, mldsa_fp)`` or ``None`` if EITHER missing."""
    d = _key_dir(tenant_id, base)
    ed_path = d / "verifying.pub"
    mldsa_path = d / "verifying-mldsa65.pub"
    if not (ed_path.exists() and mldsa_path.exists()):
        return None
    from cryptography.hazmat.primitives import serialization
    ed_pub = serialization.load_pem_public_key(ed_path.read_bytes())
    mldsa_pub = serialization.load_pem_public_key(mldsa_path.read_bytes())
    # Same PEM -> raw derivation an outside auditor uses, so the fingerprint an
    # insider pins and the one an outsider computes can never disagree.
    ed_raw = public_key_raw_from_pem(ed_path.read_bytes())
    mldsa_raw = public_key_raw_from_pem(mldsa_path.read_bytes())
    return ed_pub, _fingerprint(ed_raw), mldsa_pub, _fingerprint(mldsa_raw)


def dual_sign(message: bytes, tenant_id, *, base: Optional[Path] = None) -> dict:
    """Dual-sign ``message`` with the tenant's Ed25519 + ML-DSA-65 private keys.

    Returns the audit block::

        {"scheme": "ed25519+ml-dsa-65",
         "ed25519": {"signature": "<hex>", "key_fingerprint": "<8hex>"},
         "mldsa65": {"signature": "<hex>", "key_fingerprint": "<8hex>"}}

    Fail-LOUD on sign: raises ``RuntimeError`` if either key is unprovisioned.
    Silently producing a single-signed (or unsigned) record when dual custody
    was requested would defeat the exact guarantee D2 locks in.
    """
    privs = _load_private_keys(tenant_id, base)
    if privs is None:
        raise RuntimeError(
            f"dual_sign: tenant {tenant_id!r} is missing Ed25519 and/or ML-DSA-65 "
            f"signing material. Run "
            f"key_provisioning.provision_tenant_pq_keys({tenant_id!r}) first."
        )
    ed_priv, mldsa_priv = privs
    _ed_pub, ed_fp, _mldsa_pub, mldsa_fp = _load_public_keys(tenant_id, base)
    return {
        "scheme": SCHEME,
        "ed25519": {"signature": ed_priv.sign(message).hex(), "key_fingerprint": ed_fp},
        "mldsa65": {"signature": mldsa_priv.sign(message).hex(), "key_fingerprint": mldsa_fp},
    }


def _verify_one(pub, sig_block, expected_fp: str, message: bytes) -> Verdict:
    """Verify ONE limb: fingerprint binding + signature. VERIFIED or BROKEN.

    A missing block, missing field, fingerprint mismatch (key substitution), or
    bad signature all fail BROKEN — there is no benign single-limb failure in a
    dual record.
    """
    if not isinstance(sig_block, dict):
        return Verdict.BROKEN
    sig_hex = sig_block.get("signature")
    fp = sig_block.get("key_fingerprint")
    if not sig_hex or not fp:
        return Verdict.BROKEN
    if fp != expected_fp:  # key-substitution attempt
        return Verdict.BROKEN
    try:
        pub.verify(bytes.fromhex(sig_hex), message)
        return Verdict.VERIFIED
    except Exception:
        return Verdict.BROKEN


def _dual_verify_with_keys(message, audit, ed_pub, ed_fp, mldsa_pub, mldsa_fp) -> Verdict:
    """Fail-closed AND of both limbs. VERIFIED iff BOTH verify, else BROKEN."""
    ed_v = _verify_one(ed_pub, audit.get("ed25519"), ed_fp, message)
    mldsa_v = _verify_one(mldsa_pub, audit.get("mldsa65"), mldsa_fp, message)
    if ed_v == Verdict.VERIFIED and mldsa_v == Verdict.VERIFIED:
        return Verdict.VERIFIED
    return Verdict.BROKEN


def dual_verify(message: bytes, audit: dict, tenant_id, *, base: Optional[Path] = None) -> Verdict:
    """Fail-closed dual verification against the tenant's PINNED public keys.

    DEGRADED if the record is not a dual-scheme record or the verifying material
    is unavailable (never a silent VERIFIED). Otherwise BOTH signatures must
    verify against the pinned keys with matching fingerprints → VERIFIED, else
    BROKEN.
    """
    if not isinstance(audit, dict) or audit.get("scheme") != SCHEME:
        return Verdict.DEGRADED
    pubs = _load_public_keys(tenant_id, base)
    if pubs is None:
        return Verdict.DEGRADED
    ed_pub, ed_fp, mldsa_pub, mldsa_fp = pubs
    return _dual_verify_with_keys(message, audit, ed_pub, ed_fp, mldsa_pub, mldsa_fp)


def ed25519_limb_verify_external(message: bytes, audit: dict, ed_pub_raw: bytes) -> Verdict:
    """Verify ONLY the Ed25519 limb of a dual record against a supplied raw key.

    **This is NOT dual verification and a VERIFIED here does NOT establish dual
    custody.** It answers one narrower question: on an interpreter whose
    ``cryptography`` build has no ``mldsa`` module, can the classical limb still
    be checked? ``dual_verify_external`` cannot even be called there — it
    imports ``mldsa`` unconditionally — so without this a reader on an older
    build gets no signature evidence at all, when half of it was available.

    The caller MUST say, in its own output, that the post-quantum limb went
    unchecked. Silently rendering this as full custody would defeat the
    fail-closed AND that ``_dual_verify_with_keys`` exists to enforce, which is
    why this returns the limb's verdict and never a custody judgement.

    Fail-closed on the limb it does check: a substituted key fails the
    fingerprint binding in ``_verify_one`` and returns BROKEN. DEGRADED only
    when the record is not a dual-scheme record at all.
    """
    if not isinstance(audit, dict) or audit.get("scheme") != SCHEME:
        return Verdict.DEGRADED
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        ed_pub = Ed25519PublicKey.from_public_bytes(bytes(ed_pub_raw))
    except Exception:
        return Verdict.BROKEN
    return _verify_one(ed_pub, audit.get("ed25519"), _fingerprint(bytes(ed_pub_raw)), message)


def dual_verify_external(
    message: bytes, audit: dict, ed_pub_raw: bytes, mldsa_pub_raw: bytes
) -> Verdict:
    """Verify a dual-signed record against EXTERNALLY-supplied raw public keys.

    For an outside auditor who holds only the published verifying keys (e.g.
    fetched from a published key file) and does NOT trust GRIP. BOTH
    must verify against the supplied keys, whose fingerprints must bind the
    record's declared fingerprints. Fail-closed; malformed key bytes → BROKEN.

    GRIP publishes its own keys in the GRIP repository at
    ``docs/verification/grip-verify-keys.json``, with a standalone checker
    beside them. An earlier version of this docstring named
    a product domain's ``/.well-known/...`` path in the present tense; measured 2026-09-22,
    that domain does not resolve, and reading it as a live address cost a real
    detour -- so take any address in a docstring as intended, not as live.

    Whichever key file you use, note what it can and cannot settle. A key
    fetched from the same place as the record it checks cannot prove the record
    is not a forgery, because a forger publishes a matching key beside their own
    fabricated record and every internal check passes. Only an address the
    record's author does not control breaks that, which is a deployment
    property rather than anything this function can verify.
    """
    if not isinstance(audit, dict) or audit.get("scheme") != SCHEME:
        return Verdict.DEGRADED
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric import mldsa
    try:
        ed_pub = Ed25519PublicKey.from_public_bytes(bytes(ed_pub_raw))
        mldsa_pub = mldsa.MLDSA65PublicKey.from_public_bytes(bytes(mldsa_pub_raw))
    except Exception:
        return Verdict.BROKEN
    return _dual_verify_with_keys(
        message, audit, ed_pub, _fingerprint(bytes(ed_pub_raw)),
        mldsa_pub, _fingerprint(bytes(mldsa_pub_raw)),
    )
