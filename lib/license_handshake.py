"""GRIP Suite creator-approval admin-unlock handshake.

Four verbs — ``request`` -> ``issue`` -> ``verify`` -> ``status`` — implement a
creator-approval unlock: a requester asks the creator (V>>) to approve an
admin-unlock of the GRIP Suite on a specific machine; the creator mints a
DYNAMIC, time-bound, nonce-bound, machine-bound code; the requester verifies it,
which writes an admin-unlock marker and emits a signed IDR audit leaf.

No static secret ships
----------------------
The unlock code is NEVER a constant baked into the installer or any doc. Each
code is minted per-request and is:

* **machine-bound** — a sha256 fingerprint of (OS, arch, per-install random
  salt); no hardware serials, so no covert device tracking / PII.
* **nonce-bound** — single-use; a replayed code is rejected once consumed.
* **time-bound** — the grant carries an ``expires_ts`` (default 24h).
* **HMAC-signed** — over the canonical grant body, with a key that lives in
  ``state/grip-suite/unlock-signing.key`` (auto-generated, mode 0600), in
  ``$GRIP_SUITE_UNLOCK_KEY``, or injected. It is per-machine and never in the
  repo/installer/doc. This is a SEPARATE key from the audit-chain signing key
  (``lib.audit_chain``): that key signs every IDR for tamper-evidence and is
  present on every machine that writes IDRs, so reusing it would let any such
  machine mint unlocks. The unlock authority key is its own trust domain.

Security model (v1 HMAC) vs the asymmetric ideal (v2, deferred)
--------------------------------------------------------------
v1 uses a symmetric HMAC key: whoever holds it can both mint and verify, so
creator-approval is enforced by *key custody* (the key lives only on machines
the creator provisions) plus the creator personally running ``issue``. For a
fully-distributed install where the verifier runs on an untrusted requester
machine, the cryptographically-enforced shape is asymmetric signing (the
suite ships only a PUBLIC verification key; only the creator's PRIVATE key can
mint). That is intentionally OUT OF v1 scope and listed under roadmap, not
stubbed here — ``lib.pq_dual_sign`` (Ed25519 + ML-DSA-65) is the intended
substrate.

Failure semantics
-----------------
``verify`` returns ``{"ok": False, "reason": <why>}`` for every rejection
(``malformed_code`` / ``bad_signature`` / ``wrong_kind`` / ``bad_nonce`` /
``expired`` / ``machine_mismatch`` / ``replayed`` /
``unknown_or_expired_challenge`` / ``challenge_expired``) and never raises.
The IDR emission is best-effort: a successful unlock stands even if the audit
leaf fails to write, and the failure is surfaced as ``idr_error`` in the
result (the unlock is the user-facing effect; the leaf is the audit record).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional

_LIB = Path(__file__).resolve().parent
_ROOT = _LIB.parent
for _p in (str(_ROOT), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from claude_dir import get_claude_dir  # noqa: E402
from wa_identities import operator_jid  # noqa: E402

__all__ = ["request", "issue", "verify", "status", "machine_fingerprint"]

GRANT_KIND = "grip-suite-unlock-grant"
IDR_KIND = "grip-suite-unlock-verified"
SCOPE_ADMIN = "admin"
ENV_KEY = "GRIP_SUITE_UNLOCK_KEY"
# Ed25519 asymmetric path (the durable cross-machine fix). The creator signs
# with a PRIVATE key that never leaves their machines; every suite pins the
# PUBLIC key below and verifies with it — so an unlock code can be checked on
# any machine WITHOUT sharing a secret (the HMAC path required a shared key,
# which is why cross-machine unlock returned bad_signature). A signature is
# 128 hex chars ⇒ Ed25519; 64 hex chars ⇒ legacy HMAC (kept for same-machine
# back-compat). The public key is SAFE to ship — it can verify, never mint.
ENV_PUBKEY = "GRIP_SUITE_UNLOCK_PUBKEY"  # override/rotation for the pinned key
# Creator (V>>) Ed25519 public key — raw 32 bytes, hex. Provisioned once via
# provision_creator_keypair(); private key stays in state/grip-suite/ (0600).
CREATOR_ED25519_PUBLIC_HEX = "1ec37322a495c50afd65be59eac83b8486efb7a4db825a1f9a122b27d8cdb086"  # V>> creator key; provisioned 2026-07-07
STATE_SUBDIR = "grip-suite"
DEFAULT_GRANT_TTL_S = 24 * 60 * 60
DEFAULT_CHALLENGE_TTL_S = 10 * 60
# The operator's (V>>) WhatsApp ID, from the private file (lib/wa_identities.py).
# None when none is configured: a request is still created, but nobody is notified.
V_OPERATOR_JID = operator_jid()

_NONCE_RE = re.compile(r"\A[0-9a-fA-F]{8,64}\Z")


# ---------------------------------------------------------------------------
# State layout
# ---------------------------------------------------------------------------

def _state_dir(base: Optional[Path | str] = None) -> Path:
    """Resolve the grip-suite state directory (injectable for tests)."""
    if base is not None:
        return Path(base)
    return get_claude_dir() / "state" / STATE_SUBDIR


def _key_path(state_dir: Path) -> Path:
    return state_dir / "unlock-signing.key"


def _ed25519_priv_path(state_dir: Path) -> Path:
    """Creator's Ed25519 private key (raw 32 bytes, hex, mode 0600). Never
    shipped, never leaves a creator machine — only the public key is pinned."""
    return state_dir / "unlock-ed25519-private.key"


def _pending_dir(state_dir: Path) -> Path:
    return state_dir / "pending"


def _consumed_path(state_dir: Path) -> Path:
    return state_dir / "consumed-nonces.json"


def _consumed_lock_path(state_dir: Path) -> Path:
    return state_dir / "consumed-nonces.lock"


def _marker_path(state_dir: Path) -> Path:
    return state_dir / "admin-unlock.json"


def _install_salt_path(state_dir: Path) -> Path:
    return state_dir / "install-id"


def _write_secure(path: Path, data: str) -> None:
    _write_secure_bytes(path, data.encode("utf-8"))


def _write_secure_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    fdopen_succeeded = False
    try:
        os.chmod(str(tmp), 0o600)
        fh = os.fdopen(fd, "wb")
        fdopen_succeeded = True
        with fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except Exception:
        if not fdopen_succeeded:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Keys, fingerprint, codec
# ---------------------------------------------------------------------------

def _load_key(base: Optional[Path | str] = None, injected: Any = None) -> bytes:
    """Load the unlock signing key (DI > env > per-machine key file).

    The key file is auto-generated on first use (32 random bytes, mode 0600).
    It is per-machine and never shipped — satisfying the "no static secret in
    the installer/doc" requirement.
    """
    if injected is not None:
        return injected if isinstance(injected, bytes) else str(injected).encode()
    env = os.environ.get(ENV_KEY, "").strip()
    if env:
        try:
            return bytes.fromhex(env)
        except ValueError:
            return env.encode()
    kp = _key_path(_state_dir(base))
    if kp.exists():
        return kp.read_bytes()
    key = os.urandom(32)
    _write_secure_bytes(kp, key)
    return key


def machine_fingerprint(base: Optional[Path | str] = None) -> str:
    """Stable, privacy-preserving machine fingerprint.

    sha256 of (OS, arch, per-install random salt) truncated to 32 hex chars.
    The salt is persisted once per install; no hardware serials are read, so
    this cannot be used to covertly correlate the operator's devices.
    """
    sd = _state_dir(base)
    salt_file = _install_salt_path(sd)
    if salt_file.exists():
        salt = salt_file.read_text(encoding="utf-8").strip()
    else:
        salt = secrets.token_hex(16)
        _write_secure(salt_file, salt)
    raw = f"{platform.system()}|{platform.machine()}|{salt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(key: bytes, payload: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


# --- Ed25519 asymmetric path (durable cross-machine unlock) -----------------

def _load_ed25519_private(base: Optional[Path | str] = None, injected: Any = None):
    """Return an Ed25519PrivateKey (from DI hex/obj or the creator key file),
    or None when no private key is present (⇒ issue falls back to HMAC)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if injected is not None:
        if isinstance(injected, Ed25519PrivateKey):
            return injected
        raw = bytes.fromhex(injected) if isinstance(injected, str) else bytes(injected)
        return Ed25519PrivateKey.from_private_bytes(raw)
    pp = _ed25519_priv_path(_state_dir(base))
    if not pp.exists():
        return None  # legitimately absent ⇒ issue() uses the HMAC same-machine path
    try:
        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(pp.read_text().strip()))
    except (ValueError, OSError) as exc:
        # AGORA #3890: a present-but-corrupt key is a provisioning fault, NOT a
        # reason to silently downgrade to a weaker (cross-machine-broken) HMAC
        # signature. Fail loud so the operator re-provisions rather than shipping
        # a silently-degraded unlock code.
        raise RuntimeError(
            f"unlock Ed25519 private key at {pp} exists but is unreadable/corrupt "
            f"({type(exc).__name__}); re-run provision_creator_keypair() — refusing "
            f"to silently fall back to HMAC"
        ) from exc


