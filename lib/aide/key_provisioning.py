"""Per-tenant key provisioning for the IDR audit chain.

Provisions the classical Ed25519 keypair (``provision_tenant_keys``) and, for
the locked dual-custody (D2, V>> 2026-06-26), the post-quantum ML-DSA-65
(FIPS 204) keypair alongside it (``provision_tenant_pq_keys``). The dual
sign/verify primitive over these keys lives in ``lib.pq_dual_sign``.

Council-ratified design (PR #10-a, round 4 of the #10 signing-hardening arc); its
synthesis is the project memory record of the signing-hardening council, 2026-05-20.

Provisions a per-tenant Ed25519 keypair under ``state/audit/keys/<tenant_id>/``:

* ``signing.key`` — PEM-encoded private key, mode 0600 (operator-only).
* ``verifying.pub`` — PEM-encoded public key, mode 0644 (publishable).

The fingerprint (first 8 hex of SHA256 over the raw 32-byte public key) is the
stable identifier used in IDR ``key_fingerprint`` fields. The public manifest is
``<tenant_id>.pub``, in the folder passed to ``publish_verifying_key_manifest``.
With no folder passed, it goes to ``public/.well-known/<name>/`` in the GRIP
folder, where ``lib/aide/key_publication_folder.py`` names this installation's
published endpoint, and ``grip-verifying-keys`` is the name where that file is
absent.

This module is **idempotent**: re-running ``provision_tenant_keys`` on an
existing tenant returns the existing fingerprint without overwriting the
keypair. Overwriting would break every prior chain entry signed by the
old key (PR #62 chain-heal floor: never invalidate historical signatures).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

try:
    from lib.state_manager import CLAUDE_DIR
except ImportError:
    # Standalone import (e.g. tests that patch the base path). Falls back to ~/.claude.
    CLAUDE_DIR = Path.home() / ".claude"

_TENANT_KEY_BASE = CLAUDE_DIR / "state" / "audit" / "keys"
_NEUTRAL_KEYS_FOLDER = "grip-verifying-keys"


def _published_keys_folder() -> str:
    """The folder name this installation publishes its verifying keys under.

    The documented ``publish <tenant>`` command passes no folder, so the default
    must stay the installation's published endpoint (Codex on GRIP#9903). The name
    is read from the sibling file ``key_publication_folder.py`` by its path, so
    every import style finds it: the package import, the bare ``aide`` import that
    ``lib/aide/decision_logger.py`` uses, and a direct script run. The public build
    withholds that file, and only then is the name the neutral one.
    """
    import importlib.util

    sibling = Path(__file__).with_name("key_publication_folder.py")
    if not sibling.is_file():
        return _NEUTRAL_KEYS_FOLDER
    spec = importlib.util.spec_from_file_location("_grip_key_publication_folder", sibling)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PUBLIC_KEYS_FOLDER


_PUBLIC_DOCROOT_DEFAULT = CLAUDE_DIR / "public" / ".well-known" / _published_keys_folder()


def _compute_key_fingerprint(pub_raw_bytes: bytes) -> str:
    """First 8 hex chars of SHA256(pub_bytes). Stable identifier for the verifying key."""
    return hashlib.sha256(pub_raw_bytes).hexdigest()[:8]


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Write ``data`` to ``path`` atomically with ``mode`` (temp file + os.replace).

    A reader/other process never observes a partially-written key file: the
    bytes land in a sibling ``.tmp`` (chmod'd before publish), then ``os.replace``
    swaps it in atomically. Callers that write a keypair MUST write the PUBLIC
    key before the PRIVATE one, so a crash between the two can only ever leave a
    harmless orphaned public key (overwritten on the next provision) and NEVER a
    private key with no public counterpart (AGORA #3726 Speaker C blocker).
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(mode)
    os.replace(tmp, path)


def tenant_keys_exist(tenant_id) -> bool:
    """True iff both signing + verifying key files exist for this tenant."""
    if not tenant_id:
        return False
    base = _TENANT_KEY_BASE / str(tenant_id)
    return (base / "signing.key").exists() and (base / "verifying.pub").exists()


def _existing_keypair_dict(tenant_id, priv_path: Path, pub_path: Path) -> Dict[str, str]:
    """Idempotent re-discover path — reads existing pub key, no overwrite."""
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(pub_path.read_bytes())
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "tenant_id": str(tenant_id),
        "fingerprint": _compute_key_fingerprint(pub_raw),
        "signing_key_path": str(priv_path),
        "verifying_key_path": str(pub_path),
        "created": False,
    }


def _generate_and_write_keypair(priv_path: Path, pub_path: Path) -> bytes:
    """Generate a fresh Ed25519 keypair, write PEM files with correct modes.

    Returns the raw 32-byte public key bytes (for fingerprint computation).
    Caller is responsible for ensuring the parent directory exists.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    # PUBLIC before PRIVATE, each atomic — a crash can only strand a harmless
    # public key, never a private key with no public counterpart.
    _atomic_write(pub_path, pub_pem, 0o644)
    _atomic_write(priv_path, priv_pem, 0o600)
    return pub_raw


