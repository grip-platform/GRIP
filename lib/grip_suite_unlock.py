#!/usr/bin/env python3
"""grip_suite_unlock — creator-approval admin-unlock handshake for the GRIP Suite.

The unlock is **asymmetric, offline-verifiable, and dynamic per request**. The
installer embeds ONLY the creator's Ed25519 *public* key — never a secret. The
creator (V>>) holds the private key and signs a short-lived, machine-bound,
nonce-bound grant in response to each request. This is the established
"digital handshake" licensing pattern (online issue + offline cryptographic
verify), done correctly: a copy of the installer reveals no signing power.

Threat model the verifier defeats (every branch fail-closed; verdict is the
monotone two-valued GRANT|DENY — `verify_grant` can never return granted=True
unless every required check passed):

  - forge   : no private key in the installer  -> cannot mint a grant
  - replay  : the grant binds machine_fingerprint + nonce + expiry
  - swap    : the signer's pubkey fingerprint is carried in the grant + the IDR
              leaf, so swapping the embedded key is replay-detectable
  - offline : verification needs no network (pure asymmetric signature check)

Crypto is delegated to `cryptography` (Ed25519) behind the `Signer`/`Verifier`
protocols (DIP) so tests inject ephemeral keys and a future pure-python or
HAL-backed signer can swap in. The Ed25519 maths is NEVER hand-rolled here.

This mirrors the existing GRIP crypto posture (`lib/precog/idr.py` tri-state
signing): real signature when key material is present, honest DENY otherwise —
admin simply does not unlock, and the suite keeps working in sovereign
community mode (admin is additive, never blocking).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import platform
import secrets
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

PROTOCOL_VERSION = "grip-suite-unlock/1"
REQUEST_KIND = "grip-suite-unlock-request"
GRANT_KIND = "grip-suite-unlock-grant"
DEFAULT_TTL_S = 900  # 15 minutes — short-lived by construction
KNOWN_TIERS = ("community", "pro", "enterprise")


# --------------------------------------------------------------------------- #
# canonical encoding (stable bytes for signing/verifying — order-independent)  #
# --------------------------------------------------------------------------- #
def canonical(body: dict[str, Any]) -> bytes:
    """Deterministic JSON bytes: sorted keys, no insignificant whitespace.

    Matches `lib.precog.idr._canonical_json`'s contract so a grant body and an
    IDR leaf address the same way. Kept local (a one-line stdlib idiom) so this
    security module is import-self-contained and unit-testable in isolation.
    """
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------- #
# crypto seams (DIP) — Ed25519 delegated to `cryptography`, never hand-rolled  #
# --------------------------------------------------------------------------- #
class Signer(Protocol):
    def sign(self, message: bytes) -> bytes: ...


class Verifier(Protocol):
    def verify(self, message: bytes, signature: bytes) -> bool: ...


class _Ed25519Signer:
    """Creator-side signer. Holds the private key; lives only on V>>'s machine."""

    def __init__(self, private_key_bytes: bytes) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        self._key = Ed25519PrivateKey.from_private_bytes(private_key_bytes)

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)


class _Ed25519Verifier:
    """Installer-side verifier. Holds ONLY the public key (safe to embed/ship)."""

    def __init__(self, public_key_bytes: bytes) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        self._key = Ed25519PublicKey.from_public_bytes(public_key_bytes)

    def verify(self, message: bytes, signature: bytes) -> bool:
        from cryptography.exceptions import InvalidSignature

        try:
            self._key.verify(signature, message)
            return True
        except InvalidSignature:
            return False
        except Exception:
            # Any malformed signature/key state is a DENY, never a crash-open.
            return False


