"""Redacted, deterministic, content-addressed hook audit receipts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .verdicts import HookVerdict, _freeze, _thaw

AUDIT_SCHEMA_VERSION = "grip-hook-audit/1.0"
REDACTION = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "authorization",
        "auth",
        "cookie",
        "credentials",
        "client_secret",
        "id_token",
        "key",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "session_token",
        "set_cookie",
        "token",
    }
)
_COMPACT_SENSITIVE_KEYS = frozenset(item.replace("_", "") for item in _SENSITIVE_KEYS)
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"gh[pousr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|"
    r"AKIA[A-Z0-9]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}"
    r")(?![A-Za-z0-9])"
)
_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"password|passwd|secret|client[_-]?secret|authorization)"
    r"(\s*[:=]\s*)([\"']?)([^\s,\"';}]+)"
)
_PRIVATE_BLOCK = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)


def _normalise_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _sensitive_key(key: Any) -> bool:
    normalised = _normalise_key(key)
    compact = normalised.replace("_", "")
    return normalised in _SENSITIVE_KEYS or compact in _COMPACT_SENSITIVE_KEYS


def _redact_text(value: str) -> str:
    redacted = _PRIVATE_BLOCK.sub(REDACTION, value)
    redacted = _KNOWN_TOKEN.sub(REDACTION, redacted)
    redacted = _BEARER.sub(lambda match: match.group(1) + REDACTION, redacted)
    return _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}{REDACTION}",
        redacted,
    )


def redact(value: Any) -> Any:
    """Recursively copy a JSON-like value while removing common secrets."""

    if isinstance(value, Mapping):
        return {
            str(key): REDACTION if _sensitive_key(key) else redact(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((redact(item) for item in value), key=repr)
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "[REDACTED_BYTES]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _verdict_body(verdict: HookVerdict) -> dict[str, Any]:
    return verdict.to_dict()


def canonical_json(value: Any) -> str:
    """Serialise a redacted JSON value into one stable cryptographic form."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


@dataclass(frozen=True, slots=True)
class AuditReceipt:
    """Immutable receipt whose reference addresses its canonical payload."""

    schema_version: str
    algorithm: str
    digest: str
    reference: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise TypeError("audit payload must be a mapping")
        frozen = _freeze(self.payload)
        assert isinstance(frozen, Mapping)
        object.__setattr__(self, "payload", frozen)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "algorithm": self.algorithm,
            "digest": self.digest,
            "reference": self.reference,
            "payload": _thaw(self.payload),
        }


def _event_body(event: Any) -> Any:
    if isinstance(event, Mapping):
        return event
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError("event must be a mapping or expose to_dict()")


def create_audit_receipt(
    event: Any,
    verdicts: Iterable[HookVerdict],
    final_verdict: HookVerdict,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> AuditReceipt:
    """Create a deterministic receipt without writing to disk.

    Redaction occurs before canonical serialisation and hashing.  The receipt
    consequently cannot retain raw evidence secrets and repeated equivalent
    inputs produce the same content address.
    """

    raw_body = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "event": _event_body(event),
        "verdicts": [_verdict_body(verdict) for verdict in verdicts],
        "final_verdict": _verdict_body(final_verdict),
        "metadata": metadata or {},
    }
    # Validate before redaction so object reprs, NaN, bytes, or non-string keys
    # cannot make an audit address process-dependent.
    body = redact(_thaw(_freeze(raw_body)))
    encoded = canonical_json(body).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return AuditReceipt(
        schema_version=AUDIT_SCHEMA_VERSION,
        algorithm="sha256",
        digest=digest,
        reference=f"sha256:{digest}",
        payload=body,
    )


def verify_audit_receipt(receipt: AuditReceipt) -> bool:
    """Verify that a receipt still addresses its exact payload."""

    digest = hashlib.sha256(canonical_json(_thaw(receipt.payload)).encode("utf-8")).hexdigest()
    return (
        receipt.schema_version == AUDIT_SCHEMA_VERSION
        and receipt.algorithm == "sha256"
        and receipt.digest == digest
        and receipt.reference == f"sha256:{digest}"
    )


make_audit_receipt = create_audit_receipt
