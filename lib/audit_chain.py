#!/usr/bin/env python3
"""
Tamper-Evident Audit Chain for GRIP
====================================

HMAC-signed, hash-chained audit entries. Each entry contains:
- entry_hash: SHA256 of entry content (without chain fields)
- chain_hash: HMAC-SHA256(previous_chain_hash + entry_hash)
- sequence: monotonic counter

Tampering with any entry breaks the chain detectably.

Usage:
  python3 audit_chain.py verify           # Verify chain integrity
  python3 audit_chain.py sign <json>      # Sign a single entry (returns augmented JSON)
  python3 audit_chain.py status           # Show chain status

Version: 1.0.0
"""

import sys
from pathlib import Path

# Bootstrap: the bare `from file_lock import FileLock` below is a sibling
# import that only resolves if lib/ itself is on sys.path. Callers that
# import this module as `lib.audit_chain` from the repo root (the standard
# invocation pattern — e.g. `PYTHONPATH=$CLAUDE_DIR python -c "from lib..."`)
# have the repo ROOT on sys.path, not lib/, so the bare import raised
# ModuleNotFoundError. Self-bootstrapping here matches the canonical
# pattern (rules/path-portability.md) and is idempotent/back-compat-safe:
# it also still works under direct invocation (`python3 audit_chain.py`).
_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from file_lock import FileLock
from enum import Enum
from typing import Optional
import hashlib
import hmac
import json
import logging
import os
import tempfile

# Ed25519 import hoisted to module scope (Round 6 hardening). Keeping it
# inside verify_external would mask version-skew / missing-dependency
# failures as Verdict.BROKEN, hiding the real cause from auditors.
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover — cryptography is a hard dep, but defensive
    Ed25519PublicKey = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Use centralized state manager (GRASP: Pure Fabrication)
from lib.state_manager import get_state_manager, CLAUDE_DIR

AUDIT_LOG = CLAUDE_DIR / "logs" / "api-audit.jsonl"
SIGNING_KEY_FILE = CLAUDE_DIR / "state" / "audit" / "audit-signing-key"
CHAIN_STATE_FILE = get_state_manager().path("audit-chain-state")
CHAIN_LOCK_FILE = get_state_manager().lock_path("audit-chain-state")

# Directory name under ``.well-known/`` where a deployment publishes its
# externally-verifiable Ed25519 key envelopes (build_verifying_key_envelope /
# cmd_publish_verifying_key below). Overridable per deployment so a fork can
# publish under its own path with no source edit; default is generic.
# ``or`` (not a bare .get() default) so an operator's `export VAR=` typo
# (set, but empty) still falls through to the default instead of publishing
# a malformed ``.well-known//<tenant>.pub`` (sovereign-council review,
# 2026-11-22).
WELL_KNOWN_KEYS_DIR = os.environ.get("GRIP_WELL_KNOWN_KEYS_DIR") or "verifying-keys"

# Fields excluded from entry hash (they ARE the chain metadata)
CHAIN_FIELDS = {"entry_hash", "chain_hash", "sequence"}

# The canonical NON-BODY metadata set: the chain fields PLUS the signature fields
# the signer writes ONTO an entry AFTER entry_hash is computed. ``compute_entry_hash``
# (the single hash domain) and every verify path exclude this exact set, so sign and
# verify hash the identical body — see ``compute_entry_hash``. (Name kept for
# back-compat; it is now the shared sign+verify exclusion, not verify-only.)
_VERIFY_EXCLUDED_FIELDS = CHAIN_FIELDS | {"signature", "key_fingerprint", "scheme"}

# Per-tenant Ed25519 key material lives here. PR #10-b wires per-tenant signing;
# in this PR (#10-a) the directory tree exists but only the explicit Ed25519
# callers (e.g. test fixtures, future tenant-scoped loggers) touch it.
_TENANT_KEY_BASE = CLAUDE_DIR / "state" / "audit" / "keys"


class SigningScheme(str, Enum):
    """Signing scheme for chain entries.

    HMAC_SHA256 is the legacy default — single global master key at
    ``state/audit/audit-signing-key``, fast symmetric MAC, chain integrity
    via HMAC of (previous_chain_hash || entry_hash).

    ED25519 is the per-tenant asymmetric path — per-tenant keypair under
    ``state/audit/keys/<tenant_id>/`` (priv ``signing.key`` mode 0600,
    pub ``verifying.pub`` mode 0644). Signature is over ``entry_hash``.
    Chain integrity via SHA256 of (previous_chain_hash || entry_hash)
    (no key needed for chain linkage — authentication lives in the
    per-entry Ed25519 signature, linkage in preimage resistance).
    """

    HMAC_SHA256 = "hmac-sha256"
    ED25519 = "ed25519"
    AUTO = "auto"  # Round 6 — resolves to ED25519 when tenant keys present, else HMAC