def _load_ed25519_public(base: Optional[Path | str] = None, injected: Any = None):
    """Return an Ed25519PublicKey from DI > env (rotation) > pinned constant,
    or None when no public key is configured (⇒ no asymmetric verification)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    hex_key = None
    if injected is not None:
        hex_key = injected if isinstance(injected, str) else bytes(injected).hex()
    else:
        hex_key = os.environ.get(ENV_PUBKEY, "").strip() or CREATOR_ED25519_PUBLIC_HEX
    if not hex_key:
        return None
    try:
        return Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key))
    except ValueError:
        return None


def _ed25519_priv_hex(priv) -> str:
    """Raw 32-byte Ed25519 private key as hex (single serialization site)."""
    from cryptography.hazmat.primitives import serialization
    return priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()


def _ed25519_pub_hex(priv) -> str:
    """Raw 32-byte Ed25519 public key as hex, from a private key."""
    from cryptography.hazmat.primitives import serialization
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    ).hex()


def _sign_ed25519(priv, payload: bytes) -> str:
    """128-hex Ed25519 signature over the canonical grant payload."""
    return priv.sign(payload).hex()


def _verify_ed25519(pub, payload: bytes, sig_hex: str) -> bool:
    """True iff sig_hex is a valid Ed25519 signature of payload under pub."""
    from cryptography.exceptions import InvalidSignature
    try:
        pub.verify(bytes.fromhex(sig_hex), payload)
        return True
    except (InvalidSignature, ValueError):
        return False


def provision_creator_keypair(base: Optional[Path | str] = None) -> tuple[str, str]:
    """Idempotently ensure a creator Ed25519 keypair exists. Writes the private
    key (0600) to the grip-suite state dir if absent; returns (private_hex,
    public_hex). The PUBLIC hex is pinned into CREATOR_ED25519_PUBLIC_HEX and
    shipped; the private hex never leaves this machine."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    sd = _state_dir(base)
    pp = _ed25519_priv_path(sd)
    if pp.exists():
        priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(pp.read_text().strip()))
    else:
        priv = Ed25519PrivateKey.generate()
        _write_secure(pp, _ed25519_priv_hex(priv))
    return _ed25519_priv_hex(priv), _ed25519_pub_hex(priv)