def generate_keypair() -> tuple[bytes, bytes]:
    """Return (private_raw_32, public_raw_32). Creator runs this ONCE."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    priv_b = priv.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    pub_b = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv_b, pub_b


def load_signer(private_key_bytes: bytes) -> Signer:
    return _Ed25519Signer(private_key_bytes)


def load_verifier(public_key_bytes: bytes) -> Verifier:
    return _Ed25519Verifier(public_key_bytes)


def pubkey_fingerprint(public_key_bytes: bytes) -> str:
    """Short, stable fingerprint of the signing pubkey for out-of-band pinning.

    Publish this in the doc + repo + a TOFU record; the verifier and the IDR
    leaf both record it, so a swapped embedded key is detectable on replay.
    """
    return "ed25519:" + hashlib.sha256(public_key_bytes).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# machine fingerprint (privacy-preserving — never leaks a raw hostname/IP)     #
# --------------------------------------------------------------------------- #
def machine_fingerprint(material: dict[str, str]) -> str:
    """Pure: hash of the supplied machine material. Same material -> same fp.

    GDPR/PII-safe: the output is a one-way hash of OS + arch + a per-install
    random salt. No raw hostname, IP, username, or serial leaves this function,
    so the fp is safe to show V>> and to record in the IDR ledger.
    """
    blob = canonical({k: material[k] for k in sorted(material)})
    return "fp:" + hashlib.sha256(blob).hexdigest()[:24]


def _install_salt(state_dir: Path) -> str:
    """Stable-per-install random salt (created once). Makes two identical specs
    distinct without binding to any PII identifier."""
    state_dir.mkdir(parents=True, exist_ok=True)
    salt_file = state_dir / "grip-suite-fp-salt"
    if salt_file.exists():
        return salt_file.read_text(encoding="utf-8").strip()
    salt = secrets.token_hex(16)
    salt_file.write_text(salt, encoding="utf-8")
    try:
        salt_file.chmod(0o600)
    except OSError:
        pass
    return salt


def local_fingerprint(state_dir: Path) -> str:
    """This machine's fingerprint, derived from OS/arch + the persisted salt."""
    return machine_fingerprint(
        {
            "system": platform.system(),
            "machine": platform.machine(),
            "salt": _install_salt(state_dir),
        }
    )