def _resolve_scheme(scheme: "SigningScheme", tenant_id: "Optional[str]") -> "SigningScheme":
    """Round 6 — resolve ``SigningScheme.AUTO`` to a concrete scheme.

    AUTO is the new default for ``sign_entry`` / ``sign_and_append``. Behaviour:

    - AUTO + ``tenant_id`` with Ed25519 keys provisioned → ``ED25519``.
    - AUTO + ``tenant_id`` with no keypair on disk → ``HMAC_SHA256`` (graceful fallback).
    - AUTO + ``tenant_id=None`` → ``HMAC_SHA256`` (no per-tenant context).
    - Non-AUTO schemes pass through unchanged (caller's explicit choice wins).

    Backward compat: callers passing no ``scheme`` argument now hit AUTO →
    either Ed25519 (if their ``tenant_id`` is provisioned) or HMAC (the
    pre-Round-6 default). No existing call site changes meaning unless it
    *previously* passed a ``tenant_id`` whose keys are already on disk —
    those (and only those) silently upgrade to Ed25519, which is the
    Round 6 design intent (council-ratified).
    """
    if scheme != SigningScheme.AUTO:
        return scheme
    if tenant_id and _load_tenant_ed25519_keys(tenant_id) is not None:
        return SigningScheme.ED25519
    return SigningScheme.HMAC_SHA256


class Verdict(str, Enum):
    """Tri-state verification verdict per `feedback_chain_heal_signed_count_highwater_floor`."""

    VERIFIED = "verified"
    DEGRADED = "degraded"
    BROKEN = "broken"


def _ensure_signing_key() -> bytes:
    """Load the HMAC-SHA256 signing key, creating it exactly once if absent.

    Every caller returns the key that is ON DISK, never one it merely
    generated. Several sessions or hooks on a fresh tree used to race here:
    each found no file and wrote its own key with a truncating write, so a
    caller could read the file while it was still empty, or have its key
    replaced by a later writer, and then sign records that never verified
    (GRIP#8379). ``_publish_signing_key`` closes both windows.
    """
    if not SIGNING_KEY_FILE.exists():
        _publish_signing_key(os.urandom(32))
    return SIGNING_KEY_FILE.read_bytes()


def _publish_signing_key(key: bytes) -> None:
    """Put ``key`` in place only if no key is there yet, and never half-written.

    The key is written to a temp file in the same directory, which
    ``mkstemp`` creates readable by its owner only, flushed to disk, and then
    hard-linked to the final name. A link never replaces an existing file: a
    second writer gets FileExistsError and the first writer's key stands, and
    the final name only ever points at a complete key.
    """
    SIGNING_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(SIGNING_KEY_FILE.parent), prefix=".signing-key-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.link(tmp, str(SIGNING_KEY_FILE))
        except FileExistsError:
            pass  # another caller published first; its key is the key
    finally:
        os.unlink(tmp)