def _safe_nonce(nonce: Any) -> Optional[str]:
    """Validate a nonce as hex of bounded length — defends the filesystem store
    against a crafted nonce (defence-in-depth; only signature-valid grants
    reach this point, but the nonce is still attacker-influenced data)."""
    return nonce if isinstance(nonce, str) and _NONCE_RE.match(nonce) else None


# ---------------------------------------------------------------------------
# Pending-challenge + consumed-nonce + marker stores
# ---------------------------------------------------------------------------

def _persist_pending(base: Optional[Path | str], nonce: str, data: dict) -> None:
    _write_secure(_pending_dir(_state_dir(base)) / f"{nonce}.json", json.dumps(data))


def _read_pending(base: Optional[Path | str], nonce: str) -> Optional[dict]:
    p = _pending_dir(_state_dir(base)) / f"{nonce}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _drop_pending(base: Optional[Path | str], nonce: str) -> None:
    try:
        (_pending_dir(_state_dir(base)) / f"{nonce}.json").unlink()
    except OSError:
        pass


def _count_pending(base: Optional[Path | str]) -> int:
    d = _pending_dir(_state_dir(base))
    return sum(1 for _ in d.glob("*.json")) if d.exists() else 0


def _read_consumed(base: Optional[Path | str]) -> dict:
    p = _consumed_path(_state_dir(base))
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _is_consumed(base: Optional[Path | str], nonce: str) -> bool:
    return nonce in _read_consumed(base)


def _consume(base: Optional[Path | str], nonce: str, now: float) -> None:
    consumed = _read_consumed(base)
    cutoff = int(now) - DEFAULT_GRANT_TTL_S
    consumed = {
        key: value
        for key, value in consumed.items()
        if isinstance(key, str) and isinstance(value, int) and value >= cutoff
    }
    consumed[nonce] = int(now)
    _write_secure(_consumed_path(_state_dir(base)), json.dumps(consumed))