def provision_tenant_keys(tenant_id) -> Dict[str, str]:
    """Generate (or re-discover) an Ed25519 keypair for ``tenant_id``.

    Returns a dict carrying ``tenant_id``, ``fingerprint``, ``signing_key_path``,
    ``verifying_key_path``, and ``created`` (``True`` if this call wrote new
    files, ``False`` if a pre-existing keypair was returned unchanged).

    Idempotent — re-running on an existing tenant returns the existing
    fingerprint without overwriting. The chain-heal floor (PR #62) forbids
    invalidating historical signatures.
    """
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty identifier")
    base = _TENANT_KEY_BASE / str(tenant_id)
    priv_path = base / "signing.key"
    pub_path = base / "verifying.pub"
    if tenant_keys_exist(tenant_id):
        return _existing_keypair_dict(tenant_id, priv_path, pub_path)
    base.mkdir(parents=True, exist_ok=True)
    pub_raw = _generate_and_write_keypair(priv_path, pub_path)
    return {
        "tenant_id": str(tenant_id),
        "fingerprint": _compute_key_fingerprint(pub_raw),
        "signing_key_path": str(priv_path),
        "verifying_key_path": str(pub_path),
        "created": True,
    }


def _mldsa65_paths(tenant_id) -> tuple[Path, Path]:
    """(private, public) ML-DSA-65 key paths under the tenant dir."""
    base = _TENANT_KEY_BASE / str(tenant_id)
    return base / "signing-mldsa65.key", base / "verifying-mldsa65.pub"


def tenant_pq_keys_exist(tenant_id) -> bool:
    """True iff BOTH the Ed25519 keypair AND the ML-DSA-65 keypair exist.

    The locked dual-custody (D2, V>> 2026-06-26) needs both limbs present;
    a half-provisioned tenant (Ed25519 only) is NOT dual-capable.

    Answers ONLY "are the files there". Whether this interpreter can READ them
    is a separate question with a separate answer — see ``pq_backend_available``
    — because the two can and do disagree.
    """
    if not tenant_keys_exist(tenant_id):
        return False
    priv_path, pub_path = _mldsa65_paths(tenant_id)
    return priv_path.exists() and pub_path.exists()


