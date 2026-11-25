#!/usr/bin/env python3
"""RFC 8785 JSON Canonicalization Scheme — one byte form, not ours.

WHY. Our seal hashes ``json.dumps(payload, sort_keys=True, ensure_ascii=False)``.
That works and is not a standard, so a verifier written in another language, or a
Python whose ``json`` defaults shift, can disagree about WHICH BYTES to hash and
report tampering on an untouched record. RFC 8785 exists for exactly that. A
surveyed competitor (Asqav, source-available under Elastic License 2.0 — not
MIT, as an earlier revision of this line said) already uses it; we did not.

Measured 2026-09-17, our form against JCS on the same object:

    ours : {"a": "café", "b": 1, "n": 1.0, "z": [3, 1]}
    JCS  : {"a":"café","b":1,"n":1,"z":[3,1]}

Two differences — whitespace after separators, and ``1.0`` where JCS requires ``1``.

WHAT THIS DOES NOT DO, stated plainly because a canonicaliser that quietly gets a
type wrong is worse than none: **it raises on ``float``.** JCS mandates ECMAScript
``Number::toString`` (shortest round-tripping form), which is genuinely fiddly, and
implementing it approximately would produce bytes that look canonical and are not.
Measured across 40 sealed records — 954 values inside the sealed payload — there
were **zero floats**: strings and booleans only. So refusing floats costs nothing
today and refuses loudly the day it would matter, instead of silently hashing a
wrong byte string.

DEPLOYMENT ORDER MATTERS AND THIS FILE IS ONLY HALF OF IT. Switching the SEALER to
JCS would make new records unverifiable by any checkout still computing the legacy
form. So the verifier learns both forms FIRST (keyed on a record's own
``seal_format``), reaches every machine, and only then does the sealer change. This
module is the mechanism; nothing calls it from the sealing path yet, deliberately.
"""
from __future__ import annotations

import json
from typing import Any

#: Records carrying no format field were sealed with the pre-JCS form.
LEGACY_FORMAT = "json-sorted-keys"
#: What a JCS-sealed record declares.
JCS_FORMAT = "rfc8785"


class JcsError(ValueError):
    """Raised for a value RFC 8785 cannot be applied to correctly."""


def _sort_key(key: str) -> tuple:
    """JCS sorts object keys by their UTF-16 code units, not by code point.

    Python's default string ordering compares code points, which differs for
    characters outside the Basic Multilingual Plane — an emoji key sorts one way in
    Python and the other in JCS. Encoding to UTF-16BE and comparing those bytes
    reproduces the spec's ordering.
    """
    return (key.encode("utf-16-be"),)


def _string(value: str) -> str:
    """JSON string escaping, which JCS inherits from ECMAScript ``JSON.stringify``.

    ``json.dumps`` with ``ensure_ascii=False`` already produces exactly this for a
    lone string: minimal escapes, no \\uXXXX for printable non-ASCII. Delegating
    rather than hand-rolling keeps surrogate-pair handling correct.
    """
    return json.dumps(value, ensure_ascii=False)


def canonicalize(obj: Any) -> str:
    """Serialise *obj* to its RFC 8785 canonical form. Raises on unsupported types."""
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, float):
        raise JcsError(
            "RFC 8785 requires ECMAScript Number::toString for floats, which this "
            "module does not implement. Refusing rather than emitting bytes that "
            "look canonical and are not. Convert to int or str at the call site."
        )
    if isinstance(obj, int):
        # Reached only for a genuine int: the bool branch above claims those
        # first, and it MUST stay above this one because bool subclasses int.
        return str(obj)
    if isinstance(obj, str):
        return _string(obj)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in obj) + "]"
    if isinstance(obj, dict):
        # Validate BEFORE sorting. A non-str key reaches _sort_key first and dies
        # with an AttributeError about `.encode`, which tells the caller nothing
        # about what it did wrong.
        for k in obj:
            if not isinstance(k, str):
                raise JcsError(f"JCS object keys must be strings, got {type(k).__name__}")
        items = sorted(obj.items(), key=lambda kv: _sort_key(kv[0]))
        return "{" + ",".join(f"{_string(k)}:{canonicalize(v)}" for k, v in items) + "}"
    raise JcsError(f"no RFC 8785 serialisation for {type(obj).__name__}")


def dumps(obj: Any) -> bytes:
    """The canonical UTF-8 bytes to hash."""
    return canonicalize(obj).encode("utf-8")


def legacy_dumps(obj: Any) -> bytes:
    """The pre-JCS form, kept so old records stay verifiable forever.

    Byte-identical to what ``recompute`` has always produced. It is duplicated here
    rather than imported so that a future edit to the sealer cannot silently change
    how HISTORICAL records are re-hashed — the two forms must be able to disagree.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()


def dumps_for_format(obj: Any, seal_format: str | None) -> bytes:
    """Serialise per the record's OWN declared format.

    ``None`` or absent means legacy, because every record sealed before this module
    existed carries no format field and must keep verifying unchanged.
    """
    if seal_format is not None and not isinstance(seal_format, str):
        # A malformed declaration raises rather than reading as "absent". A record
        # carrying `seal_format: 0` would otherwise verify silently against the
        # legacy form, which is the malformed-reads-as-default failure
        # `binding-memory-records.md` exists to remove: a record that believes it
        # declared a format and did not is indistinguishable from one that never
        # declared one. Measured before fixing: 0, False, [] and {} all read as
        # legacy without complaint.
        raise JcsError(
            f"seal_format must be a string or absent, got {type(seal_format).__name__} "
            f"({seal_format!r}). A malformed declaration is refused rather than "
            "silently treated as the legacy form."
        )
    fmt = seal_format or LEGACY_FORMAT
    if fmt == LEGACY_FORMAT:
        return legacy_dumps(obj)
    if fmt == JCS_FORMAT:
        return dumps(obj)
    raise JcsError(
        f"unknown seal_format {fmt!r} — refusing to guess which bytes to hash. "
        "A record whose format this build does not understand must not be silently "
        "verified against the wrong form."
    )