@contextmanager
def _consumed_lock(base: Optional[Path | str]):
    sd = _state_dir(base)
    sd.mkdir(parents=True, exist_ok=True)
    lock_path = _consumed_lock_path(sd)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    fdopen_succeeded = False
    try:
        os.chmod(str(lock_path), 0o600)
        lock = os.fdopen(fd, "r+b")
        fdopen_succeeded = True
        with lock:
            if os.name == "nt":
                import msvcrt  # type: ignore[import-not-found]

                lock.seek(0)
                if not lock.read(1):
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except Exception:
        if not fdopen_succeeded:
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _write_marker(
    base: Optional[Path | str], grant: dict, now: float, idr_leaf: Optional[str] = None
) -> Path:
    marker = {
        "unlocked": True,
        "scope": grant["scope"],
        "request_id": grant["request_id"],
        "machine_fp": grant["machine_fp"],
        "grant_ts": grant["grant_ts"],
        "expires_ts": grant["expires_ts"],
        "verified_at": int(now),
        "note": grant.get("note", ""),
        "idr_leaf": idr_leaf,
    }
    path = _marker_path(_state_dir(base))
    _write_secure(path, json.dumps(marker, indent=2))
    return path


def _read_marker(base: Optional[Path | str]) -> Optional[dict]:
    p = _marker_path(_state_dir(base))
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Default side-effect collaborators (DI-injectable)
# ---------------------------------------------------------------------------

def _default_notifier(recipient: str, text: str) -> dict:
    """Deliver the challenge to the creator via the WhatsApp transport shim."""
    from lib.whatsapp_transport import post

    return post(recipient=recipient, text=text)


def _default_idr_emitter(
    *,
    decision: dict,
    fingerprint: str,
    inputs: dict,
    idr_path: Optional[str] = None,
    kind: str = IDR_KIND,
    prompt: str = "grip-suite admin-unlock",
) -> Optional[str]:
    """Emit a signed IDR audit leaf; returns its id.

    ``kind``/``prompt`` default to the suite-unlock leaf, so every existing
    caller keeps byte-identical behaviour. The membership approval layer
    (``lib/grip_unlock_approval.py``) reuses this same emitter with its own
    kind — one emitter, one truth, no copied IDR plumbing.
    """
    from lib.precog.idr import append_idr, build_idr

    idr = build_idr(
        prompt=prompt,
        fingerprint=fingerprint,
        decision=decision,
        predecessor_idr=None,
        depth=0,
        kind=kind,
        inputs=inputs,
    )
    append_idr(idr, path=Path(idr_path) if idr_path else None)
    return idr.id


def _format_request_message(challenge: dict, encoded: str) -> str:
    """Plain-language WhatsApp body for the creator (GRIP self-identifies)."""
    return (
        "GRIP here — admin-unlock request for the GRIP Suite.\n\n"
        f"Requester: {challenge['requester']}\n"
        f"Machine: {challenge['machine_fp']}\n"
        f"Suite: {challenge.get('suite_version', 'unknown')}\n"
        f"Request ID: {challenge['request_id']}\n"
        f"Nonce: {challenge['nonce']}\n\n"
        "To APPROVE, run on your machine:\n"
        f"  grip-unlock issue {encoded}\n\n"
        "Send the resulting code back to the requester. It is single-use, "
        "bound to that machine, and expires in 24h. To DENY, do nothing."
    )


# ---------------------------------------------------------------------------
# Verb 1 — request (requester side)
# ---------------------------------------------------------------------------

def request(
    requester: str,
    *,
    suite_version: str = "unknown",
    base: Optional[Path | str] = None,
    notifier: Optional[Callable[[str, str], dict]] = None,
    recipient: Optional[str] = V_OPERATOR_JID,
    now: Optional[float] = None,
    ttl_seconds: int = DEFAULT_CHALLENGE_TTL_S,
) -> dict:
    """Create an unlock challenge, persist it pending, and notify the creator.

    Returns the challenge (``request_id``, ``nonce``, ``machine_fp``, portable
    base64 ``challenge`` string) plus the notifier ``delivery`` result. With no
    recipient, the notifier is not called and ``delivery`` reports why.
    """
    now = time.time() if now is None else now
    fp = machine_fingerprint(base)
    nonce = secrets.token_hex(16)
    request_id = "gsu-" + secrets.token_hex(6)
    challenge = {
        "requester": requester,
        "machine_fp": fp,
        "nonce": nonce,
        "ts": int(now),
        "suite_version": suite_version,
        "request_id": request_id,
    }
    _persist_pending(base, nonce, {**challenge, "expires_ts": int(now + ttl_seconds)})
    encoded = _b64e(_canon(challenge))
    if recipient:
        delivery = (notifier or _default_notifier)(
            recipient, _format_request_message(challenge, encoded)
        )
    else:
        from wa_identities import MISSING_HINT
        delivery = {"ok": False, "error": MISSING_HINT}
    return {
        "request_id": request_id,
        "nonce": nonce,
        "machine_fp": fp,
        "challenge": encoded,
        "recipient": recipient,
        "delivery": delivery,
    }