def pq_backend_available() -> bool:
    """True iff this interpreter can actually perform ML-DSA-65 + Ed25519 work.

    Deliberately separate from ``tenant_pq_keys_exist``, because a file-existence
    check is not a capability check and on GRIP's own machine the two disagree.
    Key material lives in the shared tree and is visible to every interpreter,
    but ``cryptography`` is installed only in the GRIP venv — so on the system
    python, ``tenant_pq_keys_exist`` says "dual-capable" while the very first
    signing attempt dies with ``ModuleNotFoundError``.

    A caller that asks only the file question therefore gets a confident yes
    and then crashes. Asking this one too converts that crash into an ordinary,
    announced custody downgrade (or, under
    ``GRIP_IDR_REQUIRE_DUAL=1``, an explicit refusal that names the real cause).

    Both limbs are checked, not just one: the dual seal is an AND, so a build of
    ``cryptography`` too old to carry ML-DSA-65 is just as unable to sign as no
    ``cryptography`` at all, and must be reported the same way rather than
    half-succeeding. The effective threshold is ``cryptography>=48.0.0``, and the
    two earlier numbers this docstring has carried were both wrong. 46 was wrong
    outright. 47.0.0 (2026-04-24) did add the ``mldsa`` module, but only "with
    AWS-LC and BoringSSL", and its own note says "As we ship our wheels with
    OpenSSL, most users will not have access to these APIs yet". 48.0.0
    (2026-05-04) added it "when using OpenSSL 3.5.0 or later", with the note
    "This means post-quantum algorithms are now available to users of our
    wheels". GRIP installs by pip, so the OpenSSL wheel is the path that
    matters: measured on this machine, the venv is cryptography 49.0.0 against
    OpenSSL 4.0.1 and ML-DSA-65 sign+verify succeeds. Source:
    https://github.com/pyca/cryptography/blob/main/CHANGELOG.rst

    No caching. After the first call the modules sit in ``sys.modules`` and the
    check costs a dict lookup, so a cache would buy nothing and would make the
    answer unable to follow the environment it describes.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import mldsa  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: F401
            Ed25519PrivateKey,
        )
    except Exception:  # noqa: BLE001 — any import failure means "cannot sign"
        return False
    return True


def _generate_and_write_mldsa65_keypair(priv_path: Path, pub_path: Path) -> bytes:
    """Generate a fresh ML-DSA-65 (FIPS 204) keypair, write PEM files with modes.

    Returns the raw public-key bytes (for fingerprint computation). Mirrors
    ``_generate_and_write_keypair`` (Ed25519) — PKCS8 PEM private (0600),
    SubjectPublicKeyInfo PEM public (0644) — so both limbs share one on-disk
    convention. Caller ensures the parent directory exists.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import mldsa

    priv = mldsa.MLDSA65PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_raw = pub.public_bytes_raw()
    # PUBLIC before PRIVATE, each atomic (see _atomic_write) — closes the
    # half-provisioned-orphan window AGORA #3726 flagged.
    _atomic_write(pub_path, pub_pem, 0o644)
    _atomic_write(priv_path, priv_pem, 0o600)
    return pub_raw


def _mldsa65_fingerprint_from_pub_file(pub_path: Path) -> str:
    """Re-derive the ML-DSA-65 fingerprint from an existing public PEM file."""
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(pub_path.read_bytes())
    return _compute_key_fingerprint(pub.public_bytes_raw())


def provision_tenant_pq_keys(tenant_id) -> Dict[str, str]:
    """Provision the per-tenant DUAL keyset: Ed25519 + ML-DSA-65 (FIPS 204).

    The locked D2 custody (V>> 2026-06-26): every record that leaves the
    machine or backs an outward claim is dual-signed — classical Ed25519
    (fast, tiny, battle-tested) AND post-quantum ML-DSA-65 (survives a
    quantum adversary), so a record stays verifiable even if ONE primitive is
    later broken. Provisions both private keys (operator-only 0600) and both
    publishable public keys (0644).

    Idempotent — re-running returns existing fingerprints without overwriting
    either keypair (chain-heal floor, PR #62: never invalidate historical
    signatures). The Ed25519 limb reuses ``provision_tenant_keys`` (DRY); this
    function adds the ML-DSA-65 limb alongside it.

    Returns ``tenant_id``, ``ed25519_fingerprint``, ``mldsa65_fingerprint``,
    the four key paths, and ``created`` (``True`` iff this call wrote any new
    key material).
    """
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty identifier")
    ed = provision_tenant_keys(tenant_id)  # idempotent Ed25519 limb (reuse)
    priv_path, pub_path = _mldsa65_paths(tenant_id)
    if priv_path.exists() and pub_path.exists():
        mldsa_fp = _mldsa65_fingerprint_from_pub_file(pub_path)
        mldsa_created = False
    else:
        priv_path.parent.mkdir(parents=True, exist_ok=True)
        mldsa_fp = _compute_key_fingerprint(
            _generate_and_write_mldsa65_keypair(priv_path, pub_path)
        )
        mldsa_created = True
    return {
        "tenant_id": str(tenant_id),
        "ed25519_fingerprint": ed["fingerprint"],
        "mldsa65_fingerprint": mldsa_fp,
        "ed25519_signing_key_path": ed["signing_key_path"],
        "ed25519_verifying_key_path": ed["verifying_key_path"],
        "mldsa65_signing_key_path": str(priv_path),
        "mldsa65_verifying_key_path": str(pub_path),
        "created": bool(ed.get("created")) or mldsa_created,
    }