# --------------------------------------------------------------------------- #
# request leg (user -> creator)                                               #
# --------------------------------------------------------------------------- #
def new_request(
    requester: str,
    fingerprint: str,
    *,
    tier: str = "pro",
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Pure: build the unlock request envelope (carries a fresh random nonce)."""
    if tier not in KNOWN_TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {KNOWN_TIERS}")
    ts = float(now if now is not None else time.time())
    return {
        "v": PROTOCOL_VERSION,
        "kind": REQUEST_KIND,
        "requester": requester,
        "fingerprint": fingerprint,
        "tier": tier,
        "nonce": secrets.token_hex(16),
        "ts": ts,
    }


def request_message(request: dict[str, Any]) -> str:
    """Human-readable WhatsApp/Slack text for V>> to approve. Leak-swept:
    shows only truncated, non-PII identifiers."""
    fp = str(request.get("fingerprint", ""))
    nonce = str(request.get("nonce", ""))
    return (
        "GRIP here — admin-unlock request.\n"
        f"Requester: {request.get('requester', '?')}\n"
        f"Tier: {request.get('tier', '?')}\n"
        f"Machine: {fp[:14]}…\n"
        f"Nonce: {nonce[:10]}…\n"
        "Approve  ->  grip-suite issue <request.json>\n"
        "(grant auto-expires; bound to this machine + nonce)"
    )


# --------------------------------------------------------------------------- #
# issue leg (creator-side) — sign a bound, expiring grant                      #
# --------------------------------------------------------------------------- #
def issue_grant(
    request: dict[str, Any],
    signer: Signer,
    *,
    ttl_s: int = DEFAULT_TTL_S,
    now: Optional[float] = None,
    grant_id: Optional[str] = None,
    pubkey_fp: Optional[str] = None,
) -> dict[str, Any]:
    """Creator-side: sign a grant bound to the request's machine + nonce + a TTL.

    Returns {"body": <signed dict>, "sig": <base64 ed25519 sig>}. The body binds
    fingerprint, nonce, exp, tier — so the token is useless on any other machine,
    after expiry, or against any other pending request.
    """
    if request.get("kind") != REQUEST_KIND:
        raise ValueError("not an unlock request")
    issued = float(now if now is not None else time.time())
    body = {
        "v": PROTOCOL_VERSION,
        "kind": GRANT_KIND,
        "requester": request["requester"],
        "fingerprint": request["fingerprint"],
        "tier": request.get("tier", "pro"),
        "nonce": request["nonce"],
        "issued": issued,
        "exp": issued + float(ttl_s),
        "grant_id": grant_id or secrets.token_hex(8),
        "pubkey_fp": pubkey_fp or "",
    }
    sig = signer.sign(canonical(body))
    return {"body": body, "sig": base64.b64encode(sig).decode("ascii")}


# --------------------------------------------------------------------------- #
# verify leg (installer-side) — THE security core. Fail-closed, monotone.      #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Verdict:
    granted: bool
    reason: str
    tier: Optional[str] = None
    grant_id: Optional[str] = None
    exp: Optional[float] = None
    requester: Optional[str] = None


def _deny(reason: str) -> Verdict:
    return Verdict(granted=False, reason=reason)


def _authenticate(
    token: Any, verifier: Verifier
) -> tuple[Optional[dict[str, Any]], Optional[Verdict]]:
    """Crypto + structural gate: is this an authentic, well-formed grant body?

    Returns (body, None) when the token is a syntactically valid grant whose
    Ed25519 signature verifies against the canonical body; otherwise
    (None, <deny verdict>). Pulls the forgery/tamper/shape checks out of
    `verify_grant` so each function owns ONE responsibility (SRP) — never to
    skip a check (every failure still returns a DENY).
    """
    if not isinstance(token, dict):
        return None, _deny("malformed token")
    body = token.get("body")
    sig_b64 = token.get("sig")
    if not isinstance(body, dict) or not isinstance(sig_b64, str):
        return None, _deny("malformed token envelope")
    try:
        sig = base64.b64decode(sig_b64, validate=True)
    except (ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
        return None, _deny("signature not valid base64")
    if not verifier.verify(canonical(body), sig):
        return None, _deny("signature does not verify (forged, tampered, or wrong key)")
    if body.get("kind") != GRANT_KIND:
        return None, _deny("not an unlock grant")
    return body, None


def _check_expiry(body: dict[str, Any], now: Optional[float]) -> Optional[Verdict]:
    """DENY if the grant is at or past its expiry; None if still live.
    Strict `>=` so the exact-expiry boundary is a DENY (mutation-pinned)."""
    current = float(now if now is not None else time.time())
    try:
        exp = float(body["exp"])
    except (TypeError, ValueError):
        return _deny("grant has an unreadable expiry")
    if current >= exp:
        return _deny("grant has expired")
    return None


def verify_grant(
    token: dict[str, Any],
    verifier: Verifier,
    *,
    expected_fingerprint: str,
    expected_nonce: str,
    now: Optional[float] = None,
    pubkey_fp: Optional[str] = None,
) -> Verdict:
    """Verify a grant token against THIS machine and the pending request.

    Order matters and every step is a guard: the function returns a DENY the
    instant any check fails and only reaches the final GRANT when *all* hold.
    There is no code path that returns granted=True with a missing field —
    that monotonicity is the property `tests/test_grip_suite_unlock.py` pins.
    """
    # 1-2. authenticity + shape (signature, envelope, kind) — delegated (SRP)
    body, deny = _authenticate(token, verifier)
    if deny is not None or body is None:
        return deny or _deny("malformed token")

    # required fields present (absence => DENY, never grant)
    for field in ("fingerprint", "nonce", "exp", "tier", "grant_id"):
        if body.get(field) in (None, ""):
            return _deny(f"grant missing required field: {field}")

    # 3. machine binding (replay-on-another-machine defeat)
    if not hmac.compare_digest(str(body["fingerprint"]), str(expected_fingerprint)):
        return _deny("grant is bound to a different machine")

    # 4. nonce binding (replay-against-a-different-request defeat)
    if not hmac.compare_digest(str(body["nonce"]), str(expected_nonce)):
        return _deny("grant does not match the pending request (nonce mismatch)")

    # 5. expiry (strict: now must be BEFORE exp; replay-after-expiry defeat)
    deny = _check_expiry(body, now)
    if deny is not None:
        return deny
    exp = float(body["exp"])

    # 6. tier sanity
    tier = str(body["tier"])
    if tier not in KNOWN_TIERS:
        return _deny(f"grant has an unknown tier: {tier}")

    # 7. optional pinned-signer check (swapped-embedded-key detection)
    if pubkey_fp and not hmac.compare_digest(str(body.get("pubkey_fp", "")), str(pubkey_fp)):
        return _deny("grant was signed by an unexpected key")

    # all guards passed -> the single GRANT exit
    return Verdict(
        granted=True,
        reason="creator-signed, machine-bound, unexpired",
        tier=tier,
        grant_id=str(body["grant_id"]),
        exp=exp,
        requester=str(body.get("requester", "")),
    )


# --------------------------------------------------------------------------- #
# dogfood: emit a signed IDR leaf recording the grant decision                 #
# --------------------------------------------------------------------------- #
def emit_grant_idr(
    verdict: Verdict,
    fingerprint: str,
    pubkey_fp: str,
    *,
    idr_path: Optional[Path] = None,
    appender: Optional[Callable[[Any, Optional[Path]], None]] = None,
    builder: Optional[Callable[..., Any]] = None,
) -> Optional[str]:
    """Compose `lib.precog.idr` to write a tamper-evident leaf for this grant.

    Returns the IDR entry's content address, or None if the IDR substrate is
    unavailable at runtime (an honest runtime-dependency boundary, not a stub —
    the grant still stands; only its ledger leg is degraded, mirroring IDR's own
    sha256-placeholder graceful degradation).
    """
    try:
        if builder is None or appender is None:
            from lib.precog.idr import append_idr as _append
            from lib.precog.idr import build_idr as _build

            builder = builder or _build
            appender = appender or _append
    except ImportError:
        return None

    decision = {
        "decision": "grip-suite-admin-unlock",
        "verdict": "GRANT" if verdict.granted else "DENY",
        "reason": verdict.reason,
        "tier": verdict.tier,
        "grant_id": verdict.grant_id,
        "machine_fp": fingerprint,
        "signer_pubkey_fp": pubkey_fp,
    }
    idr = builder(
        decision_id=f"grip-suite-unlock-{verdict.grant_id or 'deny'}",
        actor="grip-suite",
        decision_anatomy=decision,
    )
    appender(idr, idr_path)
    try:
        from lib.precog.idr import content_addr

        return content_addr(idr.envelope) if hasattr(idr, "envelope") else None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _pub_fp_from_priv(priv: bytes) -> str:
    """Derive the public-key fingerprint from a private key (creator-side)."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        pub_b = Ed25519PrivateKey.from_private_bytes(priv).public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return pubkey_fingerprint(pub_b)
    except Exception:
        return ""


def _cmd_keygen(args: argparse.Namespace) -> int:
    priv, pub = generate_keypair()
    print(json.dumps({
        "private_key_b64": _b64(priv),
        "public_key_b64": _b64(pub),
        "pubkey_fingerprint": pubkey_fingerprint(pub),
        "note": "store private_key_b64 in your keychain (grip-suite-signing-key); "
                "embed/publish only public_key_b64 + pubkey_fingerprint.",
    }, indent=2))
    return 0


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    print(local_fingerprint(Path(args.state_dir)))
    return 0


def _cmd_request(args: argparse.Namespace) -> int:
    fp = local_fingerprint(Path(args.state_dir))
    req = new_request(args.requester, fp, tier=args.tier)
    print(json.dumps(req, indent=2))
    sys.stderr.write("\n--- send this to V>> ---\n" + request_message(req) + "\n")
    return 0


def _cmd_issue(args: argparse.Namespace) -> int:
    request = json.loads(Path(args.request_json).read_text(encoding="utf-8"))
    priv = base64.b64decode(args.private_key_b64, validate=True)
    token = issue_grant(
        request, load_signer(priv), ttl_s=args.ttl, pubkey_fp=_pub_fp_from_priv(priv)
    )
    print(json.dumps(token, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    token = json.loads(Path(args.token_json).read_text(encoding="utf-8"))
    pub = base64.b64decode(args.public_key_b64, validate=True)
    verdict = verify_grant(
        token, load_verifier(pub),
        expected_fingerprint=args.expected_fingerprint,
        expected_nonce=args.expected_nonce, pubkey_fp=pubkey_fingerprint(pub),
    )
    print(json.dumps({
        "granted": verdict.granted, "reason": verdict.reason,
        "tier": verdict.tier, "grant_id": verdict.grant_id,
    }, indent=2))
    return 0 if verdict.granted else 1


_COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "keygen": _cmd_keygen,
    "fingerprint": _cmd_fingerprint,
    "request": _cmd_request,
    "issue": _cmd_issue,
    "verify": _cmd_verify,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grip_suite_unlock", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="creator: generate an Ed25519 keypair (run once)")
    state_default = str(_LIB.parent / "state")
    p_fp = sub.add_parser("fingerprint", help="print this machine's fingerprint")
    p_fp.add_argument("--state-dir", default=state_default)
    p_req = sub.add_parser("request", help="build an unlock request for this machine")
    p_req.add_argument("--requester", required=True)
    p_req.add_argument("--tier", default="pro", choices=KNOWN_TIERS)
    p_req.add_argument("--state-dir", default=state_default)
    p_iss = sub.add_parser("issue", help="creator: sign a grant for a request JSON")
    p_iss.add_argument("request_json")
    p_iss.add_argument("--private-key-b64", required=True)
    p_iss.add_argument("--ttl", type=int, default=DEFAULT_TTL_S)
    p_ver = sub.add_parser("verify", help="installer: verify a grant token JSON")
    p_ver.add_argument("token_json")
    p_ver.add_argument("--public-key-b64", required=True)
    p_ver.add_argument("--expected-fingerprint", required=True)
    p_ver.add_argument("--expected-nonce", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return _COMMANDS[args.cmd](args)


if __name__ == "__main__":
    # This shebang is `/usr/bin/env python3`, which need not be an interpreter
    # that can dual-sign. Without this the tool still runs and still records --
    # it just writes a forgeable demo-sealed record when a real seal was one
    # interpreter away (lib/pq_reexec.py).
    #
    # Imported as top-level `pq_reexec` off THIS checkout, never as
    # `lib.pq_reexec`: by now `lib` may already be bound to another tree's
    # package, and the import would miss the file or read another branch's copy.
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent))
    from pq_reexec import reexec_if_incapable as _reexec

    _reexec(_Path(__file__))

    raise SystemExit(main())