# ---------------------------------------------------------------------------
# Verb 2 — issue (creator side)
# ---------------------------------------------------------------------------

def _decode_challenge(challenge: str | dict) -> dict:
    d = challenge if isinstance(challenge, dict) else json.loads(_b64d(challenge.strip()))
    for field in ("request_id", "machine_fp", "nonce"):
        if not d.get(field):
            raise ValueError(f"challenge missing field: {field}")
    if not _safe_nonce(d["nonce"]):
        raise ValueError("challenge nonce is malformed")
    return d


def issue(
    challenge: str | dict,
    *,
    key: Any = None,
    base: Optional[Path | str] = None,
    ttl_seconds: int = DEFAULT_GRANT_TTL_S,
    note: str = "",
    scope: str = SCOPE_ADMIN,
    now: Optional[float] = None,
) -> str:
    """Mint a DYNAMIC, signed, time/nonce/machine-bound unlock code.

    Run by the creator after reviewing the requester + machine in the
    challenge. The returned code is ``<base64url(grant)>.<hmac_sha256_hex>``.
    """
    ch = _decode_challenge(challenge)
    now = time.time() if now is None else now
    grant = {
        "kind": GRANT_KIND,
        "request_id": ch["request_id"],
        "machine_fp": ch["machine_fp"],
        "nonce": ch["nonce"],
        "grant_ts": int(now),
        "expires_ts": int(now + ttl_seconds),
        "scope": scope,
        "suite_version": ch.get("suite_version", "unknown"),
        "note": note,
    }
    payload = _canon(grant)
    # Prefer the asymmetric path: sign with the creator's Ed25519 private key
    # (128-hex sig) so ANY machine can verify with only the pinned public key —
    # no shared secret. Fall back to legacy HMAC (64-hex) when no private key is
    # provisioned, preserving the same-machine behaviour byte-for-byte.
    priv = _load_ed25519_private(base)
    sig = _sign_ed25519(priv, payload) if priv is not None else _sign(_load_key(base, key), payload)
    return _b64e(payload) + "." + sig


# ---------------------------------------------------------------------------
# Verb 3 — verify (requester side)
# ---------------------------------------------------------------------------

def _split_code(code: Any) -> Optional[tuple[str, str]]:
    if not isinstance(code, str) or "." not in code:
        return None
    payload_b64, _, sig = code.strip().partition(".")
    return (payload_b64, sig) if payload_b64 and sig else None


_HEX_RE = re.compile(r"\A[0-9a-fA-F]+\Z")


def _signature_ok(sig: str, payload: bytes, key: Any, base: Optional[Path | str]) -> bool:
    """Verify the code signature, routing by algorithm output size.

    The two lengths are CRYPTOGRAPHICALLY FIXED, not a heuristic (AGORA #3890):
    HMAC-SHA256 is always 32 bytes ⇒ 64 hex; Ed25519 is always 64 bytes ⇒ 128
    hex. They cannot collide. Every mismatch fails CLOSED: a non-hex string, a
    wrong length, a padded HMAC, or random 128-hex noise all fall through to
    ``return False`` or are rejected by the Ed25519/HMAC check itself — no path
    accepts a forged code. Ed25519 (128) verifies with the pinned public key
    alone (cross-machine, no shared secret); HMAC (64) is the same-machine
    legacy path.
    """
    if not isinstance(sig, str) or not _HEX_RE.match(sig):
        return False
    if len(sig) == 128:
        pub = _load_ed25519_public(base, key if isinstance(key, str) and len(key) == 64 else None)
        return pub is not None and _verify_ed25519(pub, payload, sig)
    if len(sig) == 64:
        return hmac.compare_digest(_sign(_load_key(base, key), payload), sig)
    return False


