"""Tamper-evident signed receipt for a GRIP activation / heal run.

Dogfoods the cryptographic-causation moat's signing FLOOR: the receipt is signed
with the same HMAC-SHA256 primitive the IDR forest uses (`lib.audit_chain`), so it
is verifiable by re-computation — tamper the receipt and `verify_receipt` returns
False. Self-contained (no forest-chain state to corrupt); the full precog
IDR-forest leaf is a richer superset left for a later wave.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent
for _p in (_LIB, _LIB.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from claude_dir import get_claude_dir  # noqa: E402

RECEIPT_VERSION = "activation-receipt/1.0"


def build_receipt(result, *, host: str = "", when: str | None = None) -> dict:
    """Canonical receipt dict from an ActivationResult — deterministic given inputs."""
    ts = when or datetime.now(timezone.utc).isoformat()
    return {
        "version": RECEIPT_VERSION,
        "ts": ts,
        "host": host,
        "installed": bool(result.installed),
        "all_green": bool(result.all_green),
        "servers": [
            {"name": s.name, "status": s.status, "healed": bool(s.healed), "detail": s.detail}
            for s in result.servers
        ],
    }


def _canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _entry_hash(receipt: dict) -> str:
    return hashlib.sha256(_canonical(receipt).encode()).hexdigest()


def _signing_key() -> bytes:
    """The moat's HMAC-SHA256 signing key (same one lib/precog/idr._sign_real uses)."""
    try:
        from audit_chain import _ensure_signing_key  # lib on sys.path
    except ImportError:  # pragma: no cover - path-shape fallback
        from lib.audit_chain import _ensure_signing_key  # CLAUDE_DIR on sys.path
    return _ensure_signing_key()


def sign_receipt(receipt: dict, *, key: bytes | None = None) -> dict:
    """Return ``{receipt, entry_hash, signature}`` — HMAC-SHA256 over the entry hash."""
    k = key if key is not None else _signing_key()
    entry_hash = _entry_hash(receipt)
    signature = hmac.new(k, entry_hash.encode(), hashlib.sha256).hexdigest()
    return {"receipt": receipt, "entry_hash": entry_hash, "signature": signature}


def verify_receipt(signed: dict, *, key: bytes | None = None) -> bool:
    """Recompute the hash + signature; True iff both match. Tamper-evident."""
    try:
        receipt = signed["receipt"]
        entry_hash = _entry_hash(receipt)
        if not hmac.compare_digest(entry_hash, signed.get("entry_hash", "")):
            return False
        k = key if key is not None else _signing_key()
        expected = hmac.new(k, entry_hash.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signed.get("signature", ""))
    except (KeyError, TypeError):
        return False


def write_receipt(signed: dict, grip_home: Path | None = None) -> Path:
    """Persist a signed receipt under state/activation/ for day-two reconciliation."""
    home = grip_home or get_claude_dir()
    ts = signed["receipt"]["ts"]
    slug = "".join(c for c in ts if c.isalnum())
    out_dir = home / "state" / "activation"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"receipt-{slug}.json"
    target.write_text(json.dumps(signed, indent=2) + "\n")
    return target