def publish_verifying_key_manifest(
    tenant_id, public_docroot: Optional[Path] = None
) -> Dict[str, str]:
    """Idempotently copy the tenant's PUBLIC verifying key(s) into the docroot.

    Ed25519 always lands at ``<docroot>/<tenant_id>.pub``. When the tenant is
    ALSO ML-DSA-65 provisioned (dual custody, D2), the post-quantum public key
    lands beside it at ``<docroot>/<tenant_id>-mldsa65.pub``.

    Publishing BOTH limbs is what makes the dual scheme externally checkable at
    all. A dual-signed record can only be verified by an outsider who holds BOTH
    published keys (``lib.pq_dual_sign.dual_verify_external`` fails closed on a
    missing or non-binding limb), so publishing only the classical key would
    leave every dual record unverifiable by the very third party the dual
    signature exists to convince.

    NEVER touches either private key. Per the round-4 council Q6 verdict, this
    is a separate manual operator step from provisioning — provisioning is
    private (operator-only), publication is an external commitment.

    Back-compatible: ``published_path`` and ``fingerprint`` keep meaning the
    Ed25519 limb, and an Ed25519-only tenant publishes exactly as before. The
    ``mldsa65_*`` keys appear only when that limb exists.
    """
    if not tenant_keys_exist(tenant_id):
        raise FileNotFoundError(
            f"Tenant {tenant_id!r} is not provisioned. "
            f"Run provision_tenant_keys({tenant_id!r}) first."
        )

    base = _TENANT_KEY_BASE / str(tenant_id)
    pub_path = base / "verifying.pub"
    docroot = Path(public_docroot) if public_docroot is not None else _PUBLIC_DOCROOT_DEFAULT
    docroot.mkdir(parents=True, exist_ok=True)
    published_path = docroot / f"{tenant_id}.pub"
    shutil.copyfile(pub_path, published_path)
    published_path.chmod(0o644)

    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(published_path.read_bytes())
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    manifest = {
        "tenant_id": str(tenant_id),
        "fingerprint": _compute_key_fingerprint(pub_raw),
        "published_path": str(published_path),
        "docroot": str(docroot),
    }

    # The post-quantum limb, when this tenant has one. Guarded on the file
    # rather than on tenant_pq_keys_exist so publication depends only on the
    # PUBLIC key being present — an operator may legitimately hold the private
    # key elsewhere (an HSM, an offline machine) and still publish.
    _mldsa_priv, mldsa_pub_path = _mldsa65_paths(tenant_id)
    if mldsa_pub_path.exists():
        mldsa_published = docroot / f"{tenant_id}-mldsa65.pub"
        shutil.copyfile(mldsa_pub_path, mldsa_published)
        mldsa_published.chmod(0o644)
        manifest["mldsa65_published_path"] = str(mldsa_published)
        manifest["mldsa65_fingerprint"] = _mldsa65_fingerprint_from_pub_file(
            mldsa_published
        )
    return manifest


_USAGE = (
    "usage: python -m lib.aide.key_provisioning "
    "{provision|provision-pq|publish} <tenant_id> [docroot]\n"
    "  provision     Ed25519 only — NOT dual-capable, records still downgrade\n"
    "  provision-pq  Ed25519 + ML-DSA-65 — the dual custody IDR signing needs\n"
    "  publish       copy the PUBLIC verifying key(s) into a docroot"
)


def _main(argv):
    """Tiny CLI surface — mirrors the operator runbook.

    ``provision-pq`` is the one an operator turning on dual custody wants.
    ``provision`` is kept because ``lib.audit_chain``'s single-Ed25519 chain
    signing legitimately needs only that limb, but it is called out in the
    usage text as not dual-capable: a tenant provisioned with it looks present
    on disk and still downgrades every IDR to the demo seal, which is a quiet
    way to spend an afternoon.
    """
    if len(argv) < 3:
        print(_USAGE, file=sys.stderr)
        return 2
    cmd, tenant_id = argv[1], argv[2]
    if cmd == "provision":
        result = provision_tenant_keys(tenant_id)
    elif cmd == "provision-pq":
        result = provision_tenant_pq_keys(tenant_id)
    elif cmd == "publish":
        docroot = Path(argv[3]) if len(argv) > 3 else None
        result = publish_verifying_key_manifest(tenant_id, docroot)
    else:
        print(f"unknown command: {cmd!r}\n{_USAGE}", file=sys.stderr)
        return 2
    for k, v in result.items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