def _load_chain_state() -> dict:
    """Load last chain_hash and sequence from state file."""
    if CHAIN_STATE_FILE.exists():
        try:
            return json.loads(CHAIN_STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_chain_hash": "", "sequence": 0}


def _save_chain_state(state: dict):
    """Persist chain state."""
    try:
        CHAIN_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CHAIN_STATE_FILE.write_text(json.dumps(state))
        CHAIN_STATE_FILE.chmod(0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Per-log-path chain state (per-tenant independence)
#
# sign_and_append derives state + lock files from log_path so that each log
# (e.g. tenants/acme/decision-log.jsonl) has its OWN monotonic sequence that
# starts at 1, independent of every other log path.  verify_chain walks a
# single file starting from expected_seq=1, so this is the only shape that
# makes verify pass on a fresh per-tenant log.
#
# The global CHAIN_STATE_FILE / CHAIN_LOCK_FILE are preserved for:
#   - sign_entry (CLI, global AUDIT_LOG)
#   - cmd_status / cmd_backfill
# ---------------------------------------------------------------------------


def _log_state_path(log_path: Path) -> Path:
    """Sibling .chain-state.json file for log_path."""
    return log_path.with_suffix(".chain-state.json")


def _log_lock_path(log_path: Path) -> Path:
    """Sibling .chain-lock file for log_path."""
    return log_path.with_suffix(".chain-lock")


def _load_log_chain_state(log_path: Path) -> dict:
    """Load per-log chain state (genesis if file absent)."""
    state_file = _log_state_path(log_path)
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_chain_hash": "", "sequence": 0}


def _save_log_chain_state(log_path: Path, state: dict) -> None:
    """Persist per-log chain state alongside the log file."""
    state_file = _log_state_path(log_path)
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.chmod(0o600)
        # replace, not rename: on Windows, rename refuses a target that exists (GRIP#8770)
        tmp.replace(state_file)
    except OSError:
        pass


def compute_entry_hash(entry: dict) -> str:
    """SHA256 of the entry's signable BODY — excludes the non-body metadata the
    signer writes ONTO an entry (``_VERIFY_EXCLUDED_FIELDS``: the chain fields plus
    ``signature``/``key_fingerprint``/``scheme``).

    This is the SINGLE canonical hash domain shared by the sign path
    (``_sign_chain_step_*``) AND every verify path (``verify_entry`` /
    ``verify_external`` / ``_check_entry``). Sign and verify MUST hash the identical
    field set, else a legitimate entry false-fails ``BROKEN``: the signer adds
    ``signature``/``key_fingerprint``/``scheme`` AFTER hashing, and verify strips them
    before recomputing — so the sign side must exclude the same set. Previously this
    excluded only ``CHAIN_FIELDS``, so an entry body that itself carried a top-level
    ``scheme``/``signature``/``key_fingerprint`` key hashed differently at sign vs
    verify → a false tamper alarm on the integrity guarantee this chain provides. Backward-compatible:
    real entries never carry those top-level keys at sign time (the signer adds them
    afterwards), so every already-signed entry's hash is byte-unchanged.
    """
    # sorted() is belt-and-braces hash-stability: json.dumps(sort_keys=True) already
    # canonicalises key order today, but sorting here keeps the hashed content stable
    # even if a future edit to the dumps call (separators / a non-dict input path)
    # ever drops sort_keys. Hash-neutral now; defensive on crypto-load-bearing code.
    clean = {k: v for k, v in sorted(entry.items()) if k not in _VERIFY_EXCLUDED_FIELDS}
    content = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def compute_chain_hash(key: bytes, previous_chain_hash: str, entry_hash: str) -> str:
    """HMAC-SHA256(previous_chain_hash + entry_hash)."""
    message = (previous_chain_hash + entry_hash).encode()
    h = hmac.new(key, message, hashlib.sha256)
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# Ed25519 path (round-4 / PR #10-a additive)
#
# Per-entry Ed25519 signature over entry_hash; SHA256 chain hash for linkage.
# Existing HMAC callers are unchanged (scheme defaults to HMAC_SHA256).
# Council-ratified — see the project's signing-hardening council synthesis
# record from 2026-05-20.
# ---------------------------------------------------------------------------


def _compute_chain_hash_sha256(previous_chain_hash: str, entry_hash: str) -> str:
    """Pure-SHA256 chain hash for Ed25519 entries.

    No signing key required for chain linkage — authentication lives in the
    per-entry Ed25519 signature, linkage in preimage resistance. This is
    the modern audit-chain pattern (Sigstore Rekor, Solana proofs):
    signature authenticates each entry, chain links them, tampering breaks
    one or both.
    """
    message = (previous_chain_hash + entry_hash).encode()
    return "sha256:" + hashlib.sha256(message).hexdigest()


def _compute_key_fingerprint(pub_raw_bytes: bytes) -> str:
    """First 8 hex chars of SHA256(pub_bytes). Stable identifier for a verifying key."""
    return hashlib.sha256(pub_raw_bytes).hexdigest()[:8]


def _load_tenant_ed25519_keys(tenant_id):
    """Load per-tenant Ed25519 priv + pub key + fingerprint.

    Returns ``(priv, pub, fingerprint)`` tuple, or ``None`` if the tenant
    has not been provisioned (no Keychain entry / no PEM file pair). The
    caller decides whether to fall back, raise, or degrade.
    """
    if not tenant_id:
        return None
    base = _TENANT_KEY_BASE / str(tenant_id)
    priv_path = base / "signing.key"
    pub_path = base / "verifying.pub"
    if not (priv_path.exists() and pub_path.exists()):
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        priv = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
        pub = serialization.load_pem_public_key(pub_path.read_bytes())
        pub_raw = pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return priv, pub, _compute_key_fingerprint(pub_raw)
    except Exception:
        return None


def _sign_chain_step_hmac(clean_entry: dict, previous_chain_hash: str, sequence: int):
    """HMAC-SHA256 sign step (legacy path). Returns ``(signed_dict, chain_hash)``."""
    key = _ensure_signing_key()
    entry_hash = compute_entry_hash(clean_entry)
    chain_hash = compute_chain_hash(key, previous_chain_hash, entry_hash)
    signed = dict(clean_entry)
    signed["entry_hash"] = entry_hash
    signed["chain_hash"] = chain_hash
    signed["sequence"] = sequence
    return signed, chain_hash


def _sign_chain_step_ed25519(clean_entry: dict, previous_chain_hash: str, sequence: int, tenant_id):
    """Ed25519 sign step. Returns ``(signed_dict, chain_hash)`` or ``None`` if keys missing."""
    keys = _load_tenant_ed25519_keys(tenant_id)
    if keys is None:
        return None
    priv, _pub, fingerprint = keys
    entry_hash = compute_entry_hash(clean_entry)
    chain_hash = _compute_chain_hash_sha256(previous_chain_hash, entry_hash)
    signature_bytes = priv.sign(entry_hash.encode())
    signed = dict(clean_entry)
    signed["entry_hash"] = entry_hash
    signed["chain_hash"] = chain_hash
    signed["sequence"] = sequence
    signed["signature"] = signature_bytes.hex()
    signed["key_fingerprint"] = fingerprint
    signed["scheme"] = SigningScheme.ED25519.value
    return signed, chain_hash


def _verify_entry_ed25519(entry: dict, recomputed_entry_hash: str, tenant_id):
    """Ed25519-specific verification step. Returns a Verdict."""
    sig_hex = entry.get("signature")
    if not sig_hex:
        return Verdict.BROKEN
    keys = _load_tenant_ed25519_keys(tenant_id)
    if keys is None:
        return Verdict.DEGRADED
    _priv, pub, fingerprint = keys
    if entry.get("key_fingerprint") != fingerprint:
        return Verdict.BROKEN
    try:
        pub.verify(bytes.fromhex(sig_hex), recomputed_entry_hash.encode())
        return Verdict.VERIFIED
    except Exception:
        return Verdict.BROKEN


def verify_entry(entry: dict, tenant_id=None):
    """Tri-state per-entry verification.

    VERIFIED — signature + entry_hash both validate against the entry's declared scheme.
    DEGRADED — entry parses and has a recognised scheme but verification key material
               unavailable (e.g. Ed25519 entry without a fetchable verifying key).
               Informational, not tamper.
    BROKEN — signature invalid OR entry_hash does not reproduce from content → tamper.

    Per-entry only. Cross-entry chain integrity (verify_chain) walks the sequence
    and is intentionally out of scope here.
    """
    try:
        scheme_str = entry.get("scheme", SigningScheme.HMAC_SHA256.value)
        clean = {k: v for k, v in entry.items() if k not in _VERIFY_EXCLUDED_FIELDS}
        recomputed = compute_entry_hash(clean)
        if entry.get("entry_hash") != recomputed:
            return Verdict.BROKEN
        if scheme_str == SigningScheme.HMAC_SHA256.value:
            # HMAC entry — chain_hash recomputation is a chain-walk concern.
            # Entry-level VERIFIED iff entry_hash reproduces.
            return Verdict.VERIFIED
        if scheme_str == SigningScheme.ED25519.value:
            return _verify_entry_ed25519(entry, recomputed, tenant_id)
        # Unknown scheme — informational, not tamper.
        return Verdict.DEGRADED
    except Exception:
        return Verdict.BROKEN


def _sign_dispatch(clean_entry: dict, previous_chain_hash: str, sequence: int, scheme: SigningScheme, tenant_id):
    """Dispatch to the appropriate sign step (HMAC or Ed25519). Returns ``(signed, chain_hash)``.

    Single source of truth for the scheme → sign-step mapping used by both
    ``sign_entry`` and ``sign_and_append``. Round-6 extraction (SOLID / DRY)
    so both callers stay under the KISS 50-line threshold.

    Raises ``RuntimeError`` when ``scheme == ED25519`` and the per-tenant
    keypair is absent — preserves fail-loud semantics for explicit Ed25519
    callers (the chain-heal-floor PR #62 doctrine). AUTO callers have
    already been resolved by ``_resolve_scheme`` before reaching here, so
    AUTO + missing keys arrives as HMAC and never hits the raise.
    """
    if scheme == SigningScheme.ED25519:
        result = _sign_chain_step_ed25519(clean_entry, previous_chain_hash, sequence, tenant_id)
        if result is None:
            raise RuntimeError(
                f"Ed25519 scheme requested for tenant_id={tenant_id!r} "
                f"but signing keys not provisioned at "
                f"{_TENANT_KEY_BASE / str(tenant_id or '')}"
            )
        return result
    return _sign_chain_step_hmac(clean_entry, previous_chain_hash, sequence)


def verify_external(entry: dict, verifying_key_bytes: bytes) -> Verdict:
    """Round 6 — verify an Ed25519 entry against an EXTERNALLY-provided key.

    For external auditors who hold only the published verifying key (e.g.
    fetched from a published ``.well-known/verifying-keys/<tenant>.pub`` endpoint),
    not the local per-tenant key store. Closes the trust loop: regulators,
    partners, and insurers can verify a chain without trusting GRIP.

    ``verifying_key_bytes`` must be the 32-byte raw Ed25519 public key (the
    same shape published by ``cmd_publish_verifying_key``).

    Returns:
      - VERIFIED — signature + entry_hash + fingerprint all validate.
      - DEGRADED — entry is HMAC-signed (external verify only meaningfully
        applies to the asymmetric scheme).
      - BROKEN — signature invalid, entry_hash mismatch, fingerprint
        mismatch (key substitution attempt), or malformed input.

    Per-entry only; cross-entry chain integrity is verify_chain's concern.
    """
    try:
        precondition = _verify_external_preconditions(entry, verifying_key_bytes)
        if precondition is not None:
            return precondition
        clean = {k: v for k, v in entry.items() if k not in _VERIFY_EXCLUDED_FIELDS}
        recomputed = compute_entry_hash(clean)
        if entry.get("entry_hash") != recomputed:
            return Verdict.BROKEN
        pub = Ed25519PublicKey.from_public_bytes(bytes(verifying_key_bytes))
        pub.verify(bytes.fromhex(entry["signature"]), recomputed.encode())
        return Verdict.VERIFIED
    except Exception:
        return Verdict.BROKEN


def _verify_external_preconditions(entry: dict, verifying_key_bytes: bytes):
    """Round 6 — input shape + scheme + fingerprint binding preconditions.

    Returns a ``Verdict`` (BROKEN / DEGRADED) when preconditions fail, or
    ``None`` to indicate that the caller should proceed with cryptographic
    verification. SOLID split from ``verify_external`` — keeps the
    cryptographic dance compact and makes the precondition logic
    independently testable.

    Round 6 fingerprint hardening (AGORA Speaker C): ``key_fingerprint``
    presence is MANDATORY for Ed25519 entries. An attacker-crafted entry
    that omits the field must NOT slip past the binding check. Pre-Round-6
    code had ``if entry_fp and ...`` which short-circuited on missing fp —
    that was the defence-in-depth bypass this hardening closes.
    """
    if not isinstance(verifying_key_bytes, (bytes, bytearray)) or len(verifying_key_bytes) != 32:
        return Verdict.BROKEN
    if Ed25519PublicKey is None:
        return Verdict.BROKEN
    scheme_str = entry.get("scheme", SigningScheme.HMAC_SHA256.value)
    if scheme_str != SigningScheme.ED25519.value:
        return Verdict.DEGRADED
    if not entry.get("signature"):
        return Verdict.BROKEN
    entry_fp = entry.get("key_fingerprint")
    if not entry_fp:
        return Verdict.BROKEN
    if entry_fp != _compute_key_fingerprint(verifying_key_bytes):
        return Verdict.BROKEN
    return None


def sign_entry(entry: dict, scheme: SigningScheme = SigningScheme.AUTO, tenant_id=None) -> dict:
    """Sign an audit entry with chain hashes (global audit log path).

    Round 6 default is ``SigningScheme.AUTO`` — resolved by ``_resolve_scheme``:
    AUTO + provisioned ``tenant_id`` → Ed25519; AUTO + no/missing keys → HMAC.
    Explicit ``ED25519`` without keys raises (fail-loud, chain-heal-floor
    PR #62). Concurrent-session safe via fcntl.flock; never raises to
    caller (audit-logging-must-not-break — returns entry unsigned on any
    failure, caller can detect absence of ``entry_hash``).
    """
    try:
        with FileLock(CHAIN_LOCK_FILE, timeout=10):
            scheme = _resolve_scheme(scheme, tenant_id)
            state = _load_chain_state()
            sequence = state["sequence"] + 1
            previous_chain_hash = state["last_chain_hash"]
            clean_entry = {k: v for k, v in entry.items() if k not in CHAIN_FIELDS}
            signed, chain_hash = _sign_dispatch(clean_entry, previous_chain_hash, sequence, scheme, tenant_id)
            _save_chain_state({"last_chain_hash": chain_hash, "sequence": sequence})
        return signed
    except Exception:
        return entry


def sign_and_append(
    entry: dict,
    log_path: Path,
    scheme: SigningScheme = SigningScheme.AUTO,
    tenant_id=None,
) -> dict:
    """Atomically sign an entry and append it to a JSONL log file.

    Holds the chain lock for the entire cycle: read state → sign →
    append to file → save state. This prevents concurrent sessions
    from interleaving entries out of sequence order.

    Round 6 — default is ``SigningScheme.AUTO``, same resolution rules
    as ``sign_entry``: AUTO + provisioned ``tenant_id`` → Ed25519; AUTO
    + missing keys (or no tenant_id) → HMAC fallback. Explicit ED25519
    without keys raises RuntimeError (preserved fail-loud semantics).
    """
    lock_file = _log_lock_path(log_path)
    try:
        with FileLock(lock_file, timeout=10):
            scheme = _resolve_scheme(scheme, tenant_id)
            state = _load_log_chain_state(log_path)
            sequence = state["sequence"] + 1
            previous_chain_hash = state["last_chain_hash"]
            clean_entry = {k: v for k, v in entry.items() if k not in CHAIN_FIELDS}
            signed, chain_hash = _sign_dispatch(clean_entry, previous_chain_hash, sequence, scheme, tenant_id)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                f.write(json.dumps(signed, default=str) + "\n")
            _save_log_chain_state(log_path, {"last_chain_hash": chain_hash, "sequence": sequence})
        return signed
    except Exception as exc:
        # Chain integrity > telemetry completeness. NEVER append unsigned or
        # unchained bytes into a hash-chain file: the next signed append
        # links against a state that never saw this record, and verification
        # breaks from here to the end (measured 2026-09-15: the llm-usage
        # chain broke at seq 1297 under a lock-timeout fallback append).
        # The decision record goes to an overflow sidecar; the chain file
        # is left byte-identical.
        logger.warning("sign_and_append overflow for %s: %s", log_path, exc)
        try:
            overflow = log_path.with_suffix(log_path.suffix + ".overflow")
            overflow.parent.mkdir(parents=True, exist_ok=True)
            with open(overflow, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass
        return entry


def _empty_verify_result() -> dict:
    """Verdict shape for an empty/missing audit log — vacuously valid."""
    return {
        "valid": True,
        "total_entries": 0,
        "signed_entries": 0,
        "unsigned_entries": 0,
        "first_break": None,
        "break_reason": None,
        "last_sequence": 0,
    }


def _iter_chain_entries(log_file: Path):
    """Yield ``(line_num, entry_dict)`` for each well-formed JSONL line. Blank/garbage skipped."""
    with open(log_file, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_num, json.loads(line)
            except json.JSONDecodeError:
                continue


def _check_entry(entry: dict, line_num: int, expected_seq: int, prev_chain_hash: str, key: bytes):
    """Verify ONE entry against the expected sequence + prior chain hash.

    Returns ``(status, seq, reason_or_none)`` where status is one of
    ``"ok"`` / ``"unsigned"`` / ``"entry_hash"`` / ``"chain_hash"`` / ``"sequence"``.
    Pure function — no I/O, no printing — caller handles aggregation + verbose output.
    """
    if "entry_hash" not in entry or "chain_hash" not in entry:
        return "unsigned", 0, None
    seq = entry.get("sequence", 0)
    _clean_for_hash = {k: v for k, v in entry.items() if k not in _VERIFY_EXCLUDED_FIELDS}
    if entry["entry_hash"] != compute_entry_hash(_clean_for_hash):
        return "entry_hash", seq, f"entry_hash mismatch at sequence {seq} (line {line_num})"
    scheme = entry.get("scheme", SigningScheme.HMAC_SHA256.value)
    if scheme == SigningScheme.ED25519.value:
        expected_chain_hash = _compute_chain_hash_sha256(prev_chain_hash, entry["entry_hash"])
    else:
        expected_chain_hash = compute_chain_hash(key, prev_chain_hash, entry["entry_hash"])
    if entry["chain_hash"] != expected_chain_hash:
        return "chain_hash", seq, f"chain_hash mismatch at sequence {seq} (line {line_num})"
    if seq != expected_seq:
        return "sequence", seq, f"sequence gap at {seq}, expected {expected_seq} (line {line_num})"
    return "ok", seq, None


def _process_chain_entry(entry: dict, line_num: int, state: dict, key: bytes, verbose: bool):
    """Apply one entry's check result to the shared verify accumulator (in-place).

    ``state`` carries ``total``, ``signed``, ``unsigned``, ``prev_chain_hash``,
    ``expected_seq``, ``first_break``, ``break_reason``. Mutates state and prints
    optional verbose feedback per the documented format.
    """
    state["total"] += 1
    status, seq, reason = _check_entry(entry, line_num, state["expected_seq"], state["prev_chain_hash"], key)
    if status == "unsigned":
        state["unsigned"] += 1
        if verbose:
            print(f"  Line {line_num}: unsigned (skipped)")
        return
    state["signed"] += 1
    if status != "ok":
        if state["first_break"] is None:
            state["first_break"] = seq
            state["break_reason"] = reason
        if verbose:
            print(f"  Line {line_num}: {status.upper()} MISMATCH (seq {seq})")
        if status != "sequence":
            return
    if verbose:
        print(f"  Line {line_num}: OK (seq {seq})")
    state["prev_chain_hash"] = entry["chain_hash"]
    state["expected_seq"] = seq + 1


def verify_chain(log_path: Path = None, verbose: bool = False) -> dict:
    """Walk the audit chain and verify integrity. Coordinator.

    Per-entry semantics live in ``_check_entry``; per-iteration accumulator
    updates live in ``_process_chain_entry``. Both are independently testable.
    """
    log_file = log_path or AUDIT_LOG
    if not log_file.exists():
        return _empty_verify_result()
    key = _ensure_signing_key()
    state = {
        "total": 0, "signed": 0, "unsigned": 0,
        "prev_chain_hash": "", "expected_seq": 1,
        "first_break": None, "break_reason": None,
    }
    for line_num, entry in _iter_chain_entries(log_file):
        _process_chain_entry(entry, line_num, state, key, verbose)
    return {
        "valid": state["first_break"] is None,
        "total_entries": state["total"],
        "signed_entries": state["signed"],
        "unsigned_entries": state["unsigned"],
        "first_break": state["first_break"],
        "break_reason": state["break_reason"],
        "last_sequence": state["expected_seq"] - 1,
    }


def cmd_verify():
    """CLI: Verify audit chain integrity."""
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    result = verify_chain(verbose=verbose)

    print(f"Audit Chain Verification")
    print(f"========================")
    print(f"Total entries:    {result['total_entries']}")
    print(f"Signed entries:   {result['signed_entries']}")
    print(f"Unsigned entries: {result['unsigned_entries']}")
    print(f"Last sequence:    {result['last_sequence']}")
    print()

    if result["valid"]:
        print(f"Chain: VERIFIED (no breaks detected)")
    else:
        print(f"Chain: BROKEN")
        print(f"  First break at sequence: {result['first_break']}")
        print(f"  Reason: {result['break_reason']}")

    return 0 if result["valid"] else 1


def cmd_status():
    """CLI: Show chain status."""
    state = _load_chain_state()
    key_exists = SIGNING_KEY_FILE.exists()

    print(f"Audit Chain Status")
    print(f"==================")
    print(f"Signing key:   {'present' if key_exists else 'not generated'}")
    print(f"Last sequence: {state['sequence']}")
    print(f"Chain hash:    {state['last_chain_hash'][:24]}..." if state["last_chain_hash"] else "Chain hash:    (genesis)")
    print(f"Log file:      {AUDIT_LOG}")
    print(f"Key file:      {SIGNING_KEY_FILE}")


def cmd_sign():
    """CLI: Sign a single JSON entry from stdin or argument."""
    if len(sys.argv) > 2:
        raw = sys.argv[2]
    else:
        raw = sys.stdin.read()

    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        print("Error: invalid JSON", file=sys.stderr)
        return 1

    signed = sign_entry(entry)
    print(json.dumps(signed))
    return 0


def _read_log_entries_skip_bad(log_file: Path):
    """Return ``(entries, skipped_count)``. Blank lines are skipped silently; JSON-decode failures counted."""
    entries = []
    skipped = 0
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    return entries, skipped


def _print_backfill_summary(total: int, already_signed: int, unsigned: int, skipped: int):
    """Operator-facing summary of the audit log shape before any rewrite."""
    print("Audit Chain Backfill")
    print("====================")
    print(f"Total entries:     {total}")
    print(f"Already signed:    {already_signed}")
    print(f"Unsigned:          {unsigned}")
    print(f"Skipped (bad JSON): {skipped}")


def _resign_entries_from_genesis(entries, key: bytes):
    """Strip chain fields, re-sign every entry from sequence 1 with HMAC.

    NOTE: this re-signs historical entries — a chain-heal-floor concern
    (PR #62). It exists because the original ``cmd_backfill`` shipped before
    the chain-heal floor doctrine was articulated; full alignment with
    the floor is a separate (future) issue. Behaviour is preserved here
    verbatim — this decomposition is structural, not semantic.
    """
    prev_chain_hash = ""
    signed_entries = []
    for sequence, entry in enumerate(entries, start=1):
        clean = {k: v for k, v in entry.items() if k not in CHAIN_FIELDS}
        entry_hash = compute_entry_hash(clean)
        chain_hash = compute_chain_hash(key, prev_chain_hash, entry_hash)
        signed = dict(clean)
        signed["entry_hash"] = entry_hash
        signed["chain_hash"] = chain_hash
        signed["sequence"] = sequence
        signed_entries.append(signed)
        prev_chain_hash = chain_hash
    return signed_entries, prev_chain_hash


def _atomic_replace_log(log_file: Path, signed_entries):
    """Write signed entries to a sibling .tmp file then ``os.replace`` for atomicity."""
    tmp_path = log_file.with_suffix(".jsonl.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for entry in signed_entries:
            f.write(json.dumps(entry, default=str) + "\n")
    os.replace(str(tmp_path), str(log_file))


def _finalise_backfill(signed_entries, last_chain_hash: str) -> int:
    """Print completion summary, run verification, return CLI exit code."""
    print("\nBackfill complete:")
    print(f"  Signed:         {len(signed_entries)} entries")
    print(f"  Last sequence:  {len(signed_entries)}")
    print(f"  Chain hash:     {last_chain_hash[:32]}...")
    result = verify_chain()
    if result["valid"]:
        print("  Verification:   PASSED")
        return 0
    print(f"  Verification:   FAILED ({result['break_reason']})")
    return 1


def cmd_backfill():
    """CLI: Sign all unsigned entries in the audit log. Coordinator.

    Carries a chain-heal-floor (PR #62) caveat: this command re-signs
    historical entries, which the floor forbids in newer code. Behaviour
    is preserved verbatim for backward compatibility — aligning ``cmd_backfill``
    with the floor (via supersede entries instead of wholesale re-signing)
    is a separate follow-up.
    """
    dry_run = "--dry-run" in sys.argv
    log_file = AUDIT_LOG
    if not log_file.exists():
        print("No audit log found.", file=sys.stderr)
        return 1
    entries, skipped = _read_log_entries_skip_bad(log_file)
    if not entries:
        print("No entries to backfill.")
        return 0
    unsigned = sum(1 for e in entries if "entry_hash" not in e)
    _print_backfill_summary(len(entries), len(entries) - unsigned, unsigned, skipped)
    if unsigned == 0:
        print("\nAll entries already signed. Nothing to backfill.")
        return 0
    if dry_run:
        print(f"\n[DRY RUN] Would sign {unsigned} entries and reset chain state.")
        return 0
    key = _ensure_signing_key()
    signed_entries, last_chain_hash = _resign_entries_from_genesis(entries, key)
    _atomic_replace_log(log_file, signed_entries)
    _save_chain_state({"last_chain_hash": last_chain_hash, "sequence": len(signed_entries)})
    return _finalise_backfill(signed_entries, last_chain_hash)


def build_verifying_key_envelope(tenant_id: str) -> Optional[dict]:
    """Round 6 — build the .well-known envelope for a per-tenant Ed25519 key.

    Pure function, unit-testable in isolation. Returns the envelope dict on
    success, ``None`` if the tenant has no provisioned keypair.

    The envelope is suitable for publication on your own domain, at:
      ``.well-known/<WELL_KNOWN_KEYS_DIR>/<tenant>.pub``
    (``WELL_KNOWN_KEYS_DIR`` defaults to ``"verifying-keys"``; override per
    deployment via the ``GRIP_WELL_KNOWN_KEYS_DIR`` environment variable.)
    """
    keys = _load_tenant_ed25519_keys(tenant_id)
    if keys is None:
        return None
    _priv, pub, fingerprint = keys
    from cryptography.hazmat.primitives import serialization
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "tenant_id": tenant_id,
        "scheme": SigningScheme.ED25519.value,
        "fingerprint": fingerprint,
        "verifying_key_hex": pub_raw.hex(),
        "verifying_key_bytes": len(pub_raw),
        "well_known_path": f".well-known/{WELL_KNOWN_KEYS_DIR}/{tenant_id}.pub",
        "verify_with": "audit_chain.verify_external(entry, bytes.fromhex(verifying_key_hex))",
    }


def cmd_publish_verifying_key():
    """Round 6 CLI: thin wrapper around ``build_verifying_key_envelope``.

    Reads ``tenant_id`` from ``sys.argv[2]`` per the existing cmd_* CLI
    convention, delegates to the pure function, prints the JSON envelope
    on success, prints to stderr + exits 1 on missing tenant.

    Usage:
      python3 audit_chain.py publish-key <tenant_id>
    """
    if len(sys.argv) < 3:
        print("Usage: audit_chain.py publish-key <tenant_id>", file=sys.stderr)
        return 1
    tenant_id = sys.argv[2]
    envelope = build_verifying_key_envelope(tenant_id)
    if envelope is None:
        print(
            f"Error: tenant {tenant_id!r} has no Ed25519 keypair provisioned at "
            f"{_TENANT_KEY_BASE / tenant_id}",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 audit_chain.py verify [-v]      - Verify chain integrity")
        print("  python3 audit_chain.py status            - Show chain status")
        print("  python3 audit_chain.py sign <json>       - Sign a JSON entry")
        print("  python3 audit_chain.py backfill          - Sign all unsigned entries")
        print("  python3 audit_chain.py publish-key <id>  - Publish Ed25519 verifying key envelope")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "verify":
        sys.exit(cmd_verify())
    elif cmd == "status":
        cmd_status()
    elif cmd == "sign":
        sys.exit(cmd_sign())
    elif cmd == "backfill":
        sys.exit(cmd_backfill())
    elif cmd == "publish-key":
        sys.exit(cmd_publish_verifying_key())
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