def _validate_code(
    code: Any, key: Any, base: Optional[Path | str], now: float
) -> tuple[Optional[dict], str]:
    """Pure validation — returns (grant, "ok") or (None, reason). No writes."""
    parts = _split_code(code)
    if parts is None:
        return None, "malformed_code"
    payload_b64, sig = parts
    try:
        payload = _b64d(payload_b64)
    except ValueError:  # binascii.Error is a ValueError subclass
        return None, "malformed_code"
    if not _signature_ok(sig, payload, key, base):
        return None, "bad_signature"
    try:
        grant = json.loads(payload)
    except json.JSONDecodeError:
        return None, "malformed_grant"
    if grant.get("kind") != GRANT_KIND:
        return None, "wrong_kind"
    nonce = _safe_nonce(grant.get("nonce"))
    if nonce is None:
        return None, "bad_nonce"
    if int(grant.get("expires_ts", 0)) <= int(now):
        return None, "expired"
    if grant.get("machine_fp") != machine_fingerprint(base):
        return None, "machine_mismatch"
    if _is_consumed(base, nonce):
        return None, "replayed"
    pending = _read_pending(base, nonce)
    if pending is None:
        return None, "unknown_or_expired_challenge"
    if int(pending.get("expires_ts", 0)) <= int(now):
        _drop_pending(base, nonce)
        return None, "challenge_expired"
    return grant, "ok"


def verify(
    code: str,
    *,
    key: Any = None,
    base: Optional[Path | str] = None,
    now: Optional[float] = None,
    idr_emitter: Optional[Callable[..., Optional[str]]] = None,
    idr_path: Optional[str] = None,
) -> dict:
    """Validate an unlock code; on success write the marker + emit an IDR leaf.

    Rejections return ``{"ok": False, "reason": ...}`` and write nothing.
    """
    now = time.time() if now is None else now
    with _consumed_lock(base):
        grant, reason = _validate_code(code, key, base, now)
        if grant is None:
            return {"ok": False, "reason": reason, "idr_leaf": None}
        nonce = grant["nonce"]
        _consume(base, nonce, now)  # single-use: a replay now hits "replayed"

    _drop_pending(base, nonce)
    marker_path = _write_marker(base, grant, now)

    result = {
        "ok": True,
        "reason": "unlocked",
        "scope": grant["scope"],
        "request_id": grant["request_id"],
        "expires_ts": grant["expires_ts"],
        "machine_fp": grant["machine_fp"],
        "marker": str(marker_path),
        "idr_leaf": None,
    }
    emitter = idr_emitter or _default_idr_emitter
    try:
        idr_id = emitter(
            decision={
                "action": "admin-unlock",
                "scope": grant["scope"],
                "request_id": grant["request_id"],
                "nonce": nonce,
                "grant_ts": grant["grant_ts"],
                "expires_ts": grant["expires_ts"],
                "verified": True,
            },
            fingerprint=grant["machine_fp"],
            inputs={"suite_version": grant.get("suite_version"), "note": grant.get("note", "")},
            idr_path=idr_path,
        )
        result["idr_leaf"] = idr_id
        _write_marker(base, grant, now, idr_leaf=idr_id)
    except Exception as exc:  # noqa: BLE001 — unlock stands; surface the audit gap
        result["idr_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return result


# ---------------------------------------------------------------------------
# Verb 4 — status
# ---------------------------------------------------------------------------

def status(*, base: Optional[Path | str] = None, now: Optional[float] = None) -> dict:
    """Report current unlock state, separating LIVE from expired.

    A marker whose ``expires_ts`` has passed reports ``unlocked: False`` with
    ``expired: True`` — never folding a stale grant into the live state
    (rules/lifecycle-closed-or-expired.md).
    """
    now = time.time() if now is None else now
    out: dict[str, Any] = {
        "unlocked": False,
        "expired": False,
        "scope": None,
        "expires_ts": None,
        "seconds_remaining": None,
        "request_id": None,
        "pending_requests": _count_pending(base),
        "machine_fp": machine_fingerprint(base),
        "key_present": _key_path(_state_dir(base)).exists()
        or bool(os.environ.get(ENV_KEY, "").strip()),
    }
    marker = _read_marker(base)
    if marker:
        exp = int(marker.get("expires_ts", 0))
        active = exp > int(now)
        out.update(
            unlocked=active,
            expired=not active,
            scope=marker.get("scope"),
            expires_ts=exp,
            seconds_remaining=max(0, exp - int(now)),
            request_id=marker.get("request_id"),
            idr_leaf=marker.get("idr_leaf"),
        )
    return out
